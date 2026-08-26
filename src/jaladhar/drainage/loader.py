"""WF-1 drain-graph loader: robust BBMP KML ingestion -> EPSG:32643 reach table.

First pipeline stage. Parses primary/secondary drain KMLs into Reach records
(imported from jaladhar.drainage.stitch - single source of truth), transforming
WGS84 lon,lat -> EPSG:32643 via pyproj (always_xy). reach_id is assigned
deterministically ascending per class after sorting placemarks by OBJECTID when
present, else document order; the sort basis is recorded in diagnostics.

ROBUST RECOVERY LADDER per file: (1) fiona/GDAL KML driver; (2) ogr2ogr
subprocess if available; (3) stdlib raw-XML parse (namespace-stripped; handles
<coordinates> and gx:coord tuples; malformed placemarks are SKIPPED AND COUNTED,
never fatal - partial recovery of valid features is required behavior).

Tertiary KML is REPORT-ONLY (exclusion ruling stands): parsed through the same
ladder for census/junction-candidate diagnostics, NEVER emitted as reaches.

Diagnostics are REPORTED-NOT-ASSERTED except one hard gate: the overall P+S
invalid-geometry fraction exceeding cfg diagnostics.max_invalid_fraction raises
LoaderError BEFORE anything downstream can start.

Rule 6 manifest written at run start (status running) and updated in place.
Rule 7 config resolution aggregates ALL problems into ONE ValueError.
CPU-only; no torch/CUDA import anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import typer
import yaml
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import is_valid_reason
from shapely.geometry import LineString

from jaladhar.drainage.stitch import Reach

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

SOURCE_CRS = "EPSG:4326"
SNAP_TOLERANCES_M = (0.5, 2.0, 10.0)
DEFAULT_MAX_INVALID_FRACTION = 0.01
_LENGTH_M_FIELDS = ("SHAPE_Leng", "Shape_Leng", "Shape.STLength()")


class LoaderError(RuntimeError):
    """A loader gate or ingestion failure."""


# ---------------------------------------------------------------------------
# rule-7 config resolution + binding
# ---------------------------------------------------------------------------

_REQUIRED_KEYS: list[str] = [
    "crs",
    "grid.width",
    "grid.height",
    "grid.transform",
    "inputs.kml_primary",
    "inputs.kml_secondary",
    "inputs.kml_tertiary_report_only",
    "inputs.elevation_surface",
    "inputs.elevation_sha256",
    "diagnostics.max_invalid_fraction",
    "outputs.run_dir",
]

_BOUND_CFG: dict | None = None


def bind_config(cfg: dict) -> None:
    """Bind a resolved drainage config for subsequent load_reaches() calls.

    load_reaches keeps its contracted positional interface but needs the gate
    threshold and elevation-surface identity from config; the CLI binds the
    resolve_config() result here. Unbound calls fall back to resolving
    REPO/configs/drainage.yaml."""
    global _BOUND_CFG
    _BOUND_CFG = cfg


def _active_cfg() -> dict:
    if _BOUND_CFG is not None:
        return _BOUND_CFG
    return resolve_config(str(REPO / "configs" / "drainage.yaml"))


def _repo(p: Any) -> Path:
    path = Path(str(p))
    return path if path.is_absolute() else REPO / path


def resolve_config(config_path: str) -> dict:
    """Rule-7 pre-flight: touch every key this module will ever need and raise ONE
    aggregated ValueError listing ALL missing/invalid keys, not just the first."""
    problems: list[str] = []
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

    crs = get("crs")
    if crs is not None and not (isinstance(crs, str) and re.fullmatch(r"EPSG:\d+", crs)):
        problems.append(f"drainage.crs must match EPSG:<int>, got {crs!r}")

    def want_int(key: str, minimum: int = 1) -> None:
        v = get(key)
        if v is None:
            return
        if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
            problems.append(f"drainage.{key} must be int >= {minimum}, got {v!r}")

    want_int("grid.width")
    want_int("grid.height")

    t = get("grid.transform")
    if t is not None and (
        not isinstance(t, list)
        or len(t) != 6
        or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in t
        )
    ):
        problems.append(f"drainage.grid.transform must be a list of 6 finite numbers, got {t!r}")

    frac = get("diagnostics.max_invalid_fraction")
    if frac is not None and (
        isinstance(frac, bool) or not isinstance(frac, (int, float)) or not 0 <= frac < 1
    ):
        problems.append(
            f"drainage.diagnostics.max_invalid_fraction must be number in [0,1), got {frac!r}"
        )

    sha = get("inputs.elevation_sha256")
    if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
        problems.append("drainage.inputs.elevation_sha256 must be 64 lowercase hex chars")

    run_dir = get("outputs.run_dir")
    if run_dir is not None and (not isinstance(run_dir, str) or not run_dir.strip()):
        problems.append(f"drainage.outputs.run_dir must be a non-empty string, got {run_dir!r}")

    for key in (
        "inputs.kml_primary",
        "inputs.kml_secondary",
        "inputs.kml_tertiary_report_only",
        "inputs.elevation_surface",
    ):
        v = get(key)
        if v is not None and not _repo(v).exists():
            problems.append(f"drainage.{key} file does not exist: {v}")

    if problems:
        raise ValueError(
            f"config resolution failed for {config_path} with {len(problems)} problem(s):\n"
            + "\n".join(f"- {p}" for p in problems)
        )
    d["_resolved_at"] = _utc_now()
    d["_config_path"] = str(config_path)
    return d


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(obj: Any) -> Any:
    """Recursively convert numpy scalars so json.dumps never chokes on diagnostics."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return v if math.isfinite(v) else str(v)
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    return obj


class _UnionFind:
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


# ---------------------------------------------------------------------------
# placemark model + recovery ladder
# ---------------------------------------------------------------------------


@dataclass
class _Place:
    """One KML placemark as extracted by any rung of the recovery ladder."""

    oid: int | None = None  # OBJECTID if present and parsable
    length_attr: float | None = None  # KML 'Length' attribute exactly as recorded
    shape_leng: float | None = None  # first of SHAPE_Leng / Shape_Leng / Shape.STLength()
    parts: list[list[tuple[float, float]]] = field(default_factory=list)  # lon/lat lines
    nonline_geometry: bool = False  # Point/Polygon/other non-line geometry present
    malformed: bool = False  # unparsable/truncated -> skipped at parse time


class _BadCoords(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _lonlat_pairs(text: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for tok in text.split():
        bits = tok.split(",")
        try:
            lon, lat = float(bits[0]), float(bits[1])
        except (IndexError, ValueError) as e:
            raise _BadCoords(f"token {tok!r}") from e
        pts.append((lon, lat))
    return pts


def _gx_pairs(text: str) -> list[tuple[float, float]]:
    vals: list[float] = []
    for tok in text.split():
        try:
            vals.append(float(tok))
        except ValueError as e:
            raise _BadCoords(f"token {tok!r}") from e
    if len(vals) % 3 != 0:
        raise _BadCoords(f"gx:coord triplets incomplete ({len(vals)} scalars)")
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 3)]


def _placemarks_rawxml(path: Path) -> list[_Place]:
    root = ET.parse(str(path)).getroot()
    out: list[_Place] = []
    for pm in (el for el in root.iter() if _local(el.tag) == "Placemark"):
        place = _Place()
        for el in pm.iter():
            t = _local(el.tag)
            if t == "SimpleData":
                name = el.get("name")
                txt = (el.text or "").strip()
                if name == "OBJECTID":
                    try:
                        place.oid = int(float(txt))
                    except ValueError:
                        pass
                elif name == "Length":
                    try:
                        place.length_attr = float(txt)
                    except ValueError:
                        pass
                elif name in _LENGTH_M_FIELDS and place.shape_leng is None:
                    try:
                        place.shape_leng = float(txt)
                    except ValueError:
                        pass
            elif t in ("Point", "Polygon", "GroundOverlay"):
                place.nonline_geometry = True
            elif t == "LineString":
                coords = [c for c in el if _local(c.tag) == "coordinates"]
                gx = [c for c in el if _local(c.tag) == "coord"]
                part: list[tuple[float, float]] = []
                ok = True
                try:
                    if coords:
                        part = _lonlat_pairs(coords[0].text or "")
                    elif gx:
                        part = _gx_pairs(" ".join((c.text or "") for c in gx))
                    else:
                        ok = False
                except _BadCoords:
                    ok = False
                if ok and len(part) >= 2:
                    place.parts.append(part)
                else:
                    place.malformed = True
        if not place.parts and not place.nonline_geometry:
            place.malformed = True  # broken LineString element or no usable geometry at all
        out.append(place)
    return out


def _place_from_geojson(geom: dict | None, props: dict) -> _Place:
    place = _Place()
    for key, val in props.items():
        if val is None:
            continue
        if key == "OBJECTID":
            try:
                place.oid = int(float(val))
            except (TypeError, ValueError):
                pass
        elif key == "Length":
            try:
                place.length_attr = float(val)
            except (TypeError, ValueError):
                pass
        elif key in _LENGTH_M_FIELDS and place.shape_leng is None:
            try:
                place.shape_leng = float(val)
            except (TypeError, ValueError):
                pass
    gtype = (geom or {}).get("type")
    gcoords = (geom or {}).get("coordinates")
    if gtype == "LineString":
        part = _geojson_part(gcoords)
        if part is None:
            place.malformed = True
        else:
            place.parts.append(part)
    elif gtype == "MultiLineString":
        for sub in gcoords or []:
            part = _geojson_part(sub)
            if part is None:
                place.malformed = True
            else:
                place.parts.append(part)
    else:
        place.nonline_geometry = True
        if gcoords is None:
            place.malformed = True
    if not place.parts and not place.nonline_geometry:
        place.malformed = True
    return place


def _geojson_part(coords: Any) -> list[tuple[float, float]] | None:
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    out: list[tuple[float, float]] = []
    for c in coords:
        try:
            out.append((float(c[0]), float(c[1])))
        except (IndexError, TypeError, ValueError):
            return None
    return out


def _placemarks_fiona(path: Path) -> list[_Place] | None:
    """Rung 1. Returns None when this rung is unavailable or dies wholesale."""
    try:
        import fiona
    except ImportError:
        return None
    try:
        src = fiona.open(str(path))
    except Exception:
        return None
    places: list[_Place] = []
    try:
        for feat in src:
            places.append(_place_from_geojson(feat.get("geometry"), dict(feat.get("properties"))))
    except Exception:
        if not places:
            src.close()
            return None
    src.close()
    return places


def _placemarks_ogr2ogr(path: Path) -> list[_Place] | None:
    """Rung 2. Returns None when ogr2ogr is absent or the conversion fails."""
    exe = shutil.which("ogr2ogr")
    if exe is None:
        return None
    fd, tmp_name = tempfile.mkstemp(suffix=".geojson")
    Path(tmp_name).unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [exe, "-f", "GeoJSON", tmp_name, str(path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(Path(tmp_name).read_text())
    except Exception:
        return None
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return [
        _place_from_geojson(feat.get("geometry"), feat.get("properties") or {})
        for feat in data.get("features", [])
    ]


_PARSE_RUNGS: tuple[tuple[str, Any], ...] = (
    ("fiona", _placemarks_fiona),
    ("ogr2ogr", _placemarks_ogr2ogr),
    ("raw_xml", _placemarks_rawxml),
)


def _parse_kml(path: Path) -> tuple[list[_Place], str]:
    """Recovery ladder: first rung that yields placemarks wins; a rung that yields
    zero or dies falls through to the next. Never abort on one bad placemark."""
    failures: dict[str, str] = {}
    for name, fn in _PARSE_RUNGS:
        try:
            places = fn(path)
        except Exception as e:  # a rung must never abort the ladder
            failures[name] = f"{type(e).__name__}: {e}"
            continue
        if places:
            return places, name
        failures[name] = "produced zero placemarks"
    raise LoaderError(f"{path.name}: all parse rungs failed: {failures}")


# ---------------------------------------------------------------------------
# attempts: parse output -> candidate geometries
# ---------------------------------------------------------------------------


@dataclass
class _Attempt:
    place: _Place
    doc_index: int
    geom: LineString | None = None  # merged LineString in target CRS
    status: str = "ok"  # ok | invalid | skipped
    reason: str | None = None


def _build_attempts(places: list[_Place], tf: Transformer) -> list[_Attempt]:
    attempts: list[_Attempt] = []
    for i, p in enumerate(places):
        att = _Attempt(place=p, doc_index=i)
        if p.malformed:
            att.status, att.reason = "skipped", "malformed_placemark"
        elif p.nonline_geometry and not p.parts:
            att.status, att.reason = "invalid", "non_line_geometry"
        elif not p.parts:
            att.status, att.reason = "invalid", "null_or_empty_geometry"
        else:
            coords = [xy for part in p.parts for xy in part]  # merge parts document order
            try:
                xs, ys = zip(*coords, strict=True)
                tx, ty = tf.transform(xs, ys)
            except Exception as e:
                att.status, att.reason = "invalid", f"transform_failed:{type(e).__name__}"
                att.geom = None
                attempts.append(att)
                continue
            att.geom = LineString(zip(tx, ty, strict=True))
        attempts.append(att)
    return attempts


def _flip_invalid_geoms(attempts: list[_Attempt]) -> None:
    """shapely validity on the transformed geoms: not-valid entries leave the table
    and join the invalid numerator (gate); non-simple lines stay (reported only)."""
    for att in attempts:
        if att.status == "ok" and att.geom is not None:
            reason = is_valid_reason(att.geom)
            if reason != "Valid Geometry":
                att.status, att.reason = "invalid", f"not_valid:{reason}"
                att.geom = None


def _sort_attempts(attempts: list[_Attempt]) -> tuple[list[_Attempt], str]:
    usable = [a for a in attempts if a.status == "ok"]
    n_oid = sum(1 for a in usable if a.place.oid is not None)
    if n_oid == len(usable) and usable:
        basis = "OBJECTID"
    elif n_oid == 0:
        basis = "document_order"
    else:
        basis = f"OBJECTID_partial_{len(usable) - n_oid}_missing_document_order_tiebreak"

    def key(a: _Attempt) -> tuple:
        oid = a.place.oid
        return (oid is None, oid if oid is not None else a.doc_index, a.doc_index)

    return sorted(usable, key=key), basis


# ---------------------------------------------------------------------------
# diagnostics helpers
# ---------------------------------------------------------------------------


def _spacing_stats(geoms: list[LineString]) -> dict:
    chunks = []
    for g in geoms:
        arr = np.asarray(g.coords, dtype=float)
        if len(arr) >= 2:
            chunks.append(np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1])))
    if not chunks:
        return {"n_pairs": 0, "min": None, "p50": None, "mean": None, "max": None}
    v = np.concatenate(chunks)
    return {
        "n_pairs": int(v.size),
        "min": float(v.min()),
        "p50": float(np.percentile(v, 50)),
        "mean": float(v.mean()),
        "max": float(v.max()),
    }


def _endpoint_multiplicity(geoms: list[LineString]) -> dict:
    eps: list[tuple[int, float, float]] = []
    for gi, g in enumerate(geoms):
        cs = list(g.coords)
        eps.append((gi, cs[0][0], cs[0][1]))
        eps.append((gi, cs[-1][0], cs[-1][1]))
    out: dict[str, Any] = {"endpoints_total": len(eps)}
    arr = np.array([[x, y] for _, x, y in eps], dtype=float) if eps else np.zeros((0, 2))
    tree = cKDTree(arr) if len(arr) >= 2 else None
    for tol in SNAP_TOLERANCES_M:
        uf = _UnionFind(len(eps))
        if tree is not None:
            for i, j in tree.query_pairs(tol):
                uf.union(i, j)
        roots = [uf.find(i) for i in range(len(eps))]
        sizes = Counter(Counter(roots).values())
        out[f"{tol}_m"] = {
            "clusters_total": len(set(roots)),
            "cluster_size_to_endpoint_count": {str(k): v for k, v in sorted(sizes.items())},
        }
    return out


def _length_crosscheck(places: list[_Place], reach_pairs: list[tuple[_Attempt, Reach]]) -> dict:
    """Cross-check block: KML Length / SHAPE_Leng attribute sums vs measured
    post-transform lengths. The Length attribute's unit is NOT asserted - the
    realized median measured/attr ratio decides the km->m conversion, and the
    basis string names that assumption explicitly (V10: derivation inspectable)."""
    attr_sum = math.fsum(p.length_attr for p in places if p.length_attr is not None)
    leng_sum = math.fsum(p.shape_leng for p in places if p.shape_leng is not None)
    measured_sum = math.fsum(r.length_m for _, r in reach_pairs)

    def _median_over(getter: Any) -> float | None:
        ratios = [
            r.length_m / getter(a.place)
            for a, r in reach_pairs
            if getter(a.place) is not None and getter(a.place) > 0 and r.length_m > 0
        ]
        return float(np.median(ratios)) if ratios else None

    median_attr = _median_over(lambda p: p.length_attr)
    median_leng = _median_over(lambda p: p.shape_leng)

    unit_note = "unresolved_no_ratio_evidence"
    attr_sum_m = None
    if median_attr is not None and 900 <= median_attr <= 1100:
        attr_sum_m = attr_sum * 1000.0
        unit_note = "km_to_m_x1000_assumed_from_median_measured_over_length_attr_ratio"
    elif median_attr is not None and 0.9 <= median_attr <= 1.1:
        attr_sum_m = attr_sum
        unit_note = "metres_assumed_from_median_measured_over_length_attr_ratio"
    delta = measured_sum - attr_sum_m if attr_sum_m is not None else None
    return {
        "kml_length_attr_sum_as_recorded": attr_sum,
        "kml_shape_leng_sum_as_recorded": leng_sum,
        "measured_length_sum_m": measured_sum,
        "median_ratio_measured_over_length_attr": (
            round(median_attr, 6) if median_attr is not None else None
        ),
        "median_ratio_measured_over_shape_leng": (
            round(median_leng, 6) if median_leng is not None else None
        ),
        "kml_length_attr_unit_basis": unit_note,
        "kml_length_attr_sum_m": attr_sum_m,
        "delta_measured_minus_length_attr_m": delta,
        "delta_measured_minus_shape_leng_as_recorded": measured_sum - leng_sum,
    }


# ---------------------------------------------------------------------------
# elevation sampling diagnostic
# ---------------------------------------------------------------------------

_ELEV_CACHE: dict[tuple[str, str], tuple[np.ndarray, float | None]] = {}


def _downhill_diagnostic(reaches: list[Reach], cfg: dict) -> dict:
    res: dict[str, Any] = {
        "vertex_order_downhill_fraction": None,
        "vertex_order_downhill_reason": None,
        "elevation_surface": cfg.get("inputs", {}).get("elevation_surface"),
        "elevation_sha256_expected": cfg.get("inputs", {}).get("elevation_sha256"),
        "elevation_sha256_realized": None,
        "elevation_sha256_matched": False,
        "pairs_total": 0,
        "pairs_excluded_nodata": 0,
    }
    elev_s = cfg.get("inputs", {}).get("elevation_surface")
    expected_sha = cfg.get("inputs", {}).get("elevation_sha256")
    grid = cfg.get("grid") or {}
    transform = grid.get("transform")
    width, height = grid.get("width"), grid.get("height")

    def fail(reason: str) -> dict:
        res["vertex_order_downhill_reason"] = reason
        return res

    if elev_s is None:
        return fail("inputs.elevation_surface not configured")
    elev_path = _repo(elev_s)
    if not elev_path.exists():
        return fail(f"elevation surface not readable: {elev_path}")
    got = _sha256_file(elev_path)
    res["elevation_sha256_realized"] = got
    if expected_sha is None:
        return fail("inputs.elevation_sha256 not configured")
    if got != expected_sha:
        return fail(f"sha256 mismatch (expected {expected_sha}, realized {got})")
    res["elevation_sha256_matched"] = True
    if transform is None or width is None or height is None:
        return fail("grid transform/width/height not configured")
    if not reaches:
        return fail("no reaches to sample")

    key = (str(elev_path.resolve()), got)
    if key not in _ELEV_CACHE:
        with rasterio.open(elev_path) as src:
            z = src.read(1)
            nodata = src.nodata
        _ELEV_CACHE[key] = (z, nodata)
    z, nodata = _ELEV_CACHE[key]

    a, _, c, _, e, f = [float(x) for x in transform]
    xs: list[float] = []
    ys: list[float] = []
    for r in reaches:
        arr = np.asarray(r.geom.coords, dtype=float)
        xs.extend(arr[:, 0].tolist())
        ys.extend(arr[:, 1].tolist())
    x = np.asarray(xs)
    y = np.asarray(ys)
    col = np.clip(np.floor((x - c) / a).astype(int), 0, z.shape[1] - 1)
    row = np.clip(np.floor((f - y) / -e).astype(int), 0, z.shape[0] - 1)
    zz = z[row, col].astype(np.float64)
    ok = np.isfinite(zz)
    if nodata is not None:
        ok &= zz != float(nodata)
    pair_ok = ok[:-1] & ok[1:]
    desc = zz[1:] < zz[:-1]
    n_pairs = int(pair_ok.sum())
    res["pairs_total"] = int(len(zz) - 1)
    res["pairs_excluded_nodata"] = int(len(zz) - 1 - n_pairs)
    if n_pairs == 0:
        return fail("no samplable consecutive vertex pairs inside the elevation surface")
    res["vertex_order_downhill_fraction"] = float((desc & pair_ok).sum()) / n_pairs
    return res


# ---------------------------------------------------------------------------
# snapped-endpoint helpers (shared by P+S census cross-check and tertiary report)
# ---------------------------------------------------------------------------


def _endpoint_arrays(geoms: list[LineString]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for g in geoms:
        cs = list(g.coords)
        pts.append((cs[0][0], cs[0][1]))
        pts.append((cs[-1][0], cs[-1][1]))
    return pts


def _snapped_representatives(geoms: list[LineString], tol_m: float) -> list[tuple[float, float]]:
    """Distinct endpoint-cluster representatives (min coord member per cluster,
    matching the pinned node representative convention) at tol_m."""
    pts = _endpoint_arrays(geoms)
    reps: list[tuple[float, float]] = []
    if not pts:
        return reps
    arr = np.array(pts, dtype=float)
    tree = cKDTree(arr)
    uf = _UnionFind(len(pts))
    for i, j in tree.query_pairs(tol_m):
        uf.union(i, j)
    members: dict[int, list[tuple[float, float]]] = {}
    for si, pt in enumerate(pts):
        members.setdefault(uf.find(si), []).append(pt)
    for root in sorted(members):
        reps.append(min(members[root], key=lambda p: (round(p[0], 3), round(p[1], 3))))
    return reps


# ---------------------------------------------------------------------------
# tertiary report-only analysis
# ---------------------------------------------------------------------------


def _tertiary_report(
    path_str: str | None, tf: Transformer, ps_rep_points: list[tuple[float, float]]
) -> dict:
    rep: dict[str, Any] = {
        "path": path_str,
        "reaches_emitted": 0,
        "tertiary_placemark_count": 0,
        "tertiary_parsed_count": 0,
        "tertiary_failed_count": 0,
        "tertiary_nonline_count": 0,
        "parse_rung": None,
        "tertiary_component_count_10m": None,
        "tertiary_endpoint_clusters_10m_no_bridge": None,
        "tertiary_junction_candidates_endpoints_10m": None,
        "tertiary_junction_candidate_geoms": None,
        "ps_snapped_reference_points": len(ps_rep_points),
    }
    if path_str is None:
        rep["reason"] = "kml_tertiary_report_only not requested"
        return rep
    path = _repo(path_str)
    if not path.exists():
        rep["reason"] = f"file not found: {path}"
        return rep

    places, rung = _parse_kml(path)
    rep["tertiary_placemark_count"] = len(places)
    rep["parse_rung"] = rung
    lines_wgs: list[list[tuple[float, float]]] = []  # each PART is one geom (expect ~5815)
    failed = 0
    nonline = 0
    for p in places:
        if p.malformed:
            failed += 1
            continue
        lines_wgs.extend(p.parts)  # each PART is one geom (expect ~5815, 9 multipart placemarks)
        if p.nonline_geometry:
            nonline += 1
    rep["tertiary_failed_count"] = failed
    rep["tertiary_nonline_count"] = nonline
    rep["tertiary_parsed_count"] = len(lines_wgs)

    end_pts: list[tuple[float, float]] = []
    for line in lines_wgs:
        xs, ys = zip(*line, strict=True)
        tx, ty = tf.transform(xs, ys)
        end_pts.append((tx[0], ty[0]))
        end_pts.append((tx[-1], ty[-1]))
    if lines_wgs:
        arr = np.array(end_pts, dtype=float)
        tree = cKDTree(arr)
        uf = _UnionFind(len(end_pts))
        for i, j in tree.query_pairs(10.0):
            uf.union(i, j)
        rep["tertiary_endpoint_clusters_10m_no_bridge"] = len(
            {uf.find(i) for i in range(len(end_pts))}
        )
        for gi in range(len(lines_wgs)):  # each geom bridges its two endpoints
            uf.union(2 * gi, 2 * gi + 1)
        rep["tertiary_component_count_10m"] = len({uf.find(i) for i in range(len(end_pts))})

        if ps_rep_points:
            ps_arr = np.array(ps_rep_points, dtype=float)
            ps_tree = cKDTree(ps_arr)
            cand_eps = 0
            cand_geoms: set[int] = set()
            for gi in range(len(lines_wgs)):
                for slot in (2 * gi, 2 * gi + 1):
                    if ps_tree.query_ball_point(end_pts[slot], 10.0):
                        cand_eps += 1
                        cand_geoms.add(gi)
            rep["tertiary_junction_candidates_endpoints_10m"] = cand_eps
            rep["tertiary_junction_candidate_geoms"] = len(cand_geoms)
    return rep


# ---------------------------------------------------------------------------
# public entrypoints
# ---------------------------------------------------------------------------


def _class_diagnostics(
    cls: str,
    places: list[_Place],
    attempts: list[_Attempt],
    class_reaches: list[Reach],
    ok_attempts: list[_Attempt],
    sort_basis: str,
    parse_rung: str,
) -> dict:
    geoms = [r.geom for r in class_reaches]
    invalid = [a for a in attempts if a.status == "invalid"]
    reasons = Counter(a.reason or "?" for a in invalid)
    null_empty = sum(1 for r in reasons if r.startswith(("null_or_empty", "non_line", "transform")))
    multipart_count = sum(1 for p in places if len(p.parts) > 1)
    self_intersect = 0
    for g in geoms:
        if not g.is_simple:
            self_intersect += 1
    return {
        "geom_count": len(class_reaches),
        "placemark_count": len(places),
        "failed_parse": sum(1 for a in attempts if a.status == "skipped"),
        "parse_rung": parse_rung,
        "sort_basis": sort_basis,
        "invalid_geometry_count": len(invalid),
        "invalid_geometry_reasons": dict(reasons),
        "null_empty_count": null_empty,
        "self_intersection_count": self_intersect,
        "multipart_count": multipart_count,
        "multipart_parts_merged": sum(len(p.parts) for p in places if len(p.parts) > 1),
        "vertex_spacing_stats": _spacing_stats(geoms),
        "endpoint_multiplicity": _endpoint_multiplicity(geoms),
        "length_crosscheck": _length_crosscheck(
            places, list(zip(ok_attempts, class_reaches, strict=True))
        ),
    }


def load_reaches(
    kml_primary: str,
    kml_secondary: str,
    kml_tertiary_report_only: str | None = None,
) -> tuple[list[Reach], dict]:
    """Parse BBMP primary/secondary KMLs into Reach records (EPSG:32643) plus a
    diagnostics dict. Tertiary path is REPORT-ONLY and never emits reaches.

    Gate: overall P+S invalid-geometry fraction > cfg
    diagnostics.max_invalid_fraction raises LoaderError before returning."""
    t0 = time.perf_counter()
    cfg = _active_cfg()
    max_frac = float(
        cfg.get("diagnostics", {}).get("max_invalid_fraction", DEFAULT_MAX_INVALID_FRACTION)
    )
    target_crs = str(cfg.get("crs") or "EPSG:32643")
    tf = Transformer.from_crs(SOURCE_CRS, target_crs, always_xy=True)

    all_class_diag: dict[str, dict] = {}
    reaches: list[Reach] = []
    next_id = 1
    attempts_by_class: dict[str, list[_Attempt]] = {}

    for cls, path_str in (("primary", kml_primary), ("secondary", kml_secondary)):
        path = _repo(path_str)
        if not path.exists():
            raise LoaderError(f"{cls} KML not found: {path}")
        places, rung = _parse_kml(path)
        attempts = _build_attempts(places, tf)
        _flip_invalid_geoms(attempts)
        ordered, basis = _sort_attempts(attempts)
        class_reaches = [
            Reach(reach_id=next_id + i, drain_class=cls, geom=a.geom, length_m=float(a.geom.length))
            for i, a in enumerate(ordered)
        ]
        next_id += len(class_reaches)
        reaches.extend(class_reaches)
        attempts_by_class[cls] = attempts
        all_class_diag[cls] = _class_diagnostics(
            cls, places, attempts, class_reaches, ordered, basis, rung
        )

    # GATE: refuse before downstream starts. Scope = primary+secondary (the classes
    # feeding the stitcher); malformed-parse skips are excluded from the denominator
    # (partial-recovery semantics) - invalid means parsed-but-unusable.
    attempted = sum(
        1
        for cls in ("primary", "secondary")
        for a in attempts_by_class[cls]
        if a.status != "skipped"
    )
    invalid_n = sum(
        all_class_diag[cls]["invalid_geometry_count"] for cls in ("primary", "secondary")
    )
    observed_frac = invalid_n / attempted if attempted else 1.0
    gate = {
        "scope": "primary+secondary",
        "definition": "invalid_geoms/(parsed_ok+invalid_geoms); malformed-parse skips excluded",
        "attempted_geoms": attempted,
        "invalid_geoms": invalid_n,
        "observed_invalid_fraction": observed_frac,
        "max_invalid_fraction": max_frac,
        "passed": observed_frac <= max_frac,
    }
    all_class_diag["gate"] = gate
    if not gate["passed"]:
        raise LoaderError(
            f"invalid-geometry fraction {observed_frac:.4f} exceeds max_invalid_fraction "
            f"{max_frac} (invalid={invalid_n}/{attempted}); refusing to hand reaches downstream.\n"
            + "\n".join(
                f"- {cls}: {json.dumps(all_class_diag[cls]['invalid_geometry_reasons'])}"
                for cls in ("primary", "secondary")
                if all_class_diag[cls]["invalid_geometry_count"]
            )
        )

    ps_geoms = [r.geom for r in reaches]
    ps_reps = _snapped_representatives(ps_geoms, 10.0)
    tertiary = _tertiary_report(kml_tertiary_report_only, tf, ps_reps)
    downhill = _downhill_diagnostic(reaches, cfg)

    diagnostics: dict[str, Any] = {
        **all_class_diag,
        "total_reaches": len(reaches),
        "measured_length_sum_m_all_classes": math.fsum(r.length_m for r in reaches),
        "ps_snapped_endpoint_clusters_10m": len(ps_reps),
        "vertex_order_downhill": downhill,
        "tertiary": tertiary,
        "wall_clock_s": round(time.perf_counter() - t0, 3),
        "source_crs": SOURCE_CRS,
        "target_crs": target_crs,
    }
    return reaches, diagnostics


# ---------------------------------------------------------------------------
# CLI (rule-6 manifest at run start, updated in place)
# ---------------------------------------------------------------------------


@app.callback()
def _root() -> None:
    """WF-1 loader CLI (keeps `loader run ...` as a named subcommand)."""


@app.command()
def run(
    config: Path = typer.Option(Path("configs/drainage.yaml"), help="Path to drainage YAML"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable single JSON object"),
) -> None:
    """Load configured KMLs, print census + diagnostics, write loader manifest."""
    t0 = time.perf_counter()
    cfg = resolve_config(str(config))
    bind_config(cfg)
    run_dir = _repo(cfg["outputs"]["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "loader_manifest.json"

    inputs = {
        k: str(_repo(cfg["inputs"][k])) if cfg["inputs"].get(k) else None
        for k in ("kml_primary", "kml_secondary", "kml_tertiary_report_only")
    }
    input_shas = {k: _sha256_file(Path(v)) for k, v in inputs.items() if v and Path(v).exists()}
    manifest: dict[str, Any] = {
        "stage": "wf1_load",
        "status": "running",
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "started_utc": _utc_now(),
        "config_snapshot": _jsonable(cfg),
        "inputs": inputs,
        "input_sha256": input_shas,
        "manifest_path": str(manifest_path),
    }

    def write_manifest(update: dict) -> None:
        manifest.update(update)
        manifest_path.write_text(json.dumps(_jsonable(manifest), indent=2, sort_keys=True))

    write_manifest({})  # rule 6: manifest exists at RUN START

    try:
        reaches, diag = load_reaches(
            inputs["kml_primary"],
            inputs["kml_secondary"],
            inputs["kml_tertiary_report_only"],
        )
    except LoaderError as exc:
        write_manifest({"status": "refused", "error": str(exc), "finished_utc": _utc_now()})
        typer.echo(f"[loader-refused] {exc}", err=True)
        raise typer.Exit(code=1) from exc

    census = {
        "primary": diag["primary"]["geom_count"],
        "secondary": diag["secondary"]["geom_count"],
        "total": diag["total_reaches"],
        "measured_length_km": round(diag["measured_length_sum_m_all_classes"] / 1000.0, 3),
        "failed_parse": {
            "primary": diag["primary"]["failed_parse"],
            "secondary": diag["secondary"]["failed_parse"],
        },
    }
    wall_clock_s = round(time.perf_counter() - t0, 3)
    write_manifest(
        {
            "status": "completed",
            "finished_utc": _utc_now(),
            "wall_clock_s": wall_clock_s,
            "census": census,
            "diagnostics": _jsonable(diag),
        }
    )

    payload = {
        "census": census,
        "diagnostics": _jsonable(diag),
        "manifest_path": str(manifest_path),
        "wall_clock_s": wall_clock_s,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        typer.echo(f"census: {json.dumps(census, sort_keys=True)}")
        for cls in ("primary", "secondary"):
            d = diag[cls]
            typer.echo(
                f"[{cls}] rung={d['parse_rung']} sort={d['sort_basis']} "
                f"invalid={d['invalid_geometry_count']} selfx={d['self_intersection_count']} "
                f"multipart={d['multipart_count']} spacing={_short(d['vertex_spacing_stats'])}"
            )
        dl = diag["vertex_order_downhill"]
        typer.echo(
            f"downhill_fraction={dl['vertex_order_downhill_fraction']} "
            f"(reason={dl['vertex_order_downhill_reason']})"
        )
        t = diag["tertiary"]
        typer.echo(
            f"[tertiary-report-only] placemarks={t['tertiary_placemark_count']} "
            f"parsed={t['tertiary_parsed_count']} failed={t['tertiary_failed_count']} "
            f"comps10m={t['tertiary_component_count_10m']} "
            f"junction_candidates={t['tertiary_junction_candidates_endpoints_10m']} "
            f"reaches_emitted={t['reaches_emitted']}"
        )
        typer.echo(f"manifest: {manifest_path}")


def _short(stats: dict) -> str:
    if stats.get("min") is None:
        return "n/a"
    return f"[{stats['min']:.1f}/{stats['p50']:.1f}/{stats['mean']:.1f}/{stats['max']:.1f}]m"


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":
    app()
