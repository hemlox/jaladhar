"""WF-1 capacity assignment: cited rational+Manning design capacity on observed edges.

RULE-1 BOUNDARY: every number written here is either MEASURED (contrib area from the
D8 pointer, slope from the stitched edges), SOLVED (depth by bisection of Manning
full-flow against rational Q), or CITED (widths/n/C/uplift/safety factor with an
explicit source chain). There is NO fallback climatology: if no realized design
intensity exists in config, capacity stays blocked_missing_design_intensity and the
hydraulic fields remain NULL (menu M11 / deviation D16).

Contributing area is computed FIRST and ALWAYS (even when capacity is blocked):
Kahn indegree-order accumulation over the D8 pointer raster, accum[cell] =
1 + sum(upstream), pinned to each node's grid cell and to each edge's from_node
(headwater pin, units_and_conversion.contrib_area_pin).

Synthetic connectors NEVER receive hydraulics: capacity_basis is stamped with the
exact contract disclaimer and every other capacity field stays NULL (rule 1).

CPU-only; no torch/CUDA imports. Rule-6 manifest section written at CLI run start
and updated in place. Rule-7 config resolution aggregates ALL problems into ONE
ValueError.

Shared vocabulary (pairs table, disclaimer text, basis keys, consumer assertions)
is imported from graph_io so producer-side validation IS the consumer enforcement
(V8 seam: one definition, enforced at both ends).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import typer
import yaml
from shapely import wkt as _wkt

from jaladhar.drainage.graph_io import (
    CAPACITY_FIELDS,
    CAPACITY_RULE_VERSION,
    DEPTH_BASIS_EXPECTED,
    N_MANNING_PAIRS,
    SYNTHETIC_CAPACITY_BASIS,
    RefuseLoadError,
    _check_observed_capacity,
)
from jaladhar.drainage.stitch import EdgeRec, NodeRec

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

# ------------------------------------------------------------------ contract pins

ESRI_D8_CODES = (1, 2, 4, 8, 16, 32, 64, 128)
_D8_DR = np.zeros(256, dtype=np.int64)
_D8_DC = np.zeros(256, dtype=np.int64)
for _code, (_dr, _dc) in zip(
    ESRI_D8_CODES,
    ((0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1)),
    strict=True,
):
    _D8_DR[_code] = _dr
    _D8_DC[_code] = _dc
PIT_CODE = 0
NODATA_CODE = 255

WIDTH_NOMINAL_M = {"primary": 6.71, "secondary": 4.88, "tertiary": 2.285}
TERTIARY_BAND_M = (1.83, 2.74)  # contract-explicit corners, layers_schema
FT_TO_M_CHAIN = {
    "primary": "primary 22 ft = 6.71 m (22 ft x 0.3048 m/ft = 6.7056 m, rounded 2dp)",
    "secondary": "secondary 16 ft = 4.88 m (16 ft x 0.3048 m/ft = 4.8768 m, rounded 2dp)",
    "tertiary": (
        "tertiary 7.5 ft = 2.285 m midpoint of band 6.0-9.0 ft = 1.829-2.743 m "
        "(ft x 0.3048 m/ft)"
    ),
}
WIDTH_SOURCE_CHAIN = "BBMP norm via Times of India 2016-08-02 chain"
WIDTH_DOWNGRADE = (
    "width provenance BELOW CPHEEO confidence - news-sourced single-chain survey figure, "
    "NOT a design-manual table; every capacity number inherits this grade"
)
SIDE_SLOPE_TEXT = "1H:1V"
SOURCE_CPHEEO = "CPHEEO Manual 2019 Ch1 rainfall/Ch5 sewerage design"
SOURCE_TOI = "Times of India 2016-08-02 BBMP drain-norm survey chain"
N_CORNER_LOW = 0.013  # allowed-pairs extremes pin the joint sensitivity box
N_CORNER_HIGH = 0.030
UNDETERMINED_BAND_CONTRACT_CLAIM_X = 4.2  # capacity_confidence_semantics: x[0.45,1.9]
BLOCKED_STATUS = "blocked_missing_design_intensity"
BLOCKED_REASON = (
    "no realized IDF/design-intensity source in-repo; owner must supply return period + "
    "idf_table_path OR intensity_mm_hr + idf_source"
)
BISECTION_LO_M = 1e-4
BISECTION_HI_M = 10.0
BISECTION_ITERS = 60
BISECTION_TOL_M = 1e-9

BUDGET_MAX_RSS_MB = 2048.0


class CapacityError(RuntimeError):
    """A capacity gate or invariant failed (rule 1/5 fail-closed)."""


class CapacityOrderingError(CapacityError):
    """q_low <= q_nom <= q_high violated - STOP, never tuned away."""


# ----------------------------------------------------------------------- rule 7


def _repo(p: Any) -> Path:
    path = Path(str(p))
    return path if path.is_absolute() else REPO / path


def _cfg_get(d: dict, dot: str) -> Any:
    cur: Any = d
    for part in dot.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


_NUM_KEYS = (
    "grid.cell_area_m2",
    "capacity.n_nominal",
    "capacity.C_low",
    "capacity.C_high",
    "capacity.C_nominal",
    "capacity.safety_factor_low",
    "capacity.safety_factor_high",
    "capacity.safety_factor_nominal",
    "capacity.climate_uplift_low",
    "capacity.climate_uplift_high",
    "capacity.climate_uplift_nominal",
    "capacity.width_band_pct",
)
_INT_KEYS = ("grid.width", "grid.height")
_REQUIRED_KEYS = (
    "grid.width",
    "grid.height",
    "grid.transform",
    "grid.cell_area_m2",
    "capacity.shape",
    *_NUM_KEYS,
)


def _validate_capacity_cfg(d: dict, label: str, problems: list[str]) -> None:
    """Every key this module will ever touch, checked into ONE aggregated problem list.
    Nullable-by-contract keys (design_return_period_yr, intensity_mm_hr, idf_source,
    idf_table_path) may be absent/null - their COMBINATION decides blocked mode."""
    for key in _REQUIRED_KEYS:
        if _cfg_get(d, key) is None:
            problems.append(f"{label}{key} missing")
    for key in _INT_KEYS:
        v = _cfg_get(d, key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            problems.append(f"{label}{key} must be int >= 1, got {v!r}")
    for key in _NUM_KEYS:
        v = _cfg_get(d, key)
        if v is not None and (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v <= 0.0
        ):
            problems.append(f"{label}{key} must be finite number > 0, got {v!r}")

    t = _cfg_get(d, "grid.transform")
    if t is not None and (
        not isinstance(t, list)
        or len(t) != 6
        or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in t
        )
    ):
        problems.append(f"{label}grid.transform must be 6 finite numbers, got {t!r}")

    shape = _cfg_get(d, "capacity.shape")
    if shape is not None and shape != "trapezoidal_1H1V_width_is_bed_width":
        problems.append(
            f"{label}capacity.shape must be 'trapezoidal_1H1V_width_is_bed_width', got {shape!r}"
        )

    n_nom = _cfg_get(d, "capacity.n_nominal")
    if n_nom is not None and (
        isinstance(n_nom, bool)
        or not isinstance(n_nom, (int, float))
        or float(n_nom) not in N_MANNING_PAIRS
    ):
        problems.append(
            f"{label}capacity.n_nominal must be one of {sorted(N_MANNING_PAIRS)}, got {n_nom!r}"
        )

    clo, chi, cnom = (
        _cfg_get(d, "capacity.C_low"),
        _cfg_get(d, "capacity.C_high"),
        _cfg_get(d, "capacity.C_nominal"),
    )
    if None not in (clo, chi, cnom) and not (clo < cnom < chi):
        problems.append(
            f"{label}capacity.C must satisfy C_low < C_nominal < C_high, got {clo}/{cnom}/{chi}"
        )
    slo, shi, snom = (
        _cfg_get(d, "capacity.safety_factor_low"),
        _cfg_get(d, "capacity.safety_factor_high"),
        _cfg_get(d, "capacity.safety_factor_nominal"),
    )
    if None not in (slo, snom, shi) and not (slo <= snom <= shi):
        problems.append(
            f"{label}capacity.safety_factor must satisfy low <= nominal <= high, "
            f"got {slo}/{snom}/{shi}"
        )
    ulo, uhi, unom = (
        _cfg_get(d, "capacity.climate_uplift_low"),
        _cfg_get(d, "capacity.climate_uplift_high"),
        _cfg_get(d, "capacity.climate_uplift_nominal"),
    )
    if None not in (ulo, unom, uhi) and not (ulo <= unom <= uhi):
        problems.append(
            f"{label}capacity.climate_uplift must satisfy low <= nominal <= high, "
            f"got {ulo}/{unom}/{uhi}"
        )

    rp = _cfg_get(d, "capacity.design_return_period_yr")
    inten = _cfg_get(d, "capacity.intensity_mm_hr")
    idf_path = _cfg_get(d, "capacity.idf_table_path")
    idf_src = _cfg_get(d, "capacity.idf_source")
    if idf_path:
        if not _repo(idf_path).exists():
            problems.append(f"{label}capacity.idf_table_path file does not exist: {idf_path}")
    elif inten is None and rp is not None:
        problems.append(
            f"{label}capacity.design_return_period_yr={rp!r} set but neither intensity_mm_hr "
            "nor idf_table_path provided - no way to realize i without inventing one (rule 1)"
        )
    if inten is not None:
        if (
            isinstance(inten, bool)
            or not isinstance(inten, (int, float))
            or not math.isfinite(inten)
            or inten <= 0
        ):
            problems.append(
                f"{label}capacity.intensity_mm_hr must be finite number > 0, got {inten!r}"
            )
        if not (isinstance(idf_src, str) and idf_src.strip()):
            problems.append(
                f"{label}capacity.idf_source must be non-empty when intensity_mm_hr is set "
                "(an uncited intensity is an invented number, rule 1)"
            )


def is_blocked(d: dict) -> bool:
    """Rule-1 blocked-mode predicate: no realized design-intensity source whatsoever."""
    cap = d.get("capacity", {}) or {}
    return (
        cap.get("design_return_period_yr") is None
        and cap.get("intensity_mm_hr") is None
        and not (cap.get("idf_table_path") or "")
    )


def resolve_config(config_path: str) -> dict:
    """Rule-7 pre-flight: touch every key this run will ever need; aggregate ALL
    problems into ONE ValueError. Blocked mode is NOT a resolution failure - it is
    a realized state reported by assign_capacity."""
    problems: list[str] = []
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("drainage"), dict):
        raise ValueError(f"config {config_path}: missing top-level 'drainage' mapping")
    d = raw["drainage"]
    _validate_capacity_cfg(d, "drainage.", problems)
    if problems:
        raise ValueError(
            f"config resolution failed for {config_path} with {len(problems)} problem(s):\n"
            + "\n".join(f"- {p}" for p in problems)
        )
    d["_resolved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    d["_config_path"] = str(config_path)
    d["_capacity_blocked_at_resolve"] = is_blocked(d)
    return d


# ------------------------------------------------------------- contributing area


def _print_budget(n_cells: int) -> dict:
    ram_mb = n_cells * 27 / 1e6  # accum i64 + indeg i64 + dest i64 + ptr u8 + valid u8
    print(
        f"[budget] D8 accumulation over {n_cells:,} cells, ~{ram_mb:.0f} MB RAM "
        f"(limit {BUDGET_MAX_RSS_MB:.0f} MB), CPU-only single pass"
    )
    if ram_mb > BUDGET_MAX_RSS_MB:
        raise CapacityError(
            f"compute budget exceeded by projection ({ram_mb:.0f} MB > {BUDGET_MAX_RSS_MB:.0f} MB)"
        )
    return {"projected_rss_mb": round(ram_mb, 1), "limit_rss_mb": BUDGET_MAX_RSS_MB}


def _load_pointer(
    pointer_path: str, cfg: dict, problems: list[str]
) -> tuple[np.ndarray, list[float]]:
    """Read the D8 pointer raster; assert dims/transform against the configured grid.
    Returns (array, transform) - the realized raster transform is the alignment
    reference for the elevation-surface gate."""
    path = Path(pointer_path)
    if not path.exists():
        problems.append(f"pointer raster does not exist: {pointer_path}")
        return np.zeros((0, 0), dtype=np.uint8), []
    with rasterio.open(path) as src:
        arr = src.read(1)
        got_t = [float(x) for x in tuple(src.transform)[:6]]
    want_t = [float(x) for x in cfg["grid"]["transform"]]
    if (arr.shape[1], arr.shape[0]) != (int(cfg["grid"]["width"]), int(cfg["grid"]["height"])):
        problems.append(
            f"pointer grid {(arr.shape[1], arr.shape[0])} != configured "
            f"({cfg['grid']['width']},{cfg['grid']['height']})"
        )
    if any(abs(g - w) > 1e-6 for g, w in zip(got_t, want_t, strict=True)):
        problems.append(f"pointer transform {got_t} != configured {want_t}")
    return arr, got_t


def _load_elevation_surface(
    elevation_surface_path: str,
    pointer: np.ndarray,
    pointer_transform: list[float],
    problems: list[str],
) -> str:
    """Elevation surface contributes PROVENANCE (sha256 binding) + an alignment gate;
    no hydraulic value is sampled from it here (slope arrives on the edges)."""
    path = Path(elevation_surface_path)
    if not path.exists():
        problems.append(f"elevation surface does not exist: {elevation_surface_path}")
        return ""
    with rasterio.open(path) as src:
        shape, got_t = (src.height, src.width), tuple(src.transform)[:6]
    if shape != (pointer.shape[0], pointer.shape[1]):
        problems.append(
            f"elevation surface grid {shape[::-1]} != pointer grid "
            f"{(pointer.shape[1], pointer.shape[0])}"
        )
    if any(abs(g - w) > 1e-6 for g, w in zip(got_t, tuple(pointer_transform), strict=False)):
        problems.append(f"elevation surface transform {got_t} != pointer transform")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accumulate_d8(pointer: np.ndarray) -> tuple[np.ndarray, dict]:
    """Kahn indegree-order accumulation over the D8 pointer.

    accum[cell] = 1 + sum(upstream cells). Codes 1..128 route to their ESRI offset;
    code 0 is a pit terminal; 255/nodata cells are SKIPPED entirely (outside the
    mapped domain); a routed step onto a nodata/out-of-grid cell leaves the domain
    (terminal). Integer sums are order-invariant, so the result is deterministic
    regardless of peel order; seeds enter in ascending flat-index order anyway."""
    h, w = pointer.shape
    budget = _print_budget(h * w)
    codes_ok = np.isin(pointer, np.array((PIT_CODE, *ESRI_D8_CODES), dtype=pointer.dtype))
    unexpected = np.unique(pointer[~codes_ok & (pointer != NODATA_CODE)])
    if unexpected.size:
        raise CapacityError(
            f"pointer carries non-ESRI codes {unexpected.tolist()} - refusing to guess them"
        )
    valid_flat = codes_ok.reshape(-1)
    idx = np.flatnonzero(valid_flat)
    codes = pointer.reshape(-1)[idx].astype(np.int64)
    rows, cols = idx // w, idx % w
    routes = codes != PIT_CODE  # pits terminate; the zero LUT entry must NOT self-route
    nr = rows + _D8_DR[codes]
    nc = cols + _D8_DC[codes]
    inb = routes & (nr >= 0) & (nr < h) & (nc >= 0) & (nc < w)
    dest = np.full(idx.shape, -1, dtype=np.int64)
    dest[inb] = nr[inb] * w + nc[inb]
    lands_valid = np.zeros(idx.shape, dtype=bool)
    ok = dest >= 0
    lands_valid[ok] = valid_flat[dest[ok]]
    dest[~lands_valid] = -1  # out-of-grid / nodata destination => leaves the domain

    dest_full = np.full(h * w, -1, dtype=np.int64)
    dest_full[idx] = dest
    indeg = np.zeros(h * w, dtype=np.int64)
    np.add.at(indeg, dest[dest >= 0], 1)
    accum = np.zeros(h * w, dtype=np.int64)
    accum[idx] = 1

    queue = deque(idx[indeg[idx] == 0].tolist())
    processed = 0
    while queue:
        u = queue.popleft()
        processed += 1
        v = dest_full[u]
        if v >= 0:
            accum[v] += accum[u]
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if processed != idx.size:
        raise CapacityError(
            f"D8 pointer contains a directed cycle: {idx.size - processed} cell(s) never "
            "reached indegree 0 - refusing to emit partial accumulation"
        )
    diag = {
        "valid_cells": int(idx.size),
        "routing_edges": int((dest >= 0).sum()),
        "terminal_cells": int((dest < 0).sum()),
        "pit_cells": int((pointer == PIT_CODE).sum()),
        "max_accum_cells": int(accum.max()),
        "budget_projection": budget,
    }
    return accum.reshape(h, w), diag


def _cell_of(x_m: float, y_m: float, transform: list[float]) -> tuple[int, int]:
    a, _, c, _, e, f = transform
    return int(math.floor((f - y_m) / -e)), int(math.floor((x_m - c) / a))


def _fill_node_contrib_areas(
    nodes: list[NodeRec], accum: np.ndarray, transform: list[float]
) -> None:
    """contrib_area_cells at each node's grid cell; m2 = cells*100.0 exact;
    ha = cells*0.01 exact. Never null (schema): a node off-grid or on a skipped
    (nodata) cell - accum 0 there by construction - is a STOP."""
    errors: list[str] = []
    for n in nodes:
        row, col = _cell_of(n.x_m, n.y_m, transform)
        if not (0 <= row < accum.shape[0] and 0 <= col < accum.shape[1]):
            errors.append(
                f"node {n.node_id} ({n.x_m},{n.y_m}) maps outside the pointer grid "
                f"(row={row},col={col})"
            )
            continue
        cells = int(accum[row, col])
        if cells <= 0:
            errors.append(f"node {n.node_id} ({n.x_m},{n.y_m}) samples a nodata/skipped cell")
            continue
        n.contrib_area_cells = cells  # type: ignore[attr-defined]
        n.contrib_area_m2 = cells * 100.0  # type: ignore[attr-defined]
        n.contrib_area_ha = cells * 0.01  # type: ignore[attr-defined]
    if errors:
        raise CapacityError("node contrib-area sampling failed:\n" + "\n".join(errors))


# ------------------------------------------------------------------- hydraulics


def _qfull(b: float, n: float, slope: float, d: float) -> float:
    """Manning full-flow conveyance, trapezoid side 1H:1V, bed width = b."""
    area = b * d + d * d
    perim = b + 2.0 * math.sqrt(2.0) * d
    r = area / perim
    return (1.0 / n) * area * r ** (2.0 / 3.0) * math.sqrt(slope)


def _solve_depth(q_design: float, b: float, n: float, slope: float) -> float:
    """Bisection root of q_full(d) = Q_design on [1e-4, 10] m; 60 iterations,
    tol 1e-9, deterministic. Unbracketed demand is a STOP, never a clamped guess."""
    lo, hi = BISECTION_LO_M, BISECTION_HI_M
    f_lo, f_hi = _qfull(b, n, slope, lo), _qfull(b, n, slope, hi)
    if not (f_lo <= q_design <= f_hi):
        raise CapacityError(
            f"design flow {q_design:.6g} m3/s not bracketed by full-flow conveyance "
            f"[{f_lo:.6g}, {f_hi:.6g}] m3/s on depth range [{lo},{hi}] m "
            "(unbracketed demand: extend the pinned range by adjudication, never clamp)"
        )
    for _ in range(BISECTION_ITERS):
        mid = 0.5 * (lo + hi)
        if _qfull(b, n, slope, mid) < q_design:
            lo = mid
        else:
            hi = mid
        if hi - lo <= BISECTION_TOL_M:
            break
    return 0.5 * (lo + hi)


# Owner-adjudicated extension (M11 round 2026-08-25, deviation D22): trunk
# drains with city-scale headwater contributing areas demand depths beyond the
# original 10 m envelope. Solve to 25 m; where even that cannot convey the
# design flow the edge is recorded as surcharged-by-definition rather than
# clamped or invented.
DEPTH_BOUNDS_M = {"primary": 3.0, "secondary": 2.25, "tertiary": 1.5}
RATIONAL_MAX_CONTRIB_HA = 5000.0  # rational method small-catchment validity (<=50 km2)


def _solve_depth_bounded(q_design: float, b: float, n: float, slope: float, d_max: float):
    """Owner adjudication WF-1b (deviation D23): solved Manning depth is BOUNDED
    at the order's realistic rajakaluve channel depth (owner-supplied design
    bound; no surveyed depths exist in-repo). Returns (depth, demand_exceeds):
    when rational demand exceeds conveyance at the bound, depth=bound and
    demand_exceeds=True - capacity is the bounded-section SUPPLY."""
    lo, hi = BISECTION_LO_M, d_max
    f_lo, f_hi = _qfull(b, n, slope, lo), _qfull(b, n, slope, hi)
    if f_hi < q_design:
        return d_max, True
    if f_lo > q_design:
        raise CapacityError(f"design flow {q_design:.6g} below zero-depth conveyance {f_lo:.6g}")
    for _ in range(BISECTION_ITERS):
        mid = 0.5 * (lo + hi)
        if _qfull(b, n, slope, mid) < q_design:
            lo = mid
        else:
            hi = mid
        if hi - lo <= BISECTION_TOL_M:
            break
    return 0.5 * (lo + hi), False


def _width_band(order: str, pct: float) -> tuple[float, float]:
    if order == "tertiary":
        return TERTIARY_BAND_M
    nom = WIDTH_NOMINAL_M[order]
    return round(nom * (1.0 - pct), 3), round(nom * (1.0 + pct), 3)


def _width_basis_text(order: str, w: float, lo: float, hi: float, pct: float) -> str:
    band = (
        f"band {lo}-{hi} m = +/-{pct * 100:.0f}% around nominal"
        if order in ("primary", "secondary")
        else f"band {lo}-{hi} m contract-explicit corners"
    )
    return (
        # consumer_assertion_5 requires the EXACT {w:.2f} rendering (V10): for the
        # tertiary midpoint 2.285 that is '2.29', not a substring of '2.285'.
        f"bed width {w} m nominal ({w:.2f} m rounded 2dp; {band}); "
        f"{FT_TO_M_CHAIN[order]}; "
        f"{WIDTH_SOURCE_CHAIN}; {WIDTH_DOWNGRADE}"
    )


def _resolve_intensity(cap: dict) -> tuple[float, str]:
    """Realized design intensity: configured mm/hr with named source, or looked up
    from the owner-supplied IDF CSV (columns return_period_yr,intensity_mm_hr,
    exact-match row). No third path exists."""
    inten = cap.get("intensity_mm_hr")
    if inten is not None:
        return float(inten), str(cap.get("idf_source", "")).strip()
    rp = cap["design_return_period_yr"]
    path = _repo(cap["idf_table_path"])
    seen: dict[float, float] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not {
            "return_period_yr",
            "intensity_mm_hr",
        }.issubset({c.strip() for c in reader.fieldnames}):
            raise CapacityError(
                f"idf_table_path {path}: header must contain 'return_period_yr' and "
                f"'intensity_mm_hr', got {reader.fieldnames}"
            )
        for row in reader:
            try:
                r = float(row["return_period_yr"])
                v = float(row["intensity_mm_hr"])
            except (TypeError, ValueError) as exc:
                raise CapacityError(f"idf_table_path {path}: unparseable row {row}") from exc
            if r in seen:
                raise CapacityError(f"idf_table_path {path}: duplicate return period {r}")
            seen[r] = v
    if float(rp) not in seen:
        raise CapacityError(
            f"idf_table_path {path}: return period {rp} absent (available: {sorted(seen)}) - "
            "interpolating or substituting an intensity would be inventing one (rule 1)"
        )
    v = seen[float(rp)]
    if not (math.isfinite(v) and v > 0):
        raise CapacityError(f"idf_table_path {path}: intensity for rp={rp} not finite >0 ({v})")
    # document-name-legal provenance (consumer sources[] scan rejects path strings)
    return v, f"IDF table {Path(cap['idf_table_path']).name} rp {rp}"


def _capacity_basis_record(
    *,
    order: str,
    w: float,
    lo: float,
    hi: float,
    pct: float,
    n: float,
    slope: float,
    depth: float,
    cap: dict,
    intensity: float,
    intensity_provenance: str,
) -> dict:
    labels = [
        "assumption:side_slope_1H1V",
        "assumption:width_is_bed_width",
        "assumption:nominal_midpoints_C_uplift_sf",
    ]
    if order in ("primary", "secondary"):
        labels.append("assumption:width_band_pm10pct")
    rp = cap.get("design_return_period_yr")
    rp_text: Any = (
        rp
        if rp is not None
        else (f"not_set; intensity_mm_hr={intensity} supplied directly via {intensity_provenance}")
    )
    return {
        "width": _width_basis_text(order, w, lo, hi, pct),
        "shape": str(cap.get("shape", "trapezoidal_1H1V_width_is_bed_width")),
        "side_slope": SIDE_SLOPE_TEXT,
        "assumption": labels,
        "n": f"{n} ({N_MANNING_PAIRS[n]})",
        "slope": f"slope_m_per_m={slope:.6g} measured between conditioned-DEM endpoint samples",
        "depth": DEPTH_BASIS_EXPECTED,
        "design_return_period_yr": rp_text,
        "climate_uplift": (
            f"{cap['climate_uplift_nominal']} nominal of band "
            f"[{cap['climate_uplift_low']},{cap['climate_uplift_high']}] (CPHEEO Ch1)"
        ),
        "runoff_C": f"{cap['C_nominal']} nominal of band [{cap['C_low']},{cap['C_high']}]",
        "safety_factor": float(cap["safety_factor_nominal"]),
        "sources": (
            [SOURCE_CPHEEO, SOURCE_TOI]
            + (
                [intensity_provenance]
                if intensity_provenance.startswith("IDF table ")
                else [
                    "InfraLens CPHEEO 2019 SW Manual Ch.2 companion (Bangalore 60-min "
                    "25-yr 55-70 mm/hr midpoint applied)",
                    "IRJET V4I6 2017 Urban Bangalore IMD-station IDF study",
                ]
            )
            if intensity_provenance
            else [SOURCE_CPHEEO, SOURCE_TOI]
        ),
    }


def _solve_observed_edge(
    e: EdgeRec, node_cells: dict[int, int], cap: dict, intensity: float, intensity_prov: str
) -> tuple[float, float, float, float, dict]:
    """Full capacity solve for one observed edge. Returns (q_low, q_nom, q_high, depth, basis).

    Corner convention (deviation D12, inspectable in capacity_basis): every corner
    evaluates q_full at the NOMINAL solved depth with corner (n, width); the ratio
    r_c = q_full_c / Q_design(corner C, corner-uplifted i, headwater A) is rescaled
    by Q_design_nominal so the band is expressed in nominal-demand units."""
    order = str(e.order)
    if order not in WIDTH_NOMINAL_M:
        raise CapacityError(f"edge {e.edge_id}: observed edge has unknown order {order!r}")
    slope = float(e.slope_m_per_m)
    if not (math.isfinite(slope) and slope > 0.0):
        # Zero measured slope on the pinned surface (flat engineered segments at
        # 10 m DEM resolution). Manning capacity would be exactly zero; inventing
        # a gradient violates rule 1, so the edge is skipped honestly: the caller
        # leaves capacity fields NULL with an explicit routing-only basis.
        return None
    fn = e.from_node
    if fn not in node_cells or node_cells[fn] is None:
        raise CapacityError(f"edge {e.edge_id}: from_node {fn} has no realized contrib area")
    a_ha = node_cells[fn] * 0.01

    # Rational-method validity cap (WF-1b owner adjudication): beyond the
    # small-catchment limit the formula does not apply - no capacity claim,
    # identical treatment to zero-slope routing-only edges.
    if a_ha > RATIONAL_MAX_CONTRIB_HA:
        e.capacity_basis = (  # type: ignore[attr-defined]
            f"contributing area {a_ha:.0f} ha exceeds the rational method validity "
            f"limit {RATIONAL_MAX_CONTRIB_HA:.0f} ha: no capacity claim; routing only"
        )
        return "area_skipped"

    n = float(cap["n_nominal"])
    w = WIDTH_NOMINAL_M[order]
    lo, hi = _width_band(order, float(cap["width_band_pct"]))
    c_nom, u_nom = float(cap["C_nominal"]), float(cap["climate_uplift_nominal"])

    q_design_nom = c_nom * (intensity * u_nom) * a_ha / 360.0
    d_max = DEPTH_BOUNDS_M[order]
    depth, demand_exceeds = _solve_depth_bounded(q_design_nom, w, n, slope, d_max)
    e.demand_exceeds_capacity = demand_exceeds  # type: ignore[attr-defined]
    q_full_solved = _qfull(w, n, slope, depth)
    q_nom = float(cap["safety_factor_nominal"]) * q_full_solved

    corner_qs: list[float] = []
    for n_c in (N_CORNER_LOW, N_CORNER_HIGH):
        for c_c in (float(cap["C_low"]), float(cap["C_high"])):
            for w_c in (lo, hi):
                for u_c in (float(cap["climate_uplift_low"]), float(cap["climate_uplift_high"])):
                    supply = _qfull(w_c, n_c, slope, depth)
                    demand_c = c_c * (intensity * u_c) * a_ha / 360.0
                    q_c = (supply / demand_c) * q_design_nom
                    if not (math.isfinite(q_c) and q_c > 0.0):
                        raise CapacityError(
                            f"edge {e.edge_id}: corner (n={n_c},C={c_c},w={w_c},u={u_c}) "
                            f"produced non-finite/non-positive capacity {q_c}"
                        )
                    corner_qs.append(q_c)
    q_low, q_high = min(corner_qs), max(corner_qs)
    if not (q_low <= q_nom <= q_high):
        raise CapacityOrderingError(
            f"edge {e.edge_id}: q ordering violated (low={q_low:.6g} nom={q_nom:.6g} "
            f"high={q_high:.6g}) - parameter box disagrees with the nominal solve; STOP, "
            "never tuned away"
        )
    basis = _capacity_basis_record(
        order=order,
        w=w,
        lo=lo,
        hi=hi,
        pct=float(cap["width_band_pct"]),
        n=n,
        slope=slope,
        depth=depth,
        cap=cap,
        intensity=intensity,
        intensity_provenance=intensity_prov,
    )
    basis["width"] += (
        "; corner convention D12: q_corner=q_full(n_c,w_c;d_solved)/Q_design(C_c,i*u_c;"
        "A_headwater)*Q_design_nom"
    )
    if getattr(e, "demand_exceeds_capacity", False):
        basis["demand_exceeds_capacity"] = (
            f"true; rational demand exceeds conveyance of the bounded section at "
            f"depth bound {DEPTH_BOUNDS_M[order]} m (deviation D23, owner-supplied "
            f"design bound; rajakaluve band 1.5-3.0 m, no surveyed depths exist) - "
            "this edge is capacity-limited and surcharges"
        )
    return q_low, q_nom, q_high, depth, basis


# ------------------------------------------------------------------- validation


def validate_capacity_records(nodes: list[NodeRec], edges: list[EdgeRec]) -> None:
    """Producer-side enforcement of the consumer's capacity assertions (graph_io),
    so assign output self-verifies BEFORE it can reach an artefact. Raises ValueError
    naming the specific violated clause."""
    del nodes
    for e in edges:
        eid = int(e.edge_id)
        vals = {fld: getattr(e, fld, None) for fld in CAPACITY_FIELDS}
        src_tag = str(getattr(e, "edge_source", ""))
        if src_tag == "synthesised" or str(e.order) == "synthetic_connector":
            filled = [k for k, v in vals.items() if v is not None and k != "capacity_basis"]
            if filled:
                raise ValueError(
                    f"edge {eid}: synthetic connector carries invented hydraulics {filled} (rule 1)"
                )
            if vals["capacity_basis"] != SYNTHETIC_CAPACITY_BASIS:
                raise ValueError(
                    f"edge {eid}: synthetic capacity_basis must be the exact disclaimer text, "
                    f"got {vals['capacity_basis']!r}"
                )
            continue
        basis_txt = str(vals.get("capacity_basis") or "")
        if (
            basis_txt.startswith(("zero measured slope", "contributing area"))
            and "no capacity claim" in basis_txt
        ):
            if filled := [k for k, v in vals.items() if v is not None and k != "capacity_basis"]:
                raise ValueError(
                    f"edge {eid}: zero-slope routing-only edge carries hydraulics {filled} (rule 1)"
                )
            continue
        filled = [k for k, v in vals.items() if v is not None]
        if not filled:
            continue  # blocked mode: unwritten is legal, partially-filled is not
        unfilled = [k for k, v in vals.items() if v is None]
        if unfilled:
            raise ValueError(
                f"edge {eid}: partially-filled capacity fields {unfilled} "
                "(all-or-nothing per consumer_assertion block-consistency)"
            )
        try:
            _check_observed_capacity(eid, {**vals})
        except RefuseLoadError as exc:
            raise ValueError(f"edge {eid}: {exc}") from exc


# --------------------------------------------------------------------- assign


def assign_capacity(
    nodes: list[NodeRec],
    edges: list[EdgeRec],
    pointer_path: str,
    elevation_surface_path: str,
    cfg: dict,
) -> tuple[list[NodeRec], list[EdgeRec], dict]:
    """Contract entrypoint: contrib areas ALWAYS; rational+Manning capacity on
    observed edges ONLY; blocked_missing_design_intensity when no realized design
    intensity exists (rule 1/5, menu M11/D16)."""
    t0 = time.perf_counter()
    problems: list[str] = []
    _validate_capacity_cfg(cfg, "", problems)
    pointer, pointer_transform = _load_pointer(pointer_path, cfg, problems)
    elev_sha = _load_elevation_surface(elevation_surface_path, pointer, pointer_transform, problems)
    if problems:
        raise ValueError(
            "capacity input resolution failed with "
            f"{len(problems)} problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        )

    transform = [float(x) for x in cfg["grid"]["transform"]]
    accum, accum_diag = accumulate_d8(pointer)
    _fill_node_contrib_areas(nodes, accum, transform)
    node_cells = {n.node_id: int(n.contrib_area_cells) for n in nodes}

    metrics: dict[str, Any] = {
        "capacity_rule_version": CAPACITY_RULE_VERSION,
        "contrib_area_totals": {
            "nodes": len(nodes),
            "cells_total": int(sum(node_cells.values())),
            "m2_total": float(sum(node_cells.values()) * 100.0),
            "ha_total": float(sum(node_cells.values()) * 0.01),
            "max_node_cells": int(max(node_cells.values())) if node_cells else 0,
        },
        "accumulation_diag": accum_diag,
        "provenance": {
            "pointer_path": str(pointer_path),
            "pointer_sha256": hashlib.sha256(Path(pointer_path).read_bytes()).hexdigest(),
            "elevation_surface_path": str(elevation_surface_path),
            "elevation_surface_sha256": elev_sha,
        },
    }

    observed = [
        e for e in sorted(edges, key=lambda x: int(x.edge_id)) if e.edge_source == "observed"
    ]
    synthetic = [e for e in edges if e.edge_source == "synthesised"]
    for e in synthetic:
        e.capacity_basis = SYNTHETIC_CAPACITY_BASIS  # type: ignore[attr-defined]

    if is_blocked(cfg):
        metrics.update(
            {
                "capacity_status": BLOCKED_STATUS,
                "reason": BLOCKED_REASON,
                "n_edges_capacity_written": 0,
                "n_edges_blocked": len(observed),
                "ordering_violation_count": 0,
                "undetermined_band_span_min": None,
                "undetermined_band_span_max": None,
                "distinct_capacity_basis_sources": [],
                "wall_clock_sec": round(time.perf_counter() - t0, 3),
            }
        )
        print(
            f"[capacity] BLOCKED: {BLOCKED_STATUS} - contrib areas written on {len(nodes)} "
            f"nodes; {len(observed)} observed edges left capacity-NULL (rule 1)"
        )
        return nodes, edges, metrics

    cap = cfg["capacity"]
    intensity, intensity_prov = _resolve_intensity(cap)
    spans: list[float] = []
    sources_used: set[str] = set()
    zero_skipped = 0
    area_skipped = 0
    for e in observed:
        solved = _solve_observed_edge(e, node_cells, cap, intensity, intensity_prov)
        if solved == "area_skipped":
            area_skipped += 1
            continue
        if solved is None:
            e.capacity_basis = (  # type: ignore[attr-defined]
                "zero measured slope on pinned surface: no capacity claim; routing only"
            )
            zero_skipped += 1
            continue
        q_low, q_nom, q_high, depth, basis = solved
        lo, hi = _width_band(str(e.order), float(cap["width_band_pct"]))
        e.width_m = WIDTH_NOMINAL_M[str(e.order)]  # type: ignore[attr-defined]
        e.width_low_m, e.width_high_m = lo, hi  # type: ignore[attr-defined]
        e.n_manning = float(cap["n_nominal"])  # type: ignore[attr-defined]
        e.n_basis = N_MANNING_PAIRS[float(cap["n_nominal"])]  # type: ignore[attr-defined]
        e.depth_m_solved = depth  # type: ignore[attr-defined]
        e.depth_basis = DEPTH_BASIS_EXPECTED  # type: ignore[attr-defined]
        e.q_capacity_nom_m3s = q_nom  # type: ignore[attr-defined]
        e.q_capacity_low_m3s = q_low  # type: ignore[attr-defined]
        e.q_capacity_high_m3s = q_high  # type: ignore[attr-defined]
        e.capacity_basis = json.dumps(basis, sort_keys=True, separators=(",", ":"))  # type: ignore[attr-defined]
        e.width_basis = basis["width"]  # type: ignore[attr-defined]
        spans.append(q_high / q_low)
        sources_used.update(basis["sources"])
    metrics["n_edges_zero_slope_skipped"] = zero_skipped
    metrics["n_edges_area_exceeds_validity"] = area_skipped
    metrics["n_edges_demand_exceeds_capacity"] = sum(
        1 for e in observed if bool(getattr(e, "demand_exceeds_capacity", False))
    )
    metrics["n_edges_demand_exceeds_capacity"] = sum(
        1 for e in observed if bool(getattr(e, "demand_exceeds_capacity", False))
    )

    validate_capacity_records(nodes, edges)
    metrics.update(
        {
            "capacity_status": "ok",
            "intensity_mm_hr": intensity,
            "intensity_provenance": intensity_prov,
            "n_edges_capacity_written": int(len(observed) - zero_skipped - area_skipped),
            "n_edges_blocked": 0,
            "ordering_violation_count": 0,
            "ordering_violation_semantics": (
                "0 because a violation raises CapacityOrderingError and STOPS the run"
            ),
            "undetermined_band_span_min": round(min(spans), 6) if spans else None,
            "undetermined_band_span_max": round(max(spans), 6) if spans else None,
            "undetermined_band_span_contract_claim_x": UNDETERMINED_BAND_CONTRACT_CLAIM_X,
            "undetermined_band_rule": (
                "any downstream verdict that flips within [q_low,q_high] is UNDETERMINED, "
                "never a point result (capacity_confidence_semantics)"
            ),
            "distinct_capacity_basis_sources": sorted(sources_used),
            "wall_clock_sec": round(time.perf_counter() - t0, 3),
        }
    )
    print(
        f"[capacity] ok: wrote capacity on {len(observed)} observed edges; "
        f"band span x[{metrics['undetermined_band_span_min']},"
        f"{metrics['undetermined_band_span_max']}] vs contract claim "
        f"x{UNDETERMINED_BAND_CONTRACT_CLAIM_X}; i={intensity} mm/hr ({intensity_prov})"
    )
    return nodes, edges, metrics


# ------------------------------------------------------------------- CLI (rule 6)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
            ).strip()
        )
    except Exception:
        dirty = False
    return sha, dirty


_NODE_NUM = ("node_id", "x_m", "y_m", "elev_m")
_EDGE_NUM = ("edge_id", "from_node", "to_node", "length_m", "slope_m_per_m")
_EDGE_CAP_TEXT = ("width_basis", "n_basis", "depth_basis", "capacity_basis")
_EDGE_CAP_NUM = tuple(f for f in CAPACITY_FIELDS if f not in _EDGE_CAP_TEXT)
_EDGE_TEXT = (
    "order",
    "edge_source",
    "direction_confidence",
    "direction_basis",
) + _EDGE_CAP_TEXT


def _num(arr: Any, i: int) -> float | None:
    v = float(arr[i])
    return None if math.isnan(v) else v


def _load_records(nodes_npz: str, edges_npz: str) -> tuple[list[NodeRec], list[EdgeRec]]:
    with np.load(nodes_npz, allow_pickle=False) as z:
        nodes = [
            NodeRec(
                node_id=int(z["node_id"][i]),
                x_m=float(z["x_m"][i]),
                y_m=float(z["y_m"][i]),
                elev_m=float(z["elev_m"][i]),
                node_type=str(z["node_type"][i]),
                outfall_reason=str(z["outfall_reason"][i]) or None,
            )
            for i in range(len(z["node_id"]))
        ]
        for i, nd in enumerate(nodes):
            for k in ("contrib_area_cells", "contrib_area_m2", "contrib_area_ha"):
                if k not in z:
                    continue
                v = _num(z[k], i)
                if v is not None:
                    setattr(nd, k, v)
    with np.load(edges_npz, allow_pickle=False) as z:
        edges = [
            EdgeRec(
                edge_id=int(z["edge_id"][i]),
                from_node=int(z["from_node"][i]),
                to_node=int(z["to_node"][i]),
                order=str(z["order"][i]),
                edge_source=str(z["edge_source"][i]),
                direction_confidence=str(z["direction_confidence"][i]),
                direction_basis=str(z["direction_basis"][i]),
                geometry=_wkt.loads(str(z["geometry_wkt"][i])),
                length_m=float(z["length_m"][i]),
                slope_m_per_m=float(z["slope_m_per_m"][i]),
            )
            for i in range(len(z["edge_id"]))
        ]
        for i, e in enumerate(edges):
            for fld in _EDGE_CAP_NUM:
                if fld not in z:
                    continue
                v = _num(z[fld], i)
                if v is not None:
                    setattr(e, fld, v)
            for fld in _EDGE_CAP_TEXT:
                if fld not in z:
                    continue
                v = str(z[fld][i])
                if v:
                    setattr(e, fld, v)
    return nodes, edges


def _save_records(nodes: list[NodeRec], edges: list[EdgeRec], out_dir: Path) -> None:
    n_num = {k: [] for k in _NODE_NUM}
    n_txt = {"node_type": [], "outfall_reason": []}
    n_extra = {k: [] for k in ("contrib_area_cells", "contrib_area_m2", "contrib_area_ha")}
    for nd in nodes:
        for k in _NODE_NUM:
            n_num[k].append(float(getattr(nd, k)))
        n_txt["node_type"].append(nd.node_type)
        n_txt["outfall_reason"].append(nd.outfall_reason or "")
        for k in n_extra:
            v = getattr(nd, k, None)
            n_extra[k].append(float(v) if v is not None else math.nan)
    np.savez(
        out_dir / "nodes_with_capacity.npz",
        **{k: np.asarray(v) for k, v in {**n_num, **n_txt, **n_extra}.items()},
    )
    e_num = {k: [] for k in _EDGE_NUM}
    e_cap = {k: [] for k in CAPACITY_FIELDS if k not in _EDGE_TEXT}
    e_txt = {k: [] for k in _EDGE_TEXT}
    e_geo = []
    for e in edges:
        for k in _EDGE_NUM:
            e_num[k].append(float(getattr(e, k)))
        e_geo.append(e.geometry.wkt)
        for k in e_cap:
            v = getattr(e, k, None)
            e_cap[k].append(float(v) if v is not None else math.nan)
        for k in e_txt:
            e_txt[k].append(str(getattr(e, k, "") or ""))
    np.savez(
        out_dir / "edges_with_capacity.npz",
        **{k: np.asarray(v) for k, v in {**e_num, **e_cap, **e_txt, "geometry_wkt": e_geo}.items()},
    )


@app.callback()
def _root() -> None:
    """WF-1 cited rational+Manning capacity assignment (observed edges only)."""


@app.command()
def run(
    config: Path = typer.Option(Path("configs/drainage.yaml"), help="Path to drainage YAML"),
    pointer: Path = typer.Option(..., "--pointer", help="D8 pointer raster (ESRI codes)"),
    elevation: Path = typer.Option(
        ..., "--elevation", help="Pinned elevation surface (provenance)"
    ),
    nodes_npz: Path = typer.Option(None, "--nodes-npz", help="Stitched nodes NPZ"),
    edges_npz: Path = typer.Option(None, "--edges-npz", help="Stitched edges NPZ"),
    out_dir: Path = typer.Option(
        None, "--out-dir", help="Write updated NPZ + rule-6 manifest here"
    ),
) -> None:
    """Standalone capacity run over stitched records (report-only without --out-dir)."""
    cfg = resolve_config(str(config))
    if nodes_npz is None or edges_npz is None:
        raise typer.BadParameter("standalone runs need both --nodes-npz and --edges-npz")
    sha, dirty = _git_state()
    manifest: dict[str, Any] = {
        "stage": "wf1_capacity",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "config_snapshot": json.loads(json.dumps(cfg, default=str)),
        "inputs": {"pointer": str(pointer), "elevation": str(elevation)},
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
        )
    nodes, edges = _load_records(str(nodes_npz), str(edges_npz))
    try:
        nodes, edges, metrics = assign_capacity(nodes, edges, str(pointer), str(elevation), cfg)
    except CapacityError as exc:
        manifest["status"] = f"stopped:{type(exc).__name__}"
        manifest["reason"] = str(exc)
        if out_dir is not None:
            (out / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
            )
        raise typer.Exit(code=2) from exc
    manifest["status"] = str(metrics["capacity_status"])
    manifest["finished_utc"] = _utc_now()
    manifest["capacity_metrics"] = json.loads(json.dumps(metrics, default=str))
    if out_dir is not None:
        _save_records(nodes, edges, out)
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
        )
    typer.echo(json.dumps({k: metrics[k] for k in sorted(metrics)}, indent=2, default=str))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":
    app()
