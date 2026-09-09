"""WF-6 build lane L2, module 1 — street watchlist from a realised depth product.

Consumes a WF-3 product in EITHER realised shape and emits a JSON-safe street
watchlist ranked by severity:

* ``flat``   — ``runs/<run>/products/segment_status.csv|json`` (single
  event-maximum/realised-time product);
* ``frame``  — a WF-3b frame-series run directory whose manifest declares
  ``series_kind = "instantaneous_solver_frames"``; the watchlist is then
  PER-FRAME (``--frame TAG|INDEX``).

The caller passes a path to either shape; :func:`resolve_product_source`
decides which one it is holding.  No HTTP lives here — an integrator wires
FastAPI routes against these plain functions and their exact dict contracts.

Street identity (shared with :mod:`jaladhar.web.intersections`, which imports
these helpers): the classified named subset of the road centreline file
(``CLASSIFIED_HIGHWAYS`` imported from :mod:`jaladhar.web.wards` is THE
definition — 10,318 segments, audit-reconciled) merges into street entities as
per-name endpoint-connected components at ENDPOINT_TOL_M = 1.0 m.  A component
NEVER spans two names: junction-touching different streets stay separate
entities.

Lead-time honesty (A2): flat products realise ``forecast_lead_minutes`` (0 for
the replay baseline) and frames realise a HINDCAST OFFSET — minutes of the
frame's valid time past the series start, never a forecast lead.  Both the
source block and every row carry ``lead_kind`` ∈ {"forecast_lead",
"hindcast_offset"} so no UI can conflate the two.

Every number emitted is read from the realised bytes on disk; nothing is
hardcoded (rule 3) and nothing is synthesised (rule 1).

Run standalone:
``python -m jaladhar.web.watchlist build --product <dir> [--frame TAG|INDEX] --out <json>``
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import typer

from jaladhar.web.series import parse_frame_entries, read_series_manifest
from jaladhar.web.wards import CLASSIFIED_HIGHWAYS

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Street watchlist builder (dashboard A1/A11 consumers)."""


REPO_ROOT = Path(__file__).resolve().parents[3]
ROADS_GPKG_PATH = REPO_ROOT / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"
WARD_JOIN_PATH = REPO_ROOT / "data" / "interim" / "context" / "segment_ward_2022.csv.gz"

#: Endpoint snap tolerance of the canonical street-entity merge (metres).  The
#: owner-adjudicated counting method below is defined AT this tolerance.
ENDPOINT_TOL_M = 1.0

#: Owner adjudication record, 2026-08-26 (M3 item 5): the street-entity count
#: produced by the RECOVERABLE per-name endpoint-connected-components merge at
#: ENDPOINT_TOL_M over EPSG:32643 coordinates is CANONICAL.  The earlier audit
#: figure of 1689 could not be reproduced and its derivation was unrecoverable
#: in-repo; it is retained as recorded history only -- recorded, not averaged,
#: never blended into the canonical number -- and the adjudication reopens
#: automatically if that audit method is ever recovered and disagrees.
CANONICAL_COUNT_METHOD = "per-name endpoint-connected components (tolerance 1.0 m, EPSG:32643)"
CANONICAL_ADJUDICATION_DATE = "2026-08-26"
RETIRED_AUDIT_ENTITY_COUNT = 1689


class WatchlistError(RuntimeError):
    """A realised input could not be resolved, parsed, or reconciled."""


# --------------------------------------------------------------------- geometry


def segment_endpoints(geom: Any) -> list[tuple[float, float]]:
    """Flat [start, end] point pairs of every part of a (Multi)LineString."""
    parts = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    pts: list[tuple[float, float]] = []
    for part in parts:
        coords = list(part.coords)
        if len(coords) >= 2:
            pts.append((float(coords[0][0]), float(coords[0][1])))
            pts.append((float(coords[-1][0]), float(coords[-1][1])))
    return pts


def load_classified_named_segments(
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Classified named street segments from the realised centreline file.

    Returns ``[{segment_id, name, geometry, length_m, endpoints}]`` ordered by
    ``segment_id``.  The CRS must be EPSG:32643 (frozen domain CRS); geometry
    stays lazy-loaded per caller need but lengths are realised here.
    """

    import geopandas as gpd

    gpkg = (
        ROADS_GPKG_PATH if repo_root is None else repo_root / ROADS_GPKG_PATH.relative_to(REPO_ROOT)
    )
    if not gpkg.is_file():
        raise WatchlistError(f"road centreline file missing: {gpkg}")
    roads = gpd.read_file(gpkg)
    if roads.crs is None or "32643" not in str(roads.crs.to_epsg()):
        raise WatchlistError(f"road CRS {roads.crs} does not match frozen EPSG:32643")
    names = roads["name"].fillna("")
    mask = names.str.strip().ne("") & roads["highway"].isin(CLASSIFIED_HIGHWAYS)
    subset = roads.loc[mask]
    segments: list[dict[str, Any]] = []
    for sid, name, geom in zip(
        subset["segment_id"].to_numpy(),
        subset["name"].to_numpy(),
        subset.geometry.to_numpy(),
        strict=True,
    ):
        endpoints = segment_endpoints(geom)
        if not endpoints:
            continue  # degenerate geometry cannot participate in any seam
        segments.append(
            {
                "segment_id": int(sid),
                "name": str(name),
                "geometry": geom,
                "length_m": float(geom.length),
                "endpoints": endpoints,
            }
        )
    if not segments:
        raise WatchlistError("no classified named segments realised in the centreline file")
    return segments


def _union_find_find(parent: list[int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def merge_street_entities(segments: list[dict[str, Any]]) -> list[list[int]]:
    """Per-name endpoint-connected components (tol ENDPOINT_TOL_M).

    Returns member index lists into ``segments``, ordered by each component's
    minimum segment_id so entity numbering is deterministic.
    """

    from scipy.spatial import cKDTree

    by_name: dict[str, list[int]] = {}
    for i, seg in enumerate(segments):
        by_name.setdefault(seg["name"], []).append(i)

    parent = list(range(len(segments)))

    def find(x: int) -> int:
        return _union_find_find(parent, x)

    for indices in by_name.values():
        if len(indices) < 2:
            continue
        points: list[tuple[float, float]] = []
        owner: list[int] = []
        for j, i in enumerate(indices):
            for pt in segments[i]["endpoints"]:
                points.append(pt)
                owner.append(j)
        tree = cKDTree(np.asarray(points))
        for p, q in tree.query_pairs(ENDPOINT_TOL_M, output_type="ndarray"):
            a, b = find(indices[owner[int(p)]]), find(indices[owner[int(q)]])
            if a != b:
                parent[a] = b

    components: dict[int, list[int]] = {}
    for i in range(len(segments)):
        components.setdefault(find(i), []).append(i)
    members = sorted(components.values(), key=lambda ms: min(segments[i]["segment_id"] for i in ms))
    for ms in members:
        ms.sort(key=lambda i: segments[i]["segment_id"])
    return members


# ------------------------------------------------------------------ product IO


@dataclass(frozen=True)
class ProductSource:
    """One realised product location, resolved to its kind."""

    kind: str  # "flat" | "frame"
    run_dir: Path
    products_dir: Path
    run_id: str
    manifest_path: Path
    manifest: dict[str, Any]
    frames: list[Any]  # SeriesFrame entries when kind == "frame", else []


def resolve_product_source(product: str | Path, repo_root: Path | None = None) -> ProductSource:
    """Resolve a flat products dir OR a frame-series run dir to its kind.

    Accepted shapes: ``<run>/products`` (flat twins or a frame series one
    level up), ``<run>`` for a frame-series run, or the run's manifest path.
    """

    root = repo_root if repo_root is not None else REPO_ROOT
    path = Path(product).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    manifest_candidates: list[Path] = []
    if path.is_dir():
        manifest_candidates.append(path / "manifest.json")
        if path.name == "products":
            # A frame series run keeps its manifest one level above products/.
            manifest_candidates.append(path.parent / "manifest.json")
    elif path.is_file():
        manifest_candidates.append(path)
    else:
        raise WatchlistError(f"product path does not exist: {path}")

    for candidate in manifest_candidates:
        if not candidate.is_file():
            continue
        payload = read_series_manifest(candidate)
        if payload is not None:
            run_dir = candidate.parent
            try:
                frames = parse_frame_entries(run_dir, payload, repo_root=root)
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim
                raise WatchlistError(f"frame series rejected: {exc}") from exc
            return ProductSource(
                kind="frame",
                run_dir=run_dir,
                products_dir=run_dir / "products",
                run_id=run_dir.name,
                manifest_path=candidate,
                manifest=payload,
                frames=frames,
            )
        # A flat products dir has segment_status.csv beside its manifest.
        flat_csv = candidate.parent / "segment_status.csv"
        if flat_csv.is_file() and candidate.parent.name == "products":
            run_dir = candidate.parent.parent
            return ProductSource(
                kind="flat",
                run_dir=run_dir,
                products_dir=candidate.parent,
                run_id=run_dir.name,
                manifest_path=candidate,
                manifest=_read_json(candidate),
                frames=[],
            )
    # Flat products dir without any manifest: still consumable — the CSV is
    # the realised state (V1); the manifest only adds provenance labels.
    flat_csv = path / "segment_status.csv" if path.is_dir() else None
    if flat_csv is not None and flat_csv.is_file():
        return ProductSource(
            kind="flat",
            run_dir=path.parent,
            products_dir=path,
            run_id=path.parent.name,
            manifest_path=path / "manifest.json",
            manifest={},
            frames=[],
        )
    raise WatchlistError(
        f"{path} is neither a flat products dir (segment_status.csv) nor a "
        "frame-series run (manifest declaring series_kind)"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchlistError(f"unreadable JSON {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_frame(source: ProductSource, frame: str | int | None) -> tuple[int, Any]:
    """Pick one frame of a frame-series source by TAG or INDEX (0-based)."""

    if source.kind != "frame":
        raise WatchlistError("frame selection applies only to a frame-series source")
    if frame is None:
        tags = ", ".join(f.tag for f in source.frames[:3])
        raise WatchlistError(
            f"--frame is required for a frame-series source "
            f"(n_frames={len(source.frames)}; e.g. {tags}, ...)"
        )
    if isinstance(frame, int) or (isinstance(frame, str) and frame.isdigit()):
        idx = int(frame)
        if not 0 <= idx < len(source.frames):
            raise WatchlistError(f"frame index {idx} outside 0..{len(source.frames) - 1}")
        return idx, source.frames[idx]
    for idx, entry in enumerate(source.frames):
        if entry.tag == str(frame):
            return idx, entry
    raise WatchlistError(f"frame tag {frame!r} not in series ({len(source.frames)} frames)")


def load_segment_status_rows(
    source: ProductSource, frame: str | int | None = None
) -> dict[str, Any]:
    """Realised per-segment rows for the selected product/frame.

    Returns ``{"valid_time_utc", "issue_time_utc", "lead_minutes", "rows":
    {segment_id: row-dict}}`` where row-dict carries band_low_cm, band_high_cm,
    flood_status.  Frame CSVs are byte-verified against the manifest SHA before
    their rows are trusted (V1).
    """

    import pandas as pd

    if source.kind == "flat":
        csv_path = source.products_dir / "segment_status.csv"
        table = pd.read_csv(csv_path)
        leads = table["forecast_lead_minutes"].astype(int)
        lead_values = sorted(leads.unique().tolist())
        if len(lead_values) != 1:
            # Honest per-row leads: keep the column, flag the spread.
            lead_minutes: int | None = int(lead_values[-1])
        else:
            lead_minutes = int(lead_values[0])
        valid_times = sorted(table["valid_time_utc"].astype(str).unique().tolist())
        rows = {
            int(r.segment_id): {
                "band_low_cm": int(r.band_low_cm),
                "band_high_cm": int(r.band_high_cm),
                "flood_status": str(r.flood_status),
            }
            for r in table.itertuples(index=False)
        }
        return {
            "valid_time_utc": valid_times[-1],
            "issue_time_utc": (
                str(table["issue_time_utc"].iloc[0]) if "issue_time_utc" in table else ""
            ),
            "lead_minutes": lead_minutes,
            "lead_spread": lead_values,
            "rows": rows,
        }

    _idx, entry = select_frame(source, frame)
    csv_path = entry.csv_path
    realized_sha = sha256_file(csv_path)
    if realized_sha != entry.csv_sha256:
        raise WatchlistError(
            f"{entry.tag}: frame CSV sha mismatch realized {realized_sha} != "
            f"manifest {entry.csv_sha256}; refusing its rows"
        )
    table = pd.read_csv(csv_path)
    rows = {
        int(r.segment_id): {
            "band_low_cm": int(r.band_low_cm),
            "band_high_cm": int(r.band_high_cm),
            "flood_status": str(r.flood_status),
        }
        for r in table.itertuples(index=False)
    }
    return {
        "valid_time_utc": entry.valid_time_utc,
        "issue_time_utc": str(source.manifest.get("issue_time_utc", "")),
        "lead_minutes": entry.offset_seconds // 60,
        "lead_spread": [entry.offset_seconds // 60],
        "rows": rows,
    }


def load_ward_join(repo_root: Path | None = None) -> dict[int, dict[str, Any]]:
    """segment_id -> {ward_name, ward_number} from the realised U0f join."""

    import pandas as pd

    path = (
        WARD_JOIN_PATH if repo_root is None else repo_root / WARD_JOIN_PATH.relative_to(REPO_ROOT)
    )
    if not path.is_file():
        raise WatchlistError(f"ward join missing: {path}")
    table = pd.read_csv(path)
    return {
        int(r.segment_id): {"ward_name": str(r.ward_name), "ward_number": int(r.ward_no)}
        for r in table.itertuples(index=False)
    }


# -------------------------------------------------------------------- building


def _modal_name(names: list[str]) -> str:
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


#: Sorts "no lead" after every real lead, never equal to one.
_NO_LEAD = 10**9


# ----------------------------------------------------------------- first flood

#: segment_id -> earliest realized flooding, cached per source. Frames are
#: immutable and byte-verified (same SHA rule as the single-frame path), so a
#: per-source cache can never serve stale rows.
_FIRST_FLOOD_CACHE_LOCK = threading.Lock()
_FIRST_FLOOD_CACHE: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = {}


def first_flood_index(source: ProductSource) -> dict[int, dict[str, Any]]:
    """Earliest realized flooding per segment across a frame-series source.

    Returns ``{segment_id: {"valid_time_utc": str, "offset_minutes": int}}``
    for every segment flooded in ANY realized frame; frames are taken in
    manifest order, so the first hit per segment is its earliest flood. Flat
    products carry a single lead by construction — the index is empty there
    and the row-level fallback keeps today's ordering. Every frame CSV is
    byte-verified against the manifest before its rows are trusted (V1); a
    mismatch refuses the index instead of aggregating unverified rows.
    """

    if source.kind != "frame":
        return {}
    key = (
        source.run_id,
        tuple((f.tag, f.csv_sha256) for f in source.frames),
    )
    with _FIRST_FLOOD_CACHE_LOCK:
        cached = _FIRST_FLOOD_CACHE.get(key)
    if cached is not None:
        return cached

    import pandas as pd

    index: dict[int, dict[str, Any]] = {}
    for entry in source.frames:
        realized_sha = sha256_file(entry.csv_path)
        if realized_sha != entry.csv_sha256:
            raise WatchlistError(
                f"{entry.tag}: frame CSV sha mismatch realized {realized_sha} != "
                f"manifest {entry.csv_sha256}; refusing the first-flood index"
            )
        table = pd.read_csv(entry.csv_path, usecols=["segment_id", "flood_status"])
        flooded = table.loc[table["flood_status"] == "flooded", "segment_id"]
        offset_minutes = entry.offset_seconds // 60
        for seg in flooded.astype(int).unique():
            if seg not in index:  # manifest order => first hit is the earliest
                index[seg] = {
                    "valid_time_utc": entry.valid_time_utc,
                    "offset_minutes": offset_minutes,
                }
    with _FIRST_FLOOD_CACHE_LOCK:
        _FIRST_FLOOD_CACHE[key] = index
    return index


def severity_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """Primary watchlist order: depth DESC, soonest lead, then name."""

    lead = row["lead_minutes"] if row["lead_minutes"] is not None else _NO_LEAD
    return (-row["worst_depth_cm"], lead, row["street_name"])


def soonest_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """Alternate order: first flood across the series, then severity, name.

    A frame-series row is ordered by its earliest realized flooding
    (first_flood_offset_minutes — an offset from series start, never a
    forecast lead). Flat products have no series: the field is None there and
    the key falls back to the row's own lead_minutes, which keeps the
    historical flat-product order byte-for-byte.
    """

    lead = row.get("first_flood_offset_minutes")
    if lead is None:
        lead = row["lead_minutes"] if row["lead_minutes"] is not None else _NO_LEAD
    return (lead, -row["worst_depth_cm"], row["street_name"])


def build_watchlist(
    product_source: str | Path | ProductSource,
    frame: str | int | None = None,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the severity-ranked street watchlist (JSON-safe dict).

    Ranking: worst_depth_cm DESC, tie-break soonest lead_minutes ASC, then
    street_name ASC.  ``alternates.soonest_asc`` re-orders the same rows' ranks
    by first flood across the realized series first (offset semantics, A2) so
    the UI can offer a time-ordered view without conflating its meaning
    (lead_kind travels with every row).

    ``status`` is a STREET-LEVEL OR over member segments (flooded if any
    member is flooded; unknown otherwise-if-any; else not_flooded) — the
    topological claim the product can support.  ``depth_band_cm`` /
    ``worst_depth_cm`` / ``worst_segment_id`` describe the DEEPEST member row.
    """

    root = repo_root if repo_root is not None else REPO_ROOT
    source = (
        product_source
        if isinstance(product_source, ProductSource)
        else resolve_product_source(product_source, repo_root=root)
    )
    status = load_segment_status_rows(source, frame)
    segments = load_classified_named_segments(repo_root=root)
    entities = merge_street_entities(segments)
    ward_by_segment = load_ward_join(repo_root=root)
    # A2/soonest: each street's earliest realized flooding across the series
    # (cached per source; empty for flat products).
    first_flood = first_flood_index(source)

    rows_out: list[dict[str, Any]] = []
    flooded_streets = 0
    for members in entities:
        member_rows = [
            (seg, status["rows"].get(seg["segment_id"])) for seg in (segments[i] for i in members)
        ]
        known = [(seg, r) for seg, r in member_rows if r is not None]
        if not known:
            continue  # entity entirely outside the product's realised set

        # Worst row: deepest band_high, then lowest segment_id (lead is
        # constant within one product/frame, so it cannot break this tie).
        worst_seg, worst_row = min(
            known, key=lambda kr: (-kr[1]["band_high_cm"], kr[0]["segment_id"])
        )
        # Street-level status is an OR over members: a street is FLOODED when
        # ANY member segment is flooded, UNKNOWN when any carries unknown and
        # none is flooded. The realised flat product allows not_flooded rows
        # with band_high far above the threshold (measured max 376 cm), so
        # the worst-depth row's own status would under-report street flooding
        # and desynchronise from counts.flooded_streets.
        statuses = {r["flood_status"] for _, r in known}
        if "flooded" in statuses:
            entity_status = "flooded"
        elif "unknown" in statuses:
            entity_status = "unknown"
        else:
            entity_status = "not_flooded"
        if entity_status == "flooded":
            flooded_streets += 1
        ward = ward_by_segment.get(worst_seg["segment_id"])
        member_first = [
            first_flood[seg["segment_id"]]
            for seg, _ in known
            if seg["segment_id"] in first_flood
        ]
        if member_first:
            first = min(member_first, key=lambda f: f["offset_minutes"])
            first_time: str | None = first["valid_time_utc"]
            first_offset: int | None = int(first["offset_minutes"])
        else:
            first_time = None
            first_offset = None
        rows_out.append(
            {
                "street_name": _modal_name([seg["name"] for seg, _ in known]),
                "ward_name": ward["ward_name"] if ward else None,
                "ward_number": ward["ward_number"] if ward else None,
                "depth_band_cm": {
                    "low": int(worst_row["band_low_cm"]),
                    "high": int(worst_row["band_high_cm"]),
                },
                "worst_depth_cm": int(worst_row["band_high_cm"]),
                "status": entity_status,
                "lead_minutes": status["lead_minutes"],
                "lead_kind": "hindcast_offset" if source.kind == "frame" else "forecast_lead",
                "valid_time_utc": status["valid_time_utc"],
                "first_flood_valid_time_utc": first_time,
                "first_flood_offset_minutes": first_offset,
                "segment_ids": [seg["segment_id"] for seg, _ in known],
                "worst_segment_id": int(worst_seg["segment_id"]),
                "length_m": round(sum(seg["length_m"] for seg, _ in known), 1),
            }
        )

    rows_out.sort(key=severity_key)
    # Contract column order: rank first.
    rows_out = [{"rank": rank, **row} for rank, row in enumerate(rows_out, start=1)]

    alternates = {"soonest_asc": [row["rank"] for row in sorted(rows_out, key=soonest_key)]}

    realized_entities = len(rows_out)
    lead_kind = "hindcast_offset" if source.kind == "frame" else "forecast_lead"
    src_block: dict[str, Any] = {
        "run_id": source.run_id,
        "kind": source.kind,
        "lead_kind": lead_kind,
    }
    if source.kind == "frame":
        _idx, entry = select_frame(source, frame)
        src_block["frame_tag"] = entry.tag
        src_block["frame_index"] = _idx
        src_block["valid_time_utc"] = entry.valid_time_utc
    else:
        src_block["valid_time_utc"] = status["valid_time_utc"]

    return {
        "source": src_block,
        "rows": rows_out,
        "order": "severity_desc",
        "alternates": alternates,
        "counts": {
            "streets_total": realized_entities,
            "flooded_streets": flooded_streets,
            "denominator_note": (
                f"classified named entities={realized_entities} by "
                f"{CANONICAL_COUNT_METHOD} is CANONICAL per owner adjudication "
                f"{CANONICAL_ADJUDICATION_DATE}; the earlier audit figure "
                f"{RETIRED_AUDIT_ENTITY_COUNT} could not be reproduced and its "
                "derivation was unrecoverable; recorded, not averaged; reopened "
                "automatically if the audit method is ever recovered and disagrees"
            ),
        },
    }


# ------------------------------------------------------------------------- CLI


@app.command()
def build(
    product: Path = typer.Option(..., help="Flat products dir OR frame-series run dir"),
    frame: str = typer.Option(None, help="Frame TAG or INDEX when the product is a frame series"),
    out: Path = typer.Option(..., help="Scratch JSON output path"),
    repo_root: Path = typer.Option(REPO_ROOT, help="Repository root"),
) -> None:
    """Build the street watchlist and print its counts."""

    source = resolve_product_source(product, repo_root=repo_root)
    payload = build_watchlist(source, frame if frame is not None else None, repo_root=repo_root)
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    typer.echo(
        json.dumps(
            {
                "out": str(out),
                **payload["counts"],
                "source": payload["source"],
                "top_row": payload["rows"][0] if payload["rows"] else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
