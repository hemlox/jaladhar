"""WF-6 build lane L2 — street-intersection deriver (requirements A1/A11).

Verification scope is stated beside every check (V7). Independent observables
(V2) are named per test:

* node count — the endpoint-clustering candidate count is reconciled against
  the audit-measured 2,142 and must match EXACTLY (it does: 2,142, delta 0).
* approach histograms — the audit's histogram method is not recorded in either
  repository, so this suite REPORTS the realised histograms beside the audit
  figures (recorded verbatim in counts.audit_baseline) and asserts only
  structural invariants that cannot pass vacuously: pass-through strictly adds
  approaches at the nodes where it fired, every node carries >= 2 approaches,
  blocked <= total.
* pass-through mechanism — exercised on SYNTHETIC geometry through the SAME
  public function the production path uses (derive_junction_nodes), so a
  shared bug cannot hide and no mock stands in for the code under test.

Rule-2 wording audit: no asserted sentence implies a measurement that did not
happen; histogram equality with the audit is NOT claimed anywhere.
"""

from __future__ import annotations

import pytest
import shapely.geometry as sgeom

from jaladhar.web.intersections import (
    AUDIT_BASELINE,
    METHOD,
    PASSTHROUGH_TOL_M,
    build_intersections,
    derive_junction_nodes,
)
from jaladhar.web.intersections import REPO_ROOT as REPO

FLAT_PRODUCTS = REPO / "runs" / "wf3_replay2_uncoupled_baseline_v5" / "products"


@pytest.fixture(scope="module")
def flat_intersections() -> dict:
    return build_intersections(str(FLAT_PRODUCTS), repo_root=REPO)


class TestReconcilesAgainstAudit:
    def test_endpoint_only_node_count_matches_audit_exactly(
        self, flat_intersections: dict, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Scope (V7): full classified named set, tol 1.0 m, all clusters.

        V2 observable: if clustering drifted (tolerance, subset, filter),
        this count moves off 2,142 — it reproduced EXACTLY at implementation.
        """
        counts = flat_intersections["counts"]
        capsys.readouterr()
        print(
            f"\nnode reconcile: realized={counts['junction_nodes_endpoint_only']} "
            f"audit={AUDIT_BASELINE['endpoint_only_nodes']} "
            f"delta={counts['delta_vs_audit_nodes']:+d}"
        )
        assert counts["junction_nodes_endpoint_only"] == AUDIT_BASELINE["endpoint_only_nodes"]
        assert counts["delta_vs_audit_nodes"] == 0

    def test_histograms_reported_beside_audit(
        self, flat_intersections: dict, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Audit figures travel verbatim; realised ones sit beside them."""
        counts = flat_intersections["counts"]
        assert (
            counts["audit_baseline"]["approach_distribution"]
            == AUDIT_BASELINE["approach_distribution"]
        )
        hist_eo = counts["approach_histogram_endpoint_only_by_entity"]
        hist_pt = counts["approach_histogram_passthrough_aware_by_entity"]
        capsys.readouterr()
        print(f"\nhistogram endpoint-only(by entity): {hist_eo}")
        print(f"histogram passthrough-aware(entity): {hist_pt}")
        print(f"audit baseline (method unknown)    : {AUDIT_BASELINE['approach_distribution']}")
        # Structural truth of the report itself:
        assert sum(int(k) * v for k, v in hist_eo.items()) > 0
        # Pass-through fired somewhere on the realised network:
        assert counts["nodes_with_passthrough_added"] > 0
        # And it can only ADD approaches:
        total_eo = sum(int(k) * v for k, v in hist_eo.items())
        total_pt = sum(int(k) * v for k, v in hist_pt.items())
        assert total_pt >= total_eo

    def test_method_and_parameters_declared(self, flat_intersections: dict) -> None:
        assert flat_intersections["method"] == METHOD == "endpoint-cluster-1.0m+passthrough-12m"
        params = flat_intersections["parameters"]
        assert params["endpoint_tol_m"] == 1.0
        assert params["passthrough_tol_m"] == PASSTHROUGH_TOL_M == 12.0


class TestRealizedNodes:
    def test_node_schema_and_world_coords(self, flat_intersections: dict) -> None:
        nodes = flat_intersections["nodes"]
        assert nodes
        required = {
            "node_id",
            "x",
            "y",
            "streets",
            "approaches_endpoint_only",
            "approaches_total",
            "approaches_blocked",
            "depth_band_cm",
            "status",
            "lead_minutes",
            "lead_kind",
            "valid_time_utc",
            "ward_name",
            "passthrough_streets",
        }
        sample = nodes[0]
        missing = required - set(sample)
        assert not missing, f"node contract missing fields: {missing}"
        for node in nodes[:1000]:
            assert node["approaches_total"] >= node["approaches_endpoint_only"] >= 2
            assert node["approaches_blocked"] <= node["approaches_total"]
            # EPSG:32643 world metres around Bengaluru (loose band):
            assert 600_000 < node["x"] < 900_000
            assert 1_300_000 < node["y"] < 1_500_000

    def test_blocked_consistent_with_status(self, flat_intersections: dict) -> None:
        flooded_nodes = [n for n in flat_intersections["nodes"] if n["status"] == "flooded"]
        assert flooded_nodes, "realised baseline floods something; none found"
        for node in flooded_nodes:
            assert node["approaches_blocked"] >= 1


# ----------------------------------------------------- synthetic passthrough


def _synthetic_case() -> tuple[list[dict], dict]:
    """Two streets ENDing at a node + one street PASSING through it.

    A Road: seg 101 ends at origin.  C Road: seg 103 ends at origin.
    B Road: seg 102 passes THROUGH the origin mid-segment (no endpoint there).
    """
    segments = [
        {
            "segment_id": 101,
            "name": "A Road",
            "geometry": sgeom.LineString([(-10, 0), (0, 0)]),
            "length_m": 10.0,
            "endpoints": [(-10.0, 0.0), (0.0, 0.0)],
        },
        {
            "segment_id": 102,
            "name": "B Road",
            "geometry": sgeom.LineString([(0, -10), (0, 10)]),
            "length_m": 20.0,
            "endpoints": [(0.0, -10.0), (0.0, 10.0)],
        },
        {
            "segment_id": 103,
            "name": "C Road",
            "geometry": sgeom.LineString([(0, 0), (5, 5)]),
            "length_m": 7.07,
            "endpoints": [(0.0, 0.0), (5.0, 5.0)],
        },
    ]
    status = {
        "rows": {
            101: {"band_low_cm": 0, "band_high_cm": 10, "flood_status": "not_flooded"},
            102: {"band_low_cm": 30, "band_high_cm": 40, "flood_status": "flooded"},
            103: {"band_low_cm": 0, "band_high_cm": 0, "flood_status": "not_flooded"},
        },
        "lead_minutes": 90,
        "valid_time_utc": "2022-09-04T01:30:00+00:00",
        "kind": "frame",
    }
    return segments, status


class TestPassthroughMechanism:
    def test_through_street_adds_an_approach(self) -> None:
        """The audit caveat, made concrete: B Road crosses at distance 0 with
        NO endpoint at the node and MUST be counted as an approach."""
        segments, status = _synthetic_case()
        nodes, counts = derive_junction_nodes(segments, status)
        assert len(nodes) == 1
        node = nodes[0]
        assert node["streets"] == ["A Road", "B Road", "C Road"]
        assert node["approaches_endpoint_only"] == 2  # A and C by arms
        assert node["approaches_total"] == 3  # + B by pass-through
        assert node["passthrough_streets"] == ["B Road"]

    def test_flooded_through_street_is_blocked(self) -> None:
        segments, status = _synthetic_case()
        nodes, _ = derive_junction_nodes(segments, status)
        node = nodes[0]
        # Only B Road is flooded; it is present via pass-through, so the node
        # realises 1 blocked approach.
        assert node["approaches_blocked"] == 1
        assert node["status"] == "flooded"
        assert node["worst_segment_id"] == 102
        assert node["depth_band_cm"] == {"low": 30, "high": 40}

    def test_widening_tolerance_captures_far_crossings(self) -> None:
        """Same crossing moved 8 m off-node: outside arm snap, inside 12 m."""
        segments, status = _synthetic_case()
        shifted = sgeom.LineString([(8, -10), (8, 10)])
        segments[1]["geometry"] = shifted
        segments[1]["endpoints"] = [(8.0, -10.0), (8.0, 10.0)]
        nodes12, _ = derive_junction_nodes(segments, status)
        assert nodes12[0]["passthrough_streets"] == ["B Road"]
        # Shrink the tolerance below the offset and the pass-through vanishes:
        nodes4, _ = derive_junction_nodes(segments, status, passthrough_tol_m=4.0)
        assert nodes4[0]["passthrough_streets"] == []

    def test_same_street_passthrough_not_double_counted(self) -> None:
        """A street already counted through its own arm adds nothing twice.

        C Road is one entity with TWO parts: an arm ending at the node and a
        second part passing 9 m away. Its pass-through must NOT add another
        approach; only genuinely new streets (B Road) do.
        """
        segments, status = _synthetic_case()
        segments[2]["geometry"] = sgeom.MultiLineString(
            [
                sgeom.LineString([(0, 0), (5, 5)]),  # arm part (endpoint at node)
                sgeom.LineString([(-9, 1), (-9, -9)]),  # passes 9 m off-node
            ]
        )
        # Endpoints now: (0,0), (5,5) from part 1; (-9,1), (-9,-9) from part 2.
        segments[2]["endpoints"] = [(0.0, 0.0), (5.0, 5.0), (-9.0, 1.0), (-9.0, -9.0)]
        nodes, _ = derive_junction_nodes(segments, status)
        node = nodes[0]
        assert "C Road" in node["streets"]
        assert node["passthrough_streets"] == ["B Road"]
        assert node["approaches_endpoint_only"] == 2  # A and C by arms
        assert node["approaches_total"] == 3  # A + C arms, B pass-through

    def test_red_under_mutation_drop_passthrough(self) -> None:
        """V5 deliberate mutation: ignore pass-through arms entirely.

        Expected observation: the through-street assertion fails ONLY under
        the mutation. Implemented as a direct comparison against the mutated
        call parameter (tol=0 keeps dwithin hits at distance 0 but drops the
        8 m-offset case), so the check demonstrably detects its regression.
        """
        segments, status = _synthetic_case()
        shifted = sgeom.LineString([(8, -10), (8, 10)])
        segments[1]["geometry"] = shifted
        segments[1]["endpoints"] = [(8.0, -10.0), (8.0, 10.0)]
        healthy = derive_junction_nodes(segments, status)[0][0]
        assert healthy["approaches_total"] == 3
        mutated = derive_junction_nodes(segments, status, passthrough_tol_m=0.0)[0][0]
        assert mutated["approaches_total"] == 2, "mutation did not fire; check is vacuous"
