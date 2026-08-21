"""Synthetic checks for independent catchment face-flux accounting.

V2 observable: if face orientation or units are wrong, hand-computed export
from a small grid changes. If missing flux is replaced by a remainder, the
unknown-account test returns a number instead of ``None``.

V5 red mutation record: replacing ``mask[:, :-1] - mask[:, 1:]`` with its
negative made ``test_face_flux_orientation_and_units`` return -100 instead of
+100 m3. Replacing the missing-measurement branch with
``rainfall-drain-storage`` made ``test_missing_flux_cannot_close_budget`` fail.

V6: code was wrong. The previous implementation defined routed outflow as the
remainder and then used that same remainder to assert closure; the test and
physical conservation requirement were not changed.

Scope: these tests cover synthetic 2x2 and 2x3 grids for one timestep. Full
domain/full-duration closure remains BLOCKED until a solver rerun records every
catchment-boundary face flux and timestep.
"""

import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from jaladhar.analysis.water_tracer import (
    integrate_catchment_boundary_flux,
    integrate_catchment_transport_depth,
    resolve_boundary_account,
)
from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.state import build_static_fields


def test_face_flux_orientation_and_units() -> None:
    mask = np.array([[True, False], [False, False]])
    fx_m = np.array([[2.0], [99.0]])
    fy_m = np.array([[3.0, 99.0]])

    volume = integrate_catchment_boundary_flux(mask, fx_m, fy_m, cell_area_m2=20.0)

    assert volume == pytest.approx((2.0 + 3.0) * 20.0)


def test_internal_faces_cancel_for_realized_increments() -> None:
    mask = np.array([[True, True, False], [False, False, False]])
    fx_m = np.array([[100.0, 2.0], [50.0, 60.0]])
    fy_m = np.array([[0.5, -0.5, 70.0]])

    volume = integrate_catchment_boundary_flux(
        mask,
        fx_m,
        fy_m,
        cell_area_m2=20.0,
    )

    # Internal fx=100 and wholly external faces are excluded. The second fy
    # is inward, so its signed contribution reduces net export.
    assert volume == pytest.approx((2.0 + 0.5 - 0.5) * 20.0)


def test_cumulative_cell_transport_matches_oriented_boundary_flux() -> None:
    mask = np.array([[True, True, False], [False, False, False]])
    fx_m = np.array([[100.0, 2.0], [50.0, 60.0]])
    fy_m = np.array([[0.5, -0.5, 70.0]])
    cell_transport = np.zeros(mask.shape, dtype=np.float64)
    cell_transport[:, :-1] -= fx_m
    cell_transport[:, 1:] += fx_m
    cell_transport[:-1, :] -= fy_m
    cell_transport[1:, :] += fy_m

    from_faces = integrate_catchment_boundary_flux(mask, fx_m, fy_m, cell_area_m2=20.0)
    from_cells = integrate_catchment_transport_depth(mask, cell_transport, cell_area_m2=20.0)

    assert from_cells == pytest.approx(from_faces)


def test_limiter_active_realized_flux_matches_state_change() -> None:
    """Post-limiter diagnostics conserve; returned raw q overstates transport.

    Observable if false: oriented realized transport differs from water gained
    outside the one-cell catchment. V5 mutation: exposing pre-limiter ``fx``
    in diagnostics makes measured transport exceed 0.1 m3 and this test red.
    Scope: one limiter-active step on four cells; full-run recording is BLOCKED.
    """
    h = torch.tensor([[0.1, 0.0], [0.0, 0.0]], dtype=torch.float32)
    qx = torch.tensor([[10.0], [0.0]], dtype=torch.float32)
    qy = torch.zeros((1, 2), dtype=torch.float32)
    static = SimpleNamespace(
        dz_x=torch.zeros((2, 1)),
        dz_y=torch.zeros((1, 2)),
        n_x=torch.zeros((2, 1)),
        n_y=torch.zeros((1, 2)),
        c_x=torch.ones((2, 1)),
        c_y=torch.ones((1, 2)),
        drain_cap_m_s=torch.zeros((2, 2)),
        infil_rate_m_s=torch.zeros((2, 2)),
    )
    params = SolverParams(
        dx=1.0,
        gravity=9.81,
        depth_threshold_m=1e-3,
        hf_floor_m=1e-6,
        wetdry_mode="hard",
        ramp_width_m=1e-3,
        min_conveyance_factor=0.05,
        boundary_mode="closed",
        min_bed_slope=1e-4,
    )

    h_new, qx_raw, qy_raw, diagnostics = acc_step(h, qx, qy, static, 1.0, params)
    raw_pre_limiter_depth_m = float(qx_raw[0, 0] + qy_raw[0, 0])
    realized_volume_m3 = integrate_catchment_boundary_flux(
        np.array([[True, False], [False, False]]),
        diagnostics["realized_face_depth_increment_x_m"].numpy(),
        diagnostics["realized_face_depth_increment_y_m"].numpy(),
        cell_area_m2=1.0,
    )
    outside_gain_m3 = float(h_new.sum() - h_new[0, 0])

    assert raw_pre_limiter_depth_m > float(h[0, 0])
    assert realized_volume_m3 == pytest.approx(outside_gain_m3, abs=1e-7)
    assert realized_volume_m3 == pytest.approx(float(h[0, 0] - h_new[0, 0]), abs=1e-7)
    assert realized_volume_m3 < raw_pre_limiter_depth_m


def test_cell_transport_includes_realized_domain_outfall() -> None:
    """A full-domain mask exports exactly the solver's boundary outflow.

    Observable if false: internal faces cancel but the transport raster reports
    zero while the independent boundary diagnostic is positive. V5 red
    mutation: omitting ``-b_out`` from the cell diagnostic returns zero.
    Scope: one free-boundary step on a 3x3 flat mathematical test surface.
    """
    shape = (3, 3)
    static = build_static_fields(
        np.full(shape, 800.0),
        np.full(shape, 0.03),
        np.ones(shape),
        np.zeros(shape),
        dx=1.0,
        infil_mm_hr=0.0,
        min_conveyance_factor=0.05,
        min_bed_slope=1e-4,
    )
    params = SolverParams(
        dx=1.0,
        gravity=9.81,
        depth_threshold_m=1e-3,
        hf_floor_m=1e-3,
        wetdry_mode="hard",
        ramp_width_m=1e-3,
        min_conveyance_factor=0.05,
        boundary_mode="free",
        min_bed_slope=1e-4,
    )
    h = torch.full(shape, 0.2)
    _, _, _, diagnostics = acc_step(
        h, torch.zeros((3, 2)), torch.zeros((2, 3)), static, 0.1, params
    )

    exported = integrate_catchment_transport_depth(
        np.ones(shape, dtype=bool),
        diagnostics["transport_depth_change_m"].numpy(),
        cell_area_m2=1.0,
    )

    assert float(diagnostics["boundary_out_m"]) > 0.0
    assert exported == pytest.approx(float(diagnostics["boundary_out_m"]), abs=1e-8)


def test_realized_sink_fields_match_independent_scalar_diagnostics() -> None:
    """Per-cell sink instruments sum to the mass-budget observables.

    Observable if false: catchment sink rasters and global sink accounting
    disagree. V5 red mutation: recording configured capacity instead of
    realized removal exceeds available water and fails this equality.
    Scope: one closed-boundary 2x2 step with active drain and infiltration.
    """
    shape = (2, 2)
    static = build_static_fields(
        np.full(shape, 800.0),
        np.full(shape, 0.03),
        np.ones(shape),
        np.full(shape, 360.0),
        dx=1.0,
        infil_mm_hr=180.0,
        min_conveyance_factor=0.05,
        min_bed_slope=1e-4,
    )
    params = SolverParams(
        dx=1.0,
        gravity=9.81,
        depth_threshold_m=1e-3,
        hf_floor_m=1e-3,
        wetdry_mode="hard",
        ramp_width_m=1e-3,
        min_conveyance_factor=0.05,
        boundary_mode="closed",
        min_bed_slope=1e-4,
    )
    _, _, _, diagnostics = acc_step(
        torch.full(shape, 0.001),
        torch.zeros((2, 1)),
        torch.zeros((1, 2)),
        static,
        10.0,
        params,
    )

    assert float(diagnostics["drained_depth_m"].sum(dtype=torch.float64)) == pytest.approx(
        float(diagnostics["drained_m"])
    )
    assert float(
        diagnostics["infiltrated_depth_m"].sum(dtype=torch.float64)
    ) == pytest.approx(float(diagnostics["infiltrated_m"]))


def test_missing_flux_cannot_close_budget_and_serializes_as_null() -> None:
    pct, residual, closed, status = resolve_boundary_account(
        rainfall_m3=100.0,
        drain_m3=20.0,
        infiltration_m3=0.0,
        end_storage_m3=30.0,
        measured_outflux_m3=None,
    )

    assert pct is None
    assert residual is None
    assert closed is False
    assert status.startswith("UNRESOLVED:")
    encoded = json.dumps({"outflux_pct": pct, "residual_m3": residual}, allow_nan=False)
    assert encoded == '{"outflux_pct": null, "residual_m3": null}'


def test_measured_flux_produces_independent_signed_residual() -> None:
    pct, residual, closed, status = resolve_boundary_account(
        rainfall_m3=100.0,
        drain_m3=20.0,
        infiltration_m3=5.0,
        end_storage_m3=30.0,
        measured_outflux_m3=40.0,
    )

    assert pct == pytest.approx(40.0)
    assert residual == pytest.approx(5.0)
    assert closed is False
    assert status.startswith("MEASURED, NOT CLOSED:")


def test_flux_shape_contract_rejects_misaligned_faces() -> None:
    with pytest.raises(ValueError, match="realized face-increment shapes"):
        integrate_catchment_boundary_flux(
            np.ones((2, 2), dtype=bool),
            np.zeros((2, 2)),
            np.zeros((1, 2)),
            cell_area_m2=100.0,
        )
