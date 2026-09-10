"""against the frozen contract configs/contracts/drain_graph.json.
component labels -> observed edges + synthesised connectors/stubs ->
observed-over-synthesised dedup -> degenerate drops -> literal DAG enforcement ->
CPU-only; no torch/CUDA imports. Rule-6 manifest written at run start and updated"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import typer
import yaml
from rasterio.features import rasterize
from shapely.geometry import LineString

from jaladhar.provenance import sha256_file

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

TIE_BREAK_ORDER_PINNED = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
OFFSETS: tuple[tuple[str, int, int, int, float], ...] = (
    ("E", 1, 0, 1, 10.0),
    ("NE", 2, -1, 1, math.sqrt(200.0)),
    ("N", 4, -1, 0, 10.0),
    ("NW", 8, -1, -1, math.sqrt(200.0)),
    ("W", 16, 0, -1, 10.0),
    ("SW", 32, 1, -1, math.sqrt(200.0)),
    ("S", 64, 1, 0, 10.0),
    ("SE", 128, 1, 1, math.sqrt(200.0)),
)
ORDER_RANK = {"primary": 0, "secondary": 1, "tertiary": 2, "synthetic_connector": 3}
NODE_KIND_RANK = {"snapped": 0, "junction": 1, "outfall": 2}
OUTFALL_REASONS = {"pit", "domain_exit"}
POLICY_FRONTIER_TABLE = [
    {
        "policy": "all-walks-write, no flat resolution",
        "post_stitch_comps": 191,
        "invented_length_km": 48.3,
        "fraction_of_observed": 0.063,
    },
    {
        "policy": "all-walks-write, flat-resolved (design-phase prototype: UNMASKED "
        "flats; numbers are pre-build design-phase measurements, NOT realized)",
        "post_stitch_comps": 34,
        "invented_length_km": 777.2,
        "fraction_of_observed": 1.013,
    },
    {
        "policy": "per-reach higher-elev-endpoint, flat-resolved",
        "post_stitch_comps": 227,
        "invented_length_km": 402.4,
        "fraction_of_observed": 0.525,
    },
    {
        "policy": "component-outlet-only, flat-resolved",
        "post_stitch_comps": 46,
        "invented_length_km": 245.8,
        "fraction_of_observed": 0.32,
    },
]
OBSERVED_NETWORK_DENOMINATOR_KM = 767.3

BUDGET_MAX_MINUTES = 10.0
BUDGET_MAX_RSS_MB = 2048.0
_WALK_STEPS_PER_SEC_ASSUMED = 200_000.0

EXPECTED_CENSUS = {"components": 365, "nodes": 1376}


class StitchError(RuntimeError):
    """A stitching gate or invariant failed."""


@dataclass
class Reach:
    reach_id: int
    drain_class: str
    geom: LineString
    length_m: float


@dataclass
class NodeRec:
    node_id: int
    x_m: float
    y_m: float
    elev_m: float
    node_type: str
    outfall_reason: str | None


@dataclass
class EdgeRec:
    edge_id: int
    from_node: int
    to_node: int
    order: str
    edge_source: str
    direction_confidence: str
    direction_basis: str
    geometry: LineString
    length_m: float
    slope_m_per_m: float


@dataclass
class StitchResult:
    nodes: list[NodeRec]
    edges: list[EdgeRec]
    metrics: dict


@dataclass
class _Node:
    x: float
    y: float
    z: float
    kind: str
    outfall_reason: str | None = None
    comp_seed: int = -1

    def key(self) -> tuple:
        return (
            round(self.x, 3),
            round(self.y, 3),
            self.x,
            self.y,
            NODE_KIND_RANK[self.kind],
            self.outfall_reason or "",
        )


@dataclass
class _EdgeCand:
    src: tuple[int, int]
    order: str
    edge_source: str
    direction_confidence: str
    basis: dict = field(default_factory=dict)
    geometry: LineString = None  # type: ignore[assignment]
    length_m: float = 0.0
    slope_m_per_m: float = 0.0
    kind: str = "connector"  # observed | connector | stub
    prov_id: int = 0
    cells: frozenset[tuple[int, int]] | None = None

    def wkb_hex(self) -> str:
        return self.geometry.wkb_hex

    def sort_key(self) -> tuple:
        return (
            self.src[0],
            self.src[1],
            ORDER_RANK[self.order],
            round(self.length_m, 6),
            hashlib.sha256(self.wkb_hex().encode()).hexdigest(),
        )


class _DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        p = self.parent
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _assert_grid_profile(
    path: Path, width: int, height: int, transform: list[float], crs: str
) -> None:
    """Consumer-assertion-10 substance (contract: Grid.assert_aligned(exports,
    pntr_d8.tif)) applied producer-side to every derived raster: exact lattice"""
    from affine import Affine

    with rasterio.open(path) as src:
        got = [float(v) for v in src.transform.to_gdal()[:6]]
        want = [float(v) for v in Affine(*transform).to_gdal()[:6]]
        bad = (
            src.width != width
            or src.height != height
            or any(abs(a - b) > 1e-9 for a, b in zip(got, want, strict=True))
            or src.crs.to_string() != crs
        )
    if bad:
        raise StitchError(
            f"derived raster {path.name} is not aligned to the buffered grid "
            f"(want {width}x{height} @ {transform} {crs})"
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        return bool(out.strip())
    except Exception:
        return False


_REQUIRED_KEYS: list[str] = [
    "crs",
    "grid.width",
    "grid.height",
    "grid.transform",
    "grid.cell_area_m2",
    "inputs.elevation_surface",
    "inputs.elevation_sha256",
    "inputs.retained_basins_csv",
    "inputs.waterways_gpkg",
    "inputs.kml_primary",
    "inputs.kml_secondary",
    "stitch.snap_tolerance_m",
    "stitch.burn_buffer_m",
    "stitch.quad_segs",
    "stitch.max_connector_steps",
    "stitch.flat_increment_m",
    "stitch.tie_break_order",
    "stitch.self_hit_granularity",
    "stitch.stub_max_length_m",
    "stitch.split_max_distance_m",
    "stitch.corridor_merge_min_jaccard",
    "outputs.run_dir",
    "outputs.gpkg",
    "outputs.adjacency",
    "outputs.manifest",
    "outputs.publish_gpkg",
    "outputs.publish_adjacency",
    "consumer.allow_disconnected_load",
    "consumer.owner_adjudication_ref",
]


def resolve_config(config_path: str) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("drainage"), dict):
        raise ValueError(f"config {config_path}: missing top-level 'drainage' mapping")
    d = raw["drainage"]

    def get(dot: str) -> Any:
        cur: Any = d
        for part in dot.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    for key in _REQUIRED_KEYS:
        if get(key) is None:
            problems.append(f"missing key drainage.{key}")

    def want_int(key: str, minimum: int = 1) -> None:
        v = get(key)
        if v is None:
            return
        if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
            problems.append(f"drainage.{key} must be int >= {minimum}, got {v!r}")

    def want_num(key: str, minimum: float) -> None:
        v = get(key)
        if v is None:
            return
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v < minimum
        ):
            problems.append(f"drainage.{key} must be finite number >= {minimum}, got {v!r}")

    want_int("grid.width")
    want_int("grid.height")
    want_int("stitch.quad_segs", 0)
    want_int("stitch.max_connector_steps")
    want_num("grid.cell_area_m2", 0.0)
    want_num("stitch.snap_tolerance_m", 0.0)
    want_num("stitch.burn_buffer_m", 0.0)
    want_num("stitch.flat_increment_m", 0.0)
    want_num("stitch.stub_max_length_m", 0.0)
    want_num("stitch.split_max_distance_m", 0.0)
    want_num("stitch.corridor_merge_min_jaccard", 0.0)

    t = get("grid.transform")
    if t is not None and (
        not isinstance(t, list)
        or len(t) != 6
        or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in t
        )
    ):
        problems.append(f"drainage.grid.transform must be a list of 6 finite numbers, got {t!r}")

    if get("stitch.tie_break_order") != TIE_BREAK_ORDER_PINNED:
        problems.append(
            f"drainage.stitch.tie_break_order must equal pinned {TIE_BREAK_ORDER_PINNED}, "
            f"got {get('stitch.tie_break_order')!r}"
        )
    if get("stitch.self_hit_granularity") != "reach":
        problems.append(
            f"drainage.stitch.self_hit_granularity must be 'reach' (literal pin), "
            f"got {get('stitch.self_hit_granularity')!r}"
        )

    for key in ("inputs.elevation_surface", "inputs.retained_basins_csv", "inputs.waterways_gpkg"):
        v = get(key)
        if v is not None and not _repo(v).exists():
            problems.append(f"drainage.{key} file does not exist: {v}")
    for key in ("inputs.kml_primary", "inputs.kml_secondary"):
        v = get(key)
        if v is not None and not _repo(v).exists():
            warnings.append(f"drainage.{key} file does not exist (loader will refuse): {v}")

    if problems:
        raise ValueError(
            f"config resolution failed for {config_path} with {len(problems)} problem(s):\n"
            + "\n".join(f"- {p}" for p in problems)
        )
    d["_resolved_at"] = _utc_now()
    d["_resolve_warnings"] = warnings
    d["_config_path"] = str(config_path)
    return d


def _repo(p: Any) -> Path:
    path = Path(str(p))
    return path if path.is_absolute() else REPO / path


def _print_budget(n_cells: int, n_reaches: int) -> dict:
    arrays_mb = n_cells * 4 * 8 / 1e6
    walks = 2 * n_reaches
    walk_s = walks * 10000 / _WALK_STEPS_PER_SEC_ASSUMED
    fill_s = 120.0
    total_min = (walk_s + fill_s + 60.0) / 60.0
    print(
        f"[budget] grid cells={n_cells:,}; ~8 full-grid arrays ~= {arrays_mb:.0f} MB RAM; "
        f"walks<={walks} x max_connector_steps; priority-flood fill (in-repo, deterministic)"
    )
    print(
        f"[budget] worst-case projection ~{total_min:.1f} min / ~{arrays_mb:.0f} MB "
        f"(limits {BUDGET_MAX_MINUTES:.0f} min / {BUDGET_MAX_RSS_MB:.0f} MB)"
    )
    if total_min > BUDGET_MAX_MINUTES or arrays_mb > BUDGET_MAX_RSS_MB:
        raise StitchError(
            f"compute budget exceeded by projection ({total_min:.1f} min / {arrays_mb:.0f} MB) "
            "- refusing to start"
        )
    return {
        "projected_minutes_worst_case": round(total_min, 2),
        "projected_rss_mb": round(arrays_mb, 1),
        "limit_minutes": BUDGET_MAX_MINUTES,
        "limit_rss_mb": BUDGET_MAX_RSS_MB,
    }


def _elevation_sha_gate(elev_path: Path, expected_sha: str) -> None:
    got = sha256_file(elev_path)
    if got != expected_sha:
        raise StitchError(
            "elevation SHA gate FAILED: refusing to start, ZERO writes.\n"
            f"  expected {expected_sha}\n"
            f"  got      {got}\n"
            "The pinned surface binds every Phase-3 finding; building on other bytes is forbidden."
        )


def _load_grid_arrays(
    elev_path: Path, width: int, height: int, transform: list[float], crs: str
) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(elev_path) as src:
        if (src.width, src.height) != (width, height):
            raise StitchError(
                f"elevation grid {(src.width, src.height)} != configured {(width, height)}"
            )
        got_t = tuple(src.transform)[:6]
        for got, want in zip(got_t, transform, strict=True):
            if abs(got - want) > 1e-6:
                raise StitchError(f"elevation transform {got_t} != configured {transform}")
        if src.crs is not None and crs and src.crs.to_string() != crs:
            raise StitchError(f"elevation crs {src.crs.to_string()} != configured {crs}")
        z = src.read(1).astype(np.float32)
        nodata = src.nodata
    valid = np.isfinite(z)
    if nodata is not None:
        valid &= z != np.float32(nodata)
    return z, valid


def _build_retained_mask(
    z: np.ndarray, valid: np.ndarray, csv_path: Path
) -> tuple[np.ndarray, dict]:
    """realized mask cell count are recorded beside the register's own cell totals."""
    mask = np.zeros(z.shape, dtype=bool)
    csv_rows = 0
    bad_rows: list[str] = []
    spills: list[tuple[float, int, int]] = []
    register_n_cells_sum = 0
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            csv_rows += 1
            m = re.fullmatch(r"\((\d+),\s*(\d+)\)", row.get("outlet_cell", "").strip())
            try:
                register_n_cells_sum += int(row["n_cells"])
            except (KeyError, TypeError, ValueError):
                pass
            if not m:
                bad_rows.append(str(row.get("id", "?")))
                continue
            spills.append((float(row["spill_elevation_m"]), int(m.group(1)), int(m.group(2))))
    if bad_rows:
        raise StitchError(
            "retained_basins.csv: unparseable outlet_cell on "
            f"{len(bad_rows)} row(s): {bad_rows[:5]}"
        )

    h, w = z.shape
    max_basin_flood = 0
    for spill, r0, c0 in sorted(spills):
        if not (0 <= r0 < h and 0 <= c0 < w) or not valid[r0, c0] or mask[r0, c0]:
            continue
        if not z[r0, c0] < spill:
            continue
        visited = 0
        q = deque([(r0, c0)])
        mask[r0, c0] = True
        while q:
            r, c = q.popleft()
            visited += 1
            for _, _, dr, dc, _ in OFFSETS:
                nr, nc = r + dr, c + dc
                if (
                    0 <= nr < h
                    and 0 <= nc < w
                    and not mask[nr, nc]
                    and valid[nr, nc]
                    and z[nr, nc] < spill
                ):
                    mask[nr, nc] = True
                    q.append((nr, nc))
        max_basin_flood = max(max_basin_flood, visited)

    diag = {
        "retained_basins_csv": str(csv_path),
        "csv_sha256": sha256_file(csv_path),
        "csv_rows": csv_rows,
        "register_n_cells_sum": register_n_cells_sum,
        "mask_cell_count": int(mask.sum()),
        "mask_to_register_cell_ratio": round(float(mask.sum()) / max(register_n_cells_sum, 1), 4),
        "max_single_basin_flood_cells": max_basin_flood,
        "construction": (
            "strict flood z < spill_elevation_m, 8-connected from outlet_cell, on pinned surface"
        ),
    }
    return mask, diag


def _deterministic_priority_flood_fill(z: np.ndarray, valid: np.ndarray, eps: float) -> np.ndarray:
    """VALUE-nondeterministic run-to-run (measured 2026-08-25: hybrid surface drifts
    at 11,227 cells / max 0.166 m across identical-input draws; derived pointer
    contract determinism.rerun_stability guarantee. This implementation is"""
    import heapq

    h, w = z.shape
    filled = z.astype(np.float64).copy()
    closed = np.zeros((h, w), dtype=bool)
    heap: list[tuple[float, int, int, int]] = []
    counter = 0
    for r in range(h):
        r_edge = r == 0 or r == h - 1
        for c in range(w):
            if not valid[r, c]:
                continue
            if r_edge or c == 0 or c == w - 1:
                closed[r, c] = True
                heapq.heappush(heap, (filled[r, c], counter, r, c))
                counter += 1
    if not heap:
        return filled
    while heap:
        ze, _, r, c = heapq.heappop(heap)
        for _, _esri, dr, dc, _dist in OFFSETS:
            nr, nc = r + dr, c + dc
            if nr < 0 or nr >= h or nc < 0 or nc >= w:
                continue
            if closed[nr, nc] or not valid[nr, nc]:
                continue
            zn = filled[nr, nc]
            lifted = ze + eps
            filled[nr, nc] = zn if zn > lifted else lifted
            closed[nr, nc] = True
            heapq.heappush(heap, (filled[nr, nc], counter, nr, nc))
            counter += 1
    return filled


def _derive_hybrid_and_pointer(
    z: np.ndarray,
    valid: np.ndarray,
    mask: np.ndarray,
    elev_path: Path,
    run_dir: Path,
    crs: str,
    transform: list[float],
    width: int,
    height: int,
    flat_increment: float,
) -> tuple[np.ndarray, np.ndarray, Path, dict]:
    from affine import Affine

    aff = Affine(*transform)
    base_profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "crs": crs,
        "transform": aff,
        "width": width,
        "height": height,
        "nodata": -9999.0,
        "compress": "deflate",
    }
    filled = _deterministic_priority_flood_fill(z, valid, flat_increment)

    # ties (measured: small basins returned to all-zero pointers). The hybrid
    # artefact is written float64 so provenance matches what routed.
    hybrid = np.where(mask, filled, z.astype(np.float64))

    hybrid_path = run_dir / "hybrid_filled_dem.tif"
    hybrid_profile = dict(base_profile)
    hybrid_profile["dtype"] = "float64"
    with rasterio.open(hybrid_path, "w", **hybrid_profile) as dst:
        dst.write(hybrid, 1)

    h_, w_ = hybrid.shape
    inf = np.inf
    padded = np.full((h_ + 2, w_ + 2), inf, dtype=np.float64)
    padded[1:-1, 1:-1] = hybrid
    best = np.full((h_, w_), -inf, dtype=np.float64)
    code = np.zeros((h_, w_), dtype=np.uint8)
    for _, esri, dr, dc, dist in OFFSETS:
        zn = padded[1 + dr : 1 + dr + h_, 1 + dc : 1 + dc + w_]
        drop = (hybrid - zn) / np.float32(dist)
        better = (drop > best) & (drop > 0)
        code[better] = esri
        best[better] = drop[better]
    # Domain-rim outward drainage: a boundary cell with no in-grid descent leaves the
    # this the pinned domain_exit stop condition could never fire. Outward direction
    bottom = np.zeros((h_, w_), dtype=bool)
    bottom[h_ - 1, :] = True
    left = np.zeros((h_, w_), dtype=bool)
    left[:, 0] = True
    top = np.zeros((h_, w_), dtype=bool)
    top[0, :] = True
    right = np.zeros((h_, w_), dtype=bool)
    right[:, w_ - 1] = True
    for esri, side in ((64, bottom), (16, left), (4, top), (1, right)):
        rim = side & (code == 0) & valid
        code[rim] = esri
    code[~valid] = 255

    pointer_path = run_dir / "derived_pntr_d8.tif"
    ptr_profile = dict(base_profile)
    ptr_profile.update({"dtype": "uint8", "nodata": 255})
    with rasterio.open(pointer_path, "w", **ptr_profile) as dst:
        dst.write(code, 1)

    for p in (pointer_path, hybrid_path):
        _assert_grid_profile(p, width, height, transform, crs)

    shas = {
        "derived_pointer": sha256_file(pointer_path),
        "hybrid_filled_dem": sha256_file(hybrid_path),
    }
    return hybrid, code, pointer_path, shas


def _rasterize_footprints(
    reaches: list[Reach],
    width: int,
    height: int,
    transform: list[float],
    crs: str,
    burn_buffer_m: float,
    quad_segs: int,
    run_dir: Path,
) -> tuple[np.ndarray, dict]:
    from affine import Affine

    shapes = []
    for idx, r in enumerate(reaches):
        shapes.append((r.geom.buffer(burn_buffer_m, quad_segs=quad_segs), idx + 1))
    fp = rasterize(
        shapes, out_shape=(height, width), transform=Affine(*transform), fill=0, dtype="int32"
    )
    path = run_dir / "reach_footprints.tif"
    prof = {
        "driver": "GTiff",
        "dtype": "int32",
        "count": 1,
        "crs": crs,
        "transform": Affine(*transform),
        "width": width,
        "height": height,
        "nodata": 0,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(fp, 1)
    _assert_grid_profile(path, width, height, transform, crs)
    diag = {
        "footprints_path": str(path),
        "footprints_sha256": sha256_file(path),
        "burned_cell_count": int((fp > 0).sum()),
        "rasterization_rule": (
            f"burn cells whose CENTER is within {burn_buffer_m} m of geometry "
            f"(buffer quad_segs={quad_segs}); overlaps last-writer-wins ascending reach_id"
        ),
    }
    return fp, diag


def _cell_of(x: float, y: float, transform: list[float]) -> tuple[int, int]:
    a, _, c, _, e, f = transform
    return int(math.floor((f - y) / -e)), int(math.floor((x - c) / a))


def _center_of(row: int, col: int, transform: list[float]) -> tuple[float, float]:
    a, _, c, _, e, f = transform
    return c + (col + 0.5) * a, f + (row + 0.5) * e


def _snap_slots(
    reaches: list[Reach], transform: list[float], tol: float
) -> tuple[list[tuple[float, float]], _DSU, list[tuple[int, int]]]:
    """slot coordinates, DSU over slots (pairs within tol united), slot grid cells."""
    pts: list[tuple[float, float]] = []
    for r in reaches:
        coords = list(r.geom.coords)
        pts.append((coords[0][0], coords[0][1]))
        pts.append((coords[-1][0], coords[-1][1]))
    dsu = _DSU(len(pts))
    inv = 1.0 / tol
    buckets: dict[tuple[int, int], list[int]] = {}
    for si, (x, y) in enumerate(pts):
        buckets.setdefault((int(math.floor(x * inv)), int(math.floor(y * inv))), []).append(si)
    neigh = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 0), (0, 1), (1, -1), (1, 0), (1, 1))
    for bkey in sorted(buckets):
        for dx, dy in neigh:
            other = buckets.get((bkey[0] + dx, bkey[1] + dy))
            if not other:
                continue
            for si in buckets[bkey]:
                x1, y1 = pts[si]
                for sj in other:
                    if sj <= si:
                        continue
                    x2, y2 = pts[sj]
                    if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= tol * tol:
                        dsu.union(si, sj)
    cells = [_cell_of(x, y, transform) for x, y in pts]
    return pts, dsu, cells


@dataclass
class _WalkOutcome:
    outcome: str
    terminal: tuple[int, int] | None
    visited: list[tuple[int, int]]
    flat_cells: int
    entered_root: int | None = None
    self_reentries: int = 0


def _do_walk(
    start_cell: tuple[int, int],
    other_cell: tuple[int, int],
    my_fp_label: int,
    my_root: int,
    pointer: np.ndarray,
    footprints: np.ndarray,
    seeds: np.ndarray,
    dsu: _DSU,
    raised: np.ndarray,
    max_steps: int,
) -> _WalkOutcome:
    """One D8 walk under the pinned per-step stop-condition order, with the
    footprint made reach-granularity aborts a burn-width artifact (measured
    1031/2066 walks killed). Re-entries are counted for provenance; the walk"""
    h, w = pointer.shape
    r, c = start_cell
    lab = int(footprints[r, c])
    if lab != 0 and dsu.find(int(seeds[lab])) != my_root:
        return _WalkOutcome(
            "junction_entry_at_start",
            (r, c),
            [(r, c)],
            int(raised[r, c]),
            dsu.find(int(seeds[lab])),
        )
    visited = [(r, c)]
    flat_cells = int(raised[r, c])
    steps = 0
    self_reentries = 0
    while True:
        code = int(pointer[r, c])
        if code == 0 or code == 255:
            return _WalkOutcome(
                "pit" if steps > 0 else "pit_start_no_edge",
                (r, c),
                visited,
                flat_cells,
                self_reentries=self_reentries,
            )
        _, _, dr, dc, _ = OFFSETS[code.bit_length() - 1]
        nr, nc = r + dr, c + dc
        if (nr, nc) == other_cell:
            return _WalkOutcome(
                "reached_other", None, visited, flat_cells, self_reentries=self_reentries
            )
        if not (0 <= nr < h and 0 <= nc < w):
            return _WalkOutcome(
                "domain_exit", (r, c), visited, flat_cells, self_reentries=self_reentries
            )
        ncode = int(pointer[nr, nc])
        if ncode == 0 or ncode == 255:
            return _WalkOutcome(
                "pit",
                (nr, nc),
                visited + [(nr, nc)],
                flat_cells + int(raised[nr, nc]),
                self_reentries=self_reentries,
            )
        lab = int(footprints[nr, nc])
        if lab != 0:
            lroot = dsu.find(int(seeds[lab]))
            if lroot != my_root:
                return _WalkOutcome(
                    "junction_entry",
                    (nr, nc),
                    visited + [(nr, nc)],
                    flat_cells + int(raised[nr, nc]),
                    lroot,
                    self_reentries,
                )
        r, c = nr, nc
        if lab == my_fp_label:
            self_reentries += 1
        visited.append((r, c))
        flat_cells += int(raised[r, c])
        steps += 1
        if steps > max_steps:
            return _WalkOutcome(
                "max_steps_abort", None, visited, flat_cells, self_reentries=self_reentries
            )


def _polyline(cells: list[tuple[int, int]], transform: list[float]) -> tuple[float, LineString]:
    pts = [_center_of(r, c, transform) for r, c in cells]
    dist = sum(
        math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False)
    )
    geom = LineString(pts) if len(pts) >= 2 else LineString([pts[0], pts[0]])
    return dist, geom


def _basis(rule: str, flat_cells: int, reached_other: bool) -> dict:
    return {
        "terminating_rule": rule,
        "flat_cells_traversed": int(flat_cells),
        "reached_other": bool(reached_other),
    }


def _enforce_dag(edges: list[_EdgeCand], break_enabled: bool) -> tuple[list[_EdgeCand], list[dict]]:
    """lowest-slope SYNTHETIC edge in the found cycle (tie: highest provisional id);"""
    rounds: list[dict] = []

    def wcc_count(es: list[_EdgeCand]) -> int:
        parent: dict[int, int] = {}

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for e in es:
            parent.setdefault(e.src[0], e.src[0])
            parent.setdefault(e.src[1], e.src[1])
        for e in es:
            ru, rv = find(e.src[0]), find(e.src[1])
            if ru != rv:
                parent[max(ru, rv)] = min(ru, rv)
        return len({find(n) for n in parent})

    def remainder(es: list[_EdgeCand]) -> set[int]:
        adj: dict[int, list[int]] = {}
        indeg: dict[int, int] = {}
        for e in es:
            u, v = e.src
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, [])
            indeg[u] = indeg.get(u, 0)
            indeg[v] = indeg.get(v, 0) + 1
        q = deque(sorted(n for n, dg in indeg.items() if dg == 0))
        seen = set(q)
        while q:
            u = q.popleft()
            for v in sorted(adj[u]):
                indeg[v] -= 1
                if indeg[v] == 0:
                    seen.add(v)
                    q.append(v)
        return set(indeg) - seen

    working = list(edges)
    while True:
        rem = remainder(working)
        if not rem:
            break
        if not break_enabled:
            raise StitchError(
                f"cycles detected (toposort remainder {len(rem)} nodes) and DAG-break is "
                "disabled (cfg stitch.dag_break_enabled=false) - refusing to emit a cyclic graph"
            )
        emap: dict[tuple[int, int], list[_EdgeCand]] = {}
        rem_adj: dict[int, set[int]] = {u: set() for u in rem}
        for e in working:
            u, v = e.src
            if u in rem and v in rem:
                rem_adj[u].add(v)
                emap.setdefault((u, v), []).append(e)
        cycle_edges: list[tuple[int, int]] = []
        color: dict[int, int] = {}

        for start in sorted(rem):
            if cycle_edges or color.get(start, 0) != 0:
                continue
            stack: list[tuple[int, Any]] = [(start, iter(sorted(rem_adj[start])))]
            color[start] = 1
            path = [start]
            while stack and not cycle_edges:
                u, it = stack[-1]
                advanced = False
                for v in it:
                    if color.get(v, 0) == 1:
                        i = path.index(v)
                        cyc = path[i:]
                        cycle_edges = list(zip(cyc, cyc[1:], strict=False))
                        cycle_edges.append((cyc[-1], cyc[0]))
                        break
                    if color.get(v, 0) == 0:
                        color[v] = 1
                        path.append(v)
                        stack.append((v, iter(sorted(rem_adj[v]))))
                        advanced = True
                        break
                if not advanced and not cycle_edges:
                    color[u] = 2
                    path.pop()
                    stack.pop()

        if not cycle_edges:
            raise StitchError("toposort remainder non-empty but no cycle found - internal error")
        candidates = [e for ce in cycle_edges for e in emap.get(ce, [])]
        synth = [e for e in candidates if e.edge_source == "synthesised"]
        pool = synth if synth else candidates
        victim = min(pool, key=lambda e: (e.slope_m_per_m, -e.prov_id))
        wcc_before = wcc_count(working)
        working.remove(victim)
        rounds.append(
            {
                "cycle_size": len(cycle_edges),
                "dropped_edge_id": victim.prov_id,
                "dropped_edge": {
                    "order": victim.order,
                    "edge_source": victim.edge_source,
                    "slope_m_per_m": victim.slope_m_per_m,
                    "length_m": victim.length_m,
                },
                "pool": "synthetic" if synth else "any_fallback_no_synthetic_in_cycle",
                "wcc_before": wcc_before,
                "wcc_after": wcc_count(working),
            }
        )
    return working, rounds


def _apply_dedup_and_degenerate(
    edges: list[_EdgeCand],
) -> tuple[list[_EdgeCand], list[dict], int, int]:
    """Step 8 parallel dedup on (from,to,order): keep OBSERVED over SYNTHESISED,
    self_loop_dropped_count; OBSERVED edges with length < 0.05 m or null slope"""
    dropped_records: list[dict] = []
    by_key: dict[tuple[int, int, str], list[int]] = {}
    for ei, e in enumerate(edges):
        by_key.setdefault((e.src[0], e.src[1], e.order), []).append(ei)
    keep: list[int] = []
    for key in sorted(by_key):
        idxs = by_key[key]
        if len(idxs) == 1:
            keep.append(idxs[0])
            continue
        ranked = sorted(
            idxs,
            key=lambda ei: (
                0 if edges[ei].edge_source == "observed" else 1,
                -edges[ei].length_m,
                ei,
            ),
        )
        keep.append(ranked[0])
        for ei in ranked[1:]:
            dropped_records.append(
                {
                    "stage": "parallel_dedup",
                    "from_working_pos": edges[ei].src[0],
                    "to_working_pos": edges[ei].src[1],
                    "order": edges[ei].order,
                    "edge_source": edges[ei].edge_source,
                    "length_m": edges[ei].length_m,
                }
            )
    survivors: list[_EdgeCand] = []
    self_loop_dropped = 0
    zero_length_dropped = 0
    for ei in sorted(keep):
        e = edges[ei]
        if e.src[0] == e.src[1]:
            self_loop_dropped += 1
            dropped_records.append(
                {
                    "stage": "self_loop",
                    "from_working_pos": e.src[0],
                    "to_working_pos": e.src[1],
                    "order": e.order,
                    "edge_source": e.edge_source,
                    "length_m": e.length_m,
                }
            )
            continue
        if e.kind == "observed" and (e.length_m < 0.05 or e.slope_m_per_m is None):
            zero_length_dropped += 1
            dropped_records.append(
                {
                    "stage": "zero_length",
                    "order": e.order,
                    "edge_source": e.edge_source,
                    "length_m": e.length_m,
                }
            )
            continue
        survivors.append(e)
    survivors.sort(key=lambda e: e.sort_key())
    for pid, e in enumerate(survivors, start=1):
        e.prov_id = pid
    return survivors, dropped_records, self_loop_dropped, zero_length_dropped


def stitch_components(reaches: list[Reach], cfg: dict) -> StitchResult:
    """Frozen contract entrypoint: build the stitched drain graph under the"""
    t0 = time.perf_counter()
    grid = cfg["grid"]
    st = cfg["stitch"]
    inputs = cfg["inputs"]
    outputs = cfg["outputs"]
    width, height = int(grid["width"]), int(grid["height"])
    transform = [float(x) for x in grid["transform"]]
    crs = str(cfg["crs"])

    reaches = sorted(reaches, key=lambda r: r.reach_id)
    for r in reaches:
        if r.drain_class not in ("primary", "secondary"):
            raise StitchError(
                f"reach {r.reach_id} has drain_class {r.drain_class!r}; stitcher takes P+S only"
            )
    budget = _print_budget(width * height, len(reaches))

    elev_path = _repo(inputs["elevation_surface"])
    _elevation_sha_gate(elev_path, inputs["elevation_sha256"])

    run_dir = _repo(outputs["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    # Rule-6 single-source-of-truth fix (wave-3 incident, 2026-08-25): the run
    # manifest lives INSIDE outputs.run_dir. Deriving it from the separate
    # outputs.manifest key let a redirected-run_dir rerun overwrite the repo
    # manifest with a stage skeleton; both keys must never diverge again.
    manifest_path = _repo(outputs["run_dir"]) / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "stage": "wf1_stitch",
        "status": "running",
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "started_utc": _utc_now(),
        "config_snapshot": json.loads(json.dumps(cfg, default=str)),
        "budget_projection": budget,
    }

    def write_manifest(update: dict) -> None:
        manifest.update(update)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))

    write_manifest({})  # rule 6: manifest exists at run start, updated in place

    z, valid = _load_grid_arrays(elev_path, width, height, transform, crs)
    mask, mask_diag = _build_retained_mask(z, valid, _repo(inputs["retained_basins_csv"]))
    hybrid, pointer, pointer_path, raster_shas = _derive_hybrid_and_pointer(
        z,
        valid,
        mask,
        elev_path,
        run_dir,
        crs,
        transform,
        width,
        height,
        float(st["flat_increment_m"]),
    )
    raised = mask & (hybrid != z) & valid
    footprints, fp_diag = _rasterize_footprints(
        reaches,
        width,
        height,
        transform,
        crs,
        float(st["burn_buffer_m"]),
        int(st["quad_segs"]),
        run_dir,
    )
    del hybrid

    #   snap_dsu over endpoint SLOTS -> node identity (pairs within tolerance unite);
    slot_pts, snap_dsu, slot_cells = _snap_slots(reaches, transform, float(st["snap_tolerance_m"]))
    n_reach = len(reaches)
    root_members: dict[int, list[tuple[float, float]]] = {}
    for si, (x, y) in enumerate(slot_pts):
        root_members.setdefault(snap_dsu.find(si), []).append((x, y))
    nodes: list[_Node] = []
    pos_of_root: dict[int, int] = {}
    nodata_errors: list[str] = []
    for root in sorted(root_members):
        x, y = min(root_members[root], key=lambda p: (round(p[0], 3), round(p[1], 3)))
        r, c = _cell_of(x, y, transform)
        if not valid[r, c]:
            nodata_errors.append(f"snapped node ({x},{y}) samples a nodata cell")
            continue
        pos_of_root[root] = len(nodes)
        nodes.append(
            _Node(x=float(round(x, 3)), y=float(round(y, 3)), z=float(z[r, c]), kind="snapped")
        )
    if nodata_errors:
        raise StitchError("elevation sampling failed:\n" + "\n".join(nodata_errors))
    slot_node = [pos_of_root[snap_dsu.find(si)] for si in range(2 * n_reach)]

    comp_dsu = _DSU(len(nodes))
    for pos in range(len(nodes)):
        nodes[pos].comp_seed = pos
    for i in range(n_reach):
        comp_dsu.union(slot_node[2 * i], slot_node[2 * i + 1])
    comp_of_reach = [comp_dsu.find(slot_node[2 * i]) for i in range(n_reach)]
    component_count_pre = len(set(comp_of_reach))
    seeds = np.zeros(int(footprints.max()) + 1, dtype=np.int64)
    for i, root in enumerate(comp_of_reach):
        seeds[i + 1] = root

    histogram = {
        "reached_other": 0,
        "junction_entry": 0,
        "junction_entry_at_start": 0,
        "pit": 0,
        "domain_exit": 0,
        "self_hit_abort": 0,
        "max_steps_abort": 0,
    }
    lost_walks: list[list[Any]] = []
    edges: list[_EdgeCand] = []
    synth_cells: set[tuple[int, int]] = set()
    counters = {
        "self_hit_count": 0,
        "self_reentry_count": 0,
        "junction_split_count": 0,
        "split_unresolved_count": 0,
        "corridor_merge_dropped_count": 0,
        "zero_length_connector_skipped": 0,
        "dropped_connector_count": 0,
        "stub_count": 0,  # retired by D21 split-at-projection; kept for manifest stability
        "stub_total_length_m": 0.0,
        "stub_overlength_count": 0,
    }
    max_steps = int(st["max_connector_steps"])
    dyn_nodes: dict[tuple[str, int, int], int] = {}

    def node_at(kind: str, reason: str | None, cell: tuple[int, int]) -> int | None:
        key = (kind, cell[0], cell[1])
        if key in dyn_nodes:
            return dyn_nodes[key]
        if not valid[cell[0], cell[1]]:
            return None
        x, y = _center_of(cell[0], cell[1], transform)
        dyn_nodes[key] = len(nodes)
        nodes.append(
            _Node(
                x=float(round(x, 3)),
                y=float(round(y, 3)),
                z=float(z[cell[0], cell[1]]),
                kind=kind,
                outfall_reason=reason,
            )
        )
        return dyn_nodes[key]

    # component's nearest OBSERVED reach geometry; that reach is split into two
    # segments sharing the new junction node (true confluence; observed total
    # 791 refused joins >25 m and drove the 269-component residual.
    from shapely.geometry import Point
    from shapely.ops import substring as _substring

    splits: dict[int, list[tuple[float, int]]] = {}
    xy_nodes: dict[tuple[float, float], int] = {}
    split_max_distance_m = float(st.get("split_max_distance_m", 15.0))

    def split_node_for(entered_root: int, cell: tuple[int, int]) -> int | None:
        cx, cy = _center_of(cell[0], cell[1], transform)
        pt = Point(cx, cy)
        best: tuple[float, int, float] | None = None
        for j in range(n_reach):
            if comp_dsu.find(comp_of_reach[j]) != entered_root:
                continue
            g = reaches[j].geom
            d_along = float(g.project(pt))
            proj = g.interpolate(d_along)
            dist = float(proj.distance(pt))
            cand = (round(dist, 6), int(reaches[j].reach_id), d_along)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None or best[0] > split_max_distance_m:
            return None
        _dist, _rid, d_along = best
        win = None
        for j in range(n_reach):
            if comp_dsu.find(comp_of_reach[j]) != entered_root:
                continue
            g = reaches[j].geom
            d_along_j = float(g.project(pt))
            dist_j = round(float(g.interpolate(d_along_j).distance(pt)), 6)
            if (dist_j, int(reaches[j].reach_id)) == (best[0], best[1]):
                win = (j, d_along_j)
                break
        assert win is not None
        j, d_along = win
        proj = reaches[j].geom.interpolate(d_along)
        key = (round(float(proj.x), 3), round(float(proj.y), 3))
        if key in xy_nodes:
            return xy_nodes[key]
        r_s, c_s = _cell_of(key[0], key[1], transform)
        if not valid[r_s, c_s]:
            return None
        pos = len(nodes)
        nodes.append(_Node(x=key[0], y=key[1], z=float(z[r_s, c_s]), kind="junction"))
        xy_nodes[key] = pos
        splits.setdefault(j, []).append((d_along, pos))
        counters["junction_split_count"] += 1
        return pos

    def handle_walk(i: int, end_tag: str, oc: _WalkOutcome, my_seed_root: int) -> None:
        reach_id = reaches[i].reach_id
        hist_key = "pit" if oc.outcome == "pit_start_no_edge" else oc.outcome
        histogram[hist_key] += 1
        counters["self_reentry_count"] += getattr(oc, "self_reentries", 0)
        if oc.outcome == "max_steps_abort":
            counters["dropped_connector_count"] += 1
            lost_walks.append([reach_id, end_tag, oc.outcome])
            return
        if oc.outcome == "pit_start_no_edge":
            lost_walks.append([reach_id, end_tag, oc.outcome])
            return
        if oc.outcome == "reached_other":
            return
        frm_pos = slot_node[2 * i if end_tag == "A" else 2 * i + 1]
        term = oc.terminal
        assert term is not None

        def _emit_connector(to_pos: int) -> None:
            length, geom = _polyline(oc.visited, transform)
            if length <= 0.0:
                counters["zero_length_connector_skipped"] += 1
                return
            synth_cells.update(oc.visited)
            fn, tn = nodes[frm_pos], nodes[to_pos]
            edges.append(
                _EdgeCand(
                    src=(frm_pos, to_pos),
                    order="synthetic_connector",
                    edge_source="synthesised",
                    direction_confidence="low" if oc.flat_cells > 0 else "high",
                    basis=_basis(oc.outcome, oc.flat_cells, oc.outcome == "reached_other"),
                    geometry=geom,
                    length_m=length,
                    slope_m_per_m=(abs(fn.z - tn.z) / length) if length > 0 else 0.0,
                    kind="connector",
                    cells=frozenset(oc.visited),
                )
            )

        if oc.outcome in ("junction_entry", "junction_entry_at_start"):
            assert oc.entered_root is not None
            s_pos = split_node_for(oc.entered_root, term)
            if s_pos is not None:
                counters["junction_split_count"] += 0 if oc.outcome == "junction_entry" else 0
                _emit_connector(s_pos)
                comp_dsu.union(my_seed_root, oc.entered_root)
                return
            counters["split_unresolved_count"] += 1
            lost_walks.append([reach_id, end_tag, f"{oc.outcome}_split_unresolved"])
            return

        if oc.outcome == "pit":
            to_pos = node_at("outfall", "pit", term)
        else:
            to_pos = node_at("outfall", "domain_exit", term)
        if to_pos is None:
            lost_walks.append([reach_id, end_tag, f"{oc.outcome}_nodata_terminal"])
            return
        _emit_connector(to_pos)

    reach_dir: dict[int, tuple[int, str, int, bool, str]] = {}

    for i in range(n_reach):
        reach = reaches[i]
        cell_a, cell_b = slot_cells[2 * i], slot_cells[2 * i + 1]
        walk_a = _do_walk(
            cell_a,
            cell_b,
            i + 1,
            comp_dsu.find(comp_of_reach[i]),
            pointer,
            footprints,
            seeds,
            comp_dsu,
            raised,
            max_steps,
        )
        handle_walk(i, "A", walk_a, comp_of_reach[i])
        walk_b = _do_walk(
            cell_b,
            cell_a,
            i + 1,
            comp_dsu.find(comp_of_reach[i]),
            pointer,
            footprints,
            seeds,
            comp_dsu,
            raised,
            max_steps,
        )
        handle_walk(i, "B", walk_b, comp_of_reach[i])

        # Step 6: direction rule for the observed reach (emission deferred until
        pa, pb = slot_pts[2 * i], slot_pts[2 * i + 1]
        if walk_a.outcome == "reached_other" or walk_b.outcome == "reached_other":
            if walk_a.outcome == "reached_other" and walk_b.outcome == "reached_other":
                from_end = 0 if (pa[0], pa[1]) <= (pb[0], pb[1]) else 1
            else:
                from_end = 0 if walk_a.outcome == "reached_other" else 1
            dec = walk_a if from_end == 0 else walk_b
            rule, flats, reached = "reached_other", dec.flat_cells, True
            conf = "high" if flats == 0 else "low"
        else:
            za, zb = nodes[slot_node[2 * i]].z, nodes[slot_node[2 * i + 1]].z
            if za > zb:
                from_end = 0
            elif zb > za:
                from_end = 1
            else:
                from_end = 0 if (pa[0], pa[1]) <= (pb[0], pb[1]) else 1
            rule, flats, reached = "elevation_fallback", 0, False
            conf = "medium"
        reach_dir[i] = (from_end, rule, flats, reached, conf)

    # Emit observed reaches AFTER the walk loop: a reach with junction splits is
    # (true confluences; segment lengths sum to the original measured length).
    for i in range(n_reach):
        reach = reaches[i]
        from_end, rule, flats, reached, conf = reach_dir[i]
        oriented = LineString(
            [(float(x), float(y)) for x, y in reach.geom.coords]
            if from_end == 0
            else [(float(x), float(y)) for x, y in reversed(list(reach.geom.coords))]
        )
        src_pos = slot_node[2 * i if from_end == 0 else 2 * i + 1]
        dst_pos = slot_node[2 * i + 1 if from_end == 0 else 2 * i]
        total_len = float(reach.length_m)
        cuts_raw = sorted(
            (
                (d_along if from_end == 0 else total_len - d_along, pos)
                for d_along, pos in splits.get(i, [])
            ),
            key=lambda t: t[0],
        )
        cuts: list[tuple[float, int]] = []
        for d_along, pos in cuts_raw:
            if pos in (src_pos, dst_pos):
                continue
            if cuts and (cuts[-1][1] == pos or d_along - cuts[-1][0] < 0.05):
                continue
            cuts.append((d_along, pos))
        boundaries = [(0.0, src_pos)] + cuts + [(total_len, dst_pos)]
        for k in range(len(boundaries) - 1):
            s_d, s_pos = boundaries[k]
            e_d, e_pos = boundaries[k + 1]
            sub = _substring(oriented, max(s_d, 0.0), min(e_d, total_len))
            seg_len = float(sub.length)
            zf, zt = nodes[s_pos].z, nodes[e_pos].z
            edges.append(
                _EdgeCand(
                    src=(s_pos, e_pos),
                    order=reach.drain_class,
                    edge_source="observed",
                    direction_confidence=conf,
                    basis=_basis(rule, flats, reached),
                    geometry=sub,
                    length_m=seg_len if seg_len > 0 else float(e_d - s_d),
                    slope_m_per_m=(abs(zf - zt) / float(sub.length) if sub.length > 0 else 0.0),
                    kind="observed",
                )
            )

    min_jaccard = float(st.get("corridor_merge_min_jaccard", 0.35))

    def _merge_corridors(cands: list[_EdgeCand]) -> tuple[list[_EdgeCand], list[dict]]:
        conns = [e for e in cands if e.kind == "connector" and e.cells]
        if not conns:
            return cands, []
        by_cell: dict[tuple[int, int], list[int]] = {}
        for ci, e in enumerate(conns):
            for cell in e.cells:
                by_cell.setdefault(cell, []).append(ci)
        parent = list(range(len(conns)))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        seen_pairs: set[tuple[int, int]] = set()
        for _, members in by_cell.items():
            for x in range(len(members)):
                for yy in range(x + 1, len(members)):
                    a_i, b_i = members[x], members[yy]
                    key = (min(a_i, b_i), max(a_i, b_i))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    A, B = conns[a_i].cells, conns[b_i].cells
                    inter = len(A & B)
                    if not inter:
                        continue
                    jac = inter / len(A | B)
                    if jac >= min_jaccard:
                        ra, rb = find(a_i), find(b_i)
                        if ra != rb:
                            parent[max(ra, rb)] = min(ra, rb)
        clusters: dict[int, list[int]] = {}
        for ci in range(len(conns)):
            clusters.setdefault(find(ci), []).append(ci)
        dropped: list[dict] = []
        drop_idx: set[int] = set()
        for _root, members in clusters.items():
            if len(members) <= 1:
                continue
            keep = max(
                members,
                key=lambda ci: (
                    round(conns[ci].length_m, 6),
                    hashlib.sha256(conns[ci].wkb_hex().encode()).hexdigest(),
                ),
            )
            for ci in members:
                if ci != keep:
                    drop_idx.add(ci)
                    dropped.append(
                        {
                            "stage": "corridor_merge",
                            "order": conns[ci].order,
                            "edge_source": conns[ci].edge_source,
                            "length_m": conns[ci].length_m,
                            "kept_length_m": conns[keep].length_m,
                        }
                    )
        if not drop_idx:
            return cands, []
        kept_set_cells: set[tuple[int, int]] = set()
        out: list[_EdgeCand] = []
        for ci, e in enumerate(conns):
            if ci in drop_idx:
                continue
            kept_set_cells |= set(e.cells or ())
            out.append(e)
        synth_cells.clear()
        synth_cells.update(kept_set_cells)
        merged = [e for e in cands if not (e.kind == "connector" and e.cells)] + out
        counters["corridor_merge_dropped_count"] = len(dropped)
        return merged, dropped

    survivors_pre, corridor_records = _merge_corridors(edges)

    # Steps 8+9: parallel dedup (observed-over-synthesised) + degenerate drops.
    survivors, dropped_records, self_loop_dropped, zero_length_dropped = (
        _apply_dedup_and_degenerate(survivors_pre)
    )
    dropped_records = list(dropped_records) + list(corridor_records)

    dag_enabled = bool(st.get("dag_break_enabled", True))
    survivors, cycle_rounds = _enforce_dag(survivors, dag_enabled)

    used: set[int] = set()
    for e in survivors:
        used.update(e.src)
    orphan_count = 0
    remap: dict[int, int] = {}
    new_nodes: list[_Node] = []
    for pos, node in enumerate(nodes):
        if pos in used or node.kind == "snapped":
            remap[pos] = len(new_nodes)
            new_nodes.append(node)
        else:
            orphan_count += 1
    for e in survivors:
        e.src = (remap[e.src[0]], remap[e.src[1]])

    order_by_key = sorted(range(len(new_nodes)), key=lambda p: new_nodes[p].key())
    final_id_of_pos: dict[int, int] = {}
    node_recs: list[NodeRec] = []
    for rank, pos in enumerate(order_by_key, start=1):
        nd = new_nodes[pos]
        final_id_of_pos[pos] = rank
        node_recs.append(
            NodeRec(
                node_id=rank,
                x_m=nd.x,
                y_m=nd.y,
                elev_m=nd.z,
                node_type="outfall" if nd.kind == "outfall" else "junction",
                outfall_reason=nd.outfall_reason,
            )
        )
    survivors.sort(
        key=lambda e: (
            final_id_of_pos[e.src[0]],
            final_id_of_pos[e.src[1]],
            ORDER_RANK[e.order],
            round(e.length_m, 6),
            hashlib.sha256(e.wkb_hex().encode()).hexdigest(),
        )
    )
    edge_recs: list[EdgeRec] = []
    for eid, e in enumerate(survivors, start=1):
        edge_recs.append(
            EdgeRec(
                edge_id=eid,
                from_node=final_id_of_pos[e.src[0]],
                to_node=final_id_of_pos[e.src[1]],
                order=e.order,
                edge_source=e.edge_source,
                direction_confidence=e.direction_confidence,
                direction_basis=json.dumps(e.basis, sort_keys=True, separators=(",", ":")),
                geometry=e.geometry,
                length_m=e.length_m,
                slope_m_per_m=e.slope_m_per_m,
            )
        )
    fingerprint_payload = json.dumps(
        [
            [
                er.from_node,
                er.to_node,
                er.order,
                round(er.length_m, 6),
                hashlib.sha256(er.geometry.wkb_hex.encode()).hexdigest(),
            ]
            for er in edge_recs
        ],
        separators=(",", ":"),
    )
    graph_fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()

    final_dsu = _DSU(len(node_recs) + 1)
    for er in edge_recs:
        final_dsu.union(er.from_node, er.to_node)
    precomp_groups: dict[int, set[int]] = {}
    precomp_reaches: dict[int, list[int]] = {}
    for i in range(n_reach):
        root = comp_of_reach[i]
        fid = final_id_of_pos[remap[slot_node[2 * i]]]
        precomp_groups.setdefault(root, set()).add(fid)
        precomp_reaches.setdefault(root, []).append(reaches[i].reach_id)
    group_roots: dict[int, set[int]] = {}
    for root in sorted(precomp_groups):
        reps = {final_dsu.find(f) for f in precomp_groups[root]}
        group_roots.setdefault(min(reps), set()).add(root)
    component_count_post = len(group_roots)
    stranded: list[dict] = []
    for gid, (_, roots) in enumerate(sorted(group_roots.items()), start=1):
        stranded.append(
            {
                "component_id": gid,
                "size": len(roots),
                "reach_ids": sorted(rid for root in roots for rid in precomp_reaches[root]),
            }
        )

    # Bidirectional enum assertion + V-OUTFALL measured-evidence check.
    unresolved_outfall_count = 0
    outfall_evidence: dict[int, set[str]] = {}
    for er in edge_recs:
        if (er.edge_source == "synthesised") != (er.order == "synthetic_connector"):
            raise StitchError(
                f"edge {er.edge_id}: edge_source/order bidirectional tag violated "
                f"({er.edge_source}/{er.order})"
            )
        basis = json.loads(er.direction_basis)
        rule = basis.get("terminating_rule")
        if rule in ("pit", "domain_exit"):
            outfall_evidence.setdefault(er.to_node, set()).add(rule)
    for nr in node_recs:
        if nr.node_type == "outfall":
            if nr.outfall_reason not in OUTFALL_REASONS or nr.outfall_reason not in (
                outfall_evidence.get(nr.node_id, set())
            ):
                unresolved_outfall_count += 1
    if unresolved_outfall_count > 0:
        raise StitchError(
            f"V-OUTFALL violated: {unresolved_outfall_count} outfall node(s) lack measured "
            "pit/domain_exit evidence - STOP"
        )

    negative_slope_edge_count = sum(
        1
        for er in edge_recs
        if node_recs[er.to_node - 1].elev_m > node_recs[er.from_node - 1].elev_m
    )
    flat_flagged_edge_count = sum(1 for er in edge_recs if er.direction_confidence == "low")
    invented_length_km = (
        sum(er.length_m for er in edge_recs if er.edge_source == "synthesised") / 1000.0
    )
    observed_length_km = (
        sum(er.length_m for er in edge_recs if er.edge_source == "observed") / 1000.0
    )
    fraction = invented_length_km / OBSERVED_NETWORK_DENOMINATOR_KM

    metrics = {
        "component_count_pre_stitch": component_count_pre,
        "component_count_post_stitch": component_count_post,
        "stranded_component_ids": stranded,
        "walk_outcome_histogram": histogram,
        "lost_walk_audit": {"entries": lost_walks, "total_no_edge": len(lost_walks)},
        "cycle_break_rounds": cycle_rounds,
        "counters": {
            "self_hit_count": counters["self_hit_count"],
            "self_reentry_count": counters["self_reentry_count"],
            "junction_split_count": counters["junction_split_count"],
            "split_unresolved_count": counters["split_unresolved_count"],
            "corridor_merge_dropped_count": counters["corridor_merge_dropped_count"],
            "dropped_connector_count": counters["dropped_connector_count"],
            "dropped_edge_count": len(dropped_records),
            "dropped_edge_ids": [
                (
                    f"{d['stage']}:{d.get('order', '?')}:"
                    f"{round(d.get('length_m', 0.0), 3)}m"
                    f"@{d.get('from_working_pos', '-')}->{d.get('to_working_pos', '-')}"
                )
                for d in dropped_records
            ],
            "self_loop_dropped_count": self_loop_dropped,
            "zero_length_dropped_count": zero_length_dropped,
            "cycle_break_count": len(cycle_rounds),
            "flat_flagged_edge_count": flat_flagged_edge_count,
            "negative_slope_edge_count": negative_slope_edge_count,
            "stub_count": counters["stub_count"],
            "stub_total_length_m": round(counters["stub_total_length_m"], 3),
            "stub_overlength_count": counters["stub_overlength_count"],
            "orphan_node_count": orphan_count,
            "unresolved_outfall_count": unresolved_outfall_count,
            "cycle_break_observed_edge_dropped_count": sum(
                1 for r in cycle_rounds if r.get("pool") != "synthetic"
            ),
            "synthesised_walked_unique_cells": len(synth_cells),
            "synthesised_walked_coverage_km_lower": round(len(synth_cells) * 10.0 / 1000.0, 3),
            "synthesised_walked_coverage_km_upper": round(
                len(synth_cells) * math.sqrt(200.0) / 1000.0, 3
            ),
        },
        "output_sha256": {
            "derived_pointer": raster_shas["derived_pointer"],
            "hybrid_filled_dem": raster_shas["hybrid_filled_dem"],
            "footprints": fp_diag["footprints_sha256"],
        },
        "output_paths": {
            "derived_pointer": str(pointer_path),
            "footprints": fp_diag["footprints_path"],
            "run_dir": str(run_dir),
        },
        "graph_fingerprint": graph_fingerprint,
        "graph_is_dag": True,
        "synthesised_fraction_of_total_length": round(fraction, 6),
        "observed_length_km_measured_from_edges": round(observed_length_km, 3),
        "stitcher_params": {
            "snap_tolerance_m": float(st["snap_tolerance_m"]),
            "max_connector_steps": int(st["max_connector_steps"]),
            "burn_buffer_m": float(st["burn_buffer_m"]),
            "quad_segs": int(st["quad_segs"]),
            "flat_increment_m": float(st["flat_increment_m"]),
            "tie_break_order": TIE_BREAK_ORDER_PINNED,
            "self_hit_granularity": "reach",
            "split_max_distance_m": float(st.get("split_max_distance_m", 15.0)),
            "corridor_merge_min_jaccard": float(st.get("corridor_merge_min_jaccard", 0.35)),
            "dag_break_enabled": dag_enabled,
            "pointer_derivation": (
                "in-repo deterministic priority-flood + wang-liu increments "
                "(deviation D17: replaces whitebox fill_depressions, whose output is "
                "value-nondeterministic run-to-run - measured 11,227 hybrid-cell drift, "
                "max 0.166 m, refuting rerun_stability byte-identity)"
            ),
            "elevation_sha256_binding": inputs["elevation_sha256"],
            "retained_basin_mask": mask_diag,
            "rasterization_rule": fp_diag["rasterization_rule"],
            "notes": [
                "domain_exit outfall placed at last inside-cell center; pit at pit-cell center",
                "junction_entry_at_start writes only the stub into the ENTERED component "
                "(literal pin); the walking side attaches through the merged component label; "
                "refused stubs leave the walk edgeless and appear in lost_walk_audit",
                "cycle_break_rounds cite provisional edge ids assigned after dedup/degenerate "
                "drops and before final contiguous renumbering",
                "zero-length degenerate drops apply to OBSERVED edges only; a walked connector "
                "always spans at least one 10 m step (pit_start_no_edge emits nothing)",
                "direction_confidence grading: high=reached_other and flat==0; low=flat>0 on "
                "the deciding walk; medium=elevation fallback",
                "retained-basin mask reconstructed from register CSV (outlet_cell + "
                "spill_elevation_m strict flood on pinned surface) because the CSV carries "
                "no pixel lists; realized mask diagnostics recorded beside register totals",
                "self_loop_dropped_count counts OBSERVED reaches whose two endpoints snap "
                "together at 10 m (input-geometry artifact, not a build defect); contract "
                "degenerate policy makes the candidate permanently refuse-load while >0 - "
                "owner adjudication menu M5b",
                "stub_count is CREATIONS; stubs surviving dedup appear in artefacts as "
                "direction_basis.terminating_rule=='junction_stub*' - creation-vs-survival "
                "reconciliation in graph_io.finalize_manifest stub_survived_count",
                "synthesised_walked_coverage_km_lower/upper bound UNIQUE-FOOTPRINT "
                "contribution per distinct cell (10 m cardinal / 14.142 m diagonal step); "
                "they do NOT bound the headline walked-length numerator - connectors "
                "re-traverse shared corridors (realized mean ~7.5 crossings/cell)",
            ],
        },
        "policy_frontier_table": POLICY_FRONTIER_TABLE,
        "policy_frontier_denominator_km": OBSERVED_NETWORK_DENOMINATOR_KM,
        "realized_policy_row": {
            "policy": "realized literal-pin composite (MASKED flats: wang-liu increments "
            "inside retained basins only; outside-basin pseudo-pits terminate as "
            "outfall(pit) per pinned stop conditions)",
            "post_stitch_comps": component_count_post,
            "invented_length_km": round(invented_length_km, 3),
            "fraction_of_observed": round(fraction, 6),
        },
        "wall_clock_sec": round(time.perf_counter() - t0, 2),
    }

    status = "stopped_owner_adjudication" if component_count_post > 1 else "completed"
    reasons = []
    if component_count_post > 1:
        reasons.append("expected connectivity_post_stitch_gt_1")
    if any(r.get("pool") != "synthetic" for r in cycle_rounds):
        reasons.append(
            "cycle_break_dropped_observed_edge_owner_attention_menu_M4: a directed "
            "cycle contained no synthetic edge to drop; pinned lowest-slope-synthetic "
            "rule could not resolve it and an OBSERVED edge was dropped instead"
        )
    write_manifest(
        {
            "status": status,
            "finished_utc": _utc_now(),
            "wall_clock_sec": metrics["wall_clock_sec"],
            "metrics_headline": {
                "component_count_pre_stitch": component_count_pre,
                "component_count_post_stitch": component_count_post,
                "node_count": len(node_recs),
                "edge_count": len(edge_recs),
                "synthesised_fraction_of_total_length": metrics[
                    "synthesised_fraction_of_total_length"
                ],
                "graph_fingerprint": graph_fingerprint,
            },
            "counters": metrics["counters"],
            "reasons": reasons,
            "artefact_paths": {"run_dir": str(run_dir)},
            "publication_note": (
                "candidate artefacts only under runs/drain_graph_build/; NOTHING placed at "
                "data/processed/ while component_count_post_stitch > 1"
            ),
        }
    )
    print(
        f"[stitch] pre={component_count_pre} post={component_count_post} "
        f"nodes={len(node_recs)} edges={len(edge_recs)} "
        f"synth_fraction={metrics['synthesised_fraction_of_total_length']} "
        f"fingerprint={graph_fingerprint[:12]}... status={status}"
    )
    from shapely.geometry import Point as _Point

    named_outfalls: list[dict[str, Any]] = []
    _VALLEY_OF = {
        "bellandur": "Koramangala-Challaghatta",
        "varthur": "Koramangala-Challaghatta",
        "madiwala": "Koramangala-Challaghatta",
        "agara": "Koramangala-Challaghatta",
        "ulsoor": "Koramangala-Challaghatta",
        "hebbal": "Hebbal",
        "yelahanka": "Hebbal",
        "jakkur": "Hebbal",
        "vrishabhavathi": "Vrishabhavathi",
        "kempambudhi": "Vrishabhavathi",
        "sankey": "Vrishabhavathi",
    }
    water_path = _repo(inputs.get("osm_water_gpkg", ""))
    waters = None
    try:
        import geopandas as _gpd

        if Path(water_path).exists():
            waters = _gpd.read_file(water_path)
            if "name" not in waters.columns:
                waters = None
    except Exception:
        waters = None
    for nd in node_recs:
        if nd.node_type != "outfall":
            continue
        pt = _Point(nd.x_m, nd.y_m)
        best_name, best_d = None, float("inf")
        if waters is not None:
            for _, wrow in waters.iterrows():
                nm = wrow.get("name")
                if not isinstance(nm, str) or not nm.strip():
                    continue
                d = pt.distance(wrow.geometry)
                if d < best_d:
                    best_name, best_d = nm.strip(), d
        valley = _VALLEY_OF.get(best_name.lower(), "unmapped_valley") if best_name else "unmapped"
        named_outfalls.append(
            {
                "node_id": int(nd.node_id),
                "x_m": nd.x_m,
                "y_m": nd.y_m,
                "outfall_reason": nd.outfall_reason,
                "nearest_named_water": best_name or "unnamed",
                "distance_m": round(best_d, 1) if best_d < float("inf") else None,
                "valley": valley,
                "justification": (
                    f"terminates by {nd.outfall_reason} per pinned stop conditions "
                    f"(walk evidence in direction_basis of its terminating connector); "
                    f"nearest named receiving water '{best_name or 'unnamed'}' at "
                    f"{best_d:.0f} m; assigned valley {valley}"
                ),
            }
        )

    metrics["named_outfalls"] = named_outfalls
    return StitchResult(nodes=node_recs, edges=edge_recs, metrics=metrics)


def _load_reaches_for_census(cfg: dict) -> tuple[list[Reach], str]:
    try:
        from jaladhar.drainage.loader import load_reaches

        reaches, _diag = load_reaches(cfg["inputs"]["kml_primary"], cfg["inputs"]["kml_secondary"])
        return reaches, "loader"
    except ImportError:
        pass
    import geopandas as gpd

    gdf = gpd.read_file(_repo(cfg["inputs"]["waterways_gpkg"]))
    gdf = gdf[gdf["drain_class"].isin(["primary", "secondary"])]
    reaches: list[Reach] = []
    multipart_parts = 0
    rid = 0
    for cls in ("primary", "secondary"):
        sub = gdf[gdf["drain_class"] == cls]
        for _, row in sub.iterrows():
            geom = row.geometry
            parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
            if len(parts) > 1:
                multipart_parts += 1
            for part in parts:
                rid += 1
                reaches.append(
                    Reach(
                        reach_id=rid,
                        drain_class=cls,
                        geom=LineString(part.coords),
                        length_m=float(part.length),
                    )
                )
    reaches.sort(key=lambda r: r.reach_id)
    if multipart_parts:
        print(f"[census] NOTE: {multipart_parts} multi-part feature(s) split into parts")
    return reaches, "waterways_gpkg_fallback"


@app.command()
def census(
    config: Path = typer.Option(Path("configs/drainage.yaml"), help="Path to drainage YAML"),
) -> None:
    cfg = resolve_config(str(config))
    reaches, source = _load_reaches_for_census(cfg)
    transform = [float(x) for x in cfg["grid"]["transform"]]
    t0 = time.perf_counter()
    _, dsu, _ = _snap_slots(reaches, transform, float(cfg["stitch"]["snap_tolerance_m"]))
    node_count = len({dsu.find(si) for si in range(2 * len(reaches))})
    for i in range(len(reaches)):
        dsu.union(2 * i, 2 * i + 1)
    comps = len({dsu.find(2 * i) for i in range(len(reaches))})
    ok = comps == EXPECTED_CENSUS["components"] and node_count == EXPECTED_CENSUS["nodes"]
    print(
        f"[census] source={source} reaches={len(reaches)} components={comps} "
        f"nodes={node_count} @snap_tolerance_m={cfg['stitch']['snap_tolerance_m']} "
        f"(expected {EXPECTED_CENSUS['components']}/{EXPECTED_CENSUS['nodes']}) "
        f"=> {'PASS' if ok else 'FAIL'} [{time.perf_counter() - t0:.1f}s]"
    )
    manifest = {
        "stage": "wf1_census",
        "status": "completed" if ok else "census_mismatch",
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "finished_utc": _utc_now(),
        "source": source,
        "reaches": len(reaches),
        "components": comps,
        "nodes": node_count,
        "expected": EXPECTED_CENSUS,
        "snap_tolerance_m": float(cfg["stitch"]["snap_tolerance_m"]),
    }
    out = _repo(cfg["outputs"]["run_dir"]) / "census_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def stitch(
    config: Path = typer.Option(Path("configs/drainage.yaml"), help="Path to drainage YAML"),
    reaches_from_loader: bool = typer.Option(
        False, "--reaches-from-loader", help="Require jaladhar.drainage.loader (no gpkg fallback)"
    ),
) -> None:
    cfg = resolve_config(str(config))
    if reaches_from_loader:
        try:
            from jaladhar.drainage.loader import load_reaches
        except ImportError as exc:
            raise StitchError(
                "jaladhar.drainage.loader is not available; --reaches-from-loader refuses "
                "to fall back to the report-only gpkg reader"
            ) from exc
        reaches, _diag = load_reaches(cfg["inputs"]["kml_primary"], cfg["inputs"]["kml_secondary"])
    else:
        reaches, _source = _load_reaches_for_census(cfg)
    result = stitch_components(reaches, cfg)
    m = result.metrics
    typer.echo(
        f"pre_stitch={m['component_count_pre_stitch']} "
        f"post_stitch={m['component_count_post_stitch']}"
    )
    typer.echo(f"walk_histogram={json.dumps(m['walk_outcome_histogram'], sort_keys=True)}")
    typer.echo(f"counters={json.dumps(m['counters'], sort_keys=True)}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":
    app()
