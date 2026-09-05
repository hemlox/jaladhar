"""tests/drainage/test_terminal.py — WF-2 M1 terminal-seed resolution suite.

Red→green coverage for ``jaladhar.drainage.terminal`` (definition v2-lake-boundary,
``configs/terminal_definition.yaml``):

- synthetic N<=12-node chains terminating INSIDE a fake lake polygon and at the
  domain boundary minus tolerance — each flips dead_end -> outfall_terminating;
- the tolerance predicate is exercised on BOTH sides: a node at EXACTLY the
  configured tolerance fires (inclusive d <= tol), a node at tolerance+epsilon
  does not;
- ``counts_by_rule`` partitions the seed union EXACTLY (declared > lake >
  boundary priority; no double-count of a declared passthrough node);
- V8 CROSS-ASSERTION: the torch BFS behind router._compute_component_classes and
  the canonical stdlib :func:`classify_reachability` produce the SAME partition
  on a toy graph assembled through ``build_drain_graph``;
- REAL-BYTES ANCHOR (single integration point): on the realized producer bytes
  nodes 1183 / 1512 become ``lake_polygon`` terminal seeds and the
  ``domain_boundary`` rule adds ZERO seeds — skip→BLOCKED naming the missing
  artefact if any input is absent;
- V5 MUTATION PROBES on /tmp/opencode COPIES of terminal.py (repo untouched):
  * M-T1 — inclusive '<=' flipped to strict '<' in ``_within_tolerance``;
  * M-T2 — the lake rule dropped (``if tdef.lake_enabled:`` -> ``if False and ...``).
  Each mutation must redden its NAMED assertion below; ids are recorded here.

V7 SCOPE (per test, beside each claim): synthetic scenarios run on <=12-node
fixtures at ONE tolerance point each (tol=10.0) plus single-point epsilon
probes — they exercise the PREDICATE and PARTITION logic, NOT city-scale
geometry; the single real-graph anchor covers realized bytes once (1721 nodes).
The sensitivity SWEEP itself lives in the post-hoc split product script, not
here; this suite cannot catch a wrong sweep grid.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from jaladhar.drainage.terminal import (
    RULE_BOUNDARY,
    RULE_DECLARED,
    RULE_LAKE,
    GridBlock,
    TerminalDefinitionError,
    classify_reachability,
    load_terminal_definition,
    parse_grid_block,
    resolve_terminal_nodes,
    with_tolerances,
)

REPO = Path(__file__).resolve().parents[2]
TDEF_PATH = REPO / "configs" / "terminal_definition.yaml"
MUTATION_DIR = Path("/tmp/opencode/wf2_m1_mutations")

# Toy grid: transform [10, 0, 0, 0, -10, 1000] over 100x100 cells -> x[0,1000],
# y[0,1000]. Boundary distances are then |x|, |1000-x|, |y|, |1000-y|.
TOY_GRID: dict = {
    "height": 100,
    "width": 100,
    "transform": [10.0, 0.0, 0.0, 0.0, -10.0, 1000.0],
    "cell_area_m2": 100.0,
}


# ---------------------------------------------------------------------------
# Fixtures (synthetic, written fresh into tmp_path — never repo data)
# ---------------------------------------------------------------------------


def _write_lake_gpkg(path: Path, name: str = "Fake Lake") -> Path:
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"name": [name]}, geometry=[box(400.0, 400.0, 600.0, 600.0)], crs="EPSG:32643"
    )
    gdf.to_file(path, layer="osm_water_polygons", driver="GPKG")
    return path


def _write_tdef_yaml(
    tmp_path: Path,
    gpkg_path: Path,
    *,
    lake_tol_m: float = 10.0,
    bnd_tol_m: float = 10.0,
    require_sink: bool = True,
) -> Path:
    """A minimal-but-complete definition exercising EVERY validated key."""
    p = tmp_path / "terminal_definition.yaml"
    p.write_text(f"""
definition_version: "v2-lake-boundary-test"
rules:
  declared_gpkg_outfall:
    source: "synthetic fixture: none declared"
  lake_polygon:
    enabled: true
    require_sink: {str(require_sink).lower()}
    source_path: "{gpkg_path.as_posix()}"
    layer: "osm_water_polygons"
    crs: "EPSG:32643"
    name_field: "name"
    selection:
      mode: "exact-name"
      names: ["Fake Lake"]
    snap_tolerance_m: {lake_tol_m}
    justification: "test fixture quoting the owner suspicion verbatim."
  domain_boundary:
    enabled: true
    require_sink: {str(require_sink).lower()}
    bbox_source: "graph_manifest.config_snapshot.grid"
    tolerance_m: {bnd_tol_m}
    justification: "test fixture quoting the owner suspicion verbatim."
sensitivity_tolerances_m: [0, 5, 10, 25, 50]
provenance:
  pointer: "{(REPO / 'runs' / 'terrain_water' / 'manifest.json').as_posix()}"
""")
    return p


@pytest.fixture()
def tdef(tmp_path):
    gpkg = _write_lake_gpkg(tmp_path / "fake_lakes.gpkg")
    return load_terminal_definition(_write_tdef_yaml(tmp_path, gpkg))


# ---------------------------------------------------------------------------
# Aggregated validation of the definition loader (rule-7 style)
# ---------------------------------------------------------------------------


class TestDefinitionLoading:
    def test_real_repo_definition_loads(self):
        """V7 scope: the ONE real config file, every validated key touched."""
        tdef = load_terminal_definition(TDEF_PATH)
        assert tdef.definition_version == "v2-lake-boundary"
        assert tdef.lake_selection_names == ("Bellandur Lake", "Varthur Lake")
        assert tdef.lake_snap_tolerance_m == 10.0
        assert tdef.boundary_tolerance_m == 10.0
        assert tdef.sensitivity_tolerances_m == (0.0, 5.0, 10.0, 25.0, 50.0)

    def test_missing_file_refuses_immediately(self, tmp_path):
        with pytest.raises(TerminalDefinitionError, match="not found"):
            load_terminal_definition(tmp_path / "absent.yaml")

    def test_many_defects_aggregate_into_one_error(self, tmp_path):
        """Observable: if aggregation were broken (fail-fast), the error text
        would name ONE of the planted defects while others stay silent."""
        raw = TDEF_PATH.read_text()
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            raw.replace("snap_tolerance_m: 10.0", "snap_tolerance_m: -1.0")
            .replace('names: ["Bellandur Lake", "Varthur Lake"]', "names: []")
            .replace(
                'bbox_source: "graph_manifest.config_snapshot.grid"',
                'bbox_source: "somewhere_else"',
            )
        )
        with pytest.raises(TerminalDefinitionError) as ei:
            load_terminal_definition(bad)
        msg = str(ei.value)
        assert "snap_tolerance_m" in msg
        assert "selection.names" in msg
        assert "bbox_source" in msg
        assert len(ei.value.problems) >= 3, "ALL planted defects named in ONE error"


# ---------------------------------------------------------------------------
# Synthetic classification scenarios (N <= 12 nodes, one tolerance point)
# ---------------------------------------------------------------------------


def _resolve(nodes_xy, edge_pairs, declared, tdef, grid=TOY_GRID):
    return resolve_terminal_nodes(
        node_xy=nodes_xy,
        edge_pairs=edge_pairs,
        declared_outfall_ids=declared,
        tdef=tdef,
        grid_block=grid,
    )


class TestSyntheticClassification:
    def test_chain_into_fake_lake_flips_dead_end_to_outfall_terminating(self, tdef):
        """Chain 1->2->3 with node 3 INSIDE the fake lake (dist 0.0), no declared
        outfalls. V7 scope: 3-node directed chain, tol=10 only.

        Observable (V2): under v1 declared-only semantics ALL THREE nodes are
        dead_end; under the extended definition node 3 seeds (lake_polygon) and
        reachability pulls 1 and 2 into outfall_terminating."""
        xy = [(200.0, 500.0), (300.0, 500.0), (500.0, 500.0)]
        pairs = [(1, 2), (2, 3)]

        # v1 baseline: no extended rules -> nothing reaches a terminal seed
        v1_classes = classify_reachability(pairs, [])
        assert v1_classes == set(), "v1: chain reaching nowhere is all dead_end"

        res = _resolve(xy, pairs, [], tdef)
        assert res.seeds_by_rule[RULE_LAKE] == [3]
        assert res.seeds_by_rule[RULE_BOUNDARY] == []
        assert res.counts_by_rule == {RULE_DECLARED: 0, RULE_LAKE: 1, RULE_BOUNDARY: 0}
        assert res.seed_union == [3]
        rec3 = res.records[3]
        assert rec3.rule_fired == RULE_LAKE and rec3.is_sink and rec3.dist_to_lake_m == 0.0
        # the flip, end to end:
        v2 = classify_reachability(pairs, res.seed_union)
        assert v2 == {1, 2, 3}, "chain terminating in the lake HAS reached a terminal sink"

    def test_boundary_minus_tolerance_flip_inclusive_at_exact_tolerance(self, tdef):
        """Chain 1->2->3 with node 3 at x=990 -> EXACTLY 10.0 m from the east
        grid edge (= configured boundary tolerance). Inclusive predicate: it
        MUST fire. V7 scope: 3-node chain, single tolerance point."""
        xy = [(700.0, 500.0), (800.0, 500.0), (990.0, 500.0)]
        pairs = [(1, 2), (2, 3)]
        res = _resolve(xy, pairs, [], tdef)
        assert res.seeds_by_rule[RULE_BOUNDARY] == [3]
        rec3 = res.records[3]
        assert rec3.dist_to_boundary_m == 10.0
        assert classify_reachability(pairs, res.seed_union) == {1, 2, 3}

    def test_boundary_tolerance_plus_epsilon_stays_dead_end(self, tdef):
        """Node at tolerance + epsilon (10.001 m) must NOT fire — the predicate
        is exercised on BOTH sides. V7 scope: single node, single offset."""
        xy = [(989.999, 500.0)]
        res = _resolve(xy, [], [], tdef)
        assert res.records[1].rule_fired is None
        assert res.records[1].dist_to_boundary_m == pytest.approx(10.001, abs=1e-9)
        assert res.seed_union == []

    def test_lake_tolerance_plus_epsilon_stays_dead_end(self, tdef):
        """Lake edge at x=600, tol=10: x=610.0 fires, x=610.001 does not."""
        res_fire = _resolve([(610.0, 500.0)], [], [], tdef)
        assert res_fire.records[1].rule_fired == RULE_LAKE
        res_stay = _resolve([(610.001, 500.0)], [], [], tdef)
        assert res_stay.records[1].rule_fired is None

    def test_require_sink_non_sink_node_near_lake_does_not_fire(self, tdef):
        """A node inside the lake that still has an outgoing edge has NOT
        terminated there — require_sink=true keeps it non-terminal. V7 scope:
        2-node chain fully inside the polygon."""
        xy = [(500.0, 500.0), (520.0, 520.0)]
        res = _resolve(xy, [(1, 2)], [], tdef)
        assert res.records[1].is_sink is False and res.records[1].rule_fired is None
        assert res.records[2].is_sink is True and res.records[2].rule_fired == RULE_LAKE

    def test_declared_passthrough_no_double_count_and_partition_exact(self, tdef):
        """A DECLARED node sitting inside the lake keeps rule_fired=RULE_DECLARED
        (priority), appears ONCE in the union, and counts partition exactly.
        V7 scope: 3-node fixture with one declared passthrough."""
        xy = [(200.0, 500.0), (500.0, 500.0), (990.0, 500.0)]
        # node 1 flows INTO declared node 2 (in the lake); node 3 is a boundary sink
        pairs = [(1, 2)]
        res = _resolve(xy, pairs, [2], tdef)
        assert res.records[2].rule_fired == RULE_DECLARED
        assert res.counts_by_rule[RULE_LAKE] == 0, "no lake double-count of the declared node"
        assert res.counts_by_rule[RULE_BOUNDARY] == 1
        total_seeded = sum(res.counts_by_rule.values())
        assert total_seeded == len(res.seed_union) == len(set(res.seed_union))
        labelled = [r.rule_fired for r in res.records.values() if r.rule_fired]
        assert len(labelled) == total_seeded, "every node carries AT MOST one label"

    def test_counts_partition_over_a_mixed_twelve_node_fixture(self, tdef):
        """12 nodes mixing declared/lake/boundary/interior/deep-water sinks:
        labels partition EXACTLY and the union is duplicate-free. V7 scope:
        full label space at tol=10 only."""
        xy = [
            (200.0, 700.0),  # 1 interior sink, near nothing -> dead_end
            (200.0, 701.0),  # 2 flows into 1
            (990.0, 990.0),  # 3 boundary-corner sink
            (450.0, 450.0),  # 4 inside lake, sink
            (450.0, 451.0),  # 5 flows into 4 (non-sink, inside lake)
            (609.0, 500.0),  # 6 lake ring at 9.0 m, sink -> fires
            (611.0, 500.0),  # 7 at 11.0 m, sink -> stays dead_end
            (995.0, 500.0),  # 8 boundary at 5.0 m, sink
            (500.0, 990.0),  # 9 north-edge boundary sink
            (500.0, 978.0),  # 10 at 22 m off north edge -> dead_end
            (100.0, 100.0),  # 11 isolated interior sink
            (200.0, 700.5),  # 12 flows into 1 too (non-sink interior)
        ]
        pairs = [(2, 1), (5, 4), (12, 1), (10, 9)]
        res = _resolve(xy, pairs, [], tdef)
        assert res.seeds_by_rule[RULE_LAKE] == [4, 6]
        assert sorted(res.seeds_by_rule[RULE_BOUNDARY]) == [3, 8, 9]
        assert res.counts_by_rule == {RULE_DECLARED: 0, RULE_LAKE: 2, RULE_BOUNDARY: 3}
        assert res.seed_union == sorted(res.seed_union)
        assert len(set(res.seed_union)) == len(res.seed_union)
        reached = classify_reachability(pairs, res.seed_union)
        # 1/11/12 stay dead_end; 10 flips because it flows into boundary seed 9
        assert reached == {3, 4, 5, 6, 8, 9, 10}, "upstream of every seed flips"

    def test_sensitivity_copy_moves_both_tolerances_together(self, tdef):
        """with_tolerances() is the sweep-point constructor: BOTH tolerances take
        the sweep value; nothing else mutates. V7 scope: constructor behaviour."""
        tight = with_tolerances(tdef, 0.0)
        assert tight.lake_snap_tolerance_m == 0.0 and tight.boundary_tolerance_m == 0.0
        wide = with_tolerances(tdef, 50.0)
        assert wide.lake_snap_tolerance_m == 50.0 and wide.boundary_tolerance_m == 50.0
        assert wide.definition_version == tdef.definition_version


# ---------------------------------------------------------------------------
# V8 cross-assertion: router torch BFS == canonical reference traversal
# ---------------------------------------------------------------------------


class TestCrossTraversalAssertion:
    def test_router_bfs_matches_canonical_on_toy_graph_through_build_drain_graph(self, tdef):
        """V8 at the traversal seam: assemble a toy graph through
        router.build_drain_graph with the EXTENDED seed tensor and assert the
        torch BFS partition equals :func:`classify_reachability` exactly —
        if either traversal drifts, this symmetric difference reddens.
        V7 scope: 4-node toy graph (3-chain + 1 isolated), one code path."""

        import torch

        from jaladhar.coupling.router import build_drain_graph

        # toy geography: chain 1->2->3 ending inside the fake lake, node 4 isolated
        xy = [(200.0, 500.0), (300.0, 500.0), (500.0, 500.0), (900.0, 900.0)]
        pairs = [(1, 2), (2, 3)]
        res = _resolve(xy, pairs, [], tdef)
        assert res.seed_union == [3]

        n = len(xy)
        outfall = torch.zeros(n, dtype=torch.bool)
        for nid in res.seed_union:
            outfall[nid - 1] = True

        g = build_drain_graph(
            edge_from=torch.tensor([u - 1 for u, _ in pairs], dtype=torch.int64),
            edge_to=torch.tensor([v - 1 for _, v in pairs], dtype=torch.int64),
            capacity_bearing=torch.ones(len(pairs), dtype=torch.bool),
            q_cap_nom_m3s=torch.tensor([0.5, 0.5], dtype=torch.float64),
            width_mean_m=torch.full((n,), 6.71, dtype=torch.float64),
            shaft_length_proxy_m=1.0,
            node_elev_m=torch.linspace(10.0, 0.0, n, dtype=torch.float64),
            contrib_area_m2=torch.zeros(n, dtype=torch.float64),
            outfall_node=outfall,
            node_cell_row=torch.tensor([1, 1, 1, 3], dtype=torch.int32),
            node_cell_col=torch.tensor([20, 30, 50, 90], dtype=torch.int32),
            node_id_map=torch.full((4, 100), -1, dtype=torch.int32),
            node_cell_count=torch.tensor([1, 1, 1, 0], dtype=torch.int64),
        )
        torch_ot = {i + 1 for i, c in enumerate(g.component_class) if c == "outfall_terminating"}
        canon_ot = classify_reachability(pairs, res.seed_union)
        assert (
            torch_ot == canon_ot
        ), f"traversal divergence: torch-BFS={sorted(torch_ot)} canonical={sorted(canon_ot)}"
        assert g.component_class[3] == "dead_end", "isolated node stays dead_end"


# ---------------------------------------------------------------------------
# V5 MUTATION PROBES (/tmp copies only — repo untouched)
# ---------------------------------------------------------------------------


def _load_mutant(mutation_id: str, old: str, new: str):
    """Copy terminal.py to /tmp/opencode/wf2_m1_mutations/, apply ONE textual
    mutation, import it standalone (module has no intra-package imports)."""
    import jaladhar.drainage.terminal as term_mod

    src = Path(term_mod.__file__).read_text()
    assert src.count(old) == 1, f"mutation anchor not unique: {old!r}"
    MUTATION_DIR.mkdir(parents=True, exist_ok=True)
    mpath = MUTATION_DIR / f"terminal_{mutation_id}.py"
    mpath.write_text(src.replace(old, new))
    spec = importlib.util.spec_from_file_location(f"terminal_{mutation_id}", mpath)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclasses._process_class resolves cls.__module__ through sys.modules;
    # skipping registration breaks @dataclass at exec time.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _exact_tolerance_lake_scenario(mod, tdef_path: Path) -> None:
    """Named assertion M-T1 must redden: the node at EXACTLY snap_tolerance
    (10.0 m off the lake edge) must fire under the inclusive predicate.

    Uses the FAKE-LAKE fixture definition — the real Bengaluru lakes sit ~1.6 Mm
    away from the toy grid, which would make every predicate variant pass/fail
    for the wrong reason."""
    tdef = mod.load_terminal_definition(tdef_path)
    res = mod.resolve_terminal_nodes(
        node_xy=[(610.0, 500.0)],  # exactly 10.0 m off the x=600 edge
        edge_pairs=[],
        declared_outfall_ids=[],
        tdef=tdef,
        grid_block=TOY_GRID,
    )
    assert (
        res.records[1].rule_fired == mod.RULE_LAKE
    ), "M-T1 named assertion: exact-tolerance node must seed (d <= tol inclusive)"


def _lake_seed_scenario(mod, tdef_path: Path) -> None:
    """Named assertion M-T2 must redden: the in-lake chain terminus must appear
    under seeds_by_rule['lake_polygon']."""
    tdef = mod.load_terminal_definition(tdef_path)
    res = mod.resolve_terminal_nodes(
        node_xy=[(200.0, 500.0), (500.0, 500.0)],
        edge_pairs=[(1, 2)],
        declared_outfall_ids=[],
        tdef=tdef,
        grid_block=TOY_GRID,
    )
    assert res.seeds_by_rule[mod.RULE_LAKE] == [
        2
    ], "M-T2 named assertion: lake rule must seed the in-polygon sink"
    assert 2 in res.seed_union


_PRISTINE_ANCHOR = 'RULE_DECLARED = "declared_gpkg_outfall"'


def _load_pristine_copy(copy_id: str):
    """An UNMUTATED /tmp copy, loaded the same way as the mutants — the control
    proving each named assertion is green before its mutation reddens it."""
    return _load_mutant(copy_id, _PRISTINE_ANCHOR, _PRISTINE_ANCHOR)


class TestMutationProbes:
    """V5: every invariant test here is demonstrated RED under its deliberate
    mutation, executed against a /tmp COPY of terminal.py; the repo file is
    never modified. Mutation ids: M-T1, M-T2."""

    def test_mt1_strict_comparison_reddens_exact_tolerance_assertion(self, tmp_path):
        yml = _write_tdef_yaml(tmp_path, _write_lake_gpkg(tmp_path / "fake_lakes.gpkg"))
        _exact_tolerance_lake_scenario(_load_pristine_copy("mt1_control"), yml)
        mut = _load_mutant(
            "mt1",
            "return dist_m <= tol_m",
            "return dist_m < tol_m",
        )
        with pytest.raises(AssertionError, match="M-T1"):
            _exact_tolerance_lake_scenario(mut, yml)

    def test_mt2_dropped_lake_rule_reddens_lake_seed_assertion(self, tmp_path):
        yml = _write_tdef_yaml(tmp_path, _write_lake_gpkg(tmp_path / "fake_lakes.gpkg"))
        _lake_seed_scenario(_load_pristine_copy("mt2_control"), yml)
        mut = _load_mutant(
            "mt2",
            "if tdef.lake_enabled:\n        lake_geom",
            "if False and tdef.lake_enabled:\n        lake_geom",
        )
        with pytest.raises(AssertionError, match="M-T2"):
            _lake_seed_scenario(mut, yml)


# ---------------------------------------------------------------------------
# REAL-BYTES ANCHOR (the one integration point onto realized state)
# ---------------------------------------------------------------------------

_REAL_ARTEFACTS = (
    REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg",
    REPO / "runs" / "drain_graph_build" / "drain_graph_adjacency.json",
    REPO / "data" / "raw" / "osm" / "osm_water.gpkg",
    TDEF_PATH,
)

pytestmark_real = pytest.mark.skipif(
    not all(p.exists() for p in _REAL_ARTEFACTS),
    reason="BLOCKED: missing realized artefact(s) "
    + ", ".join(str(p) for p in _REAL_ARTEFACTS if not p.exists())
    + " — restore them to un-block this anchor",
)


@pytestmark_real
class TestRealBytesAnchor:
    def test_nodes_1183_1512_become_lake_terminal_boundary_adds_zero(self):
        """On the REALIZED producer bytes: adjacency sinks 1183/1512 sit at 0.0 m
        inside Bellandur/Varthur and become lake_polygon seeds; the
        domain_boundary rule adds ZERO seeds at the configured tolerance (that
        zero IS the answer to suspicion #2's boundary half); the declared-only
        reachable population is 55 and the extended one 68 [F, measured].

        V7 scope: ONE pass over the full realized 1721-node graph at the single
        configured tolerance pair (10.0/10.0); no sweep here."""
        import geopandas as gpd

        adj = json.loads(
            (REPO / "runs" / "drain_graph_build" / "drain_graph_adjacency.json").read_text()
        )
        pairs = [(int(e["from"]), int(e["to"])) for e in adj["edges"].values()]
        nodes = gpd.read_file(
            REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg", layer="drain_nodes"
        )
        xy = list(zip(nodes["x_m"].astype(float), nodes["y_m"].astype(float), strict=True))
        declared = [
            int(i) for i in nodes.loc[nodes["node_type"].astype(str) == "outfall", "node_id"]
        ]
        assert len(declared) == 17

        man = json.loads((REPO / "runs" / "drain_graph_build" / "manifest.json").read_text())
        grid_block = man["config_snapshot"]["grid"]
        gb: GridBlock = parse_grid_block(grid_block)
        assert (gb.width, gb.height) == (3615, 3521)

        tdef = load_terminal_definition(TDEF_PATH)
        res = resolve_terminal_nodes(
            node_xy=xy,
            edge_pairs=pairs,
            declared_outfall_ids=declared,
            tdef=tdef,
            grid_block=grid_block,
        )
        assert res.seeds_by_rule[RULE_LAKE] == [
            1183,
            1512,
        ], "measured smoking guns: both are graph sinks at 0.0 m inside the lakes"
        for nid in (1183, 1512):
            r = res.records[nid]
            assert r.is_sink and r.declared is False
            assert r.rule_fired == RULE_LAKE
            assert r.dist_to_lake_m == pytest.approx(0.0, abs=1e-9)
        assert (
            res.seeds_by_rule[RULE_BOUNDARY] == []
        ), "ZERO boundary seeds at 10.0 m — the answer to suspicion #2's boundary half"
        assert res.counts_by_rule[RULE_DECLARED] == 17
        assert sum(res.counts_by_rule.values()) == len(res.seed_union) == 19

        v1_reached = classify_reachability(pairs, declared)
        v2_reached = classify_reachability(pairs, res.seed_union)
        assert len(v1_reached) == 55, "realized v1 directed split [F]"
        assert len(v2_reached) == 68, "extended directed split [F]"
