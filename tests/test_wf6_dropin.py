"""WF-6 INT unit — drop-in swap proof (ADDENDUM L7).

The dashboard's label/issue/frame reflection must change PURELY with the
product directory it is pointed at — no code change, no reconfiguration.
This test constructs :class:`DashboardStore` twice against the two REALIZED
product shapes on disk (never fixtures, never baselines this repo produced
for testing — both are producer-written runs, V3-clean):

* flat event-maximum twins: ``runs/wf3_replay2_uncoupled_baseline_v5``
* frame series:           ``runs/wf3_replay2_uncoupled_baseline_frames_v1``

and asserts the state payloads reflect different run ids, temporal
aggregations and frame counts while every other input is identical.

Cost note: the flat side validates 176,171 rows x CSV/JSON twins (~30 s
measured); that cost IS the admission gate doing its job, so it is paid here
once rather than bypassed with a lighter constructor that would not prove
drop-in behaviour at the served surface.
"""

from __future__ import annotations

from jaladhar.web.app import REPO_ROOT, DashboardStore

FLAT_CSV = REPO_ROOT / "runs/wf3_replay2_uncoupled_baseline_v5/products/segment_status.csv"
SERIES_RUN = REPO_ROOT / "runs/wf3_replay2_uncoupled_baseline_frames_v1"


def test_dropin_swap_between_realized_product_shapes() -> None:
    flat = DashboardStore(product=FLAT_CSV)
    series = DashboardStore(product=SERIES_RUN)

    assert flat.status == "ready"
    assert series.status == "ready"

    fp = flat.state_payload()
    sp = series.state_payload()

    # Different reflection purely from the product directory argument:
    assert "mode" not in fp or fp.get("mode") != "series"
    assert sp["mode"] == "series"

    flat_snap = fp["snapshots"][0]
    assert flat_snap["run_id"] == "wf3_replay2_uncoupled_baseline_v5"
    assert sp["series"]["run_id"] == "wf3_replay2_uncoupled_baseline_frames_v1"

    # Same producer label travels with both shapes; the SHAPE differs below.
    assert flat_snap["product_label"] == sp["series"]["product_label"]

    # Frame/lead structure differs with the directory, not with code:
    assert len(fp["leads"]) == 1  # one realized lead (event maximum)
    assert sp["series"]["n_frames"] == 97
    assert flat_snap["temporal_aggregation"] == "event_maximum"
    assert sp["series"]["temporal_aggregation"] == "frame_series"

    # The series realizes strictly more frames than the flat summary:
    assert sp["series"]["n_frames"] > len(fp["leads"])
