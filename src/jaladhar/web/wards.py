"Ward/locality context assets for the operator dashboard (WF-6 build unit U0f). Builds, from REALISED BBMP boundary sources, the three artefacts the N2 label, N5 search and N10 inspector consumers read: * ``data/interim/context/wards_<vintage>.bin`` (+ ``.meta.json`` sidecar) -- ward polygons packed exactly like the basemap lake blob: little-endian ``n_rings:u32 | ring_offsets:u32[n+1] | interleaved f32 x,y`` coordinates in EPSG:32643 world metres, so the existing Canvas 2D loaders consume them unchanged; * ``data/interim/context/segment_ward_<vintage>.csv.gz`` -- segment_id -> ward join for the classified named street subset (centroid-within predicate); * ``data/interim/context/search_index.json`` -- ward + street lookup entries with lowercased keys. Nothing here synthesises geography. The KML sources are parsed as XML with the standard library because the installed fiona build carries no KML driver (audit-confirmed); every coordinate comes from the realised files and their sha256 hashes travel in the manifest and the meta payloads. VINTAGE ADJUDICATION (owner, binding). ``bbmp_wards_2022.kml`` is CANONICAL for locality labels: the operator product replays the September 2022 event, while BBMP's December-2022/2023 restructuring changed ward numbers and boundaries substantially (measured divergence below; ward NAMES are comparatively stable). Both layers are always built and reported; which layer a consumer resolves by default is keyed to the product's event window through ``configs/context.yaml`` (owner adjudication item 4 relocated the lookup from code constants into config; there is deliberately NO code fallback -- a missing or incomplete config fails the build at startup, rule 7), overridable with ``--ward-vintage``. No PIN codes are held by this module and none are sought (rule 3): the search index carries ward and street names only. Run standalone: ``python -m jaladhar.web.wards build [--ward-vintage 2022|2023]``"  # noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import typer

from jaladhar.provenance import write_json_atomic

if TYPE_CHECKING:
    import geopandas as gpd
    import pandas as pd

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    pass


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTEXT_DIR = REPO_ROOT / "data" / "interim" / "context"
KML_PATHS: dict[int, Path] = {
    2022: REPO_ROOT / "data" / "raw" / "boundary" / "bbmp_wards_2022.kml",
    2023: REPO_ROOT / "data" / "raw" / "boundary" / "bbmp_wards_2023_final.kml",
}
SEGMENT_LOOKUP_PATH = REPO_ROOT / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
ROADS_GPKG_PATH = REPO_ROOT / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"

CONTEXT_CONFIG_PATH = REPO_ROOT / "configs" / "context.yaml"

CONTEXT_CONFIG_REQUIRED_KEYS: tuple[str, ...] = (
    "ward_layer_rule",
    "ward_vintage_by_product_kind",
    "default_vintage",
)

_CONTEXT_CONFIG_CACHE: dict[str, Any] | None = None


def _resolve_context_config(path: Path) -> dict[str, Any]:

    import yaml

    problems: list[str] = []
    payload: Any = None
    parsed_ok = False
    if not path.is_file():
        problems.append(f"config file not found: {path}")
    else:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            parsed_ok = True
        except yaml.YAMLError as exc:
            problems.append(f"config file is not valid YAML: {path}: {exc}")
    if parsed_ok and payload is None:
        problems.append("config file is empty")
    if parsed_ok and not isinstance(payload, dict):
        problems.append(f"config must be a YAML mapping, got {type(payload).__name__}")

    resolved: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key in CONTEXT_CONFIG_REQUIRED_KEYS:
            if key not in payload or payload[key] is None:
                problems.append(f"missing required key: {key}")

        rule = payload.get("ward_layer_rule")
        if rule is not None:
            if not isinstance(rule, str) or not rule.strip():
                problems.append("ward_layer_rule must be a non-empty string")
            else:
                resolved["ward_layer_rule"] = rule

        mapping = payload.get("ward_vintage_by_product_kind")
        if mapping is not None:
            if not isinstance(mapping, dict) or not mapping:
                problems.append(
                    "ward_vintage_by_product_kind must be a non-empty mapping of "
                    "product kind -> vintage year"
                )
            else:
                by_kind: dict[str, int] = {}
                for kind, value in mapping.items():
                    try:
                        vintage = int(str(value))
                    except (TypeError, ValueError):
                        problems.append(
                            f"ward_vintage_by_product_kind[{kind!r}]: {value!r} is "
                            "not an integer year"
                        )
                        continue
                    if vintage not in KML_PATHS:
                        problems.append(
                            f"ward_vintage_by_product_kind[{kind!r}]: vintage "
                            f"{vintage} has no realised KML layer (known: {sorted(KML_PATHS)})"
                        )
                        continue
                    by_kind[str(kind)] = vintage
                if by_kind:
                    resolved["ward_vintage_by_product_kind"] = by_kind

        default_raw = payload.get("default_vintage")
        if default_raw is not None:
            try:
                default_vintage = int(str(default_raw))
            except (TypeError, ValueError):
                problems.append(f"default_vintage: {default_raw!r} is not an integer year")
            else:
                if default_vintage not in KML_PATHS:
                    problems.append(
                        f"default_vintage {default_vintage} has no realised KML layer "
                        f"(known: {sorted(KML_PATHS)})"
                    )
                else:
                    resolved["default_vintage"] = default_vintage
    elif parsed_ok or not path.is_file():
        for key in CONTEXT_CONFIG_REQUIRED_KEYS:
            problems.append(f"unresolved required key: {key}")

    if problems:
        raise WardContextConfigError(
            f"configs/context.yaml failed startup resolution ({len(problems)} problem(s)); "
            "fix the file at configs/context.yaml -- there is no code fallback:\n  - "
            + "\n  - ".join(problems)
        )
    return resolved


def context_config() -> dict[str, Any]:

    global _CONTEXT_CONFIG_CACHE
    if _CONTEXT_CONFIG_CACHE is None:
        _CONTEXT_CONFIG_CACHE = _resolve_context_config(CONTEXT_CONFIG_PATH)
    return _CONTEXT_CONFIG_CACHE


def reset_context_config_cache() -> None:

    global _CONTEXT_CONFIG_CACHE
    _CONTEXT_CONFIG_CACHE = None


WARD_VINTAGE_DECISION_REASON: str = (
    "Owner adjudication (binding), WF-6 U0f, condensed for the manifest: the operator "
    "product renders the September 2022 event, so locality labels resolve to the 2022 "
    "BBMP layer -- the 2023 restructuring changed ward numbers and boundaries "
    "substantially (243 wards -> 225; median boundary-area delta ~13%) while ward NAMES "
    "remained comparatively stable. Vintage matching requires labelling each product "
    "with the boundary vintage of its own event window."
)

CLASSIFIED_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
    }
)

KML_NS = "{http://www.opengis.net/kml/2.2}"
TARGET_CRS = "EPSG:32643"

_NAME_NORMALISE_RE = re.compile(r"\s+")
_TRAILING_WARD_RE = re.compile(r"\s+ward$")


class WardContextError(RuntimeError):
    pass


class WardContextConfigError(WardContextError):
    pass


def sha256_file(path: Path) -> str:

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_coordinates(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError as exc:
                raise WardContextError(f"KML coordinate is not numeric: {token!r}") from exc
    return points


def _parse_kml_placemarks(path: Path) -> list[dict[str, Any]]:

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise WardContextError(f"KML source is not well-formed XML: {path}: {exc}") from exc
    placemarks: list[dict[str, Any]] = []
    for element in root.iter(f"{KML_NS}Placemark"):
        fields = {
            simple.attrib["name"]: (simple.text or "").strip()
            for simple in element.iter(f"{KML_NS}SimpleData")
        }
        outer_rings: list[list[tuple[float, float]]] = []
        inner_rings: list[list[tuple[float, float]]] = []
        for polygon in element.iter(f"{KML_NS}Polygon"):
            for tag, sink in (
                ("outerBoundaryIs", outer_rings),
                ("innerBoundaryIs", inner_rings),
            ):
                for boundary in polygon.iter(f"{KML_NS}{tag}"):
                    coords = boundary.find(f"{KML_NS}LinearRing/{KML_NS}coordinates")
                    if coords is None or not coords.text:
                        continue
                    ring = _parse_coordinates(coords.text)
                    if len(ring) >= 4:
                        sink.append(ring)
        placemarks.append({"fields": fields, "outer": outer_rings, "inner": inner_rings})
    if not placemarks:
        raise WardContextError(f"KML source contains no Placemark elements: {path}")
    return placemarks


def _ward_fields(vintage: int, fields: dict[str, str]) -> tuple[int, str]:
    if vintage == 2022:
        raw_no, name = fields.get("WardNo"), fields.get("WardName")
    else:
        raw_no, name = fields.get("id"), fields.get("name_en")
    if not raw_no or not name:
        raise WardContextError(f"ward placemark lacks number/name fields: {fields!r}")
    return int(raw_no), name.strip()


def load_wards(vintage: int) -> gpd.GeoDataFrame:

    import geopandas as gpd
    import shapely
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    if vintage not in KML_PATHS:
        raise WardContextError(f"unknown ward vintage {vintage!r}; known: {sorted(KML_PATHS)}")
    path = KML_PATHS[vintage]
    if not path.is_file():
        raise WardContextError(f"realised ward KML is missing: {path}")

    records: list[dict[str, Any]] = []
    for placemark in _parse_kml_placemarks(path):
        ward_no, name = _ward_fields(vintage, placemark["fields"])
        parts: list[Polygon] = []
        used_inner: set[int] = set()
        for outer in placemark["outer"]:
            outer_polygon = Polygon(outer)
            holes = []
            for index, inner in enumerate(placemark["inner"]):
                if index not in used_inner and outer_polygon.contains(Point(inner[0])):
                    holes.append(inner)
                    used_inner.add(index)
            parts.append(Polygon(outer, holes=holes))
        if not parts:
            raise WardContextError(f"ward {ward_no} ({name}) carries no outer ring")
        geometry = unary_union(parts) if len(parts) > 1 else parts[0]
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
            if geometry.is_empty:
                raise WardContextError(f"ward {ward_no} ({name}) produced an empty geometry")
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                polygonal = [
                    part
                    for part in getattr(geometry, "geoms", [geometry])
                    if part.geom_type in {"Polygon", "MultiPolygon"}
                ]
                if not polygonal:
                    raise WardContextError(
                        f"ward {ward_no} ({name}) produced no polygonal geometry after repair"
                    )
                geometry = unary_union(polygonal)
        records.append({"ward_no": ward_no, "name": name, "geometry": geometry})

    numbers = [record["ward_no"] for record in records]
    duplicates = {number for number in numbers if numbers.count(number) > 1}
    if duplicates:
        raise WardContextError(f"duplicate ward numbers in {path.name}: {sorted(duplicates)}")

    wards = gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(TARGET_CRS)
    total_area_km2 = float(wards.area.sum()) / 1e6
    if not 650.0 <= total_area_km2 <= 750.0:
        raise WardContextError(
            f"{path.name}: reprojected total area {total_area_km2:.1f} km^2 is outside "
            "the plausible BBMP band 650-750 km^2"
        )
    return wards


def normalise_key(name: str) -> str:

    return _NAME_NORMALISE_RE.sub(" ", name.strip().lower())


def short_key(name: str) -> str:

    return _TRAILING_WARD_RE.sub("", normalise_key(name))


def _pack_ring_blob(rings: list[np.ndarray]) -> bytes:

    offsets = np.zeros(len(rings) + 1, dtype="<u4")
    np.cumsum([len(ring) // 2 for ring in rings], dtype="<i8", out=offsets[1:])
    header = np.asarray([len(rings)], dtype="<u4").tobytes()
    coords = np.concatenate(rings) if rings else np.zeros(0, dtype="<f4")
    return header + offsets.tobytes() + coords.astype("<f4", copy=False).tobytes()


def ward_rings(
    wards: gpd.GeoDataFrame,
) -> tuple[list[np.ndarray], list[list[tuple[int, int]]], list[int]]:

    rings: list[np.ndarray] = []
    ranges_per_ward: list[list[tuple[int, int]]] = []
    parts_per_ward: list[int] = []
    vertex_cursor = 0
    for geometry in wards.geometry:  # type: ignore[attr-defined]
        if geometry.geom_type == "Polygon":
            polygons = [geometry]
        elif geometry.geom_type == "MultiPolygon":
            polygons = list(geometry.geoms)
        else:
            raise WardContextError(f"unexpected ward geometry type {geometry.geom_type}")
        ranges: list[tuple[int, int]] = []
        for polygon in polygons:
            for ring in [polygon.exterior, *polygon.interiors]:
                xy = np.asarray(ring.coords, dtype="<f4").ravel()
                if len(xy) < 6:
                    continue
                start = vertex_cursor
                vertex_cursor += len(xy) // 2
                rings.append(xy)
                ranges.append((start, vertex_cursor))
        ranges_per_ward.append(ranges)
        parts_per_ward.append(len(polygons))
    return rings, ranges_per_ward, parts_per_ward


def validate_bundle(blob: bytes, meta: dict[str, Any]) -> None:
    "Realized-state self-check binding the emitted bytes to their metadata. Used by the build (after re-reading from disk) and by the tests, so a corrupted bundle reddens the same code path that green-lights a clean one."  # noqa: E501

    n_rings = int(np.frombuffer(blob[:4], dtype="<u4")[0])
    offsets = np.frombuffer(blob[4 : 4 + 4 * (n_rings + 1)], dtype="<u4")
    coord_floats = len(blob) - 4 - 4 * (n_rings + 1)
    if coord_floats < 0 or coord_floats % 8 != 0:
        raise WardContextError(f"bundle byte length {len(blob)} inconsistent with {n_rings} rings")
    coords = np.frombuffer(blob[4 + 4 * (n_rings + 1) :], dtype="<f4")
    declared_vertices = int(meta["n_vertices"])
    if len(coords) // 2 != declared_vertices:
        raise WardContextError(
            f"bundle realises {len(coords) // 2} vertices, metadata declares {declared_vertices}"
        )
    if len(offsets) != n_rings + 1 or offsets[0] != 0 or offsets[-1] != declared_vertices:
        raise WardContextError("bundle ring offsets do not span the declared vertex count")
    if not bool(np.all(np.diff(offsets) > 0)):
        raise WardContextError("bundle contains a collapsed (empty) ring")
    if not bool(np.isfinite(coords).all()):
        raise WardContextError("bundle contains non-finite coordinates")
    bounds = meta["bbox"]
    xs, ys = coords[0::2], coords[1::2]
    pad = 1.0
    if (
        xs.min() < bounds[0] - pad
        or ys.min() < bounds[1] - pad
        or xs.max() > bounds[2] + pad
        or ys.max() > bounds[3] + pad
    ):
        raise WardContextError("bundle coordinates fall outside the declared bbox")

    cursor = 0
    for entry in meta["wards"]:
        vertex_count = 0
        for start, end in entry["ring_ranges"]:
            if start != cursor or end <= start:
                raise WardContextError(
                    f"ward {entry['ward_no']} ring ranges do not partition the vertex space "
                    f"(expected run to start at {cursor}, got [{start}, {end}))"
                )
            cursor = end
            vertex_count += end - start
        if vertex_count != entry["vertex_count"]:
            raise WardContextError(
                f"ward {entry['ward_no']} declares {entry['vertex_count']} vertices, "
                f"its ranges hold {vertex_count}"
            )
    if cursor != declared_vertices:
        raise WardContextError(
            f"ward ring ranges cover {cursor} vertices, bundle realises {declared_vertices}"
        )


def emit_vintage(vintage: int, *, started_iso: str) -> dict[str, Any]:

    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    wards = load_wards(vintage)
    rings, ranges_per_ward, parts_per_ward = ward_rings(wards)

    blob = _pack_ring_blob(rings)
    bounds = wards.total_bounds
    entries = []
    for row, ranges, n_parts in zip(
        wards.itertuples(index=False), ranges_per_ward, parts_per_ward, strict=True
    ):
        centroid = row.geometry.centroid
        vertex_count = sum(end - start for start, end in ranges)
        entries.append(
            {
                "ward_no": int(row.ward_no),
                "name": str(row.name),
                "centroid_m": [round(float(centroid.x), 2), round(float(centroid.y), 2)],
                "area_km2": round(float(row.geometry.area) / 1e6, 4),
                "n_parts": n_parts,
                "vertex_count": vertex_count,
                "ring_ranges": [[int(start), int(end)] for start, end in ranges],
            }
        )

    meta: dict[str, Any] = {
        "crs": TARGET_CRS,
        "bbox": [float(v) for v in bounds],
        "n_rings": len(rings),
        "n_vertices": int(sum(len(ring) // 2 for ring in rings)),
        "bytes": len(blob),
        "vintage": vintage,
        "source_kml": {
            "path": _repo_relative(KML_PATHS[vintage]),
            "sha256": sha256_file(KML_PATHS[vintage]),
        },
        "layout": "n_rings:u32 | ring_offsets:u32[n+1] | interleaved f32 x,y (EPSG:32643 metres)",
        "wards": entries,
        "generated_utc": started_iso,
    }

    bin_path = CONTEXT_DIR / f"wards_{vintage}.bin"
    meta_path = CONTEXT_DIR / f"wards_{vintage}.meta.json"
    bin_path.write_bytes(blob)
    write_json_atomic(meta_path, meta)

    realized = bin_path.read_bytes()
    validate_bundle(realized, json.loads(meta_path.read_text(encoding="utf-8")))
    if len(realized) != len(blob):
        raise WardContextError(f"emitted {bin_path.name} differs from validated bytes")
    return meta


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def measure_divergence() -> dict[str, Any]:

    import numpy as np

    wards_by_year = {year: load_wards(year) for year in sorted(KML_PATHS)}
    names_by_year = {
        year: {short_key(str(name)): str(name) for name in frame["name"]}
        for year, frame in wards_by_year.items()
    }
    only_2022 = sorted(set(names_by_year[2022]) - set(names_by_year[2023]))
    only_2023 = sorted(set(names_by_year[2023]) - set(names_by_year[2022]))
    common = sorted(set(names_by_year[2022]) & set(names_by_year[2023]))

    area_by_key: dict[int, dict[str, list[float]]] = {year: {} for year in wards_by_year}
    plain_area_by_key: dict[int, dict[str, list[float]]] = {year: {} for year in wards_by_year}
    for year, frame in wards_by_year.items():
        for name, geometry in zip(frame["name"], frame.geometry, strict=True):
            area_by_key[year].setdefault(short_key(str(name)), []).append(float(geometry.area))
            plain_area_by_key[year].setdefault(normalise_key(str(name)), []).append(
                float(geometry.area)
            )

    def _delta_stats(area_lookup: dict[int, dict[str, list[float]]], keys: list[str]) -> tuple:
        deltas_max: list[float] = []
        deltas_min: list[float] = []
        worst_min = ("", 0.0)
        for key in keys:
            area_22 = float(np.mean(area_lookup[2022][key]))
            area_23 = float(np.mean(area_lookup[2023][key]))
            dmax = abs(area_22 - area_23) / max(area_22, area_23)
            dmin = abs(area_22 - area_23) / min(area_22, area_23)
            deltas_max.append(dmax)
            deltas_min.append(dmin)
            if dmin > worst_min[1]:
                worst_min = (key, dmin)
        return (
            round(float(np.median(deltas_max)) * 100, 2),
            round(worst_min[1] * 100, 1),
            worst_min[0],
        )

    # Both name-pairing rules are reported (V10: derivation beside the number).
    stripped_median, stripped_worst_pct, stripped_worst = _delta_stats(area_by_key, common)
    plain_common = sorted(set(plain_area_by_key[2022]) & set(plain_area_by_key[2023]))
    plain_median, plain_worst_pct, _ = _delta_stats(plain_area_by_key, plain_common)

    return {
        "ward_counts": {str(year): int(len(frame)) for year, frame in wards_by_year.items()},
        "total_area_km2": {
            str(year): round(float(frame.area.sum()) / 1e6, 2)
            for year, frame in wards_by_year.items()
        },
        "names": {
            "match_rule": "whitespace-collapsed lowercase with trailing ' ward' token stripped",
            "only_2022": len(only_2022),
            "only_2023": len(only_2023),
            "common": len(common),
        },
        "boundary_area_delta": {
            "formula_median": "|A22-A23| / max(A22,A23)",
            "median_pct_pairs_plain_lower": plain_median,
            "median_pct_pairs_ward_stripped": stripped_median,
            "audit_quoted_median_pct": 13.18,
            "reconciliation_note": (
                "Audit's 13.18% median reconciles under the plain-lowercase pairing; "
                "the ward-stripped pairing pairs two additional wards and gives "
                f"{stripped_median}%. Both are recorded; neither is hidden."
            ),
            "formula_worst": "|A22-A23| / min(A22,A23)",
            "worst_name": stripped_worst,
            "worst_pct_min_denominator": stripped_worst_pct,
        },
    }


def _safe_points_frame(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:

    import geopandas as gpd

    centroids = segments.geometry.centroid
    frame = gpd.GeoDataFrame(
        {
            "segment_id": segments["segment_id"].to_numpy(),
            "street_name": segments["name"].astype(str).to_numpy(),
            "geometry": centroids.to_numpy(),
        },
        crs=segments.crs,
    )
    if len(frame) != len(segments):
        raise WardContextError(
            f"point frame construction lost rows: {len(frame)} of {len(segments)}"
        )
    return frame


def join_segments_to_wards(vintage: int) -> dict[str, Any]:
    "Classified named segments -> ward via centroid-within (audit predicate). Also measures the intersects predicate for the manifest. Returns the assignment table plus both predicates' realised counts."  # noqa: E501

    import geopandas as gpd

    if not ROADS_GPKG_PATH.is_file():
        raise WardContextError(f"realised road centreline file is missing: {ROADS_GPKG_PATH}")
    roads = gpd.read_file(ROADS_GPKG_PATH)
    if roads.crs is None or "32643" not in str(roads.crs.to_epsg()):
        raise WardContextError(f"road CRS {roads.crs} does not match frozen EPSG:32643")

    names = roads["name"].fillna("")
    mask = names.str.strip().ne("") & roads["highway"].isin(CLASSIFIED_HIGHWAYS)
    classified_named = roads.loc[mask]
    if classified_named.empty:
        raise WardContextError("no classified named segments found in the road centreline file")

    wards = load_wards(vintage)[["ward_no", "name", "geometry"]]
    points = _safe_points_frame(classified_named)
    lines_frame = gpd.GeoDataFrame(
        {
            "segment_id": classified_named["segment_id"].to_numpy(),
            "geometry": classified_named.geometry.to_numpy(),
        },
        crs=roads.crs,
    )

    within = gpd.sjoin(points, wards, how="inner", predicate="within")
    covered_by = gpd.sjoin(points, wards, how="inner", predicate="covered_by")
    intersects = gpd.sjoin(lines_frame, wards, how="inner", predicate="intersects")

    assignments = (
        within[["segment_id", "street_name", "ward_no", "name"]]
        .rename(columns={"name": "ward_name"})
        .sort_values("segment_id")
        .reset_index(drop=True)
    )
    matched_ids = set(within["segment_id"].tolist())
    return {
        "assignments": assignments,
        "n_classified_named": int(len(classified_named)),
        "n_centroid_within_rows": int(len(within)),
        "n_covered_by_rows": int(len(covered_by)),
        "n_intersects_segments": int(intersects["segment_id"].nunique()),
        "n_intersects_raw_pairs": int(len(intersects)),
        "n_unmatched_segments": int(len(classified_named) - len(matched_ids)),
        "n_multi_ward_centroids": int(within["segment_id"].duplicated().sum()),
        "matched_wards": int(within["ward_no"].nunique()),
        "pairs_exact_case": int(
            len(set(zip(within["street_name"], within["ward_no"], strict=True)))
        ),
    }


def write_join_table(assignments: pd.DataFrame, vintage: int) -> dict[str, Any]:

    path = CONTEXT_DIR / f"segment_ward_{vintage}.csv.gz"
    assignments.to_csv(path, index=False, compression="gzip")

    import pandas as pd

    realized = pd.read_csv(path)
    if len(realized) != len(assignments):
        raise WardContextError(
            f"emitted join table realises {len(realized)} rows, built {len(assignments)}"
        )
    return {"path": _repo_relative(path), "rows": int(len(realized))}


def build_search_index(
    join_result: dict[str, Any], vintage: int, *, started_iso: str
) -> dict[str, Any]:

    wards = load_wards(vintage)
    entries: list[dict[str, Any]] = []
    ward_names: dict[int, str] = {}
    for row in wards.itertuples(index=False):
        display = str(row.name)
        key = normalise_key(display)
        alt = short_key(display)
        ward_names[int(row.ward_no)] = display
        entries.append(
            {
                "kind": "ward",
                "name": display,
                "ward_no": int(row.ward_no),
                "key": key,
                **({"alt_keys": [alt]} if alt != key else {}),
            }
        )
    n_ward_entries = len(entries)

    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in join_result["assignments"].itertuples(index=False):
        pair_key = (str(row.street_name), int(row.ward_no))
        entry = grouped.setdefault(
            pair_key,
            {
                "kind": "street",
                "name": str(row.street_name),
                "ward_no": int(row.ward_no),
                "ward_name": ward_names[int(row.ward_no)],
                "segment_ids": [],
                "key": normalise_key(str(row.street_name)),
            },
        )
        entry["segment_ids"].append(int(row.segment_id))

    street_entries = sorted(grouped.values(), key=lambda e: (e["key"], e["ward_no"]))
    for entry in street_entries:
        entry["segment_ids"] = sorted(entry["segment_ids"])
    entries.extend(street_entries)
    entries.sort(key=lambda e: (e["kind"], e["key"], e.get("ward_no", -1)))

    index = {
        "vintage": vintage,
        "generated_utc": started_iso,
        "pin_codes_held": False,
        "note": "No PIN codes are held by this project; none are sought (AGENTS.md rule 3).",
        "kind_counts": {"ward": n_ward_entries, "street": len(street_entries)},
        "entries": entries,
    }
    path = CONTEXT_DIR / "search_index.json"
    write_json_atomic(path, index)

    realized = json.loads(path.read_text(encoding="utf-8"))
    if len(realized["entries"]) != len(entries):
        raise WardContextError("emitted search index disagrees with the built entry list")
    if realized["kind_counts"]["street"] != join_result["pairs_exact_case"]:
        raise WardContextError(
            f"search index realises {realized['kind_counts']['street']} street entries, "
            f"join produced {join_result['pairs_exact_case']} distinct (name, ward) pairs"
        )
    return {"path": _repo_relative(path), "kind_counts": index["kind_counts"]}


def _git_info() -> dict[str, Any]:
    def _git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE
            ).rstrip("\r\n")
        except (OSError, subprocess.CalledProcessError) as exc:
            raise WardContextError(f"cannot establish Git provenance: {exc}") from exc

    sha = _git("rev-parse", "--verify", "HEAD^{commit}")
    dirty_lines = _git("status", "--porcelain", "--untracked-files=all")
    dirty = [line[3:] for line in dirty_lines.splitlines() if line.strip()]
    return {"git_sha": sha, "git_dirty_paths": dirty}


@app.command()
def build(
    ward_vintage: int = typer.Option(
        None,
        "--ward-vintage",
        min=2022,
        max=2023,
        help="Override the event-window vintage used for the join/index layers.",
    ),
) -> None:

    config = context_config()
    selected_vintage = ward_vintage if ward_vintage is not None else int(config["default_vintage"])
    started_at = datetime.now(UTC)
    started_iso = started_at.isoformat().replace("+00:00", "Z")

    typer.echo(
        "compute_budget: CPU-only context build (XML parse + vector joins), "
        "expected wall clock ~1-3 min on the configured host; no GPU, no timesteps."
    )

    inputs = {
        "kml_2022": {
            "path": _repo_relative(KML_PATHS[2022]),
            "sha256": sha256_file(KML_PATHS[2022]),
        },
        "kml_2023": {
            "path": _repo_relative(KML_PATHS[2023]),
            "sha256": sha256_file(KML_PATHS[2023]),
        },
        "roads_segment_lookup": {
            "path": _repo_relative(SEGMENT_LOOKUP_PATH),
            "sha256": sha256_file(SEGMENT_LOOKUP_PATH),
        },
        "roads_centrelines": {
            "path": _repo_relative(ROADS_GPKG_PATH),
            "sha256": sha256_file(ROADS_GPKG_PATH),
        },
    }

    manifest: dict[str, Any] = {
        "stage": "web_context_build",
        "status": "running",
        "started_time_iso": started_iso,
        **_git_info(),
        "git_dirty_note": (
            "require_clean_git is not applied: co-existing units own unrelated dirty "
            "paths; the full list is recorded instead."
        ),
        "inputs": inputs,
        "selected_join_vintage": selected_vintage,
        "vintage_decision": {
            "canonical_layer_for_locality_labels": "data/raw/boundary/bbmp_wards_2022.kml",
            "default_vintage": int(config["default_vintage"]),
            "config_source": _repo_relative(CONTEXT_CONFIG_PATH),
            "ward_layer_rule": config["ward_layer_rule"],
            "reason": WARD_VINTAGE_DECISION_REASON,
            "product_kind_mapping": dict(config["ward_vintage_by_product_kind"]),
            "cli_override_applied": ward_vintage is not None,
        },
        "planned_outputs": [
            _repo_relative(CONTEXT_DIR / "wards_2022.bin"),
            _repo_relative(CONTEXT_DIR / "wards_2022.meta.json"),
            _repo_relative(CONTEXT_DIR / "wards_2023.bin"),
            _repo_relative(CONTEXT_DIR / "wards_2023.meta.json"),
            _repo_relative(CONTEXT_DIR / f"segment_ward_{selected_vintage}.csv.gz"),
            _repo_relative(CONTEXT_DIR / "search_index.json"),
        ],
        "predicates": {
            "assignment": "line centroid within ward polygon (EPSG:32643)",
            "secondary": "covered_by (boundary ties) and line intersects, counted not assigned",
        },
    }
    manifest_path = CONTEXT_DIR / "context_build_manifest.json"
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest_path, manifest)

    try:
        divergence = measure_divergence()
        manifest["divergence_measured"] = divergence
        write_json_atomic(manifest_path, manifest)
        typer.echo(
            f"divergence 2022-vs-2023: wards {divergence['ward_counts']}, "
            f"areas km2 {divergence['total_area_km2']}, names "
            f"{divergence['names']}, delta {divergence['boundary_area_delta']}"
        )

        outputs: dict[str, Any] = {}
        for vintage in sorted(KML_PATHS):
            meta = emit_vintage(vintage, started_iso=started_iso)
            outputs[f"wards_{vintage}"] = {
                "path": _repo_relative(CONTEXT_DIR / f"wards_{vintage}.bin"),
                "meta_path": _repo_relative(CONTEXT_DIR / f"wards_{vintage}.meta.json"),
                "bytes": meta["bytes"],
                "n_rings": meta["n_rings"],
                "n_vertices": meta["n_vertices"],
                "sha256": sha256_file(CONTEXT_DIR / f"wards_{vintage}.bin"),
            }
            typer.echo(
                f"wards_{vintage}: {len(meta['wards'])} wards, {meta['n_rings']} rings, "
                f"{meta['n_vertices']} vertices, {meta['bytes']} bytes"
            )

        join_result = join_segments_to_wards(selected_vintage)
        table_info = write_join_table(join_result["assignments"], selected_vintage)
        outputs["segment_ward"] = {
            **table_info,
            "sha256": sha256_file(CONTEXT_DIR / f"segment_ward_{selected_vintage}.csv.gz"),
        }
        rate = join_result["n_centroid_within_rows"] / join_result["n_classified_named"]
        typer.echo(
            f"join[{selected_vintage}] centroid-within: {join_result['n_centroid_within_rows']} "
            f"of {join_result['n_classified_named']} classified named segments "
            f"({rate:.2%}); covered-by {join_result['n_covered_by_rows']}; intersects "
            f"{join_result['n_intersects_segments']} segments / "
            f"{join_result['n_intersects_raw_pairs']} raw pairs; unmatched "
            f"{join_result['n_unmatched_segments']}; distinct (name, ward) pairs "
            f"{join_result['pairs_exact_case']}"
        )

        index_info = build_search_index(join_result, selected_vintage, started_iso=started_iso)
        outputs["search_index"] = {
            **index_info,
            "sha256": sha256_file(CONTEXT_DIR / "search_index.json"),
        }

        manifest.update(
            {
                "status": "completed",
                "completed_time_iso": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "wall_clock_sec": round((datetime.now(UTC) - started_at).total_seconds(), 3),
                "outputs": outputs,
                "join_realized": {
                    key: value for key, value in join_result.items() if key != "assignments"
                },
                "search_index_realized": index_info,
            }
        )
        write_json_atomic(manifest_path, manifest)
        typer.echo("WARD_CONTEXT_BUILD: PASS")
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "failed_time_iso": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        write_json_atomic(manifest_path, manifest)
        typer.echo(f"WARD_CONTEXT_BUILD: FAIL: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
