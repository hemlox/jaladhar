"""WF-1 graph_io tests: writer/loader roundtrip on a synthetic record set + V5 red demos.

Toy fixture (in-test only, no repo data touched): 8 NodeRec / 10 EdgeRec forming one
connected DAG (component_count_post_stitch=1) with observed primary/secondary edges
carrying the full capacity field set, synthetic connectors carrying NULL capacity plus
the exact disclaimer basis, and contrib-area values satisfying cells*100 identity.

Scope note (V7): these tests exercise the schema/assertion domain of graph_io at toy
scale (8 nodes / 10 edges). Full-domain WF-1 behaviour (1033 reaches, 365 components,
walk outcomes) is covered by tests/drainage/test_stitch.py end-to-end; the capacity
field semantics are produced by capacity.py and only their load-time enforcement is
checked here.

V5 RED DEMOS (deliberate mutations, each asserted against its SPECIFIC refusal reason):
  1. manifest graph_fingerprint tampered on disk -> fingerprint mismatch refusal.
  2a. edge_source flipped alone -> bidirectional tag assertion.
  2b. edge_source+order flipped consistently on a connector -> V-SYNTHVISIBLE fraction
      mismatch (fingerprint unaffected: order change keeps tags valid and moves length
      between the synthesised/observed buckets).
  3. width_m injected on a synthetic connector -> synth-separability refusal.
  4. component_count_post_stitch=3 without override -> refuse; with the override pair
     (--allow-disconnected + non-empty adjudication ref) against a
     stopped_owner_adjudication manifest -> loads.
  5. artefact copied to publish paths while post-stitch>1 -> placement guard, even with
     the override pair.
  6. zero_length_dropped_count=1 -> degenerate-count refusal.
  7. n_manning=0.020 (not in the allowed pairing table) -> pairing refusal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
import rasterio
from shapely.geometry import LineString
from typer.testing import CliRunner

from jaladhar.drainage.graph_io import (
    SYNTHETIC_CAPACITY_BASIS,
    PlacementGuardError,
    RefuseLoadError,
    finalize_manifest,
    init_manifest,
    read_artefact,
    write_candidate,
)
from jaladhar.drainage.stitch import EdgeRec, NodeRec

TRANSFORM = [10.0, 0.0, 766440.0, 0.0, -10.0, 1454850.0]


def P(col: float, row: float) -> tuple[float, float]:
    return (TRANSFORM[2] + 10.0 * col + 5.0, TRANSFORM[5] + TRANSFORM[4] * row - 5.0)


# --------------------------------------------------------------------------- fixture


def _node(
    nid: int,
    col: float,
    row: float,
    z: float,
    kind: str = "junction",
    reason: str | None = None,
    cells: int | None = None,
) -> NodeRec:
    x, y = P(col, row)
    n = NodeRec(
        node_id=nid,
        x_m=float(round(x, 3)),
        y_m=float(round(y, 3)),
        elev_m=z,
        node_type=kind,
        outfall_reason=reason,
    )
    if cells is not None:
        # capacity.py output shape: derived contrib-area fields ride alongside NodeRec.
        n.contrib_area_cells = cells  # type: ignore[attr-defined]
        n.contrib_area_m2 = cells * 100.0  # type: ignore[attr-defined]
        n.contrib_area_ha = cells * 0.01  # type: ignore[attr-defined]
    return n


def _cap(width: float = 6.71) -> dict:
    return {
        "width_m": width,
        "width_low_m": round(width * 0.9, 3),
        "width_high_m": round(width * 1.1, 3),
        "width_basis": (
            f"primary 22 ft = {width} m (BBMP norm via Times of India 2016-08-02 chain); "
            "BELOW CPHEEO confidence; band +/-10% declared assumption:width_band_pm10pct"
        ),
        "n_manning": 0.015,
        "n_basis": "CPHEEO 2019 Ch5 open-concrete design",
        "depth_m_solved": 0.62,
        "depth_basis": "solved:rational+Manning",
        "q_capacity_nom_m3s": 1.05,
        "q_capacity_low_m3s": 0.42,
        "q_capacity_high_m3s": 2.1,
        "capacity_basis": json.dumps(
            {
                "width": f"primary {width} m nominal midpoint",
                "shape": "trapezoidal_1H1V_width_is_bed_width",
                "side_slope": "1H:1V",
                "assumption": "assumption:side_slope_1H1V",
                "n": "0.015 CPHEEO 2019 Ch5 open-concrete design",
                "slope": "measured pinned-surface slope per edge",
                "depth": "solved:rational+Manning",
                "design_return_period_yr": 5,
                "climate_uplift": 1.2,
                "runoff_C": 0.725,
                "safety_factor": 0.9,
                "sources": [
                    "CPHEEO 2019 Ch5",
                    "Times of India 2016-08-02 BBMP drain-width norm chain",
                ],
            }
        ),
    }


def _edge(
    eid: int, f: int, t: int, order: str, source: str, conf: str, rule: str, cap: dict | None = None
) -> EdgeRec:
    coords = [P(0, 0), P(0, 0)]
    e = EdgeRec(
        edge_id=eid,
        from_node=f,
        to_node=t,
        order=order,
        edge_source=source,
        direction_confidence=conf,
        direction_basis=json.dumps(
            {
                "terminating_rule": rule,
                "flat_cells_traversed": 0 if conf != "low" else 3,
                "reached_other": rule == "reached_other",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        geometry=LineString(coords),
        length_m=40.0,
        slope_m_per_m=0.005,
    )
    if cap:
        for k, v in cap.items():
            setattr(e, k, v)  # capacity.py output shape
    elif source == "synthesised":
        # Contract: synthetic connectors carry capacity_basis = the exact disclaimer text.
        e.capacity_basis = SYNTHETIC_CAPACITY_BASIS  # type: ignore[attr-defined]
    return e


def _nodes() -> list[NodeRec]:
    return [
        _node(8, 660.0, 130.0, 911.5, cells=900),
        _node(1, 665.0, 140.0, 912.3, cells=880),
        _node(2, 669.0, 148.0, 909.1, cells=760),
        _node(3, 674.0, 156.5, 905.7, cells=700),
        _node(4, 679.0, 165.0, 902.2, kind="outfall", reason="pit", cells=650),
        _node(5, 685.0, 175.0, 900.8, cells=300),
        _node(6, 690.0, 185.0, 898.4, cells=250),
        _node(7, 695.0, 195.0, 896.0, kind="outfall", reason="domain_exit", cells=200),
    ]


def _edges() -> list[EdgeRec]:
    return [
        _edge(1, 8, 1, "primary", "observed", "high", "reached_other", _cap()),
        _edge(2, 1, 2, "secondary", "observed", "medium", "elevation_fallback", _cap(4.88)),
        _edge(3, 2, 3, "primary", "observed", "high", "reached_other", _cap()),
        _edge(4, 3, 4, "synthetic_connector", "synthesised", "low", "pit"),
        _edge(5, 3, 5, "synthetic_connector", "synthesised", "low", "junction_entry"),
        _edge(6, 5, 6, "primary", "observed", "high", "reached_other", _cap()),
        _edge(7, 6, 7, "secondary", "observed", "medium", "elevation_fallback", _cap(4.88)),
        _edge(8, 1, 3, "primary", "observed", "high", "reached_other", _cap()),
        _edge(9, 8, 5, "secondary", "observed", "medium", "elevation_fallback", _cap(4.88)),
        _edge(10, 2, 6, "synthetic_connector", "synthesised", "high", "junction_stub_downhill"),
    ]


def _stitch_metrics(post: int) -> dict:
    zeros = {
        "self_hit_count": 0,
        "dropped_connector_count": 0,
        "dropped_edge_count": 0,
        "dropped_edge_ids": [],
        "self_loop_dropped_count": 0,
        "zero_length_dropped_count": 0,
        "cycle_break_count": 0,
        "flat_flagged_edge_count": 2,
        "negative_slope_edge_count": 0,
        "stub_count": 1,
        "stub_total_length_m": 14.1,
        "stub_overlength_count": 0,
        "orphan_node_count": 0,
        "unresolved_outfall_count": 0,
    }
    return {
        "component_count_pre_stitch": 11,
        "component_count_post_stitch": post,
        "stranded_component_ids": (
            []
            if post == 1
            else [{"component_id": i, "size": 2, "reach_ids": [i]} for i in range(2)]
        ),
        "walk_outcome_histogram": {"reached_other": 4, "pit": 1, "domain_exit": 1},
        "lost_walk_audit": {"entries": [], "total_no_edge": 0},
        "cycle_break_rounds": [],
        "counters": zeros,
        "output_sha256": {
            "derived_pointer": "a" * 64,
            "hybrid_filled_dem": "b" * 64,
            "footprints": "c" * 64,
        },
        "graph_is_dag": True,
        "stitcher_params": {
            "snap_tolerance_m": 10.0,
            "max_connector_steps": 10000,
            "rasterization_rule": "burn cells whose CENTER is within 5.0 m of geometry",
        },
        "policy_frontier_table": [
            {
                "policy": "all-walks-write, flat-resolved (THIS COMPOSITE)",
                "post_stitch_comps": post,
            },
        ],
        "realized_policy_row": {
            "policy": "all-walks-write, flat-resolved (THIS COMPOSITE)",
            "post_stitch_comps": post,
        },
    }


def _write_elevation(tmp_path: Path) -> Path:
    """Standalone elevation surface whose REALIZED bytes are bound into the manifest;
    the reader hashes whatever the binding names."""
    import numpy as np

    p = tmp_path / "elevation.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        dtype="float32",
        count=1,
        crs="EPSG:32643",
        transform=TRANSFORM,
        width=20,
        height=20,
    ) as dst:
        dst.write(np.full((20, 20), 900.0, dtype=np.float32), 1)
    return p


def _cfg(tmp_path: Path, elev_path: Path) -> dict:
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    wways = inputs / "waterways_bbmp.gpkg"
    wways.write_bytes(b"fixture")
    csvp = inputs / "retained_basins.csv"
    csvp.write_text('id,n_cells,spill_elevation_m,outlet_cell\n1,10,11.0,"(5,5)"\n')
    kp = inputs / "primary.kml"
    kp.write_text("<kml/>")
    ks = inputs / "secondary.kml"
    ks.write_text("<kml/>")
    return {
        "crs": "EPSG:32643",
        "grid": {"width": 20, "height": 20, "transform": TRANSFORM, "cell_area_m2": 100.0},
        "inputs": {
            "waterways_gpkg": str(wways),
            "retained_basins_csv": str(csvp),
            "kml_primary": str(kp),
            "kml_secondary": str(ks),
            "elevation_surface": str(elev_path),
            "elevation_sha256": hashlib.sha256(elev_path.read_bytes()).hexdigest(),
        },
        "outputs": {
            "run_dir": str(tmp_path / "run"),
            "gpkg": str(tmp_path / "run" / "cand.gpkg"),
            "adjacency": str(tmp_path / "run" / "cand.json"),
            "manifest": str(tmp_path / "run" / "manifest.json"),
            "publish_gpkg": str(tmp_path / "publish" / "drain_graph.gpkg"),
            "publish_adjacency": str(tmp_path / "publish" / "drain_graph_adjacency.json"),
        },
        "consumer": {"allow_disconnected_load": False, "owner_adjudication_ref": ""},
    }


def _build(
    tmp_path: Path,
    post: int = 1,
    edges: list[EdgeRec] | None = None,
    unmet_policy: bool = False,
):
    """init -> finalize -> write; returns (paths dict, manifest dict).
    unmet_policy=True appends a detached observed component WITHOUT any outfall
    (nodes 90->91, 4000 m) so the outfall-terminating length fraction drops
    below the 0.95 policy target."""
    elev = _write_elevation(tmp_path)
    cfg = _cfg(tmp_path, elev)
    run_dir = Path(cfg["outputs"]["run_dir"])
    manifest = init_manifest(str(run_dir), cfg)
    es = edges if edges is not None else _edges()
    ns = _nodes()
    if unmet_policy:
        ns = ns + [
            NodeRec(9, 700.0, 205.0, 895.0, "junction", None),
            NodeRec(10, 705.0, 215.0, 893.0, "junction", None),
        ]
        es = es + [
            EdgeRec(
                edge_id=11,
                from_node=9,
                to_node=10,
                order="secondary",
                edge_source="observed",
                direction_confidence="medium",
                direction_basis=json.dumps(
                    {
                        "terminating_rule": "elevation_fallback",
                        "flat_cells_traversed": 0,
                        "reached_other": False,
                    }
                ),
                geometry=LineString([(700.0, 205.0), (705.0, 215.0)]),
                length_m=4000.0,
                slope_m_per_m=0.0005,
            )
        ]
    manifest = finalize_manifest(
        manifest,
        nodes=_nodes(),
        edges=es,
        stitch_metrics=_stitch_metrics(post),
        capacity_metrics={"capacity_status": "ok"},
    )
    out_dir = tmp_path / ("candidate" if post == 1 else f"candidate_post{post}")
    paths = write_candidate(ns, es, manifest, str(out_dir))
    return paths, manifest


def _rewrite_gpkg(paths: dict, mutate) -> None:
    """Apply mutate(edges_gdf)->edges_gdf to the drain_edges layer, rewriting the gpkg."""
    nodes_gdf = gpd.read_file(paths["gpkg"], layer="drain_nodes")
    edges_gdf = gpd.read_file(paths["gpkg"], layer="drain_edges")
    edges_gdf = mutate(edges_gdf)
    Path(paths["gpkg"]).unlink()
    nodes_gdf.to_file(paths["gpkg"], layer="drain_nodes", driver="GPKG")
    edges_gdf.to_file(paths["gpkg"], layer="drain_edges", driver="GPKG")


# ------------------------------------------------------------------------------ green


def test_write_read_roundtrip_and_realized_schema(tmp_path: Path) -> None:
    paths, manifest = _build(tmp_path)
    assert manifest["status"] == "completed"
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    nodes_gdf, edges_gdf = art["nodes_gdf"], art["edges_gdf"]
    assert len(nodes_gdf) == 8 and len(edges_gdf) == 10
    # Realized dtypes: PK columns are integer after the GPKG roundtrip; nullable
    # contrib_area_cells degrades to float in storage and is coerced back integral.
    assert str(edges_gdf["edge_id"].dtype) == "int64"
    assert str(nodes_gdf["node_id"].dtype) == "int64"
    cells = nodes_gdf.set_index("node_id")["contrib_area_cells"]
    assert all(float(v) == int(v) for v in cells.dropna())
    assert set(nodes_gdf["geometry"].geom_type) == {"Point"}
    assert set(edges_gdf["geometry"].geom_type) == {"LineString"}
    assert nodes_gdf.crs.to_string() == "EPSG:32643"
    # Synthetic connectors realized with ALL-NULL hydraulics; observed carry them.
    synth = edges_gdf[edges_gdf["edge_source"] == "synthesised"]
    obs = edges_gdf[edges_gdf["edge_source"] == "observed"]
    assert synth["width_m"].isna().all() and synth["n_manning"].isna().all()
    assert obs["width_m"].notna().all() and obs["q_capacity_nom_m3s"].notna().all()
    # Adjacency schema exactly per contract determinism block.
    adj = art["adjacency"]
    assert set(adj) == {"nodes", "edges", "topo_order", "cycles", "dropped"}
    assert adj["cycles"] == []
    assert adj["dropped"] == {"self_loops": [], "zero_length": [], "duplicates": []}
    assert set(adj["nodes"]) == {str(i) for i in range(1, 9)}
    assert set(adj["edges"]) == {str(i) for i in range(1, 11)}
    assert adj["edges"]["4"] == {
        "from": 3,
        "to": 4,
        "order": "synthetic_connector",
        "edge_source": "synthesised",
    }


def test_topo_order_exact_and_independently_valid(tmp_path: Path) -> None:
    """If topo_order were wrong we would observe a sequence violating edge directions;
    it is checked twice: exact expected Kahn output AND an independent re-derivation
    from the LOADED gpkg rows."""
    paths, _ = _build(tmp_path)
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    assert art["adjacency"]["topo_order"] == [8, 1, 2, 3, 4, 5, 6, 7]
    rows = art["edges_gdf"].sort_values("edge_id").to_dict("records")
    indeg = {i: 0 for i in range(1, 9)}
    out: dict[int, list[int]] = {i: [] for i in range(1, 9)}
    for r in rows:
        out[int(r["from_node"])].append(int(r["to_node"]))
        indeg[int(r["to_node"])] += 1
    frontier = sorted(v for v, d in indeg.items() if d == 0)
    seen = []
    while frontier:
        u = frontier.pop(0)
        seen.append(u)
        for v in sorted(out[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                frontier.append(v)
                frontier.sort()
    assert seen == art["adjacency"]["topo_order"], "Kahn re-derivation disagrees"


def test_fingerprint_recomputes_byte_exact_from_loaded_gpkg(tmp_path: Path) -> None:
    """If the GPKG roundtrip altered any geometry byte or coordinate, the consumer-side
    recomputation over LOADED rows would diverge from the producer fingerprint."""
    paths, manifest = _build(tmp_path)
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    rows = art["edges_gdf"].sort_values("edge_id").to_dict("records")
    payload = json.dumps(
        [
            [
                int(r["from_node"]),
                int(r["to_node"]),
                r["order"],
                round(float(r["length_m"]), 6),
                hashlib.sha256(r["geometry"].wkb_hex.encode()).hexdigest(),
            ]
            for r in rows
        ],
        separators=(",", ":"),
    )
    recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert manifest["graph_fingerprint"] == recomputed
    assert art["manifest"]["graph_fingerprint"] == recomputed


def test_manifest_carries_every_producer_guarantee_field(tmp_path: Path) -> None:
    paths, manifest = _build(tmp_path)
    loaded = json.loads(Path(paths["manifest"]).read_text())
    required = [
        "status",
        "git_sha",
        "git_dirty",
        "git_porcelain_paths",
        "config_snapshot",
        "started_utc",
        "finished_utc",
        "wall_clock_sec",
        "input_sha256",
        "elevation_surface_sha256_binding",
        "connectivity",
        "component_count_pre_stitch",
        "component_count_post_stitch",
        "stranded_component_ids",
        "stitcher_params",
        "output_sha256",
        "dem_source_manifest_status",
        "pointer_provenance",
        "counts",
        "stub_counters",
        "walk_outcome_histogram",
        "lost_walk_audit",
        "cycle_break_rounds",
        "unresolved_outfall_count",
        "units_block",
        "capacity_rule_version",
        "assumed_uncalibrated_flag_note",
        "deterministic_ids",
        "graph_is_dag",
        "graph_fingerprint",
        "graph_fingerprint_serialization_pinned",
        "synthesised_fraction_of_total_length",
        "synthesised_fraction_definition",
        "node_geometry_assumption_note",
        "enum_forward_design_note",
        "lake_receiving_water_names",
        "policy_frontier_table",
        "reasons",
    ]
    missing = [k for k in required if k not in loaded]
    assert not missing, f"manifest missing guarantee fields: {missing}"
    # Verbatim contract strings survive to disk unchanged.
    from jaladhar.drainage.graph_io import (
        ASSUMED_UNCALIBRATED_COEXISTENCE_NOTE,
        CAPACITY_RULE_VERSION,
        DEM_SOURCE_MANIFEST_STATUS,
        GRAPH_FINGERPRINT_SERIALIZATION,
        NODE_GEOMETRY_ASSUMPTION_NOTE,
        UNITS_BLOCK,
    )

    assert loaded["units_block"] == UNITS_BLOCK
    assert loaded["capacity_rule_version"] == CAPACITY_RULE_VERSION
    assert loaded["assumed_uncalibrated_flag_note"] == ASSUMED_UNCALIBRATED_COEXISTENCE_NOTE
    assert loaded["graph_fingerprint_serialization_pinned"] == GRAPH_FINGERPRINT_SERIALIZATION
    assert loaded["node_geometry_assumption_note"] == NODE_GEOMETRY_ASSUMPTION_NOTE
    assert loaded["dem_source_manifest_status"] == DEM_SOURCE_MANIFEST_STATUS
    assert loaded["pointer_provenance"] == "derived_in_run"
    assert loaded["deterministic_ids"] is True and loaded["graph_is_dag"] is True
    assert loaded["connectivity"] == "D8_stitched"
    assert loaded["input_sha256"]["waterways_bbmp.gpkg"]
    assert loaded["input_sha256"]["retained_basins.csv"]
    note = loaded["enum_forward_design_note"]
    assert note["emitted_node_types"] == ["junction", "outfall"]
    assert sorted(note["emitted_orders"]) == ["primary", "secondary", "synthetic_connector"]
    assert loaded["lake_receiving_water_names"] == []
    assert loaded["stub_counters"]["stub_count"] == 1
    assert loaded["unresolved_outfall_count"] == 0
    # Fraction headline recomputed from edges, unclamped, denominator recorded.
    edges = _edges()
    frac = sum(e.length_m for e in edges if e.edge_source == "synthesised") / 767_300.0
    assert abs(loaded["synthesised_fraction_of_total_length"] - frac) <= 1e-12
    assert loaded["synthesised_fraction_definition"]["unclamped"] is True


def test_contrib_identity_holds_within_1e6(tmp_path: Path) -> None:
    paths, _ = _build(tmp_path)
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    nodes_gdf = art["nodes_gdf"]
    m2 = nodes_gdf["contrib_area_cells"] * 100.0 - nodes_gdf["contrib_area_m2"]
    ha = nodes_gdf["contrib_area_cells"] * 0.01 - nodes_gdf["contrib_area_ha"]
    assert m2.abs().max() <= 1e-6
    assert ha.abs().max() <= 1e-6


def test_finalize_status_transition_and_reasons(tmp_path: Path) -> None:
    _, connected = _build(tmp_path)
    assert connected["status"] == "completed"
    _, disconnected = _build(tmp_path / "d", post=3, unmet_policy=True)
    assert disconnected["status"] == "stopped_owner_adjudication"
    assert any(r.startswith("component_policy_unmet") for r in disconnected["reasons"])
    pol = disconnected["component_policy"]
    assert pol["met"] is False and pol["target"] == 0.95
    assert pol["outfall_terminating_observed_length_fraction"] < 0.95
    assert disconnected["stranded_component_ids"]
    blocked = {"capacity_status": "blocked_missing_design_intensity"}
    man = init_manifest(str(tmp_path / "r2"), _cfg(tmp_path, _write_elevation(tmp_path)))
    man = finalize_manifest(
        man,
        nodes=_nodes(),
        edges=_edges(),
        stitch_metrics=_stitch_metrics(1),
        capacity_metrics=blocked,
    )
    assert "blocked_missing_design_intensity" in man["reasons"]
    assert man["capacity_metrics"] == blocked


def test_publish_only_when_connected_and_copy_is_faithful(tmp_path: Path) -> None:
    elev = _write_elevation(tmp_path)
    cfg = _cfg(tmp_path, elev)
    man = init_manifest(cfg["outputs"]["run_dir"], cfg)
    man = finalize_manifest(man, nodes=_nodes(), edges=_edges(), stitch_metrics=_stitch_metrics(1))
    out = tmp_path / "cand"
    res = write_candidate(_nodes(), _edges(), man, str(out), publish=True)
    pub_g, pub_a = Path(res["published_gpkg"]), Path(res["published_adjacency"])
    assert pub_g.exists() and pub_a.exists()
    assert pub_a.read_bytes() == Path(res["adjacency"]).read_bytes()
    # Policy-unmet candidate: publish refused BEFORE anything is copied.
    elev2 = _write_elevation(tmp_path / "e2")
    cfg2 = _cfg(tmp_path / "e2", elev2)
    man3 = init_manifest(str(Path(cfg2["outputs"]["run_dir"])), cfg2)
    ns3 = _nodes() + [
        NodeRec(9, 700.0, 205.0, 895.0, "junction", None),
        NodeRec(10, 705.0, 215.0, 893.0, "junction", None),
    ]
    es3 = _edges() + [
        EdgeRec(
            11,
            9,
            10,
            "secondary",
            "observed",
            "medium",
            json.dumps(
                {
                    "terminating_rule": "elevation_fallback",
                    "flat_cells_traversed": 0,
                    "reached_other": False,
                }
            ),
            LineString([(700.0, 205.0), (705.0, 215.0)]),
            4000.0,
            0.0005,
        )
    ]
    man3 = finalize_manifest(
        man3,
        nodes=ns3,
        edges=es3,
        stitch_metrics=_stitch_metrics(3),
        capacity_metrics={"capacity_status": "ok"},
    )
    pub_g.unlink()
    pub_a.unlink()
    with pytest.raises(PlacementGuardError, match="NOTHING ships unadjudicated"):
        write_candidate(ns3, es3, man3, str(tmp_path / "cand3"), publish=True)
    assert not pub_g.exists() and not pub_a.exists()


def test_adjacency_reruns_byte_identical(tmp_path: Path) -> None:
    """Adjacency JSON must be deterministic across writes (the gpkg container embeds
    gpkg_contents.last_change and is excluded from this byte-equality claim)."""
    p1, _ = _build(tmp_path / "a")
    p2, m2 = _build(tmp_path / "b")
    assert Path(p1["adjacency"]).read_bytes() == Path(p2["adjacency"]).read_bytes()
    assert (
        m2["graph_fingerprint"] == json.loads(Path(p1["manifest"]).read_text())["graph_fingerprint"]
    )


def test_cli_inspect_pass_exit_zero(tmp_path: Path) -> None:
    from jaladhar.drainage.graph_io import app

    paths, _ = _build(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "inspect",
            "--gpkg",
            paths["gpkg"],
            "--adjacency",
            paths["adjacency"],
            "--manifest",
            paths["manifest"],
        ],
    )
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


# ------------------------------------------------------------------------------ red


def test_red_mutated_manifest_fingerprint_refused(tmp_path: Path) -> None:
    """V5 RED DEMO 1. Mutation: manifest.json graph_fingerprint replaced on disk after
    writing. Observable: reader refuses naming the mismatch (recomputed vs declared)."""
    paths, _ = _build(tmp_path)
    mp = Path(paths["manifest"])
    man = json.loads(mp.read_text())
    man["graph_fingerprint"] = "0" * 64
    mp.write_text(json.dumps(man))
    with pytest.raises(RefuseLoadError, match=r"graph_fingerprint mismatch") as ei:
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    assert "0" * 12 in str(ei.value)


def test_red_bidirectional_tag_violation(tmp_path: Path) -> None:
    """V5 RED DEMO 2a. Mutation: edge_source flipped ALONE on synthetic edge 4 ->
    synthesised<=>synthetic_connector equivalence broken."""

    def flip(gdf):
        gdf.loc[gdf["edge_id"] == 4, "edge_source"] = "observed"
        return gdf

    paths, _ = _build(tmp_path)
    _rewrite_gpkg(paths, flip)
    with pytest.raises(RefuseLoadError, match=r"consumer_assertion_2.*bidirectional"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])


def test_red_synthesised_fraction_mismatch(tmp_path: Path) -> None:
    """V5 RED DEMO 2b. Mutation: edge_source+order flipped CONSISTENTLY on connector 4
    and capacity_basis nulled (tags valid; edge masquerades as an observed secondary in
    the blocked all-NULL state). The field-level assertions therefore pass and the
    observable that MUST fire is V-SYNTHVISIBLE: the fraction recomputed from the
    written gpkg no longer matches the manifest value (edge 4's 40 m left the
    synthesised bucket; tol 1e-9)."""

    def flip(gdf):
        mask = gdf["edge_id"] == 4
        gdf.loc[mask, "edge_source"] = "observed"
        gdf.loc[mask, "order"] = "secondary"
        gdf.loc[mask, "capacity_basis"] = None
        return gdf

    paths, _ = _build(tmp_path)
    _rewrite_gpkg(paths, flip)
    with pytest.raises(RefuseLoadError, match=r"v-synthvisible.*!= manifest"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])


def test_red_width_injected_on_synthetic_edge(tmp_path: Path) -> None:
    """V5 RED DEMO 3 (SYNTH-SEPARABILITY). Mutation: width_m injected on connector 10 -
    a D8 routing conduit carrying invented hydraulics violates rule 1."""

    def inject(gdf):
        gdf.loc[gdf["edge_id"] == 10, "width_m"] = 6.71
        return gdf

    paths, _ = _build(tmp_path)
    _rewrite_gpkg(paths, inject)
    with pytest.raises(RefuseLoadError, match=r"synth_separability.*invented"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])


def test_red_disconnected_without_override_then_loads_with_pair(tmp_path: Path) -> None:
    """V5 RED DEMO 4. Artefact whose component policy is UNMET: refused without the
    startup-resolved override pair; loads with status stopped_owner_adjudication +
    allow_disconnected_load + non-empty owner_adjudication_ref."""
    paths, man = _build(tmp_path / "disc", post=3, unmet_policy=True)
    with pytest.raises(RefuseLoadError, match=r"component_policy.*fraction"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    art = read_artefact(
        paths["gpkg"],
        paths["adjacency"],
        paths["manifest"],
        allow_disconnected_load=True,
        owner_adjudication_ref="OPEN-ITEMS.md menu M1 pending",
    )
    assert art["manifest"]["component_policy"]["met"] is False
    assert man["status"] == "stopped_owner_adjudication"


def test_red_placement_guard_at_publish_paths_even_with_override(tmp_path: Path) -> None:
    """V5 RED DEMO 5. Mutation: candidate copied to config publish paths while
    post-stitch>1. The guard fires REGARDLESS of the override pair - placement, not
    permission, is the violated invariant."""
    paths, _ = _build(tmp_path / "disc", post=3, unmet_policy=True)
    snap = json.loads(Path(paths["manifest"]).read_text())["config_snapshot"]["outputs"]
    pub_g, pub_a = Path(snap["publish_gpkg"]), Path(snap["publish_adjacency"])
    pub_g.parent.mkdir(parents=True, exist_ok=True)
    pub_g.write_bytes(Path(paths["gpkg"]).read_bytes())
    pub_a.write_bytes(Path(paths["adjacency"]).read_bytes())
    with pytest.raises(RefuseLoadError, match=r"placement_guard.*unadjudicated"):
        read_artefact(
            paths["gpkg"],
            paths["adjacency"],
            paths["manifest"],
            allow_disconnected_load=True,
            owner_adjudication_ref="ref",
        )
    pub_g.unlink()
    pub_a.unlink()
    art = read_artefact(
        paths["gpkg"],
        paths["adjacency"],
        paths["manifest"],
        allow_disconnected_load=True,
        owner_adjudication_ref="ref",
    )
    assert len(art["edges_gdf"]) == 11  # 10 base + detached policy-unmet edge


def test_zero_length_counter_recorded_not_refused(tmp_path: Path) -> None:
    """V6 AMENDED 2026-08-26 (contract v1.2.0, D-G owner adjudication): the SPEC was
    amended - zero_length_dropped_count is a RECORDED PROPERTY, not a refusal ('the
    42 dropped zero-length edges are a build fact, not a defect'). The former red
    demo test_red_zero_length_counter_refused asserted the old refusal; under the
    amended contract the same mutation must LOAD and echo the count in the result."""
    elev = _write_elevation(tmp_path)
    cfg = _cfg(tmp_path, elev)
    man = init_manifest(cfg["outputs"]["run_dir"], cfg)
    man = finalize_manifest(
        man,
        nodes=_nodes(),
        edges=_edges(),
        stitch_metrics=_stitch_metrics(1),
        extra_counts={"zero_length_dropped_count": 1},
    )
    out = tmp_path / "cand_zero"
    paths = write_candidate(_nodes(), _edges(), man, str(out))
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    assert art["dropped_zero_length_count"] == 1
    # the recorded property is visible without any override flags:
    assert "dropped_zero_length_count" in art


def test_null_capacity_edge_class_accepted_and_guarded(tmp_path: Path) -> None:
    """V6 AMENDED 2026-08-26 (contract v1.2.0, D-G): an observed edge with a declared
    'no capacity claim; routing only' basis and ALL numeric capacity fields NULL is a
    legal NULL-CAPACITY EDGE CLASS member; a declared-null edge carrying ANY numeric
    field is refused - null class is NOT loadable as zero."""
    elev = _write_elevation(tmp_path)
    cfg = _cfg(tmp_path, elev)
    man = init_manifest(cfg["outputs"]["run_dir"], cfg)

    edges = _edges()
    e2 = edges[1]
    for fld in (
        "width_m",
        "width_low_m",
        "width_high_m",
        "n_manning",
        "depth_m_solved",
        "q_capacity_nom_m3s",
        "q_capacity_low_m3s",
        "q_capacity_high_m3s",
        "width_basis",
        "n_basis",
        "depth_basis",
    ):
        setattr(e2, fld, None)  # type: ignore[attr-defined]
    e2.capacity_basis = (  # type: ignore[attr-defined]
        "contributing area 12779 ha exceeds the rational method validity limit "
        "5000 ha: no capacity claim; routing only"
    )
    man = finalize_manifest(
        man, nodes=_nodes(), edges=edges, stitch_metrics=_stitch_metrics(1)
    )
    out = tmp_path / "cand_nullclass"
    paths = write_candidate(_nodes(), edges, man, str(out))
    art = read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    assert art["null_capacity_edge_count"] == 1

    # NOT loadable as zero: one numeric field on a declared-null edge refuses.
    bad = _edges()
    b2 = bad[1]
    for fld in (
        "width_m",
        "width_low_m",
        "width_high_m",
        "n_manning",
        "depth_m_solved",
        "width_basis",
        "n_basis",
        "depth_basis",
    ):
        setattr(b2, fld, None)  # type: ignore[attr-defined]
    b2.q_capacity_nom_m3s = 0.0  # type: ignore[attr-defined]
    b2.q_capacity_low_m3s = None  # type: ignore[attr-defined]
    b2.q_capacity_high_m3s = None  # type: ignore[attr-defined]
    b2.capacity_basis = (  # type: ignore[attr-defined]
        "zero measured slope on pinned surface: no capacity claim; routing only"
    )
    man_bad = init_manifest(cfg["outputs"]["run_dir"], cfg)
    man_bad = finalize_manifest(
        man_bad, nodes=_nodes(), edges=bad, stitch_metrics=_stitch_metrics(1)
    )
    out_bad = tmp_path / "cand_nullclass_bad"
    paths_bad = write_candidate(_nodes(), bad, man_bad, str(out_bad))
    with pytest.raises(RefuseLoadError, match=r"\[null_capacity_class\] edge 2"):
        read_artefact(paths_bad["gpkg"], paths_bad["adjacency"], paths_bad["manifest"])


def test_red_n_manning_pairing_refused(tmp_path: Path) -> None:
    """V5 RED DEMO 7. Mutation: n_manning=0.020 (outside the allowed {0.013,0.015,0.030}
    table) on observed edge 1 - the numeric/text pairing assertion fires."""
    edges = _edges()
    edges[0].n_manning = 0.020  # type: ignore[attr-defined]
    paths, _ = _build(tmp_path, edges=edges)
    with pytest.raises(RefuseLoadError, match=r"consumer_assertion_5.*allowed pairs"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])


def test_cli_inspect_refused_exit_two(tmp_path: Path) -> None:
    """CLI surfaces the specific refusal reason and exits 2 on a tampered manifest."""
    from jaladhar.drainage.graph_io import app

    paths, _ = _build(tmp_path / "unmet", post=1, unmet_policy=True)
    mp = Path(paths["manifest"])
    man = json.loads(mp.read_text())
    man["status"] = "stopped_owner_adjudication"
    mp.write_text(json.dumps(man))
    # sanity: the unmet candidate genuinely fails policy
    assert man["component_policy"]["met"] is False
    result = CliRunner().invoke(
        app,
        [
            "inspect",
            "--gpkg",
            paths["gpkg"],
            "--adjacency",
            paths["adjacency"],
            "--manifest",
            paths["manifest"],
        ],
    )
    assert result.exit_code == 2, result.output
    assert "REFUSED" in result.output and "component_policy" in result.output


def test_m5b_self_loop_override_loads_with_enumeration(tmp_path: Path) -> None:
    """Owner adjudication M5b (2026-08-25): input-geometry self-loops are a data
    fact; a sanctioned consumer may load when dropped ids are enumerated and an
    owner_adjudication_ref is present. Without the ref the refusal stands."""
    paths, _ = _build(tmp_path / "m5b", post=1)
    mp = Path(paths["manifest"])
    man = json.loads(mp.read_text())
    man["counts"]["self_loop_dropped_count"] = 12
    man["counts"]["dropped_edge_ids"] = [f"self_loop:secondary:{i}m@a->b" for i in range(12)]
    mp.write_text(json.dumps(man))
    with pytest.raises(RefuseLoadError, match=r"consumer_assertion_3.*M5b"):
        read_artefact(paths["gpkg"], paths["adjacency"], paths["manifest"])
    art = read_artefact(
        paths["gpkg"],
        paths["adjacency"],
        paths["manifest"],
        allow_disconnected_load=True,
        owner_adjudication_ref="WF1-M5B-adjudicated",
    )
    assert len(art["edges_gdf"]) > 0
