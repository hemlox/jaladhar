"""WF-6 build lane L2, module 3 — forcing (hyetograph) series re-derivation.

Re-derives the [96, 16] float32 anchored interval-rate matrix EXACTLY per the
flow-wiring audit's recipe and HARD-ASSERTS its SHA-256 against the identity
the goal-D run froze in ``runs/goal_d_replay2_final_config/manifest.json``:

    sha256(matrix.tobytes()) == forcing_identity.interval_rates_mm_per_halfhour_sha256

Recipe (anchored_forcing.py:77-112, p3-integration; byte-exact chain verified
there by ``verify_anchored_chain``):

1. native IMERG cell mapping over the canonical grid
   (:func:`jaladhar.forcing.imerg.compute_imerg_grid_mapping`) -> 16 distinct
   cells as sorted unique (ilon, ilat) pairs;
2. per 30-min granule whose START lies inside the window, incremental depth
   ``max(precip[ilon, ilat], 0.0) * 0.5`` at those 16 cells
   (:meth:`..._interval_rate_table_from_granules` semantics);
3. multiply by the KSNDMC alert-anchor factors from
   ``alert_anchor_factors_v1.npz`` (float32), giving float32 [96, 16].

Rule 1 hard line: nothing is substituted when inputs are missing.  Absent
granules raise :class:`ForcingUnavailable` carrying ``closes_how``; a matrix
that does NOT reproduce the frozen identity raises :class:`ForcingIdentityError`
with a full inventory dump — a legitimate BLOCKED outcome to report, never a
reason to fabricate climatology.

On success emits ``data/interim/context/forcing_series.json`` plus a rule-6
derivation manifest (written at run START with ``status: "running"``, updated
in place on completion/failure) under
``data/interim/context/forcing_derivation_manifest.json``.

Frame alignment (recorded in both outputs): solver frame i (i < 96) starts at
interval i of this series; frame index 96 ("frame 97", offset 172800 s =
96 x 1800 s) is the post-final-interval end state.

Run standalone:
``python -m jaladhar.web.forcing derive [--out ...] [--manifest-out ...]``
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Forcing hyetograph series derivation (dashboard A11 consumer)."""


REPO_ROOT = Path(__file__).resolve().parents[3]
GOAL_D_MANIFEST = REPO_ROOT / "runs" / "goal_d_replay2_final_config" / "manifest.json"
DEFAULT_FACTORS_NPZ = Path(
    "/home/darshil/Desktop/sih/p3-integration/data/processed/forcing_anchored/"
    "alert_anchor_factors_v1.npz"
)
DEFAULT_OUT = REPO_ROOT / "data" / "interim" / "context" / "forcing_series.json"
DEFAULT_MANIFEST_OUT = (
    REPO_ROOT / "data" / "interim" / "context" / "forcing_derivation_manifest.json"
)

#: Interval length implied by the IMERG half-hourly cadence and the frame
#: series cadence (configs/contracts side); asserted against the manifest.
INTERVAL_MINUTES = 30


class ForcingUnavailable(RuntimeError):
    """A required realised forcing input does not exist locally (rule 1).

    Attributes carry the structured reason so callers (and the final report)
    can state exactly how the blocker would close.
    """

    def __init__(self, message: str, closes_how: str, inventory: dict[str, Any]) -> None:
        super().__init__(message)
        self.closes_how = closes_how
        self.inventory = inventory


class ForcingIdentityError(RuntimeError):
    """The derived matrix does not reproduce the frozen forcing identity."""

    def __init__(self, message: str, inventory: dict[str, Any]) -> None:
        super().__init__(message)
        self.inventory = inventory


def _sha256_array(a: np.ndarray) -> str:
    import hashlib

    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_goal_d_identity(repo_root: Path | None = None) -> dict[str, Any]:
    """The frozen forcing identity block every assertion here targets."""

    root = repo_root if repo_root is not None else REPO_ROOT
    path = GOAL_D_MANIFEST if repo_root is None else root / GOAL_D_MANIFEST.relative_to(REPO_ROOT)
    if not path.is_file():
        raise FileNotFoundError(f"goal-D run manifest missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = payload.get("forcing_identity")
    if not isinstance(identity, dict):
        raise ForcingIdentityError(f"{path} carries no forcing_identity block", {})
    return identity


def resolve_granules_dir(repo_root: Path | None = None) -> Path:
    """Granules directory straight from configs/forcing.yaml (read-only use)."""

    import yaml

    root = repo_root if repo_root is not None else REPO_ROOT
    with open(root / "configs" / "forcing.yaml") as fh:
        cfg = yaml.safe_load(fh)
    rel = (cfg.get("historical") or {}).get("granules_dir")
    if not rel:
        raise ForcingUnavailable(
            "configs/forcing.yaml historical.granules_dir is unset",
            closes_how="set historical.granules_dir to the local IMERG granule "
            "directory and re-run",
            inventory={"config": "configs/forcing.yaml"},
        )
    return root / str(rel)


def inventory_granules(granules_dir: Path) -> list[Path]:
    """Sorted *.HDF5 granules; rule-1 failure with closes_how when absent."""

    files = sorted(granules_dir.glob("*.HDF5")) if granules_dir.is_dir() else []
    if not files:
        inv = {
            "granules_dir": str(granules_dir),
            "exists": granules_dir.is_dir(),
            "n_hdf5": len(files),
        }
        raise ForcingUnavailable(
            f"no IMERG granules under {granules_dir}; per CLAUDE.md rule 1 the "
            "series is NOT derived and no substitute (climatology, synthetic "
            "rainfall) is emitted",
            closes_how=(
                "re-acquire GPM_3IMERGHH v07 late-run granules for "
                "2022-08-28..2022-09-10 (~672 files, ~5.2 GB) into "
                "historical.granules_dir via jaladhar.forcing.fetch_imerg, then "
                "re-run this derivation; only the 96 window granules "
                "(2022-09-04T00:00..2022-09-05T23:30 starts) participate"
            ),
            inventory=inv,
        )
    return files


def _grid_native_mapping(repo_root: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """(unique_pairs, native_cell_ids, n_cells) for the canonical grid."""

    import yaml

    from jaladhar.forcing.imerg import compute_imerg_grid_mapping
    from jaladhar.terrain.grid import build_grid

    root = repo_root if repo_root is not None else REPO_ROOT
    with open(root / "configs" / "forcing.yaml") as fh:
        forcing_cfg = yaml.safe_load(fh)
    with open(root / str(forcing_cfg["domain_config"])) as fh:
        domain_cfg = yaml.safe_load(fh)
    grid, _diag = build_grid(domain_cfg, root)
    ilon_grid, ilat_grid, native_ids, n_cells = compute_imerg_grid_mapping(grid)
    unique_pairs = sorted(set(zip(ilon_grid.flatten(), ilat_grid.flatten(), strict=False)))
    return np.asarray(unique_pairs, dtype=np.int64), native_ids, int(n_cells)


def derive_interval_rates(
    *,
    repo_root: Path | None = None,
    granules_dir: Path | None = None,
    factors_npz: Path | None = None,
) -> dict[str, Any]:
    """Re-derive the [n_intervals, 16] float32 anchored interval-rate matrix.

    Returns the matrix plus realised diagnostics; raises ForcingUnavailable /
    ForcingIdentityError per the module docstring.
    """

    from jaladhar.forcing.imerg import parse_granule_timestamp

    root = repo_root if repo_root is not None else REPO_ROOT
    identity = load_goal_d_identity(repo_root=root)
    consumed = (identity.get("ksndmc_alert_anchor") or {}).get("consumed_window") or {}
    t_start = datetime.fromisoformat(identity_window(consumed, "start"))
    t_end = datetime.fromisoformat(identity_window(consumed, "end"))
    n_expected = int(identity["n_intervals"])
    n_cells_expected = int(identity["distinct_native_cells"])
    expected_matrix_sha = str(identity["interval_rates_mm_per_halfhour_sha256"])
    expected_ids_sha = str(identity["native_cell_ids_sha256"])

    gdir = granules_dir if granules_dir is not None else resolve_granules_dir(repo_root=root)
    files = inventory_granules(gdir)
    npz_path = factors_npz if factors_npz is not None else DEFAULT_FACTORS_NPZ

    # --- factor artifact: byte identity against the producer-frozen SHA (V8).
    realized_npz_sha = _sha256_file(npz_path)
    frozen_npz_sha = str((identity.get("ksndmc_alert_anchor") or {}).get("artifact_sha256") or "")
    if frozen_npz_sha and realized_npz_sha != frozen_npz_sha:
        raise ForcingIdentityError(
            f"anchor-factor NPZ bytes drifted: realized {realized_npz_sha} != "
            f"frozen {frozen_npz_sha}",
            {"factors_npz": str(npz_path), "realized_sha256": realized_npz_sha},
        )

    unique_pairs, native_ids, n_cells = _grid_native_mapping(root)
    ids_dump = {
        "computed_native_cell_ids_shape": list(native_ids.shape),
        "computed_distinct_cells": n_cells,
        "expected_distinct_cells": n_cells_expected,
    }
    if n_cells != n_cells_expected:
        raise ForcingIdentityError(
            f"native cell count {n_cells} != frozen {n_cells_expected}", ids_dump
        )
    computed_ids_sha = _sha256_array(native_ids.astype(np.int32))
    if computed_ids_sha != expected_ids_sha:
        raise ForcingIdentityError(
            "native-cell mapping drift: computed sha differs from the frozen "
            "identity — the terrain grid or the IMERG binning changed",
            {**ids_dump, "computed_sha256": computed_ids_sha, "frozen": expected_ids_sha},
        )
    payload = np.load(npz_path)
    npz_ids_sha = _sha256_array(payload["native_cell_ids"].astype(np.int32))
    if npz_ids_sha != expected_ids_sha:
        raise ForcingIdentityError(
            "anchor NPZ native_cell_ids disagree with the frozen identity",
            {**ids_dump, "npz_sha256": npz_ids_sha, "frozen": expected_ids_sha},
        )

    factors = payload["factors_per_native_id"].astype(np.float32)
    if factors.shape != (n_cells_expected,):
        raise ForcingIdentityError(
            f"factor vector shape {factors.shape} != ({n_cells_expected},)",
            {"factors_shape": list(factors.shape)},
        )

    # --- granule sweep: incremental depth at native cells per window granule.
    rows: list[np.ndarray] = []
    seen: list[str] = []
    for path in files:
        ts = parse_granule_timestamp(path.name)
        if ts < t_start or ts > t_end:
            continue
        import h5py

        with h5py.File(path, "r") as handle:
            precip = handle["Grid/precipitation"][0]
            row = [max(float(precip[i, j]), 0.0) * 0.5 for i, j in unique_pairs]
        rows.append(np.asarray(row, dtype=np.float32))
        seen.append(path.name)

    if len(rows) != n_expected:
        inv = {
            "granules_dir": str(gdir),
            "n_files_total": len(files),
            "window_start_utc": t_start.isoformat(),
            "window_end_utc": t_end.isoformat(),
            "n_window_granules": len(rows),
            "expected": n_expected,
            "first_seen": seen[0] if seen else None,
            "last_seen": seen[-1] if seen else None,
        }
        raise ForcingIdentityError(
            f"window realises {len(rows)} granules, identity expects {n_expected}",
            inv,
        )

    rates_unanchored = np.stack(rows)  # float32 [96, 16]
    anchored = rates_unanchored * factors[np.arange(rates_unanchored.shape[1])][None, :]
    anchored = anchored.astype(np.float32, copy=False)

    realized_matrix_sha = _sha256_array(anchored)
    if realized_matrix_sha != expected_matrix_sha:
        raise ForcingIdentityError(
            "derived interval-rate matrix does NOT reproduce the frozen "
            "forcing identity; per rule 1 nothing is emitted in its place",
            _identity_dump(
                gdir=gdir,
                files=files,
                npz_path=npz_path,
                realized_npz_sha=realized_npz_sha,
                factors=factors,
                rates_unanchored=rates_unanchored,
                anchored=anchored,
                realized_matrix_sha=realized_matrix_sha,
                expected_matrix_sha=expected_matrix_sha,
                window=(t_start, t_end),
            ),
        )

    # --- independent observables (V2): totals the run recorded separately.
    cumulative_canonical = anchored.sum(axis=0)[native_ids.astype(np.int64)]
    areal_mean_total_mm = float(cumulative_canonical.mean())
    peak_hourly_rate_mm_hr = float(anchored.max()) * 60.0 / INTERVAL_MINUTES
    cross_check = {
        "areal_mean_total_mm_realized": areal_mean_total_mm,
        "areal_mean_total_mm_manifest": identity.get("areal_mean_total_mm"),
        "peak_hourly_rate_mm_hr_realized": peak_hourly_rate_mm_hr,
        "peak_hourly_rate_mm_hr_manifest": identity.get("peak_hourly_rate_mm_hr"),
    }

    return {
        "matrix": anchored,
        "sha256": realized_matrix_sha,
        "window_utc": (t_start.isoformat(), t_end.isoformat()),
        "interval_minutes": INTERVAL_MINUTES,
        "n_intervals": int(anchored.shape[0]),
        "n_cells": int(anchored.shape[1]),
        "granules_used": seen,
        "cross_check": cross_check,
        "inputs": {
            "granules_dir": str(gdir),
            "n_files_total": len(files),
            "factors_npz": str(npz_path),
            "factors_npz_sha256": realized_npz_sha,
            "source_manifest": str(GOAL_D_MANIFEST.relative_to(REPO_ROOT)),
        },
    }


def identity_window(consumed: dict[str, Any], key: str) -> str:
    if not consumed.get(key):
        raise ForcingIdentityError(
            f"frozen consumed_window.{key} missing", {"consumed_window": consumed}
        )
    return str(consumed[key])


def _identity_dump(**kw: Any) -> dict[str, Any]:
    rates = kw.pop("rates_unanchored")
    anchored = kw.pop("anchored")
    factors = kw.pop("factors")
    t0, t1 = kw.pop("window")
    return {
        **kw,
        "window_start_utc": t0.isoformat(),
        "window_end_utc": t1.isoformat(),
        "rates_unanchored_shape": list(rates.shape),
        "rates_unanchored_sum": float(rates.sum()),
        "anchored_sum": float(anchored.sum()),
        "factors": [round(float(f), 6) for f in factors],
    }


# ------------------------------------------------------------------ emission


def _git_state(repo_root: Path) -> dict[str, Any]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        paths = [line[3:] for line in dirty.splitlines()]
        return {"git_sha": sha, "git_dirty_paths": paths[:20], "git_dirty_n": len(paths)}
    except Exception as exc:  # noqa: BLE001 - provenance best-effort, recorded
        return {"git_sha": "unknown", "git_error": str(exc)}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def emit_series(
    derived: dict[str, Any], *, out: Path, frames_manifest: Path | None = None
) -> dict[str, Any]:
    """Build and write forcing_series.json from a successful derivation."""

    matrix: np.ndarray = derived["matrix"]
    t0, t1 = derived["window_utc"]
    rate_per_half_hour = matrix / (INTERVAL_MINUTES / 60.0)
    series = {
        "window": {"start_utc": t0, "end_utc": t1},
        "interval_minutes": INTERVAL_MINUTES,
        "n_intervals": int(matrix.shape[0]),
        "areal_mean_mm_hr": [float(v) for v in rate_per_half_hour.mean(axis=1)],
        "peak_cell_mm_hr": [float(v) for v in rate_per_half_hour.max(axis=1)],
        "sha256": derived["sha256"],
        "source_manifest_path": derived["inputs"]["source_manifest"],
        "derived_by": (
            "anchored_forcing recipe (_interval_rate_table_from_granules x "
            "alert_anchor_factors_v1.npz)"
        ),
        "generated_utc": datetime.now(UTC).isoformat(),
        "frame_alignment": _frame_alignment(frames_manifest),
    }
    _write_json(out, series)
    return series


def _frame_alignment(frames_manifest: Path | None = None) -> dict[str, Any]:
    """Frame i (i<96) starts at interval i; frame 97 is the end state."""

    n_frames = 97
    last_offset_seconds = 96 * INTERVAL_MINUTES * 60
    source = "computed"
    candidate = (
        frames_manifest
        if frames_manifest is not None
        else (REPO_ROOT / "runs" / "wf3_replay2_uncoupled_baseline_frames_v1" / "manifest.json")
    )
    if candidate.is_file():
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        entries = raw.get("frames") or []
        if entries:
            n_frames = len(entries)
            last_offset_seconds = int(entries[-1]["offset_seconds"])
            source = str(candidate.relative_to(REPO_ROOT))
    return {
        "note": (
            "frame i (i<96) starts at interval i of this series; frame index "
            "96 ('frame 97') is the post-final-interval end state"
        ),
        "interval_seconds": INTERVAL_MINUTES * 60,
        "expected_last_frame_offset_seconds": 96 * INTERVAL_MINUTES * 60,
        "frames_manifest": source,
        "n_frames": n_frames,
        "last_frame_offset_seconds": last_offset_seconds,
    }


# ------------------------------------------------------------------------- CLI


@app.command()
def derive(
    out: Path = typer.Option(DEFAULT_OUT, help="forcing_series.json output path"),
    manifest_out: Path = typer.Option(DEFAULT_MANIFEST_OUT, help="Rule-6 manifest path"),
    granules_dir: Path | None = typer.Option(None, help="Override granules directory"),
    factors_npz: Path | None = typer.Option(None, help="Override anchor-factor NPZ path"),
    skip_series_write: bool = typer.Option(False, help="--check-only derivation"),
) -> None:
    """Re-derive the anchored forcing series, asserting the frozen identity."""

    repo_root = REPO_ROOT
    resolved_inputs = {
        "out_repo_relative": str(out),
        "manifest_out": str(manifest_out),
        "granules_dir_override": str(granules_dir) if granules_dir else None,
        "factors_npz_override": str(factors_npz) if factors_npz else None,
        "skip_series_write": skip_series_write,
    }
    # Rule-6 lifecycle: manifest written at RUN START, updated in place.
    start_payload = {
        "stage": "wf6_forcing_series_derivation",
        "status": "running",
        "start_time_iso": datetime.now(UTC).isoformat(),
        "resolved_config": resolved_inputs,
        "config_paths": {
            "forcing": "configs/forcing.yaml",
            "identity_source": "runs/goal_d_replay2_final_config/manifest.json",
        },
        **_git_state(repo_root),
    }
    _write_json(manifest_out, start_payload)

    t0 = time.perf_counter()
    try:
        derived = derive_interval_rates(
            repo_root=repo_root, granules_dir=granules_dir, factors_npz=factors_npz
        )
        series = None
        if not skip_series_write:
            series = emit_series(derived, out=out)
        done = {
            **start_payload,
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": time.perf_counter() - t0,
            "sha256": derived["sha256"],
            "n_intervals": derived["n_intervals"],
            "n_cells": derived["n_cells"],
            "window_utc": list(derived["window_utc"]),
            "cross_check": derived["cross_check"],
            "inputs": derived["inputs"],
            "n_granules_used": len(derived["granules_used"]),
            "series_written": series is not None,
            "series_out": str(out) if series is not None else None,
        }
        _write_json(manifest_out, done)
        summary = {
            "sha256": derived["sha256"],
            "n_intervals": derived["n_intervals"],
            "n_cells": derived["n_cells"],
            "areal_mean_mm_hr_peak": max(series["areal_mean_mm_hr"]) if series else None,
            "peak_cell_mm_hr_max": max(series["peak_cell_mm_hr"]) if series else None,
            "cross_check": derived["cross_check"],
            "out": str(out) if series else "(not written)",
        }
        typer.echo(json.dumps(summary, indent=2))
    except BaseException as exc:
        failed = {
            **start_payload,
            "status": "failed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": time.perf_counter() - t0,
            "error": f"{type(exc).__name__}: {exc}",
            "closes_how": getattr(exc, "closes_how", None),
            "inventory": getattr(exc, "inventory", None),
        }
        _write_json(manifest_out, failed)
        raise


if __name__ == "__main__":
    app()
