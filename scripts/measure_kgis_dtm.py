from __future__ import annotations

import csv
import json
import statistics
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pyproj
import rasterio

EGM96_GRID = "/snap/gnome-46-2404/164/usr/share/proj/egm96_15.gtx"

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / "data" / "raw" / "underpass_search" / "search_register.csv"
GT_CSV = REPO / "data" / "raw" / "groundtruth" / "sept2022_points.csv"
FABDEM_TILES = [
    REPO / "data" / "raw" / "dem" / "fabdem" / "N12E077.tif",
    REPO / "data" / "raw" / "dem" / "fabdem" / "N13E077.tif",
]
OUT_DIR = REPO / "data" / "raw" / "underpass_search" / "kgis_dtm"

BENCHMARKS = [
    {
        "name": "KIA runway (published MSL ~915 m)",
        "lat": 13.1986,
        "lon": 77.7066,
    },
    {
        "name": "MG Road Vidhana Soudha area (published MSL ~920 m)",
        "lat": 12.9795,
        "lon": 77.5912,
    },
    {
        "name": "Nandi Hills summit (published MSL ~1478 m)",
        "lat": 13.37,
        "lon": 77.683,
    },
]

IDENTIFY_URL = (
    "https://kgis.ksrsac.in/kgismaps2/rest/services/BBMP/FloodRisk_GIS/MapServer/identify"
)
KA_DTM_LAYER = "26"  # KA_DTM_BBMP.img
UA = {"User-Agent": "jaladhar-underpass-search/0.1 (research)"}
TIMEOUT_S = 30
RETRIES = 3


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def identify(x: float, y: float, layer: str) -> dict:
    params = {
        "f": "json",
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "sr": "32643",
        "layers": f"all:{layer}",
        "tolerance": 5,
        "mapExtent": "766940,1420150,802060,1454350",
        "imageDisplay": "800,600,96",
    }
    data = urllib.parse.urlencode(params).encode()
    last = None
    for i in range(RETRIES):
        req = urllib.request.Request(IDENTIFY_URL, data=data, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"identify failed for ({x},{y}): {last}")


def ka_dtm_value(d: dict) -> float | None:
    for r in d.get("results", []):
        attrs = r.get("attributes", {})
        for k, v in attrs.items():
            if "Pixel Value" in k:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()

    trans = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    # EGM96 geoid undulation (N, m): orthometric = ellipsoidal - N
    geoid = pyproj.Transformer.from_crs(
        pyproj.CRS.from_epsg(4979),
        pyproj.CRS.from_proj4(f"+proj=longlat +datum=WGS84 +geoidgrids={EGM96_GRID} +type=crs"),
        always_xy=True,
    )

    def geoid_n(lon: float, lat: float) -> float:
        _, _, H = geoid.transform(lon, lat, 0.0)
        return 0.0 - H

    points: list[dict] = []
    for r in list(csv.DictReader(open(REGISTER))):
        points.append(
            {
                "kind": "REGISTER",
                "id": r["location_name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
            }
        )
    for r in list(csv.DictReader(open(GT_CSV))):
        points.append({"kind": "GT", "id": r["id"], "lat": float(r["lat"]), "lon": float(r["lon"])})
    for b in BENCHMARKS:
        points.append({"kind": "BENCHMARK", "id": b["name"], "lat": b["lat"], "lon": b["lon"]})

    dems = [rasterio.open(f) for f in FABDEM_TILES]

    def sample_fabdem(lon: float, lat: float) -> float | None:
        for dem in dems:
            try:
                v = float(list(dem.sample([(lon, lat)]))[0][0])
            except Exception:  # noqa: BLE001
                continue
            if v is not None and v > -9000:
                return v
        return None
    rows = []
    for p in points:
        x, y = trans.transform(p["lon"], p["lat"])
        try:
            resp = identify(x, y, KA_DTM_LAYER)
            dtm = ka_dtm_value(resp)
            n_results = len(resp.get("results", []))
        except Exception as e:  # noqa: BLE001
            dtm = None
            n_results = -1
            resp = {"error": str(e)}
        try:
            fabdem = sample_fabdem(p["lon"], p["lat"])
        except Exception:  # noqa: BLE001
            fabdem = None
        try:
            n_egm96 = geoid_n(p["lon"], p["lat"])
        except Exception:  # noqa: BLE001
            n_egm96 = None
        rows.append(
            {
                "kind": p["kind"],
                "id": p["id"],
                "lat": p["lat"],
                "lon": p["lon"],
                "utm_x": round(x, 2),
                "utm_y": round(y, 2),
                "ka_dtm_m": round(dtm, 4) if dtm is not None else "",
                "identify_results": n_results,
                "fabdem_m": round(fabdem, 4) if fabdem is not None else "",
                "delta_fabdem_minus_dtm": (
                    round(fabdem - dtm, 4) if (fabdem is not None and dtm is not None) else ""
                ),
                "egm96_undulation_m": round(n_egm96, 4) if n_egm96 is not None else "",
                "resid_dtm_ortho_minus_fabdem": (
                    round(dtm - n_egm96 - fabdem, 4)
                    if (dtm is not None and fabdem is not None and n_egm96 is not None)
                    else ""
                ),
                "raw_response": json.dumps(resp)[:400],
            }
        )

    out_csv = OUT_DIR / "dtm_values.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    paired = [r["delta_fabdem_minus_dtm"] for r in rows if isinstance(r["delta_fabdem_minus_dtm"], float)]
    resid = [r["resid_dtm_ortho_minus_fabdem"] for r in rows if isinstance(r["resid_dtm_ortho_minus_fabdem"], float)]
    manifest = {
        "schema": "jaladhar.measure-kgis-dtm.v1",
        "git_sha": git_sha(),
        "started_at": started,
        "status": "completed",
        "n_points": len(rows),
        "n_ka_dtm_values": sum(1 for r in rows if isinstance(r["ka_dtm_m"], float)),
        "n_fabdem_values": sum(1 for r in rows if isinstance(r["fabdem_m"], float)),
        "n_paired": len(paired),
        "delta_fabdem_minus_dtm": (
            {
                "mean_m": round(statistics.mean(paired), 4),
                "stdev_m": round(statistics.stdev(paired), 4) if len(paired) > 1 else None,
                "min_m": round(min(paired), 4),
                "max_m": round(max(paired), 4),
            }
            if paired
            else None
        ),
        "resid_dtm_as_ortho_minus_fabdem": (
            {
                "mean_m": round(statistics.mean(resid), 4),
                "stdev_m": round(statistics.stdev(resid), 4) if len(resid) > 1 else None,
                "min_m": round(min(resid), 4),
                "max_m": round(max(resid), 4),
            }
            if resid
            else None
        ),
        "note": (
            "KA_DTM_BBMP.img served by KSRSAC KGIS ArcGIS REST (BBMP/FloodRisk_GIS MapServer layer 26). "
            "resid_dtm_as_ortho_minus_fabdem is the residual after converting KA_DTM to orthometric with "
            "the EGM96 undulation and comparing to FABDEM. A large residual mean/stdev means the datum "
            "hypothesis (KA_DTM = WGS84 ellipsoidal) is NOT confirmed and the layer's vertical reference "
            "must come from KSRSAC metadata before the values are usable."
        ),
        "endpoint": IDENTIFY_URL,
        "output_csv": str(out_csv),
    }
    with open(OUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(manifest, indent=2))
    print(f"\n{len(rows)} rows -> {out_csv}")
    for r in rows:
        print(
            f"[{r['kind']}] {r['id']:<42s} DTM={r['ka_dtm_m']!s:>10} FABDEM={r['fabdem_m']!s:>10} "
            f"delta={r['delta_fabdem_minus_dtm']!s:>10} resid={r['resid_dtm_ortho_minus_fabdem']!s:>10}"
        )


if __name__ == "__main__":
    main()