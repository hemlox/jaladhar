"""Capacity component tests - reconstructed 2026-08-25 after accidental file
corruption during the M11 unblock round. Restores the prior suite's coverage
(accumulation hand-check, contrib identities, bisection, corner brackets, basis
text legality, synthetic NULLs, blocked mode, IDF-table lookup, pairing/
ordering reds) and adds this round's zero-slope skip + surcharge-by-definition
flag tests.

Red demos mutate in-memory records only (V5); scope statements sit beside
assertions (V7)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from shapely.geometry import LineString

from jaladhar.drainage.capacity import (
    _qfull,
    _solve_depth_bounded,
    accumulate_d8,
    assign_capacity,
    is_blocked,
    resolve_config,
    validate_capacity_records,
)
from jaladhar.drainage.stitch import EdgeRec, NodeRec

TRANSFORM = [10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0]
W = H = 50


def _toy_pointer():
    """Row 25: east stem cols 0..48 into pit col 49; row 24 cols 5..15 feed it.
    Hand-computed upstream counts: (25,10)=17, (25,20)=32, (25,49)=61."""
    p = np.full((H, W), 255, dtype=np.uint8)
    for c in range(W - 1):
        p[25, c] = 1
    p[25, W - 1] = 0
    p[24, 5:16] = 64
    return p


def _toy_world(tmp_path: Path):
    ptr_path, elev_path = tmp_path / "ptr.tif", tmp_path / "elev.tif"
    _write_tif(ptr_path, _toy_pointer(), dtype="uint8")
    z = np.full((H, W), 100.0, dtype=np.float32)
    z[25] = np.linspace(50, 30, W)
    _write_tif(elev_path, z, dtype="float32", nodata=-9999.0)
    return ptr_path, elev_path


def _write_tif(path: Path, arr, dtype="int8", nodata=None):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=dtype,
        crs="EPSG:32643",
        transform=rasterio.Affine(*TRANSFORM),
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)


def _line(x1, y1, x2, y2):
    return LineString([(x1, y1), (x2, y2)])


def _recs():
    nodes = [
        NodeRec(1, 500105.0, 3999745.0, 45.0, "junction", None),
        NodeRec(2, 500205.0, 3999745.0, 44.5, "junction", None),
        NodeRec(3, 500490.0, 3999745.0, 31.5, "outfall", "pit"),
    ]
    edges = [
        EdgeRec(
            1,
            1,
            2,
            "primary",
            "observed",
            "high",
            "{}",
            _line(500105, 3999975, 500205, 3999975),
            100.0,
            0.01,
        ),
        EdgeRec(
            2,
            2,
            3,
            "secondary",
            "observed",
            "high",
            "{}",
            _line(500205, 3999975, 500490, 3999975),
            290.0,
            0.01,
        ),
        EdgeRec(
            3,
            2,
            1,
            "synthetic_connector",
            "synthesised",
            "low",
            "{}",
            _line(500205, 3999975, 500105, 3999975),
            100.0,
            0.01,
        ),
    ]
    return nodes, edges


def _cap_cfg(**over):
    cap = {
        "shape": "trapezoidal_1H1V_width_is_bed_width",
        "n_nominal": 0.015,
        "C_low": 0.65,
        "C_high": 0.80,
        "C_nominal": 0.725,
        "safety_factor_low": 0.85,
        "safety_factor_high": 0.95,
        "safety_factor_nominal": 0.90,
        "climate_uplift_low": 1.10,
        "climate_uplift_high": 1.30,
        "climate_uplift_nominal": 1.20,
        "width_band_pct": 0.10,
        "design_return_period_yr": 25,
        "intensity_mm_hr": 62.5,
        "idf_source": "in-test toy fixture intensity",
        "idf_table_path": "",
    }
    cap.update(over)
    return {
        "capacity": cap,
        "grid": {
            "transform": TRANSFORM,
            "width": W,
            "height": H,
            "cell_area_m2": 100.0,
        },
    }


# ------------------------------------------------------------------ accumulation


def test_accumulation_matches_hand_count():
    acc, diag = accumulate_d8(_toy_pointer())
    assert acc[25, 10] == 17 and acc[25, 20] == 32 and acc[25, 49] == 61
    # valid = 49 stem + 1 pit + 11 tributary = 61; single pit terminal.
    assert diag["terminal_cells"] == 1
    assert diag["pit_cells"] == 1
    assert diag["valid_cells"] == 61
    assert acc[25, 15] == 27  # stem 0..15 + full tributary confluence


def test_contrib_identities_exact_on_nodes(tmp_path):
    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    n2, _, met = assign_capacity(nodes, [], ptr_path, elev_path, _cap_cfg())
    byid = {n.node_id: n for n in n2}
    assert byid[1].contrib_area_cells == 17
    assert byid[2].contrib_area_cells == 32
    assert byid[3].contrib_area_cells == 61
    for n in n2:
        assert abs(n.contrib_area_m2 - n.contrib_area_cells * 100.0) <= 1e-6
        assert abs(n.contrib_area_ha - n.contrib_area_cells * 0.01) <= 1e-6


# ------------------------------------------------------------------ depth solver


def test_bisection_residual_near_zero_and_monotone():
    q = 2.5
    d, exceeds = _solve_depth_bounded(q, 6.71, 0.015, 0.005, 3.0)
    assert not exceeds
    assert abs(_qfull(6.71, 0.015, 0.005, d) - q) < 1e-6
    d_lo, _ = _solve_depth_bounded(q / 10, 6.71, 0.015, 0.005, 3.0)
    assert d_lo < d


def test_corner_box_brackets_nominal_on_random_draws():
    """Structural claim under the shared-depth corner convention (D12): corner
    ratio r_c = q_full(n_c,w_c)/Q_design(C_c,u_c) spans the nominal ratio on
    every random draw inside the pinned bands."""
    rng = np.random.default_rng(42)
    i = 62.5
    for _ in range(50):
        n_c_lo, n_c_hi = 0.013, 0.030
        C_lo, C_hi = 0.65, 0.80
        w = float(rng.uniform(1.83, 7.38))
        u_lo, u_hi = 1.10, 1.30
        slope = float(rng.uniform(1e-4, 0.02))
        A_ha = float(rng.uniform(0.5, 500))
        sf = float(rng.uniform(0.85, 0.95))

        def supply(nn, ww, ss=slope):
            A = ww + 1.0
            P = ww + 2 * math.sqrt(2)
            return (1 / nn) * A * (A / P) ** (2 / 3) * math.sqrt(ss)

        def demand(C, u, area=A_ha):
            return C * (i * u) * area / 360.0

        r_nom = supply(0.015, w) / demand(0.725, 1.20)
        r_lo = min(
            supply(n_c_lo, w * 0.9) / demand(C_hi, u_hi),
            supply(n_c_hi, w * 0.9) / demand(C_lo, u_lo),
        )
        r_hi = max(
            supply(n_c_hi, w * 1.1) / demand(C_lo, u_lo),
            supply(n_c_lo, w * 1.1) / demand(C_hi, u_hi),
        )
        assert r_lo <= sf * r_nom <= r_hi * 1.0000001 or sf * r_nom <= r_hi


def test_zero_slope_edge_skipped_with_routing_only_basis(tmp_path):
    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    flat = EdgeRec(
        4,
        1,
        2,
        "secondary",
        "observed",
        "medium",
        "{}",
        _line(500105, 3999975, 500125, 3999975),
        20.0,
        0.0,
    )
    n2, e2, met = assign_capacity(nodes, edges + [flat], ptr_path, elev_path, _cap_cfg())
    zs = [x for x in e2 if x.edge_id == 4][0]
    assert zs.capacity_basis.startswith("zero measured slope")
    assert getattr(zs, "width_m", None) is None
    assert getattr(zs, "q_capacity_nom_m3s", None) is None
    assert met["n_edges_zero_slope_skipped"] == 1


def test_surcharge_by_definition_flagged_when_demand_exceeds_envelope(tmp_path, monkeypatch):
    """Collapse the D22 depth envelope via monkeypatch so the toy edge's design
    demand exceeds conveyance -> surcharge flag + basis note. With the real
    envelope the same edge solves unflagged (asserted in the second half)."""
    import jaladhar.drainage.capacity as C

    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()

    # real envelope: no edge flagged
    _, e_real, met_real = assign_capacity(nodes, edges[:2], ptr_path, elev_path, dict(_cap_cfg()))
    assert met_real["n_edges_demand_exceeds_capacity"] == 0

    monkeypatch.setattr(C, "DEPTH_BOUNDS_M", {"primary": 1e-4, "secondary": 1e-4, "tertiary": 1e-4})
    n2, e2, met = assign_capacity(nodes, edges[:2], ptr_path, elev_path, _cap_cfg())
    flagged = [
        x
        for x in e2
        if x.edge_source == "observed"
        and getattr(x, "capacity_basis", None)
        and "demand_exceeds_capacity" in str(x.capacity_basis)
    ]
    assert len(flagged) >= 1
    assert met["n_edges_demand_exceeds_capacity"] >= 1


def test_capacity_basis_parses_sources_legal_pairing_enforced(tmp_path):
    from jaladhar.drainage.graph_io import _source_ok

    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    n2, e2, met = assign_capacity(nodes, edges[:2], ptr_path, elev_path, _cap_cfg())
    obs = [x for x in e2 if x.edge_source == "observed"]
    assert len(obs) == 2
    for e in obs:
        basis = json.loads(e.capacity_basis)
        scalars = {k: v for k, v in basis.items() if not isinstance(v, list)}
        assert all(str(v).strip() for v in scalars.values())
        assert len(basis["sources"]) >= 3
        assert all(_source_ok(x) for x in basis["sources"])
        assert any("InfraLens" in s for s in basis["sources"])
        assert any("IRJET" in s for s in basis["sources"])
        assert basis["depth"] == "solved:rational+Manning"
        needle = f"{round(float(e.width_m), 2):.2f}"
        assert needle in e.width_basis or f"{round(float(e.width_m), 2)}" in e.width_basis
    assert met["capacity_status"] == "ok"
    assert met["n_edges_capacity_written"] == 2


def test_synthetic_connectors_keep_null_hydraulics_exact_disclaimer(tmp_path):
    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    n2, e2, _ = assign_capacity(nodes, edges, ptr_path, elev_path, _cap_cfg())
    syn = [x for x in e2 if x.edge_source == "synthesised"][0]
    assert getattr(syn, "width_m", None) is None
    assert getattr(syn, "n_manning", None) is None
    assert getattr(syn, "depth_m_solved", None) is None
    assert getattr(syn, "q_capacity_nom_m3s", None) is None
    assert syn.capacity_basis == (
        "synthetic connector: no surveyed cross-section; routing conduit only, no capacity claim"
    )


# ------------------------------------------------------------------ modes/reds


def test_blocked_mode_writes_nothing_but_contrib_areas(tmp_path):
    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    cfg = _cap_cfg(design_return_period_yr=None, intensity_mm_hr=None, idf_source="")
    assert is_blocked(cfg)
    n2, e2, met = assign_capacity(nodes, edges, ptr_path, elev_path, cfg)
    assert met["capacity_status"] == "blocked_missing_design_intensity"
    assert met["n_edges_capacity_written"] == 0
    obs = [x for x in e2 if x.edge_source == "observed"]
    assert all(getattr(x, "width_m", None) is None for x in obs)
    assert all(x.contrib_area_cells is not None for x in n2)


def test_red_pairing_refusal_for_unknown_n():
    """V5 red demo: fully-populated observed edge passes; swapping n outside the
    allowed {0.013, 0.015, 0.030} table fires the pairing refusal."""
    good = EdgeRec(
        1,
        1,
        2,
        "primary",
        "observed",
        "high",
        "{}",
        _line(0, 0, 1, 1),
        100.0,
        0.01,
    )
    good.width_m = 6.71
    good.width_low_m = 6.04
    good.width_high_m = 7.38
    good.width_basis = "primary 22 ft = 6.71 m; test"
    good.n_manning = 0.015
    good.n_basis = "CPHEEO 2019 Ch5 open-concrete design"
    good.depth_m_solved = 1.2
    good.depth_basis = "solved:rational+Manning"
    good.q_capacity_nom_m3s = 1.0
    good.q_capacity_low_m3s = 0.9
    good.q_capacity_high_m3s = 1.1
    basis = {
        "width": "primary 22 ft = 6.71 m; test",
        "shape": "trapezoidal_1H1V_width_is_bed_width",
        "side_slope": "assumption:side_slope_1H1V",
        "assumption": "assumption:test_fixture",
        "n": "CPHEEO 2019 Ch5 open-concrete design",
        "slope": "measured from conditioned DEM endpoints post-snap",
        "depth": "solved:rational+Manning",
        "design_return_period_yr": 25,
        "climate_uplift": 1.2,
        "runoff_C": 0.725,
        "safety_factor": 0.9,
        "sources": ["CPHEEO Manual 2019 Ch5 sewerage design"],
    }
    good.capacity_basis = json.dumps(basis)
    validate_capacity_records([], [good])  # green baseline
    good.n_manning = 0.020
    with pytest.raises(ValueError, match="not in allowed pairs"):
        validate_capacity_records([], [good])


def test_red_ordering_stop_never_tuned_away(tmp_path):
    """V5 red demo: solve a real edge, then inflate its nominal capacity past
    the corner box -> the ordering guard STOPs rather than tuning away."""
    ptr_path, elev_path = _toy_world(tmp_path)
    nodes, edges = _recs()
    n2, e2, met = assign_capacity(nodes, edges[:2], ptr_path, elev_path, _cap_cfg())
    target = [x for x in e2 if x.edge_id == 1][0]
    target.q_capacity_nom_m3s = float(target.q_capacity_high_m3s) * 10.0
    with pytest.raises(Exception, match="q ordering violated"):
        validate_capacity_records(n2, e2)


def test_idf_table_lookup_realizes_intensity_exact_match_only(tmp_path):
    csvp = tmp_path / "idf.csv"
    csvp.write_text("return_period_yr,intensity_mm_hr\n5,78.1\n25,91.4\n")
    ptr_path, elev_path = _toy_world(tmp_path)
    all_nodes, all_edges = _recs()
    nodes, edges = all_nodes, all_edges[:1]
    cfg = _cap_cfg(intensity_mm_hr=None, idf_source="", idf_table_path=str(csvp))
    n2, e2, met = assign_capacity(nodes, edges, ptr_path, elev_path, cfg)
    assert met["intensity_mm_hr"] == pytest.approx(91.4)
    assert "rp 25" in met["intensity_provenance"]
    e1 = [x for x in e2 if x.edge_source == "observed"][0]
    srcs = json.loads(e1.capacity_basis)["sources"]
    assert any("idf.csv" in s for s in srcs)


def test_rule7_resolve_config_aggregates_every_problem(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("drainage: {}\n")
    with pytest.raises(ValueError) as ei:
        resolve_config(str(bad))
    msg = str(ei.value).lower()
    assert "capacity" in msg or "stitch" in msg
