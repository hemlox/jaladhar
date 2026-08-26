"""WF-6 build lane L2 — street watchlist builder (requirements A1/A11, A2).

Verification scope is stated beside every check (V7). Independent observables
(V2) are named per test:

* entity count — recomputed INSIDE this test from the realised gpkg bytes via
  fiona + a pure-python grid-bucket union-find (no scipy, no module code), so
  a regression in the module's merge cannot hide. Per owner adjudication
  2026-08-26 (M3 item 5) the recoverable per-name endpoint-connected-components
  count (2,395 at tolerance 1.0 m, EPSG:32643) is CANONICAL; the earlier audit
  figure 1,689 could not be reproduced and its derivation was unrecoverable.
  It is asserted to survive ONLY as recorded history inside denominator_note
  ("recorded, not averaged") and never as an input to the canonical number;
  the adjudication reopens automatically if the audit method is ever recovered
  and disagrees.
* top-row values — resolved by direct pandas filters over the raw product CSV,
  a mechanism entirely outside the module under test.
* red-under-mutation (V5) lives in TestRedUnderMutation and names its
  deliberate mutation: the severity ranking key is replaced by a name-only key
  and the order invariant must FAIL (and fails only then).

Rule-2 wording audit: no asserted sentence implies a measurement that did not
happen; the count adjudication is stated as an owner RECORD (canonical method,
retired audit figure kept as history), and the node-count reconciliation lives
in the intersections test module.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from jaladhar.web import watchlist as wl
from jaladhar.web.watchlist import (
    CANONICAL_ADJUDICATION_DATE,
    RETIRED_AUDIT_ENTITY_COUNT,
    ProductSource,
    build_watchlist,
    resolve_product_source,
)

REPO = REPO_ROOT = wl.REPO_ROOT
FLAT_PRODUCTS = REPO / "runs" / "wf3_replay2_uncoupled_baseline_v5" / "products"
FRAMES_RUN = REPO / "runs" / "wf3_replay2_uncoupled_baseline_frames_v1"
FRAME_TAG = "t0007200"


# ------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def flat_source() -> ProductSource:
    return resolve_product_source(FLAT_PRODUCTS, repo_root=REPO)


@pytest.fixture(scope="module")
def flat_watchlist(flat_source: ProductSource) -> dict:
    return build_watchlist(flat_source, repo_root=REPO)


@pytest.fixture(scope="module")
def frame_watchlist() -> dict:
    return build_watchlist(str(FRAMES_RUN), FRAME_TAG, repo_root=REPO)


# ------------------------------------------------------- independent recount


def _independent_entity_count() -> int:
    """Per-name SEGMENT components at 1.0 m WITHOUT module code or scipy.

    Reads the gpkg via fiona directly; two segments of one name merge when ANY
    of their part-endpoints coincide within 1.0 m (euclidean), found through
    floor-keyed 1 m grid buckets — a different mechanism from the module's
    scipy cKDTree query_pairs. If the counts disagree, the module regressed
    (V2).
    """

    import fiona

    classified = {
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
    by_name: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    with fiona.open(REPO / "data/interim/terrain/roads_centrelines.gpkg") as src:
        for feat in src:
            props = feat["properties"]
            name = (props.get("name") or "").strip()
            if not name or props.get("highway") not in classified:
                continue
            geom = feat["geometry"]
            if geom is None:
                continue
            gtype = geom["type"]
            if gtype == "LineString":
                lines = [geom["coordinates"]]
            elif gtype == "MultiLineString":
                lines = geom["coordinates"]
            else:
                continue
            pts: list[tuple[float, float]] = []
            for line in lines:
                if len(line) >= 2:
                    pts.append((line[0][0], line[0][1]))
                    pts.append((line[-1][0], line[-1][1]))
            if pts:
                by_name[name].append(pts)

    return sum(_bucket_component_count(segs_pts) for segs_pts in by_name.values())


def _bucket_component_count(segs_pts: list[list[tuple[float, float]]]) -> int:
    """Union-find over SEGMENTS of one name via floor-keyed 1 m buckets.

    ``segs_pts[i]`` holds every endpoint point of segment i. Two segments are
    unioned when any of their points lie within 1.0 m of each other.
    """
    import math

    n = len(segs_pts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = defaultdict(list)
    for si, pts in enumerate(segs_pts):
        for pt in pts:
            buckets[(math.floor(pt[0]), math.floor(pt[1]))].append((si, pt))
    neighbours = [
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 0),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    ]
    for cell, members in buckets.items():
        for dx, dy in neighbours:
            cell_b = (cell[0] + dx, cell[1] + dy)
            if cell_b not in buckets:
                continue
            for other in buckets[cell_b]:
                for si, p in members:
                    sj, q = other
                    ddx, ddy = p[0] - q[0], p[1] - q[1]
                    if ddx * ddx + ddy * ddy <= 1.0:
                        ri, rj = find(si), find(sj)
                        if ri != rj:
                            parent[ri] = rj
    return len({find(i) for i in range(n)})


# ------------------------------------------------------------------ reconciles


class TestReconciles:
    def test_entity_count_canonical_and_independently_recomputed(
        self, flat_watchlist: dict, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Canonical count per owner adjudication 2026-08-26: realized 2395.

        Scope (V7): full classified named set (10,318 segments), all of it
        merged — no subsampling. The hard assertion is against the INDEPENDENT
        recount (V2); the retired audit figure 1689 is checked to survive as
        recorded history inside denominator_note only.
        """
        counts = flat_watchlist["counts"]
        realized = counts["streets_total"]
        independent = _independent_entity_count()
        capsys.readouterr()
        print(
            f"\nentity reconcile: realized={realized} independent={independent} "
            f"retired-audit(history-only)={RETIRED_AUDIT_ENTITY_COUNT} "
            f"delta={realized - RETIRED_AUDIT_ENTITY_COUNT:+d}"
        )
        note = counts["denominator_note"]
        assert realized == independent, "module merge != independent recount"
        # The adjudicated canonical number: if the realised merge ever moves
        # off it, the owner adjudication must be revisited, not re-worded.
        assert realized == 2395
        assert (
            f"classified named entities={realized}" in note
        ), "denominator note must carry the REALIZED count"
        assert (
            f"is CANONICAL per owner adjudication {CANONICAL_ADJUDICATION_DATE}" in note
        ), "note must frame the recoverable-method count as canonical"
        assert (
            str(RETIRED_AUDIT_ENTITY_COUNT) in note
        ), "1689 must remain recorded history in the note"
        assert "recorded, not averaged" in note
        assert "reopened" in note and "disagrees" in note

    def test_top_row_matches_raw_csv(
        self, flat_source: ProductSource, flat_watchlist: dict
    ) -> None:
        """V2 observable: direct pandas filter over the RAW product csv.

        If the module's aggregation regressed (wrong member set, wrong argmax),
        the independent filter disagrees.
        """
        import pandas as pd

        top = flat_watchlist["rows"][0]
        csv = pd.read_csv(FLAT_PRODUCTS / "segment_status.csv")
        sub = csv[csv.segment_id.isin(top["segment_ids"])]
        assert len(sub) == len(top["segment_ids"])
        worst_raw = sub.loc[sub.band_high_cm.idxmax()]
        assert top["worst_depth_cm"] == int(worst_raw.band_high_cm)
        assert top["worst_segment_id"] == int(worst_raw.segment_id)
        assert top["depth_band_cm"]["high"] == int(worst_raw.band_high_cm)
        assert top["depth_band_cm"]["low"] == int(worst_raw.band_low_cm)
        assert top["status"] == str(worst_raw.flood_status)

    def test_ranking_invariant(self, flat_watchlist: dict) -> None:
        """Severity desc throughout; alternates re-order without changing rows."""
        rows = flat_watchlist["rows"]
        depths = [r["worst_depth_cm"] for r in rows]
        assert depths == sorted(depths, reverse=True)
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        alt = flat_watchlist["alternates"]["soonest_asc"]
        assert sorted(alt) == [r["rank"] for r in rows]
        by_rank = {r["rank"]: r for r in rows}
        keys = [(by_rank[r]["lead_minutes"], -by_rank[r]["worst_depth_cm"]) for r in alt[:-1]]
        assert keys == sorted(keys)

    def test_flooded_count_consistent(self, flat_watchlist: dict) -> None:
        rows = flat_watchlist["rows"]
        assert flat_watchlist["counts"]["flooded_streets"] == sum(
            1 for r in rows if r["status"] == "flooded"
        )
        assert flat_watchlist["order"] == "severity_desc"


class TestLeadHonesty:
    def test_flat_is_forecast_lead(self, flat_watchlist: dict) -> None:
        assert flat_watchlist["source"]["kind"] == "flat"
        assert flat_watchlist["source"]["lead_kind"] == "forecast_lead"
        assert all(r["lead_kind"] == "forecast_lead" for r in flat_watchlist["rows"])

    def test_frame_offset_labelled_hindcast(self, frame_watchlist: dict) -> None:
        """A2: frames realise an OFFSET from series start, never a forecast lead.

        V2 observable: offset_seconds read directly from the series manifest;
        if the module invented leads, 240 != 7200/60 would fail loudly.
        """
        import json as _json

        manifest = _json.loads((FRAMES_RUN / "manifest.json").read_text())
        entry = next(f for f in manifest["frames"] if f["tag"] == FRAME_TAG)
        src = frame_watchlist["source"]
        assert src["kind"] == "frame"
        assert src["frame_tag"] == FRAME_TAG
        assert src["valid_time_utc"] == entry["valid_time_utc"]
        lead = frame_watchlist["rows"][0]["lead_minutes"]
        assert lead == entry["offset_seconds"] // 60 == 120  # t0007200 = 2 h in
        assert src["lead_kind"] == "hindcast_offset"
        assert all(r["lead_kind"] == "hindcast_offset" for r in frame_watchlist["rows"])

    def test_frame_values_match_that_frame_csv(self, frame_watchlist: dict) -> None:
        import pandas as pd

        top = frame_watchlist["rows"][0]
        csv_path = FRAMES_RUN / "products/frames" / FRAME_TAG / "segment_status.csv"
        csv = pd.read_csv(csv_path)
        sub = csv[csv.segment_id.isin(top["segment_ids"])]
        assert top["worst_depth_cm"] == int(sub.band_high_cm.max())

    def test_json_safe_payload(self, flat_watchlist: dict, frame_watchlist: dict) -> None:
        for payload in (flat_watchlist, frame_watchlist):
            text = json.dumps(payload)  # raises on non-JSON-safe types
            assert "NaN" not in text and "Infinity" not in text


# --------------------------------------------------------- red under mutation


def _assert_severity_order(rows: list[dict]) -> None:
    depths = [r["worst_depth_cm"] for r in rows]
    assert depths == sorted(depths, reverse=True), "watchlist not in severity order"


class TestRedUnderMutation:
    def test_order_check_fails_under_tiebreak_mutation(
        self, monkeypatch: pytest.MonkeyPatch, flat_source: ProductSource
    ) -> None:
        """Deliberate mutation (V5): ranking key becomes name-only.

        Expected observation: _assert_severity_order PASSES on the real build
        and FAILS under the mutated key — the check can detect the exact
        regression it exists for. Mutation is restored by monkeypatch teardown.
        """
        real_rows = build_watchlist(flat_source, repo_root=REPO)["rows"]
        _assert_severity_order(real_rows)  # green before mutation

        monkeypatch.setattr(
            wl,
            "severity_key",
            lambda row: (row["street_name"],),  # MUTATED: name asc, depth ignored
        )
        mutated = build_watchlist(flat_source, repo_root=REPO)["rows"]
        with pytest.raises(AssertionError, match="not in severity order"):
            _assert_severity_order(mutated)
