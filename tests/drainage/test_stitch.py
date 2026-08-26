"""WF-1 stitcher tests: toy-fixture end-to-end + counter liveness + red demos.

Toy fixture (built in-test, no repo data): 200x200 DEM with a retained flat basin
(EAST edge, floods terminate fast on the un-retained rim), a hard pit outside the
register (pointer-0 -> outfall(pit)), a west domain-edge slope, a plain with a
reached_other micro-reach and a >max_steps run, a long/short component pair giving
a mid-span junction entry with an overlength stub, a host/probe trio giving a
successful stub AND a junction_entry_at_start with refused stub, a parallel pair,
and a self-loop reach.

V5 RED DEMO (recorded mutation): DAG-break disabled via cfg flag
`stitch.dag_break_enabled=false` on a cyclic candidate -> production
`_enforce_dag` raises "cycles detected". Scope note (V7): the toy terrain is a
strict-descent forest (walks, stubs and fallbacks all point downhill in pinned z),
so the end-to-end pipeline cannot naturally close a cycle here; the cycle path is
therefore exercised at unit level against the production enforcement function,
and the end-to-end test asserts graph_is_dag independently. PARTIAL by design.

Counter-liveness scope note (V7): zero_length_dropped_count cannot move end-to-end
because snapping merges any two endpoints closer than snap_tolerance_m (a post-snap
observed edge below 0.05 m between DISTINCT nodes is structurally impossible);
its drop logic is demonstrated directly against `_apply_dedup_and_degenerate`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import yaml
from shapely.geometry import LineString

from jaladhar.drainage.stitch import (
    Reach,
    StitchError,
    _apply_dedup_and_degenerate,
    _build_retained_mask,
    _EdgeCand,
    _enforce_dag,
    resolve_config,
    stitch_components,
)

WIDTH = HEIGHT = 200
TRANSFORM = [10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0]


def P(col: float, row: float) -> tuple[float, float]:
    """Cell-center coordinate of (possibly fractional) grid position."""
    return (TRANSFORM[2] + 10.0 * col + 5.0, TRANSFORM[5] + TRANSFORM[4] * row - 5.0)


def _toy_dem() -> np.ndarray:
    cols = np.arange(WIDTH, dtype=np.float32)
    rows = np.arange(HEIGHT, dtype=np.float32)
    z = 20.0 - 0.01 * cols[None, :] + 0.002 * rows[:, None]
    z = np.broadcast_to(z, (HEIGHT, WIDTH)).copy()
    # Retained flat basin hugging the EAST domain edge: flat floor + higher rim ring.
    z[65:86, 165:196] = 10.0  # floor rows 65..85, cols 165..195
    z[61:90, 161:165] = 11.0  # west ring
    z[61:90, 196:200] = 11.0  # east ring (against domain edge)
    z[61:65, 165:196] = 11.0  # north ring
    z[86:90, 165:196] = 11.0  # south ring
    # Hard pit, NOT retained: stays original bytes -> pointer 0 -> outfall(pit).
    z[20:28, 150:160] = 9.0
    z[22:26, 152:158] = 8.0
    # West domain-edge slope: descends toward col 0.
    z[98:109, 0:19] = 15.0 + 0.4 * cols[0:19][None, :]
    return z.astype(np.float32)


def _toy_reaches() -> list[Reach]:
    def rc(rid: int, cls: str, a: tuple[float, float], b: tuple[float, float]) -> Reach:
        g = LineString([P(*a), P(*b)])
        return Reach(reach_id=rid, drain_class=cls, geom=g, length_m=float(g.length))

    reaches = [
        rc(1, "primary", (168, 75), (192, 75)),  # basin floor span
        rc(2, "secondary", (192.5, 75.5), (185, 82)),  # snaps to reach 1 -> same comp
        rc(3, "secondary", (151, 22), (144, 20)),  # pit rim: pit connector + self-hit
        rc(4, "primary", (14, 103), (5, 104)),  # west slope: domain exit + self-hit
        rc(5, "secondary", (32, 111), (36, 111)),  # stub probe -> SUCCESSFUL stub into 7
        rc(6, "secondary", (43, 111), (50, 115)),  # at_start probe (start cell inside 7's fp)
        rc(8, "primary", (95, 30), (95, 60)),  # long N-S line
        rc(9, "secondary", (88, 40), (88, 44)),  # mid-span junction entry into 8 (stub>25m)
        rc(10, "secondary", (120, 150), (121.3, 150)),  # reached_other + >max_steps run
        rc(11, "secondary", (170, 57.5), (170, 67)),  # parallel pair, longer
        rc(12, "secondary", (169.5, 58.2), (169.5, 66)),  # parallel pair, shorter -> deduped
        rc(13, "secondary", (180, 180), (180.5, 180.2)),  # self-loop
    ]
    # Bent host for reach 7 (id ABOVE probe 6 so it overwrites the shared start cell):
    # probe 5's eastward walk enters the leg at ~(111,41), ~14 m from the west node
    # -> a REAL stub edge (not the coincident-node case); probe 6's start cell is
    # inside 7's footprint -> junction_entry_at_start.
    g7 = LineString([P(40, 112), P(42, 111), P(46, 111)])
    reaches.append(Reach(reach_id=7, drain_class="primary", geom=g7, length_m=float(g7.length)))
    return reaches


def _write_toy_files(tmp_path: Path) -> tuple[Path, Path]:
    dem_path = tmp_path / "toy_elevation.tif"
    prof = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        "crs": "EPSG:32643",
        "transform": TRANSFORM,
        "width": WIDTH,
        "height": HEIGHT,
        "nodata": None,
    }
    with rasterio.open(dem_path, "w", **prof) as dst:
        dst.write(_toy_dem(), 1)
    csv_path = tmp_path / "retained_basins.csv"
    csv_path.write_text("id,n_cells,spill_elevation_m,outlet_cell\n" '1,651,11.0,"(84,194)"\n')
    return dem_path, csv_path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _toy_cfg(tmp_path: Path, dem_path: Path, csv_path: Path) -> dict:
    run = tmp_path / "run"
    return {
        "crs": "EPSG:32643",
        "grid": {
            "width": WIDTH,
            "height": HEIGHT,
            "transform": TRANSFORM,
            "cell_area_m2": 100.0,
        },
        "inputs": {
            "elevation_surface": str(dem_path),
            "elevation_sha256": _sha(dem_path),
            "retained_basins_csv": str(csv_path),
            "waterways_gpkg": str(tmp_path / "waterways.gpkg"),
            "kml_primary": str(tmp_path / "p.kml"),
            "kml_secondary": str(tmp_path / "s.kml"),
        },
        "stitch": {
            "snap_tolerance_m": 10.0,
            "burn_buffer_m": 5.0,
            "quad_segs": 8,
            "max_connector_steps": 50,
            "flat_increment_m": 0.001,
            "tie_break_order": ["E", "NE", "N", "NW", "W", "SW", "S", "SE"],
            "self_hit_granularity": "reach",
            "stub_max_length_m": 25.0,
        },
        "outputs": {
            "run_dir": str(run),
            "gpkg": str(run / "cand.gpkg"),
            "adjacency": str(run / "cand.json"),
            "manifest": str(run / "manifest.json"),
            "publish_gpkg": str(tmp_path / "never" / "publish.gpkg"),
            "publish_adjacency": str(tmp_path / "never" / "publish.json"),
        },
        "consumer": {"allow_disconnected_load": False, "owner_adjudication_ref": ""},
    }


@pytest.fixture()
def toy(tmp_path: Path) -> tuple[list[Reach], dict, Path]:
    dem_path, csv_path = _write_toy_files(tmp_path)
    (tmp_path / "waterways.gpkg").write_text("")
    return _toy_reaches(), _toy_cfg(tmp_path, dem_path, csv_path), tmp_path


def test_resolve_config_aggregates_all_problems(tmp_path: Path) -> None:
    cfg = {
        "drainage": {
            "crs": "EPSG:32643",
            "grid": {"height": 200, "transform": TRANSFORM, "cell_area_m2": 100.0},
            "inputs": {
                "elevation_surface": "/nonexistent/elev.tif",
                "elevation_sha256": "x",
                "retained_basins_csv": "also-missing.csv",
                "waterways_gpkg": "w.gpkg",
                "kml_primary": "p.kml",
                "kml_secondary": "s.kml",
            },
            "stitch": {
                "burn_buffer_m": 5.0,
                "quad_segs": 8,
                "max_connector_steps": 10000,
                "flat_increment_m": 0.001,
                "tie_break_order": ["E", "NE", "N", "NW", "W", "SW", "S", "SE"],
                "self_hit_granularity": "reach",
                "stub_max_length_m": 25.0,
            },
            "outputs": {
                "run_dir": "r",
                "gpkg": "g",
                "adjacency": "a",
                "publish_gpkg": "pg",
                "publish_adjacency": "pa",
            },
            "consumer": {"allow_disconnected_load": False, "owner_adjudication_ref": ""},
        }
    }
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError) as ei:
        resolve_config(str(path))
    msg = str(ei.value)
    # ONE aggregated error names EVERY problem, not just the first.
    for fragment in (
        "grid.width",
        "stitch.snap_tolerance_m",
        "outputs.manifest",
        "retained_basins_csv",
    ):
        assert fragment in msg, f"aggregated error missing {fragment}: {msg}"
    assert msg.count("missing key") >= 3


def test_sha_gate_refuses_wrong_bytes_with_zero_writes(toy: tuple) -> None:
    reaches, cfg, tmp_path = toy
    cfg["inputs"]["elevation_sha256"] = "0" * 64
    with pytest.raises(StitchError, match="SHA gate"):
        stitch_components(reaches, cfg)
    assert not Path(cfg["outputs"]["run_dir"]).exists(), "SHA gate refusal must write NOTHING"


def test_toy_end_to_end_liveness_and_invariants(toy: tuple) -> None:
    """If counter liveness were broken (suppressed increments, vacuous stops) we would
    observe a static counter or a missing stop-condition outcome here."""
    reaches, cfg, tmp_path = toy
    result = stitch_components(reaches, cfg)
    m = result.metrics
    c = m["counters"]
    h = m["walk_outcome_histogram"]

    assert sum(h.values()) == 2 * len(reaches), "every walk must land in exactly one bucket"
    for outcome in (
        "reached_other",
        "junction_entry",
        "junction_entry_at_start",
        "pit",
        "domain_exit",
        "max_steps_abort",
    ):
        assert h[outcome] >= 1, f"stop condition {outcome} never fired: {h}"
    # Owner adjudication 2026-08-25 (D20): own-footprint re-entry is continuation,
    # so the retired abort must never fire; re-entries are counted instead.
    assert h["self_hit_abort"] == 0
    assert c["self_hit_count"] == 0
    assert c["self_reentry_count"] >= 1, "toy self-hit geometry must record re-entries"
    # Split-at-projection (D21) replaces stubs entirely.
    assert c["junction_split_count"] >= 1, "mid-piece junction entry must split a reach"
    assert c["dropped_connector_count"] >= 1
    assert c["dropped_edge_count"] >= 1, "parallel pair must produce a dedup drop"
    assert c["self_loop_dropped_count"] == 1
    # Structurally unreachable end-to-end (see module docstring); logic unit-tested below.
    assert c["zero_length_dropped_count"] == 0
    assert c["cycle_break_count"] == 0  # strict-descent toy terrain cannot close a cycle
    assert c["flat_flagged_edge_count"] >= 1, "basin-floor traversal must flag low confidence"
    assert isinstance(c["negative_slope_edge_count"], int)  # reported-not-asserted
    # Stubs retired by split-at-projection (D21): counters remain for manifest
    # stability but must stay zero; the mid-span entry now SPLITS its reach.
    assert c["stub_count"] == 0 and c["stub_overlength_count"] == 0
    assert c["split_unresolved_count"] == 0, "toy junctions are all within projection range"
    assert c["unresolved_outfall_count"] == 0

    assert m["component_count_pre_stitch"] == 11
    assert m["component_count_post_stitch"] > 1
    assert m["stranded_component_ids"], "disconnected components must be published"

    # Deterministic contiguous ids.
    assert [n.node_id for n in result.nodes] == list(range(1, len(result.nodes) + 1))
    assert [e.edge_id for e in result.edges] == list(range(1, len(result.edges) + 1))

    # Enum discipline, bidirectional tags, measured slopes on synthesised edges.
    max_stub = cfg["stitch"]["stub_max_length_m"]
    for e in result.edges:
        assert (e.edge_source == "synthesised") == (e.order == "synthetic_connector")
        basis = json.loads(e.direction_basis)
        assert set(basis) == {"terminating_rule", "flat_cells_traversed", "reached_other"}
        assert e.slope_m_per_m >= 0.0 and np.isfinite(e.slope_m_per_m)
        assert e.length_m > 0.0
        if e.edge_source == "synthesised":
            assert e.order == "synthetic_connector"
            if basis["terminating_rule"] == "junction_stub_downhill":
                assert e.length_m <= max_stub
        else:
            assert e.order in ("primary", "secondary")
    for n in result.nodes:
        assert n.node_type in ("junction", "outfall")
        assert n.elev_m == n.elev_m and np.isfinite(n.elev_m)
        if n.node_type == "outfall":
            assert n.outfall_reason in ("pit", "domain_exit")
        else:
            assert n.outfall_reason is None

    # Independent DAG re-check (not the producer's own claim).
    adj = {n.node_id: [] for n in result.nodes}
    indeg = {n.node_id: 0 for n in result.nodes}
    for e in result.edges:
        adj[e.from_node].append(e.to_node)
        indeg[e.to_node] += 1
    queue = sorted(v for v, dg in indeg.items() if dg == 0)
    seen = 0
    while queue:
        u = queue.pop()
        seen += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    assert seen == len(result.nodes), "final graph must be a DAG"

    # Fingerprint recomputes byte-exactly from returned edges (consumer-side check).
    payload = json.dumps(
        [
            [
                e.from_node,
                e.to_node,
                e.order,
                round(e.length_m, 6),
                hashlib.sha256(e.geometry.wkb_hex.encode()).hexdigest(),
            ]
            for e in result.edges
        ],
        separators=(",", ":"),
    )
    assert m["graph_fingerprint"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # Realized artefacts exist and manifest carries the stopped-adjudication status.
    for key in ("derived_pointer", "hybrid_filled_dem", "footprints"):
        assert m["output_sha256"][key], key
    manifest = json.loads(Path(cfg["outputs"]["manifest"]).read_text())
    assert manifest["status"] == "stopped_owner_adjudication"
    assert "expected connectivity_post_stitch_gt_1" in manifest["reasons"]
    assert manifest["metrics_headline"]["graph_fingerprint"] == m["graph_fingerprint"]

    lost = [tuple(x) for x in m["lost_walk_audit"]["entries"]]
    assert m["lost_walk_audit"]["total_no_edge"] == len(lost)
    # D21: stubs retired; the at-start probe now records a split or a skipped
    # zero-length connector, never a stub refusal.
    lost_kinds = {x[2] for x in lost}
    assert not any(k.endswith("stub_refused") for k in lost_kinds)


def test_two_surface_rule_realized_bytes(toy: tuple) -> None:
    """Hybrid must equal ORIGINAL pinned bytes outside the retained mask and differ
    inside; elevations used by nodes come from the pinned surface either way."""
    reaches, cfg, tmp_path = toy
    stitch_components(reaches, cfg)
    dem_path = Path(cfg["inputs"]["elevation_surface"])
    with rasterio.open(dem_path) as src:
        z = src.read(1)
    with rasterio.open(Path(cfg["outputs"]["run_dir"]) / "hybrid_filled_dem.tif") as src:
        hybrid = src.read(1)
    valid = np.isfinite(z)
    mask, diag = _build_retained_mask(
        z.astype(np.float32), valid, Path(cfg["inputs"]["retained_basins_csv"])
    )
    assert diag["mask_cell_count"] == int(mask.sum()) > 0
    assert np.allclose(hybrid[~mask], z[~mask]), "hybrid must preserve pinned bytes outside mask"
    assert not np.allclose(hybrid[mask], z[mask]), "mask interior must carry flat resolution"


def test_derived_pointer_pit_and_flat_evidence(toy: tuple) -> None:
    reaches, cfg, _tmp = toy
    stitch_components(reaches, cfg)
    with rasterio.open(Path(cfg["outputs"]["run_dir"]) / "derived_pntr_d8.tif") as src:
        ptr = src.read(1)
    assert ptr[23, 155] == 0, "hard-pit floor must be an unresolved pit (code 0)"
    floor = ptr[70:80, 170:190]
    assert (floor > 0).mean() > 0.5, "retained flat floor must be routable (non-zero codes)"
    west = ptr[100:107, 1:4]
    assert (west == 16).mean() > 0.5, "west slope must point W (ESRI 16)"


def test_degenerate_and_parallel_drop_logic() -> None:
    """Direct liveness for drops unreachable end-to-end (zero-length observed) and
    the observed-over-synthesised longer-among-equals rule."""

    def e(src, source, length, slope=0.01, order="synthetic_connector"):
        g = LineString([(0.0, 0.0), (float(length), 0.0)])
        return _EdgeCand(
            src=src,
            order=order,
            edge_source=source,
            direction_confidence="high",
            geometry=g,
            length_m=length,
            slope_m_per_m=slope,
            kind="observed" if source == "observed" else "connector",
        )

    # Zero-length OBSERVED edge between distinct nodes -> zero_length_dropped_count.
    survivors, recs, loops, zeros = _apply_dedup_and_degenerate([e((0, 1), "observed", 0.01)])
    assert zeros == 1 and loops == 0 and survivors == []
    # Self-loop -> self_loop_dropped_count.
    survivors, _r, loops, _z = _apply_dedup_and_degenerate([e((3, 3), "observed", 50.0)])
    assert loops == 1 and survivors == []
    # Parallel observed pair, same order: LONGER kept, shorter dropped.
    ea = e((0, 1), "observed", 80.0, order="secondary")
    eb = e((0, 1), "observed", 95.0, order="secondary")
    survivors, recs, _, _ = _apply_dedup_and_degenerate([ea, eb])
    assert len(survivors) == 1 and survivors[0].length_m == 95.0 and len(recs) == 1
    # Same node pair: OBSERVED beats SYNTHESISED regardless of length.
    eo = e((0, 1), "observed", 10.0, order="synthetic_connector")
    es = e((0, 1), "synthesised", 900.0, order="synthetic_connector")
    survivors, _r, _, _ = _apply_dedup_and_degenerate([es, eo])
    assert len(survivors) == 1 and survivors[0].edge_source == "observed"


def test_dag_break_red_demo_cfg_flag() -> None:
    """V5 RED DEMO. Mutation: disable DAG-break via cfg flag
    (`stitch.dag_break_enabled=false`, threaded into _enforce_dag by
    stitch_components) while the candidate contains a cycle -> the production
    enforcement raises 'cycles detected' instead of emitting a cyclic graph.
    With the flag on, the same candidate is broken at the lowest-slope synthetic
    edge and rounds are recorded."""
    fwd = _EdgeCand(
        src=(0, 1),
        order="synthetic_connector",
        edge_source="synthesised",
        direction_confidence="high",
        geometry=LineString([(0, 0), (30, 0)]),
        length_m=30.0,
        slope_m_per_m=1.0 / 300.0,
        kind="stub",
        prov_id=1,
    )
    rev = _EdgeCand(
        src=(1, 0),
        order="synthetic_connector",
        edge_source="synthesised",
        direction_confidence="high",
        geometry=LineString([(30, 0), (0, 0)]),
        length_m=30.0,
        slope_m_per_m=2.0 / 300.0,
        kind="stub",
        prov_id=2,
    )
    with pytest.raises(StitchError, match="cycles detected"):
        _enforce_dag([fwd, rev], break_enabled=False)
    kept, rounds = _enforce_dag([fwd, rev], break_enabled=True)
    assert len(rounds) == 1
    assert rounds[0]["cycle_size"] == 2
    assert rounds[0]["dropped_edge_id"] == fwd.prov_id  # lowest slope breaks first
    assert rounds[0]["pool"] == "synthetic"
    assert rounds[0]["wcc_before"] == 1 and rounds[0]["wcc_after"] == 1
    assert len(kept) == 1 and kept[0].prov_id == rev.prov_id


def test_graph_shim_reexports_entrypoint() -> None:
    from jaladhar.drainage import graph
    from jaladhar.drainage.stitch import stitch_components as impl

    assert graph.stitch_components is impl
    assert graph.__all__ == ["Reach", "NodeRec", "EdgeRec", "StitchResult", "stitch_components"]
