"""WF-6 build lane L3 — causal-link payload (requirement A5).

Verification scope is stated beside every check (V7). Independent observables
(V2) are named per test:

* predicted-set size — reconciled across THREE sources: the manifest's declared
  count, a direct fiona recount of the gpkg ``capacity_basis`` bytes performed
  INSIDE this test (not through the module under test), and the reader's
  realised set. If the module's extraction regressed, the recount disagrees.
* proximity mapping — production uses a vectorised numpy segment-distance
  path; this test resolves expectations with SHAPELY ``distance`` (a
  different mechanism entirely), so a shared bug cannot hide.
* red-under-mutation (V5) lives in ``TestRedUnderMutation`` and names its
  deliberate mutations in docstrings.

Rule-2 wording audit: no asserted sentence may imply a measurement that did
not happen; the predicted statement must carry the literal disclaimer.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import fiona
import pytest
import shapely.geometry as sgeom

from jaladhar.web import drains as drains_mod
from jaladhar.web.drains import (
    CAUSAL_PROXIMITY_M,
    NONE_MEASURED_STATEMENT,
    DrainSnapshotReader,
)

REPO = Path(__file__).resolve().parents[2]
GPKG = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"
MANIFEST = REPO / "runs" / "drain_graph_build" / "manifest.json"
NODE_BIN_CANDIDATES = (Path("/home/darshil/.nvm/versions/node/v24.15.0/bin/node"),)


# --------------------------------------------------------------- helpers (independent of drains.py)


def _gpkg_flagged_edges() -> dict[int, dict]:
    """Direct byte-level recount of the predicted set, independent of the
    module under test (V2 observable #1)."""
    flagged: dict[int, dict] = {}
    with fiona.open(GPKG, layer="drain_edges") as src:
        for feat in src:
            props = feat.get("properties") or {}
            basis = props.get("capacity_basis")
            if isinstance(basis, str) and "demand_exceeds_capacity" in basis:
                flagged[int(props["edge_id"])] = {
                    "from_node": int(props["from_node"]),
                    "to_node": int(props["to_node"]),
                    "capacity_basis": basis,
                    "geometry": feat.get("geometry"),
                }
    return flagged


def _gpkg_nodes_xy() -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    with fiona.open(GPKG, layer="drain_nodes") as src:
        for feat in src:
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates")
            if coords:
                out[int(props["node_id"])] = (float(coords[0]), float(coords[1]))
    return out


def _edge_linestring(geometry: dict | None) -> sgeom.LineString:
    coords = [(float(p[0]), float(p[1])) for p in ((geometry or {}).get("coordinates") or [])]
    return sgeom.LineString(coords)


def _probe_pair() -> tuple[int, list[float], list[float]]:
    """Deterministically pick ONE flagged edge plus two synthetic probe points
    derived from its first vertex (both derived from gpkg bytes inside this
    test, never from module state):

    * near point (+10 m east): shapely says the nearest DRAIN EDGE overall is
      the chosen flagged one, and its nearest FLAGGED edge is <=20 m away --
      well inside the 25 m tolerance;
    * far point (+60 m east): shapely distance to the nearest FLAGGED edge is
      >40 m -- outside the tolerance with quantisation margin to spare.

    Tolerances stated (V7): float32 storage quantises UTM-magnitude
    coordinates by <=~0.13 m; the 10/20/25 and 40/25 separations dominate it
    by >100x and >100x respectively.
    """
    flagged = _gpkg_flagged_edges()
    all_lines = []
    with fiona.open(GPKG, layer="drain_edges") as src:
        for feat in src:
            line = _edge_linestring(feat.get("geometry"))
            if len(line.coords):
                all_lines.append(line)
    for eid in sorted(flagged):
        line = _edge_linestring(flagged[eid]["geometry"])
        vx, vy = line.coords[0]
        near, far = [vx + 10.0, vy], [vx + 60.0, vy]
        p_near, p_far = sgeom.Point(near), sgeom.Point(far)
        nearest_overall = min(all_lines, key=lambda ln: ln.distance(p_near))
        if nearest_overall.distance(p_near) != line.distance(p_near):
            continue
        d_flag_near = min(
            p_near.distance(_edge_linestring(f["geometry"])) for f in flagged.values()
        )
        d_flag_far = min(p_far.distance(_edge_linestring(f["geometry"])) for f in flagged.values())
        if d_flag_near <= 20.0 and d_flag_far >= 40.0:
            return eid, near, far
    raise AssertionError("no usable probe pair found in realised drain graph")


# --------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def reader() -> DrainSnapshotReader:
    return DrainSnapshotReader()


@pytest.fixture(scope="module")
def probe() -> tuple[int, list[float], list[float]]:
    return _probe_pair()


@pytest.fixture(scope="module")
def predicting_reader(probe: tuple) -> DrainSnapshotReader:
    _, near, _ = probe
    return DrainSnapshotReader(geometry_provider=lambda segment_id: near)


@pytest.fixture(scope="module")
def causal_payload(predicting_reader: DrainSnapshotReader) -> dict:
    return predicting_reader.causal_for_segment("wf6-test-segment")


# --------------------------------------------------------------- predicted set


class TestPredictedSetFromBytes:
    def test_size_reconciles_manifest_gpkg_and_reader(self, reader: DrainSnapshotReader) -> None:
        """Scope: the whole predicted set over the realised drain graph build.

        V2: three independent sources must agree at 70 — the manifest's
        declared count, this test's own fiona recount, and the reader's
        realised set. If the module mis-extracted the flag, the recount would
        move off the reader's number while the manifest stayed put.
        """
        declared = json.loads(MANIFEST.read_text("utf-8"))["capacity_metrics"][
            "n_edges_demand_exceeds_capacity"
        ]
        recounted = len(_gpkg_flagged_edges())
        realized = len(reader.get().flagged_edges)
        assert declared == 70
        assert recounted == declared, "test recount vs manifest declaration"
        assert realized == recounted, "reader set vs independent recount"

    def test_provenance_block_hashes_manifest_bytes(self, causal_payload: dict) -> None:
        """Scope: the predicted_set provenance block of a predicted payload.

        V1: the sha256 must equal a hash recomputed here from the manifest's
        raw bytes; if the module hashed something else (or a cached string),
        this diverges.
        """
        block = causal_payload["predicted_set"]
        assert block["size"] == 70
        assert block["source"] == "runs/drain_graph_build/manifest.json"
        assert block["sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        assert len(block["sha256"]) == 64

    def test_measured_status_inert_under_realized_state(self, reader: DrainSnapshotReader) -> None:
        """Scope: today's realised snapshot (coupling_enabled=None, no coupled
        run). No causal payload may declare status 'measured' — if the
        measured branch were reachable without per-street events, this would
        redden."""
        snapshot = reader.get()
        assert snapshot.surcharge.get("available") is False
        for segment_id in ("wf6-test-segment", "", "99999"):
            payload = reader.causal_for_segment(segment_id)
            assert payload["status"] != "measured", segment_id


class TestProximityMapping:
    def test_synthetic_point_near_flagged_edge_resolves_it(
        self, causal_payload: dict, probe: tuple
    ) -> None:
        """Scope: one synthetic street point 10 m east of a known flagged
        edge's first vertex (coordinates pulled from the gpkg in this test).

        V2: expectation derived with shapely, exercised against the module's
        numpy path. If CRS handling, units, or the distance math were wrong,
        the chosen edge would not appear (or a different one would win).
        """
        eid, _, _ = probe
        assert causal_payload["status"] == "predicted"
        assert eid in causal_payload["matched_flagged_edge_ids"]
        assert causal_payload["statement"] == (
            f"Predicted overcapacity nearby: drain edge "
            f"{causal_payload['nearest_flagged_edge_id']} exceeds design capacity "
            "(cited design storm) — not a measured surcharge."
        )
        assert (
            causal_payload["nearest_flagged_edge_id"] in causal_payload["matched_flagged_edge_ids"]
        )

    def test_predicted_nodes_carry_real_coordinates_and_basis(
        self, causal_payload: dict, probe: tuple
    ) -> None:
        """Scope: node records of the predicted payload vs the gpkg node layer
        read independently here.

        Tolerance (V7): coordinates are stored float32; at ~1.4e6 m magnitude
        quantisation is <=~0.17 m, asserted at 0.25 m. world_xy must land on
        the REALISED node position — invented coordinates fail this.
        """
        nodes_by_id = {n["node_id"]: n for n in causal_payload["nodes"]}
        assert nodes_by_id, "predicted payload carried no nodes"
        gpkg_nodes = _gpkg_nodes_xy()
        flagged = _gpkg_flagged_edges()
        for node_id, node in nodes_by_id.items():
            gx, gy = gpkg_nodes[node_id]
            wx, wy = node["world_xy"]
            assert abs(wx - gx) <= 0.25 and abs(wy - gy) <= 0.25, node_id
            assert node["demand_exceeds_capacity"] is True
            touched = set(node["edge_ids"]) & set(flagged)
            assert touched, f"node {node_id} cites no flagged edge"
            for eid in touched:
                assert (
                    flagged[eid]["from_node"] == node_id or flagged[eid]["to_node"] == node_id
                ), f"node {node_id} does not touch edge {eid}"
            basis = node.get("capacity_basis")
            assert isinstance(basis, dict)
            assert str(basis.get("demand_exceeds_capacity", "")).startswith("true")

    def test_far_point_stays_honest_empty_state(
        self, probe: tuple, reader: DrainSnapshotReader
    ) -> None:
        """Scope: same vertex shifted 60 m east — beyond the 25 m tolerance
        from every flagged edge (shapely-verified >=40 m in the probe).

        If the tolerance were silently inflated (or distance units broken),
        this would flip to 'predicted' and redden.
        """
        eid, _, far = probe
        del eid
        payload = DrainSnapshotReader(geometry_provider=lambda _sid: far).causal_for_segment(
            "wf6-far-probe"
        )
        assert payload["status"] == "none_measured"
        assert payload["statement"] == NONE_MEASURED_STATEMENT
        assert payload["nodes"] == []

    @pytest.mark.parametrize("bad_return", [None, [], [91.0], ["a", "b"], [float("nan"), 0.0]])
    def test_degenerate_geometry_degrades_not_guesses(
        self, reader: DrainSnapshotReader, bad_return
    ) -> None:
        """Scope: provider contract violations (absent/malformed/non-finite).
        Each must yield the honest empty state with the EXACT default
        sentence — never a guessed location, never a fabricated claim."""

        def provider(_segment_id):
            return bad_return

        payload = DrainSnapshotReader(geometry_provider=provider).causal_for_segment("x")
        assert payload["status"] == "none_measured"
        assert payload["statement"] == NONE_MEASURED_STATEMENT

    def test_absent_provider_states_exact_default_sentence(
        self, reader: DrainSnapshotReader
    ) -> None:
        """Scope: integrator has not injected a geometry provider. The empty
        state sentence must be byte-identical to the designed wording (the UI
        renders it verbatim)."""
        payload = reader.causal_for_segment("any-segment")
        assert payload["status"] == "none_measured"
        assert payload["statement"] == NONE_MEASURED_STATEMENT
        assert payload["statement"] == (
            "No measured surcharge for this street — coupled run pending."
        )


class TestRuleTwoWordingAudit:
    def test_predicted_statement_disclaims_measurement(self, causal_payload: dict) -> None:
        """Scope: every sentence the builder can emit for a predicted payload.
        Rule 2: nothing may imply a measurement happened. The literal
        disclaimer 'not a measured surcharge' must be present and the sentence
        must lead with 'Predicted', not 'Measured'."""
        statement = causal_payload["statement"]
        assert "not a measured surcharge" in statement
        assert statement.startswith("Predicted overcapacity nearby:")
        for banned in ("measured surcharge recorded", "observed flooding"):
            assert banned not in statement.lower().replace("not a measured surcharge", "")

    def test_tolerance_is_the_documented_25m(self, causal_payload: dict) -> None:
        """Scope: the payload names its own tolerance and CRS so the screen can
        state them; they must be the documented constants, not drifted."""
        assert causal_payload["proximity_m"] == CAUSAL_PROXIMITY_M == 25.0
        assert causal_payload["crs"] == "EPSG:32643"


class TestRedUnderMutation:
    """V5: deliberate mutations must redden the checks above. Mutations are
    named in docstrings and applied via monkeypatch — the production module is
    never edited."""

    def test_flipped_proximity_comparison_reddens_resolution(
        self, monkeypatch: pytest.MonkeyPatch, probe: tuple
    ) -> None:
        """MUTATION: invert the proximity comparison operator
        (``distance_m <= tolerance_m`` becomes ``distance_m > tolerance_m``)
        in the single comparison site ``_distance_within``.

        The acceptance assertion below is the SAME one that is green in
        TestProximityMapping; under the flipped predicate the near probe
        resolves to none_measured, so the assert FAILS — demonstrating the
        check detects an operator flip rather than passing vacuously.
        """
        eid, near, _ = probe
        mutated_reader = DrainSnapshotReader(geometry_provider=lambda _sid: near)

        def flipped(distance_m: float, tolerance_m: float = CAUSAL_PROXIMITY_M) -> bool:
            return distance_m > tolerance_m  # <-- THE MUTATION

        monkeypatch.setattr(drains_mod, "_distance_within", flipped)
        payload = mutated_reader.causal_for_segment("mutation-probe")
        with pytest.raises(AssertionError):
            assert payload["status"] == "predicted"
            assert eid in payload["matched_flagged_edge_ids"]

    def test_zeroed_tolerance_reddens_resolution(
        self, monkeypatch: pytest.MonkeyPatch, probe: tuple
    ) -> None:
        """MUTATION: collapse the tolerance to 0.0 (exact-hit-only matching).
        A 10 m-offset probe no longer matches; the same green-elsewhere
        assertion fails here."""
        eid, near, _ = probe
        mutated_reader = DrainSnapshotReader(geometry_provider=lambda _sid: near)

        def zero_tol(distance_m: float, tolerance_m: float = 0.0) -> bool:
            return distance_m <= tolerance_m  # <-- THE MUTATION

        monkeypatch.setattr(drains_mod, "_distance_within", zero_tol)
        payload = mutated_reader.causal_for_segment("mutation-probe")
        with pytest.raises(AssertionError):
            assert payload["status"] == "predicted"
            assert eid in payload["matched_flagged_edge_ids"]

    def test_unmutated_control_is_green(
        self, monkeypatch: pytest.MonkeyPatch, probe: tuple
    ) -> None:
        """Control for the two mutations above: the untouched predicate keeps
        them green — proving the reddening comes from the mutation, not from
        the patching mechanism itself."""
        eid, near, _ = probe
        intact_reader = DrainSnapshotReader(geometry_provider=lambda _sid: near)
        monkeypatch.setattr(drains_mod, "_distance_within", drains_mod._distance_within)  # identity
        payload = intact_reader.causal_for_segment("control-probe")
        assert payload["status"] == "predicted"
        assert eid in payload["matched_flagged_edge_ids"]


class TestCausalJsSyntax:
    def test_node_check_parses_module(self, tmp_path: Path) -> None:
        """Scope: the full causal.js source parses under Node's parser.
        Copied to .mjs so `node --check` treats it as an ES module (the repo
        ships no package.json beside static/js). BLOCKED gap if no node
        binary exists: install Node >=18 to close."""
        source = REPO / "src/jaladhar/web/static/js/causal.js"
        target = tmp_path / "causal.mjs"
        shutil.copyfile(source, target)
        binary = next((b for b in NODE_BIN_CANDIDATES if b.is_file()), shutil.which("node"))
        if binary is None:
            pytest.skip("BLOCKED: no node binary found — install Node >=18 to close")
        result = subprocess.run(  # noqa: S603 - fixed argv, repo-local binary
            [str(binary), "--check", str(target)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
