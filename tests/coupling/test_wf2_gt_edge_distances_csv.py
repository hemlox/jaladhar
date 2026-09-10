"""C1 regression (WF-2 round-3 fix loop): per-point CSV endpoint join.

DEFECT RECORDED: scripts/wf2_gt_edge_distance_distribution.py used the 1-based
``nearest_edge_id`` as a POSITIONAL index into the row-ordered node arrays, so
every CSV row carried the NEXT edge's endpoints and the max-id row would have
raised IndexError. GT_10's nearest edge 601 truly runs 649 -> 1721; the buggy
CSV showed from=650 / to=527.

RED-FIRST DEMONSTRATION (2026-08-26, pre-fix artefact on disk): this test was
run BEFORE the script fix + regeneration and FAILED with
``assert 527 == 1721`` (to_node) — reddened exactly on the adjudicated defect;
the script was then fixed and runs/wf2_gt_edges/gt_edge_distances.csv
regenerated, turning this test green. Mutation class: wrong-key join.

V2 observables: the expected endpoints are LITERALS typed here (649/1721),
cross-checked at runtime against an INDEPENDENT producer — a fresh geopandas
read of the ``drain_edges`` gpkg layer — not against anything the distance
script wrote. The CSV under test is realized bytes on disk (V1).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "runs" / "wf2_gt_edges" / "gt_edge_distances.csv"
GPKG = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"

GT_10_EDGE_ID = 601
GT_10_TRUTH_FROM_NODE = 649
GT_10_TRUTH_TO_NODE = 1721


def _gt10_row() -> dict[str, str]:
    with open(CSV_PATH, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["point_id"] == "GT_10"]
    assert len(rows) == 1, f"expected exactly one GT_10 row in {CSV_PATH}, got {len(rows)}"
    return rows[0]


def test_gt10_csv_endpoint_columns_match_gpkg_truth() -> None:
    row = _gt10_row()
    assert int(row["nearest_edge_id"]) == GT_10_EDGE_ID
    assert (
        int(row["nearest_edge_from_node"]) == GT_10_TRUTH_FROM_NODE
    ), "C1 regression: from_node must be edge 601's TRUE upstream endpoint"
    assert int(row["nearest_edge_to_node"]) == GT_10_TRUTH_TO_NODE, "C1 regression: to_node"

    assert int(round(float(row["to_x_m"]))) == int(round(802585.0))
    assert int(round(float(row["to_y_m"]))) == int(round(1434675.0))
    assert int(round(float(row["from_x_m"]))) == int(round(779648.0))
    assert int(round(float(row["from_y_m"]))) == int(round(1453203.0))


def test_gt10_truth_literals_agree_with_fresh_gpkg_read() -> None:
    import geopandas as gpd

    edges = gpd.read_file(GPKG, layer="drain_edges")
    sel = edges[edges["edge_id"] == GT_10_EDGE_ID]
    assert len(sel) == 1, f"edge {GT_10_EDGE_ID} not uniquely present in {GPKG}"
    assert int(sel.iloc[0]["from_node"]) == GT_10_TRUTH_FROM_NODE
    assert int(sel.iloc[0]["to_node"]) == GT_10_TRUTH_TO_NODE


def test_all_rows_endpoint_ids_resolve_in_gpkg() -> None:
    import geopandas as gpd

    edges = gpd.read_file(GPKG, layer="drain_edges")
    truth = {
        int(eid): (int(f), int(t))
        for eid, f, t in zip(edges["edge_id"], edges["from_node"], edges["to_node"], strict=True)
    }
    with open(CSV_PATH, newline="") as f:
        rdr = csv.DictReader(f)
        n = 0
        for r in rdr:
            eid = int(r["nearest_edge_id"])
            assert eid in truth, f"row {r['point_id']}: edge {eid} absent from gpkg"
            assert (int(r["nearest_edge_from_node"]), int(r["nearest_edge_to_node"])) == truth[
                eid
            ], (
                f"C1 regression on {r['point_id']}: CSV endpoints "
                f"({r['nearest_edge_from_node']},{r['nearest_edge_to_node']}) != "
                f"gpkg truth {truth[eid]} for edge {eid}"
            )
            n += 1
    assert n == 24, f"expected the full 24-point scope, read {n}"


@pytest.mark.parametrize("path", [CSV_PATH], ids=["runs/wf2_gt_edges/gt_edge_distances.csv"])
def test_csv_exists(path: Path) -> None:
    assert path.exists(), "regenerated artefact missing — run the distance script first"
