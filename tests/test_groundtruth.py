"""- V1: Realized CSV on disk verified directly.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated for invariant checks.
- Invariants:
1. Every row has a non-empty source_url, source_publication_date, and source_quote (Rule 1 guard).
2. Every observed_date falls in the event window 2022-08-30 .. 2022-09-06."""

from __future__ import annotations

import csv
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSV_PATH = REPO / "data/raw/groundtruth/sept2022_points.csv"

# BBMP WGS84 bounding box (derived from data/raw/boundary/bbmp_wards_2023_final.kml)
BBMP_BBOX = {
    "min_lon": 77.46005,
    "min_lat": 12.83362,
    "max_lon": 77.78436,
    "max_lat": 13.14266,
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def load_rows() -> list[dict[str, str]]:
    assert CSV_PATH.exists(), f"Realized dataset missing at {CSV_PATH}"
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# Invariant 1: Rule 1 Provenance Guard (No Fabricated / Unsourced Data)


def test_rule1_provenance_every_row_has_source() -> None:
    """Invariant: Every row must carry a non-empty source provenance.
    Requires source_url, source_publication_date, and source_quote.
    the assertion fails immediately naming the violating row ID."""
    rows = load_rows()
    assert len(rows) > 0, "Dataset must not be empty"

    for idx, r in enumerate(rows, start=1):
        row_id = r.get("id", f"row_{idx}")
        url = r.get("source_url", "").strip()
        pub_date = r.get("source_publication_date", "").strip()
        quote = r.get("source_quote", "").strip()

        assert url, f"Rule 1 violation: Point {row_id} has empty source_url"
        assert url.startswith("http://") or url.startswith(
            "https://"
        ), f"Rule 1 violation: Point {row_id} has invalid URL: {url}"
        assert pub_date, f"Rule 1 violation: Point {row_id} has empty source_publication_date"
        assert quote, f"Rule 1 violation: Point {row_id} has empty source_quote"
        assert (
            len(quote) >= 15
        ), f"Rule 1 violation: Point {row_id} has suspiciously short quote: '{quote}'"


# Invariant 2: Event Temporal Window (2022-08-30 .. 2022-09-06)


def test_observed_dates_within_event_window() -> None:
    """Invariant: Every observed_date falls inside 2022-08-30 .. 2022-09-06."""
    rows = load_rows()
    for idx, r in enumerate(rows, start=1):
        row_id = r.get("id", f"row_{idx}")
        obs_date = r.get("observed_date", "").strip()
        assert obs_date, f"Point {row_id} has empty observed_date"
        assert "2022-08-30" <= obs_date <= "2022-09-06", (
            f"Point {row_id} observed_date '{obs_date}' is outside event window "
            "(2022-08-30 .. 2022-09-06)"
        )


# Invariant 3: Spatial Extent Inside BBMP Bounding Box


def test_coordinates_inside_bbmp_bounding_box() -> None:
    """Invariant: Every (lat, lon) coordinate falls strictly inside the BBMP domain.
    Any coordinate placed outside BBMP bounds raises AssertionError."""
    rows = load_rows()
    for idx, r in enumerate(rows, start=1):
        row_id = r.get("id", f"row_{idx}")
        lat = float(r["lat"])
        lon = float(r["lon"])

        assert BBMP_BBOX["min_lat"] <= lat <= BBMP_BBOX["max_lat"], (
            f"Point {row_id} latitude {lat} outside BBMP bbox "
            f"[{BBMP_BBOX['min_lat']}, {BBMP_BBOX['max_lat']}]"
        )
        assert BBMP_BBOX["min_lon"] <= lon <= BBMP_BBOX["max_lon"], (
            f"Point {row_id} longitude {lon} outside BBMP bbox "
            f"[{BBMP_BBOX['min_lon']}, {BBMP_BBOX['max_lon']}]"
        )


# Invariant 4: Depth Band Order Consistency (depth_low < depth_high)


def test_depth_bands_strictly_ordered() -> None:
    """Invariant: depth_band_low_m < depth_band_high_m wherever depth is present.
    Inverted or zero-width depth bands raise AssertionError."""
    rows = load_rows()
    for idx, r in enumerate(rows, start=1):
        row_id = r.get("id", f"row_{idx}")
        low_str = r.get("depth_band_low_m", "").strip()
        high_str = r.get("depth_band_high_m", "").strip()
        cue = r.get("depth_cue", "").strip()

        if cue == "NONE" or (not low_str and not high_str):
            assert (
                not low_str and not high_str
            ), f"Extent-only point {row_id} must have empty depth fields"
        else:
            assert (
                low_str and high_str
            ), f"Point {row_id} has partial depth band: low='{low_str}', high='{high_str}'"
            low = float(low_str)
            high = float(high_str)
            assert low < high, f"Point {row_id} depth band inverted: low {low} >= high {high}"


# Invariant 5: Spatial Deduplication (> 50 m apart)


def test_no_duplicate_points_within_50m() -> None:
    """Invariant: No two points in the dataset are within 50 meters of each other."""
    rows = load_rows()
    coords = [(r["id"], float(r["lat"]), float(r["lon"]), r["location_name"]) for r in rows]

    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            id1, lat1, lon1, name1 = coords[i]
            id2, lat2, lon2, name2 = coords[j]
            dist = haversine_m(lat1, lon1, lat2, lon2)
            assert (
                dist > 50.0
            ), f"Duplicate points within {dist:.1f} m: '{id1}: {name1}' and '{id2}: {name2}'"
