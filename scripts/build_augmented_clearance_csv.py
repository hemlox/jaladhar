"""Build the augmented clearance CSV = existing register rows + measured rows.

Measured rows come from runs/underpass_measured/new_clearance_rows.csv (the
reviewed carve-grade inventory: quote, source, URL, event year per row). The
augmented CSV keeps the existing schema and appends the provenance columns, so
derive_inverts reads clearance_m_parsed/clearance_tag_source exactly as for
OSM rows while every number stays traceable (repo rules 3, V9).

Repro: PYTHONPATH=. .venv/bin/python scripts/build_augmented_clearance_csv.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path("/home/darshil/Desktop/sih/clginternal")
TERRAIN = REPO / "data/interim/terrain"
RUN = REPO / "runs/underpass_measured"
EXISTING = TERRAIN / "underpass_register_clearance.csv"
NEW_ROWS = RUN / "new_clearance_rows.csv"
OUT = RUN / "underpass_register_clearance_augmented.csv"


def main() -> int:
    existing = pd.read_csv(EXISTING, low_memory=False)
    existing["osm_id"] = existing["osm_id"].astype(str)
    if not NEW_ROWS.exists():
        raise FileNotFoundError(f"{NEW_ROWS} not found — nothing to augment with")
    new = pd.read_csv(NEW_ROWS, low_memory=False)
    new["osm_id"] = new["osm_id"].astype(str)

    dupes = set(existing["osm_id"]) & set(new["osm_id"])
    overridden: list[str] = []
    if dupes:
        # An existing register row with NO measured value (MISSING_TAG) is
        # replaced by the measured row — same osm_id, one row. Rows that
        # already carry a measured OSM clearance are NEVER overridden.
        for oid in dupes:
            erow = existing[existing["osm_id"] == oid].iloc[0]
            if pd.notna(erow.get("clearance_m_parsed")):
                raise RuntimeError(
                    f"osm_id {oid} already has a measured clearance "
                    f"({erow['clearance_m_parsed']}); refusing to override"
                )
            overridden.append(oid)
        existing = existing[~existing["osm_id"].isin(dupes)].copy()
        print(f"overriding MISSING_TAG register rows (no measured value): {sorted(overridden)}")

    merged = pd.concat([existing, new], ignore_index=True, sort=False)
    merged["osm_id"] = merged["osm_id"].astype(str)
    merged.to_csv(OUT, index=False)
    print(f"augmented clearance CSV: {OUT}")
    print(f"existing rows: {len(existing)}, new measured rows: {len(new)}, total: {len(merged)}")
    print(f"overridden MISSING_TAG rows: {len(overridden)}")
    print("new rows:")
    print(
        new[
            ["osm_id", "clearance_m_parsed", "data_type", "quote", "source_url"]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())