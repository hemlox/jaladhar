from __future__ import annotations

import csv
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / "data" / "interim" / "terrain" / "unrepresentative_underpass_register.csv"
GT_CSV = REPO / "data" / "raw" / "groundtruth" / "sept2022_points.csv"
BBMP_DIR = REPO / "data" / "raw" / "bbmp"
OUT_DIR = REPO / "data" / "raw" / "underpass_search"

ANCHOR_RADIUS_M = 2000.0
MAX_LOCATIONS = 50

NAME_KEYWORDS = (
    "underpass",
    "under pass",
    "flyover",
    "underbridge",
    "under bridge",
    "overbridge",
    "twin tunnel",
    "subway",
    "railway",
    "rail under",
)

GENERIC_NAMES = {
    "nan",
    "underpass",
    "subway",
    "outer ring road underpass",
    "namma metro - underground ug1",
    "namma metro - underground ug2",
    "namma metro - reach 1",
    "metro - bus foot overbridge",
    "majestic foot over bridge",
    "railway station subway",
    "kkbmpl gail pipeline",
    "central avenue",
    "yelahanka lake trail",
    "kodigehalli road",
}

EXPLICIT_TARGETS = [
    {
        "resolve": "gt",
        "id": "GT_05",
        "name": "Silk Board Junction",
        "search": "Silk Board Junction underpass",
    },
    {
        "resolve": "gt",
        "id": "GT_15",
        "name": "Hebbal Flyover Underpass",
        "search": "Hebbal flyover underpass",
    },
    {
        "resolve": "gt",
        "id": "GT_17",
        "name": "Bellandur Kodi Junction",
        "search": "Bellandur Kodi underpass",
    },
    {
        "resolve": "gt",
        "id": "GT_18",
        "name": "Varthur Kodi Junction",
        "search": "Varthur Kodi underpass",
    },
    {
        "resolve": "gt",
        "id": "GT_21",
        "name": "KR Puram Lake Road",
        "search": "KR Puram underpass",
    },
    {
        "resolve": "bbmp",
        "name": "Yeshwanthpur Railway station",
        "search": "Yeshwanthpur railway station subway",
    },
    {
        "resolve": "nominatim",
        "name": "Cauvery Junction",
        "search": "Cauvery Junction Bengaluru underpass",
    },
    {
        "resolve": "gt",
        "id": "GT_16",
        "name": "Old Airport Road Railway Crossing",
        "search": "HAL Old Airport Road railway underpass",
    },
    {
        "resolve": "bbmp",
        "name": "Railway underbridge near Kino theatre",
        "search": "Railway underbridge Kino theatre Bengaluru",
    },
    {
        "resolve": "bbmp",
        "name": "Shivananda circle_Railway under pass",
        "search": "Shivananda circle railway underpass",
    },
]


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def is_real_name(name: str) -> bool:
    if not name.strip():
        return False
    low = name.strip().lower()
    if low in GENERIC_NAMES:
        return False
    if re.fullmatch(r"[A-Za-z]?[- ]?\d{2,3}", low):
        return False
    return True


def load_bbmp_points() -> list[dict]:
    import xml.etree.ElementTree as ET

    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    points = []
    for kml in sorted(BBMP_DIR.glob("*.kml")):
        tree = ET.parse(kml)
        root = tree.getroot()
        for pm in root.findall(".//kml:Placemark", ns):
            name_el = pm.find("kml:name", ns)
            coord_el = pm.find(".//kml:coordinates", ns)
            if name_el is None or coord_el is None or not coord_el.text:
                continue
            name = name_el.text.strip()
            lon, lat = (float(x) for x in coord_el.text.strip().split(",")[:2])
            if (lat, lon) == (0.0, 0.0):
                continue  # the known null-island defect row
            points.append({"name": name, "lat": lat, "lon": lon, "src": kml.name})
    return points


def name_matches(name: str) -> bool:
    if not is_real_name(name):
        return False
    low = name.strip().lower()
    return any(k in low for k in NAME_KEYWORDS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()

    reg_rows = list(csv.DictReader(open(REGISTER)))
    gt_rows = list(csv.DictReader(open(GT_CSV)))
    bbmp = load_bbmp_points()

    anchors = []
    for r in gt_rows:
        anchors.append(
            {
                "kind": "GT",
                "id": r["id"],
                "name": r["location_name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
            }
        )
    for i, p in enumerate(bbmp):
        anchors.append(
            {
                "kind": "BBMP",
                "id": f"BBMP_{i}",
                "name": p["name"],
                "lat": p["lat"],
                "lon": p["lon"],
            }
        )

    def nearest_anchor(lat: float, lon: float) -> dict:
        best = None
        for a in anchors:
            d = haversine_m(lat, lon, a["lat"], a["lon"])
            if best is None or d < best["dist_m"]:
                best = {
                    "anchor_kind": a["kind"],
                    "anchor_id": a["id"],
                    "anchor_name": a["name"],
                    "dist_m": d,
                }
        return best

    def resolve_explicit(t: dict) -> tuple[float, float, str]:
        """Return (lat, lon, source_note) for an explicit target."""
        if t["resolve"] == "gt":
            row = next(r for r in gt_rows if r["id"] == t["id"])
            return float(row["lat"]), float(row["lon"]), f"gt:{t['id']}"
        if t["resolve"] == "bbmp":
            row = next(p for p in bbmp if p["name"].strip().lower() == t["name"].lower())
            return row["lat"], row["lon"], f"bbmp:{t['name']}"
        return _nominatim(t["name"])

    def _nominatim(name: str) -> tuple[float, float, str]:
        import urllib.parse
        import urllib.request

        q = urllib.parse.urlencode(
            {"q": f"{name} Bengaluru Karnataka", "format": "jsonv2", "limit": 1}
        )
        url = f"https://nominatim.openstreetmap.org/search?{q}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "jaladhar-underpass-search/0.1 (research)"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            import json as _json

            results = _json.loads(r.read())
        if not results:
            raise RuntimeError(f"nominatim: no result for {name}")
        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        return lat, lon, f"nominatim:{results[0]['display_name'][:80]}"

    # 1) Keyword-named register segments near a GT or BBMP anchor
    candidates = []
    for r in reg_rows:
        name = r["name"].strip()
        if not name_matches(name):
            continue
        lat, lon = float(r["lat"]), float(r["lon"])
        best = nearest_anchor(lat, lon)
        if best["dist_m"] <= ANCHOR_RADIUS_M:
            candidates.append(
                {
                    "osm_id": r["osm_id"],
                    "location_name": name,
                    "search_name": name,
                    "lat": lat,
                    "lon": lon,
                    "nearest_anchor_kind": best["anchor_kind"],
                    "nearest_anchor_name": best["anchor_name"],
                    "anchor_dist_m": round(best["dist_m"], 1),
                    "nearest_gt_name": r["nearest_gt_name"].strip(),
                    "nearest_bbmp_name": r["nearest_bbmp_name"].strip(),
                    "z_min_m": float(r["z_min_m"]),
                    "z_max_m": float(r["z_max_m"]),
                    "max_dip_m": float(r["max_dip_m"]),
                    "coord_source": "register",
                    "deduped": False,
                }
            )

    # Dedup by location name; keep the closest-to-anchor occurrence
    candidates.sort(key=lambda c: c["anchor_dist_m"])
    seen = set()
    for c in candidates:
        key = c["location_name"].lower()
        if key in seen:
            c["deduped"] = True
            continue
        seen.add(key)
        c["deduped"] = False

    # 2) Explicit targets: GT underpass anchors + BBMP underpass anchors
    for t in EXPLICIT_TARGETS:
        lat, lon, src_note = resolve_explicit(t)
        best = nearest_anchor(lat, lon)
        candidates.append(
            {
                "osm_id": "",
                "location_name": t["name"],
                "search_name": t["search"],
                "lat": lat,
                "lon": lon,
                "nearest_anchor_kind": best["anchor_kind"],
                "nearest_anchor_name": best["anchor_name"],
                "anchor_dist_m": round(best["dist_m"], 1),
                "nearest_gt_name": "",
                "nearest_bbmp_name": "",
                "z_min_m": "",
                "z_max_m": "",
                "max_dip_m": "",
                "coord_source": src_note,
                "deduped": False,
            }
        )

    locations = [c for c in candidates if not c.get("deduped")]
    locations.sort(key=lambda c: c["anchor_dist_m"])
    if len(locations) > MAX_LOCATIONS:
        # keep the explicit targets (they are the named priorities) and the
        # closest register segments up to the cap
        expl = [c for c in locations if not c["osm_id"]]
        reg = [c for c in locations if c["osm_id"]]
        locations = expl + reg[: MAX_LOCATIONS - len(expl)]

    out_csv = OUT_DIR / "search_register.csv"
    fieldnames = [k for k in locations[0].keys() if k != "deduped"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for c in locations:
            w.writerow({k: c[k] for k in fieldnames})

    manifest = {
        "schema": "jaladhar.underpass-search-register.v1",
        "git_sha": git_sha(),
        "started_at": started,
        "status": "completed",
        "inputs": {
            "register_csv": str(REGISTER),
            "register_rows": len(reg_rows),
            "gt_csv": str(GT_CSV),
            "gt_points": len(gt_rows),
            "bbmp_points_loaded": len(bbmp),
            "anchor_radius_m": ANCHOR_RADIUS_M,
        },
        "selection": {
            "keyword_matches_within_radius": sum(1 for c in candidates if c["osm_id"] and not c.get("deduped")),
            "explicit_targets": len(EXPLICIT_TARGETS),
            "locations_selected": len(locations),
            "per_anchor_kind": {
                "GT": sum(1 for l in locations if l["nearest_anchor_kind"] == "GT"),
                "BBMP": sum(1 for l in locations if l["nearest_anchor_kind"] == "BBMP"),
            },
            "cutoff_note": "keyword-named register segments within 2 km of a GT/BBMP anchor, deduped by name, plus explicit GT/BBMP underpass anchors; capped at 50",
        },
        "output_csv": str(out_csv),
    }
    with open(OUT_DIR / "search_register_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(manifest, indent=2))
    print(f"\n{len(locations)} locations written to {out_csv}")
    for l in locations:
        print(
            f"{l['anchor_dist_m']:8.1f}  [{l['nearest_anchor_kind']}] {l['nearest_anchor_name']:<40s}  {l['location_name']}"
        )


if __name__ == "__main__":
    main()