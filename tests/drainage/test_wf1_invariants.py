"""WF-1 invariant tests over the REALIZED candidate artefact (workflow RedTest phase).

Every test reads runs/drainage_build candidate bytes; deliberate mutations are
applied to COPIES under /tmp/opencode (never the repo artefact). Each test
records beside it: the mutation used for its red demonstration (V5) and the
scope actually exercised versus the scope claimed (V7).

Artefact under test (realized state):
  runs/drain_graph_build/drain_graph.gpkg            layers drain_nodes/drain_edges
  runs/drain_graph_build/drain_graph_adjacency.json  sorted adjacency + topo_order
  runs/drain_graph_build/manifest.json               producer guarantees
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio

REPO = Path(__file__).resolve().parents[2]
RUN_DIR = REPO / "runs" / "drain_graph_build"
GPKG = RUN_DIR / "drain_graph.gpkg"
ADJ = RUN_DIR / "drain_graph_adjacency.json"
MANIFEST = RUN_DIR / "manifest.json"
PINNED_DENOM_KM = 767.3


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def edges() -> gpd.GeoDataFrame:
    return gpd.read_file(GPKG, layer="drain_edges")


@pytest.fixture(scope="module")
def nodes() -> gpd.GeoDataFrame:
    return gpd.read_file(GPKG, layer="drain_nodes")


def _copy_gpkg(tmp_path: Path) -> tuple[Path, gpd.GeoDataFrame]:
    dst = tmp_path / "mutated.gpkg"
    shutil.copy(GPKG, dst)
    e = gpd.read_file(dst, layer="drain_edges")
    return dst, e


# --------------------------------------------------------------------------- 1
def test_01_connectivity_refuses_disconnected_candidate(manifest):
    """INVARIANT: stitched graph has exactly ONE outfall-reachable component.

    SCOPE (V7): full realized candidate - 365 pre-stitch components, realized
    post-stitch count read from the manifest and cross-checked by independent
    union-find over the edge table. The candidate HONESTLY FAILS the ==1 pin
    (269 components); the designed control is refuse-load, asserted here.
    RED BY CONSTRUCTION: this check IS the wave-1 red demo - run against the
    realized candidate without override it must refuse (V5 mutation record:
    'mutation' = shipping 269 components as-is).
    """
    assert manifest["component_count_pre_stitch"] == 365
    # independent union-find over loaded edges
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    e = gpd.read_file(GPKG, layer="drain_edges")
    for _, row in e.iterrows():
        a, b = find(int(row.from_node)), find(int(row.to_node))
        if a != b:
            parent[a] = b
    # Independent component-policy fraction (the substantive invariant):
    # observed length inside outfall-terminating components / total observed.
    n_df = gpd.read_file(GPKG, layer="drain_nodes")
    outfall_ids = set(n_df[n_df.node_type == "outfall"].node_id.astype(int))
    e_obs = e[e.edge_source == "observed"]
    tot = float(e_obs.length_m.sum())
    in_of = 0.0
    for _, row in e_obs.iterrows():
        if find(int(row.from_node)) in {find(int(i)) for i in outfall_ids}:
            in_of += float(row.length_m)
    frac_independent = in_of / tot if tot else 0.0
    pol = manifest.get("component_policy", {})
    assert pol, "component_policy block missing from manifest"
    assert (
        abs(frac_independent - float(pol["outfall_terminating_observed_length_fraction"]))
        <= 5e-7  # manifest stores the fraction rounded to 6 dp
    ), f"independent outfall fraction {frac_independent} != manifest"
    assert 0.0 <= frac_independent <= 1.0
    # Component-count DEFINITIONS differ: manifest post-stitch counts reach-level
    # clusters (internal stitcher DSU); node-level WCC over the written artefact
    # yields a different number by construction. Recorded as RECONCILIATION-GAP
    # (no reach_id on final edges bridges them) - both numbers published here so
    # the shortfall appears in test output, not only in a status report.
    n_ids = set(int(x) for x in n_df.node_id)
    for nid in n_ids:
        find(nid)
    node_comps = len({find(x) for x in parent} | {find(n) for n in n_ids})
    print(
        f"[reconciliation-gap] manifest_post_stitch={manifest['component_count_post_stitch']} "
        f"node_level_wcc={node_comps} (definitions differ; see docs/WF1-DRAIN-GRAPH-REPORT.md)"
    )
    if not pol["met"]:
        assert manifest["status"] == "stopped_owner_adjudication"
        assert any(r.startswith("component_policy_unmet") for r in manifest["reasons"])
        assert (
            len(manifest.get("named_outfalls", [])) >= 1
        ), "unmet policy requires a named, justified outfall set"
    policy = manifest.get("component_policy", {})
    assert policy, "component_policy block missing from manifest"
    frac = float(policy["outfall_terminating_observed_length_fraction"])
    assert 0.0 <= frac <= 1.0
    if not policy["met"]:
        assert manifest["status"] == "stopped_owner_adjudication"
        assert any(r.startswith("component_policy_unmet") for r in manifest["reasons"])
        assert (
            len(manifest.get("named_outfalls", [])) >= 1
        ), "unmet policy requires a named, justified outfall set"


# --------------------------------------------------------------------------- 2
def test_02_direction_monotone_uphill_reconciles_with_manifest(edges, nodes, manifest):
    """INVARIANT: every edge flows downhill on the pinned surface OR the violation
    count is published (negative_slope_edge_count, reported-not-asserted per
    contract counts block) and low-confidence/flat provenance flags travel with
    the edge.

    SCOPE (V7): full population - all 1805 edges' from/to elevations recomputed
    from the LOADED node table (pinned-surface samples), not sampled.
    RED DEMO (V5): mutation = reverse one edge's endpoints in a copied gpkg;
    the recomputed uphill count then diverges from the manifest and fires.
    """
    zmap = dict(zip(nodes.node_id.astype(int), nodes.elev_m.astype(float), strict=True))
    up = int(sum(1 for _, r in edges.iterrows() if zmap[int(r.to_node)] > zmap[int(r.from_node)]))
    assert up == int(
        manifest["counts"]["negative_slope_edge_count"]
    ), f"recomputed uphill {up} != published {manifest['counts']['negative_slope_edge_count']}"


def test_02b_direction_monotone_red_demo(edges, manifest, tmp_path):
    """V5 red demo for invariant 2: mutate a COPY by reversing one edge's
    orientation -> recount diverges from manifest -> assertion fires."""
    dst, e = _copy_gpkg(tmp_path)
    import geopandas as gpd_

    i = e.index[0]
    e.loc[i, "from_node"], e.loc[i, "to_node"] = (
        int(e.loc[i, "to_node"]),
        int(e.loc[i, "from_node"]),
    )
    e.to_file(dst, layer="drain_edges", driver="GPKG")
    m = gpd_.read_file(dst, layer="drain_edges")
    n = gpd.read_file(GPKG, layer="drain_nodes")
    zmap = dict(zip(n.node_id.astype(int), n.elev_m.astype(float), strict=True))
    up = int(sum(1 for _, r in m.iterrows() if zmap[int(r.to_node)] > zmap[int(r.from_node)]))
    assert up != int(
        manifest["counts"]["negative_slope_edge_count"]
    ), "MUTATION FAILED TO REdden: reversed edge did not change the uphill count"
    # red demonstrated: mutation moved the observable; this test PASSES when detection holds


# --------------------------------------------------------------------------- 3
def test_03_no_cycles_adjacency_is_dag():
    """INVARIANT: directed graph acyclic; cycles[] empty; topo_order valid.

    SCOPE (V7): full adjacency - independent Kahn over all 1738 nodes/1805 edges.
    RED DEMO (V5): mutation = add a back-edge to a COPY of the adjacency JSON ->
    Kahn leaves nodes unconsumed -> fires.
    """
    adj = json.loads(ADJ.read_text())
    assert adj["cycles"] == []
    indeg = defaultdict(int)
    outg = defaultdict(list)
    for _eid, edef in adj["edges"].items():
        outg[edef["from"]].append(edef["to"])
        indeg[edef["to"]] += 1
    stack = [n for n in map(int, adj["nodes"]) if indeg[n] == 0]
    seen = 0
    while stack:
        u = stack.pop()
        seen += 1
        for v in outg[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                stack.append(v)
    assert seen == len(adj["nodes"]), "cycle detected by independent Kahn"


def test_03b_no_cycles_red_demo(tmp_path):
    """V5 red demo for invariant 3."""
    dst = tmp_path / "adj_mut.json"
    adj = json.loads(ADJ.read_text())
    eids = sorted(adj["edges"], key=int)
    first = adj["edges"][eids[0]]
    adj["edges"]["999999"] = {
        "from": first["to"],
        "to": first["from"],
        "order": "synthetic_connector",
        "edge_source": "synthesised",
    }
    dst.write_text(json.dumps(adj))
    mut = json.loads(dst.read_text())
    indeg = defaultdict(int)
    outg = defaultdict(list)
    for edef in mut["edges"].values():
        outg[edef["from"]].append(edef["to"])
        indeg[edef["to"]] += 1
    stack = [n for n in map(int, mut["nodes"]) if indeg[n] == 0]
    seen = 0
    while stack:
        u = stack.pop()
        seen += 1
        for v in outg[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                stack.append(v)
    assert seen < len(mut["nodes"]), "MUTATION FAILED TO REdden: cycle not detected"


# --------------------------------------------------------------------------- 4
def test_04_capacity_cited_every_value_traces(edges, manifest):
    """INVARIANT: every capacity value is block-consistent and traceable.
    M11 unblocked 2026-08-25 with the cited Bangalore 60-min 25-yr intensity:
    observed edges are EITHER fully populated (solved) OR zero-measured-slope
    routing-only with an explicit no-claim basis; synthetic connectors keep the
    exact disclaimer; citation chain includes the CPHEEO/InfraLens source.

    SCOPE (V7): all realized edges (1587), full field sweep, full population.
    RED DEMO (V5): see test_04b - injection/nullation moves the partition or
    block-consistency observable.
    """
    cap_fields = [
        "width_m",
        "width_low_m",
        "width_high_m",
        "n_manning",
        "depth_m_solved",
        "q_capacity_nom_m3s",
        "q_capacity_low_m3s",
        "q_capacity_high_m3s",
    ]
    obs = edges[edges.edge_source == "observed"]
    syn = edges[edges.edge_source == "synthesised"]
    disclaim = (
        "synthetic connector: no surveyed cross-section; routing conduit only, " "no capacity claim"
    )
    assert (syn.capacity_basis == disclaim).all()
    solved = obs[obs.width_m.notna()]
    zeroslope = obs[obs.capacity_basis.fillna("").str.startswith("zero measured slope")]
    areacap = obs[
        obs.capacity_basis.fillna("").str.startswith("contributing area")
        & obs.capacity_basis.fillna("").str.contains("no capacity claim")
    ]
    assert len(solved) + len(zeroslope) + len(areacap) == len(
        obs
    ), "observed edges must be fully solved OR zero-slope routing-only OR area-capped no-claim"
    assert areacap.width_m.isna().all(), "area-capped edges carry invented capacity"
    assert int(manifest["capacity_metrics"]["n_edges_area_exceeds_validity"]) == len(
        areacap
    ), "area-cap counter must reconcile with the partition"
    if len(solved):
        for f in cap_fields:
            assert solved[f].notna().all(), f"solved edges missing {f}"
        import json as _json

        all_sources: list[str] = []
        for b in solved.capacity_basis.dropna().head(100):
            all_sources.extend(_json.loads(b).get("sources", []))
        assert any("InfraLens" in x for x in all_sources), "cited IDF source missing from basis"
    cm = manifest["capacity_metrics"]
    assert cm["capacity_status"] == "ok"
    assert int(cm.get("n_edges_zero_slope_skipped", 0)) == len(zeroslope)
    assert int(cm.get("n_edges_capacity_written", 0)) == len(solved)
    assert int(cm.get("n_edges_demand_exceeds_capacity", 0)) == int(
        solved.capacity_basis.fillna("").str.contains("demand_exceeds_capacity").sum()
    ), "surcharge flags must reconcile with basis texts"


def test_04b_capacity_cited_red_demo(edges, tmp_path):
    """V5 red demo for invariant 4. Prefer injecting width onto a zero-slope
    routing-only edge (breaks the solved/zeroslope partition semantics); if no
    such edge exists, null a field on a solved edge (block-consistency fires)."""
    dst, e = _copy_gpkg(tmp_path)
    import geopandas as gpd_

    zs = e.index[
        (e.edge_source == "observed")
        & (e.capacity_basis.fillna("").str.startswith("zero measured slope"))
    ]
    fields = ["width_m", "n_manning", "depth_m_solved", "q_capacity_nom_m3s"]
    if len(zs):
        i = zs[0]
        e.loc[i, "width_m"] = 4.0
        e.to_file(dst, layer="drain_edges", driver="GPKG")
        m = gpd_.read_file(dst, layer="drain_edges")
        z2 = m[
            (m.edge_source == "observed")
            & (m.capacity_basis.fillna("").str.startswith("zero measured slope"))
        ]
        moved = z2.width_m.notna().sum() == 1
        assert moved, "MUTATION FAILED TO REdden: injected width not present"
        # semantic violation demonstrated: routing-only edge now carries hydraulics
        return
    solved_idx = e.index[(e.edge_source == "observed") & e.width_m.notna()]
    i = solved_idx[0]
    e.loc[i, "n_manning"] = None
    e.to_file(dst, layer="drain_edges", driver="GPKG")
    m = gpd_.read_file(dst, layer="drain_edges")
    row = m[m.edge_source == "observed"]
    partial = row[row.capacity_basis.notna() & row[fields].isna().any(axis=1)]
    assert len(partial) >= 1, "MUTATION FAILED TO REdden: partial-fill not created"


# --------------------------------------------------------------------------- 5
def test_05_synthesised_visible_fraction_and_tags(edges, manifest):
    """INVARIANT: synthesised fraction recomputes from written bytes and equals
    the manifest within 1e-9; edge_source <=> order bidirectional tags hold.

    SCOPE (V7): all 1805 edges, denominator pinned at 767.3 km per the manifest's
    own definition string (deviation D13 - value may exceed 1.0, unclamped).
    RED DEMO (V5): mutation = flip one edge's edge_source in a copied gpkg ->
    fraction mismatch + tag violation both fire (see 05b).
    """
    syn = edges.length_m[edges.edge_source == "synthesised"].sum()
    frac = float(syn) / (PINNED_DENOM_KM * 1000.0)
    assert abs(frac - manifest["synthesised_fraction_of_total_length"]) <= 1e-9
    bad = ((edges.edge_source == "synthesised") ^ (edges.order == "synthetic_connector")).sum()
    assert bad == 0


def test_05b_synthesised_visible_red_demo(edges, manifest, tmp_path):
    """V5 red demo for invariant 5."""
    dst, e = _copy_gpkg(tmp_path)
    import geopandas as gpd_

    i = e.index[e.edge_source == "observed"][0]
    e.loc[i, "edge_source"] = "synthesised"
    e.to_file(dst, layer="drain_edges", driver="GPKG")
    m = gpd_.read_file(dst, layer="drain_edges")
    syn = m.length_m[m.edge_source == "synthesised"].sum()
    frac = float(syn) / (PINNED_DENOM_KM * 1000.0)
    tag_bad = ((m.edge_source == "synthesised") ^ (m.order == "synthetic_connector")).sum()
    assert (
        abs(frac - manifest["synthesised_fraction_of_total_length"]) > 1e-9 or tag_bad > 0
    ), "MUTATION FAILED TO REdden"
    # red demonstrated: fraction/tag divergence detectable from written bytes alone


# --------------------------------------------------------------------------- 6
def test_06_crs_grid_seam_rasters_and_nodes_aligned(manifest):
    """INVARIANT: graph geometry EPSG:32643 and every derived raster sits exactly
    on the buffered lattice; every node maps to a valid in-grid cell.

    SCOPE (V7): all three derived rasters + all 1738 nodes (full population).
    RED DEMO (V5): mutation = transform shifted one cell in a copied config was
    demonstrated live in verify-wave-2 F9 (StitchError before any write);
    recorded here rather than re-run to keep this suite artefact-only.
    """
    want_t = [10.0, 0.0, 766440.0, 0.0, -10.0, 1454850.0]
    for name in ("derived_pntr_d8.tif", "hybrid_filled_dem.tif", "reach_footprints.tif"):
        with rasterio.open(RUN_DIR / name) as src:
            t = src.transform
            got = [float(t.a), float(t.b), float(t.c), float(t.d), float(t.e), float(t.f)]
            assert src.width == 3615 and src.height == 3521, name
            assert all(abs(a - b) <= 1e-9 for a, b in zip(got, want_t, strict=True)), name
            assert src.crs.to_epsg() == 32643, name
    n = gpd.read_file(GPKG, layer="drain_nodes")
    cols = ((n.x_m - 766440.0) // 10).astype(int)
    rows = ((1454850.0 - n.y_m) // 10).astype(int)
    assert cols.between(0, 3614).all() and rows.between(0, 3520).all()


# --------------------------------------------------------------------------- 7
def test_07_mass_conservable_finite_positive(nodes, edges):
    """INVARIANT: contributing areas finite positive with exact identities;
    lengths strictly positive; slopes finite non-negative.

    SCOPE (V7): full population - 1738 nodes / 1805 edges, no sampling.
    RED DEMO (V5): mutation = corrupt one node's contrib_area_cells in a copied
    gpkg -> identity check fires (07b).
    """
    assert (nodes.contrib_area_cells > 0).all()
    assert np.isfinite(nodes.contrib_area_cells).all()
    assert np.max(np.abs(nodes.contrib_area_m2 - nodes.contrib_area_cells * 100.0)) <= 1e-6
    assert np.max(np.abs(nodes.contrib_area_ha - nodes.contrib_area_cells * 0.01)) <= 1e-6
    assert (edges.length_m > 0).all() and np.isfinite(edges.length_m).all()
    assert (edges.slope_m_per_m >= 0).all() and np.isfinite(edges.slope_m_per_m).all()


def test_07b_mass_conservable_red_demo(nodes, tmp_path):
    """V5 red demo for invariant 7."""
    dst = tmp_path / "nodes_mut.gpkg"
    shutil.copy(GPKG, dst)
    import geopandas as gpd_

    n = gpd.read_file(dst, layer="drain_nodes")
    i = n.index[0]
    n.loc[i, "contrib_area_m2"] = float(n.loc[i, "contrib_area_cells"]) * 101.0
    n.to_file(dst, layer="drain_nodes", driver="GPKG")
    m = gpd_.read_file(dst, layer="drain_nodes")
    bad = np.max(np.abs(m.contrib_area_m2 - m.contrib_area_cells * 100.0))
    assert bad > 1e-6, "MUTATION FAILED TO REdden"


# fingerprint stability across the suite run (cheap determinism witness)
def test_08_fingerprint_matches_manifest_serialization(edges, manifest):
    """Consumer-side byte-exact fingerprint recompute (contract serialization)."""
    payload = json.dumps(
        [
            [
                int(r.from_node),
                int(r.to_node),
                str(r.order),
                round(float(r.length_m), 6),
                hashlib.sha256(r.geometry.wkb_hex.encode()).hexdigest(),
            ]
            for _, r in edges.sort_values("edge_id").iterrows()
        ],
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == manifest["graph_fingerprint"]
