"""Lazy static-basemap bundle builder for the presentation dashboard.

Builds, once per server process and strictly on first request, compact binary
geometry bundles from REALIZED local artefacts:

* ``data/interim/terrain/roads_centrelines.gpkg`` -- the same persisted OSM
  centreline file the routing graph loads (176,171 segments, EPSG:32643);
* ``data/raw/osm/osm_water.gpkg`` -- named OSM water bodies (Bellandur and
  Varthur carry the project story);

and serves them as flat float32 coordinate arrays with uint32 offsets so the
Canvas 2D engine can draw the full city without a map library.

Nothing here synthesises geometry.  Every coordinate, segment id, road class,
lake polygon and landmark anchor is read from the files above, and their
sha256 hashes travel in the metadata payload for rule-3 traceability.
Landmark anchors are derived centroids of matched real features; a landmark
that cannot be matched to a real feature is omitted, never guessed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
ROADS_PATH = REPO_ROOT / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"
WATER_PATH = REPO_ROOT / "data" / "raw" / "osm" / "osm_water.gpkg"

#: Coarse level-of-detail simplification tolerance in metres.  The full
#: resolution bundle carries every vertex (measured 856,297); the coarse
#: bundle decimates for far-zoom draws.
COARSE_TOLERANCE_M = 16.0

#: Presentation classification of realised OSM ``highway`` values into the
#: guide's two dry-road inks.  The values are data (read from the gpkg); the
#: mapping is presentation.
MAJOR_HIGHWAYS = frozenset(
    {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link"}
)

#: Landmarks the guide asks for, each anchored to a real feature or omitted.
#: Lake landmarks match the exact realised water-body name; road landmarks
#: match case-insensitive substrings so spelling variants in the data
#: ("Marathahalli"/"Marathalli") still resolve without inventing geometry.
LAKE_LANDMARKS: tuple[tuple[str, str], ...] = (
    ("Bellandur", "Bellandur Lake"),
    ("Varthur", "Varthur Lake"),
    ("Hebbal", "Hebbal Lake"),
)
ROAD_LANDMARKS: tuple[tuple[str, str], ...] = (
    ("ORR", "outer ring road"),
    ("Marathahalli", "marathall"),
)


class BasemapError(RuntimeError):
    """A realised basemap input could not be read."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flatten_lines(geoms: Any, *, tolerance_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(part_offsets, coords, part_owners)`` for the input lines.

    A segment may contribute several line *parts* (MultiLineString).  ``coords``
    is the interleaved float32 ``[x0, y0, x1, y1, ...]`` array; ``part_offsets``
    has one uint32 entry per part boundary (length ``n_parts + 1``);
    ``part_owners[j]`` is the segment row index owning part ``j``.  Segments
    whose simplification collapses below two points keep their original
    geometry so every segment renders at every level of detail.
    """

    import shapely

    simplified = (
        shapely.simplify(geoms, tolerance_m, preserve_topology=False)
        if tolerance_m > 0.0
        else geoms
    )
    coord_chunks: list[np.ndarray] = []
    part_offsets = [0]
    owners: list[int] = []
    for segment_index, (original, simplified_geom) in enumerate(zip(geoms, simplified)):
        parts: list[Any] = []
        if simplified_geom is not None and not simplified_geom.is_empty:
            if simplified_geom.geom_type == "LineString":
                parts = [simplified_geom]
            elif simplified_geom.geom_type == "MultiLineString":
                parts = list(simplified_geom.geoms)
        if not parts:
            parts = [original]
        for part in parts:
            xy = np.asarray(part.coords, dtype="<f4").ravel()
            coord_chunks.append(xy)
            part_offsets.append(part_offsets[-1] + len(xy) // 2)
            owners.append(segment_index)
    return (
        np.asarray(part_offsets, dtype="<u4"),
        np.concatenate(coord_chunks) if coord_chunks else np.zeros(0, dtype="<f4"),
        np.asarray(owners, dtype="<i4"),
    )


def _pack_blob(offsets: np.ndarray, coords: np.ndarray) -> bytes:
    header = np.asarray([len(offsets) - 1], dtype="<u4").tobytes()
    return header + offsets.tobytes() + coords.tobytes()


@dataclass(frozen=True)
class _LakePolygon:
    name: str | None
    rings: list[tuple[int, int]]


class BasemapBundle:
    """One lazily-built, immutable snapshot of the static basemap."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._built = False
        self.meta: dict[str, Any] = {}
        self._road_blobs: dict[str, bytes] = {}
        self._lake_blob: bytes = b""
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def ensure(self) -> bool:
        """Build once; subsequent calls are no-ops.  Returns success."""

        with self.lock:
            if self._built:
                return self._error is None
            try:
                self._build()
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim in the payload
                self._error = f"{type(exc).__name__}: {exc}"
            self._built = True
            return self._error is None

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        import geopandas as gpd

        if not ROADS_PATH.is_file():
            raise BasemapError(f"realised road centreline file is missing: {ROADS_PATH}")
        if not WATER_PATH.is_file():
            raise BasemapError(f"realised OSM water file is missing: {WATER_PATH}")

        started = datetime.now(UTC)
        roads = gpd.read_file(ROADS_PATH)
        if roads.crs is None or "32643" not in str(roads.crs.to_epsg()):
            raise BasemapError(f"road CRS {roads.crs} does not match frozen EPSG:32643")
        required = {"segment_id", "osm_id", "highway", "name", "geometry"}
        missing = required - set(roads.columns)
        if missing:
            raise BasemapError(f"road file lacks columns: {sorted(missing)}")

        geoms = roads.geometry.values
        bounds = roads.total_bounds
        names_series = roads["name"]
        highway_series = roads["highway"]

        distinct_names = sorted({str(value) for value in names_series.dropna().unique()})
        name_index = {value: i for i, value in enumerate(distinct_names)}
        name_ids = np.asarray(
            [
                name_index.get(str(value), -1) if value is not None and isinstance(value, str) else -1
                for value in names_series
            ],
            dtype="<i4",
        )
        classes = np.asarray(
            [1 if str(value) in MAJOR_HIGHWAYS else 0 for value in highway_series],
            dtype="u1",
        )

        lods = []
        self._road_blobs = {}
        part_owners: np.ndarray | None = None
        for lod_id, tolerance in (("full", 0.0), ("coarse", COARSE_TOLERANCE_M)):
            offsets, coords, owners = _flatten_lines(geoms, tolerance_m=tolerance)
            self._road_blobs[lod_id] = _pack_blob(offsets, coords)
            if lod_id == "full":
                part_owners = owners
            lods.append(
                {
                    "id": lod_id,
                    "tolerance_m": tolerance,
                    "n_parts": int(len(offsets) - 1),
                    "n_vertices": int(len(coords) // 2),
                    "bytes": len(self._road_blobs[lod_id]),
                }
            )
        assert part_owners is not None

        lakes_meta, lake_chunks, ring_ranges_by_polygon = [], [], []
        landmarks: list[dict[str, Any]] = []
        water = gpd.read_file(WATER_PATH)
        if water.crs is None or "32643" not in str(water.crs.to_epsg()):
            raise BasemapError(f"water CRS {water.crs} does not match frozen EPSG:32643")
        simplified_water = water.geometry.simplify(COARSE_TOLERANCE_M, preserve_topology=False)
        for name, (original, geom) in zip(
            water["name"], zip(water.geometry, simplified_water)
        ):
            use = geom if geom is not None and not geom.is_empty else original
            if use is None or use.is_empty:
                continue
            polys = [use] if use.geom_type == "Polygon" else (
                list(use.geoms) if use.geom_type == "MultiPolygon" else []
            )
            ranges: list[tuple[int, int]] = []
            for poly in polys:
                for ring in [poly.exterior, *poly.interiors]:
                    xy = np.asarray(ring.coords, dtype="<f4").ravel()
                    if len(xy) < 6:
                        continue
                    lake_chunks.append(xy)
                    index = len(lake_chunks) - 1
                    ranges.append((index, index + 1))
            if ranges:
                lakes_meta.append({"name": None if name is None else str(name)})
                ring_ranges_by_polygon.append(ranges)

        def _centroid_of_matched_lake(matched_name: str) -> tuple[float, float] | None:
            hits = water[water["name"] == matched_name]
            if hits.empty:
                return None
            centroid = hits.geometry.union_all().centroid
            return (float(centroid.x), float(centroid.y)) if math.isfinite(centroid.x) else None

        def _centroid_of_matched_road(pattern: str) -> tuple[float, float] | None:
            lowered = names_series.str.lower()
            mask = lowered.fillna("").str.contains(pattern, regex=False)
            if not mask.any():
                return None
            union = geoms[mask.values].union_all()
            if union is None or union.is_empty:
                return None
            centroid = union.centroid
            return (float(centroid.x), float(centroid.y)) if math.isfinite(centroid.x) else None

        for label, matched in LAKE_LANDMARKS:
            anchor = _centroid_of_matched_lake(matched)
            if anchor is not None:
                landmarks.append(
                    {"name": label, "x": anchor[0], "y": anchor[1], "matched": matched,
                     "source": "data/raw/osm/osm_water.gpkg"}
                )
        for label, matched in ROAD_LANDMARKS:
            anchor = _centroid_of_matched_road(matched)
            if anchor is not None:
                landmarks.append(
                    {"name": label, "x": anchor[0], "y": anchor[1], "matched": matched,
                     "match": "name_substring_case_insensitive",
                     "source": "data/interim/terrain/roads_centrelines.gpkg"}
                )

        lake_ring_vertices = [len(chunk) // 2 for chunk in lake_chunks]
        lake_offsets = np.zeros(len(lake_ring_vertices) + 1, dtype="<u4")
        np.cumsum(lake_ring_vertices, dtype="<i8", out=lake_offsets[1:])
        lake_blob = _pack_blob(
            lake_offsets,
            np.concatenate(lake_chunks) if lake_chunks else np.zeros(0, dtype="<f4"),
        )
        for entry, ranges in zip(lakes_meta, ring_ranges_by_polygon):
            entry["vertex_ranges"] = [
                (int(lake_offsets[start]), int(lake_offsets[end])) for start, end in ranges
            ]

        self._lake_blob = lake_blob
        self.meta = {
            "crs": "EPSG:32643",
            "bbox": [float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])],
            "roads": {
                "n_segments": int(len(roads)),
                "n_parts": int(len(part_owners)),
                "part_owners": [int(v) for v in part_owners],
                "lods": lods,
                "segment_ids": [int(v) for v in roads["segment_id"]],
                "classes": [int(v) for v in classes],
                "name_ids": [int(v) for v in name_ids],
                "distinct_names": distinct_names,
            },
            "lakes": {"polygons": lakes_meta, "bytes": len(lake_blob)},
            "landmarks": landmarks,
            "presentation": {
                "major_highways": sorted(MAJOR_HIGHWAYS),
                "coarse_tolerance_m": COARSE_TOLERANCE_M,
            },
            "depth_rule": _flood_threshold_cm(),
            "sources": {
                "roads": {"path": _repo_relative(ROADS_PATH), "sha256": _sha256(ROADS_PATH)},
                "water": {"path": _repo_relative(WATER_PATH), "sha256": _sha256(WATER_PATH)},
            },
            "generated_utc": started.isoformat().replace("+00:00", "Z"),
            "wall_clock_sec": round((datetime.now(UTC) - started).total_seconds(), 3),
        }

    # ------------------------------------------------------------------ serve

    def road_bytes(self, lod: str) -> bytes | None:
        return self._road_blobs.get(lod)

    @property
    def lakes_bytes(self) -> bytes:
        return self._lake_blob


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _flood_threshold_cm() -> float:
    """Flood threshold read from the frozen contract, never typed here."""

    from jaladhar.validation.depth_product_contract import load_requirements

    requirements = load_requirements(REPO_ROOT / "configs" / "contracts" / "depth_product.json")
    return float(requirements.flood_threshold_cm)


def gzip_if_accepted(handler: Any, body: bytes, content_type: str) -> None:
    """Send ``body`` honouring the request's Accept-Encoding when worthwhile."""

    accept = handler.headers.get("Accept-Encoding", "")
    if len(body) > 1024 and "gzip" in accept:
        compressed = gzip.compress(body, compresslevel=6)
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Content-Length", str(len(compressed)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(compressed)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


BUNDLE = BasemapBundle()
