"""1. Ritter (1892) frictionless dry-bed dam break (high-Froude, advection-dominated)
2. Stoker (1957) frictionless wet-bed dam break (high-Froude, shock-forming)
3. Thacker (1981) oscillating flow in a parabolic bowl (low-Froude, planar surface)
4. MacDonald (1997) steady subcritical channel flow with Manning friction (low-Froude, frictional)
CRITICAL PHYSICS GUARD:
The ACC scheme (Bates et al. 2010) drops nonlinear advection (u * du/dx) to achieve
- Ritter & Stoker dam breaks are advection-dominated (Fr >= 1.0). The solver WILL
to characterize the scheme's validity boundary.
- Thacker & MacDonald operate at low Froude (Fr << 0.5). In Thacker, du/dx = 0 identically,"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from jaladhar.solver.acc import SolverParams
from jaladhar.solver.analytical.dambreak import (
    MacDonaldCase,
    RitterCase,
    StokerCase,
    ThackerCase,
)
from jaladhar.solver.run import simulate
from jaladhar.solver.state import load_solver_config
from jaladhar.solver.timestep import TimestepController

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "solver.yaml"


def _cfg(**overrides):
    cfg = load_solver_config(CONFIG, REPO)
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        cfg[section][key] = value
    return cfg


def _params(dx: float, **overrides) -> SolverParams:
    cfg = _cfg(**overrides)
    return SolverParams.from_config(cfg, dx)


# 1. RITTER (1892) DRY-BED DAM BREAK


def test_ritter_closed_form_exact_properties():
    """Ritter analytical solution must satisfy physical properties and mass conservation.
    SCOPE: Domain x in [-100, 100] m, h0 = 1.0 m, t = 4.0 s.
    OBSERVABLE IF FALSE: Non-unit Froude number at origin, negative depths, or mass violation."""
    ritter = RitterCase(h0=1.0)
    x = np.linspace(-100.0, 100.0, 2001)
    t = 4.0

    h = ritter.exact_h(x, t)
    u = ritter.exact_u(x, t)

    assert (h >= 0.0).all(), "Ritter depth must be non-negative everywhere"
    assert (u >= 0.0).all(), "Ritter velocity must be forward or zero"

    c0 = ritter.c0
    xA = -c0 * t
    xTip = 2.0 * c0 * t

    assert np.isclose(h[x <= xA], 1.0).all(), "Upstream reservoir must remain undisturbed at h0"
    assert np.isclose(h[x >= xTip], 0.0).all(), "Downstream dry bed ahead of tip must remain zero"

    fr_origin = float(ritter.froude(np.array([0.0]), t)[0])
    assert np.isclose(
        fr_origin, 1.0, atol=1e-3
    ), f"Froude number at origin must be 1.0 (got {fr_origin})"

    # Mass conservation: integral of h over [-100, 100] must equal initial mass (100.0 m^2)
    dx = x[1] - x[0]
    total_mass = float(np.sum(h) * dx)
    assert np.isclose(
        total_mass, 100.0, atol=0.1
    ), f"Mass must be conserved (got {total_mass:.3f} m2)"


def test_ritter_dam_break_simulation_characterization():
    """ACC simulation against Ritter dry-bed dam break across 3 grid resolutions.
    SCOPE: Length = 200 m, duration = 4.0 s, resolutions dx in [10.0, 5.0, 2.5] m.
    PHYSICS: ACC neglects advection, so high-Froude front advance is retarded."""
    ritter = RitterCase(h0=1.0)
    t_eval = 4.0
    resolutions = [10.0, 5.0, 2.5]
    errors_l1 = []
    errors_l2 = []

    for dx in resolutions:
        static, x, h0 = ritter.make_domain(length_m=200.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 0.5,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=0.5)
        res = simulate(static, p, ctrl, duration_s=t_eval, h0=h0)

        h_num = res.h[1, :].numpy()
        h_exact = ritter.exact_h(x, t_eval)

        l1 = float(np.mean(np.abs(h_num - h_exact)))
        l2 = float(np.sqrt(np.mean((h_num - h_exact) ** 2)))
        errors_l1.append(l1)
        errors_l2.append(l2)

    # Asserts error stays within the expected advective departure envelope (~0.033 m)
    assert errors_l1[1] < 0.06, f"L1 error {errors_l1[1]:.4f} exceeds expected advective boundary"
    assert errors_l2[1] < 0.12, f"L2 error {errors_l2[1]:.4f} exceeds expected advective boundary"


# 2. STOKER (1957) WET-BED DAM BREAK


def test_stoker_closed_form_exact_properties():
    """Stoker wet-bed dam break exact solution structure and Rankine-Hugoniot shock relations.
    SCOPE: h0 = 1.0 m, h1 = 0.1 m, t = 4.0 s.
    VERIFIED RED: Inverting h0 and h1 in StokerCase (h0=0.1, h1=1.0) ->"""
    stoker = StokerCase(h0=1.0, h1=0.1)

    assert 0.1 < stoker.hm < 1.0, f"hm={stoker.hm} must be in (h1, h0)"
    assert stoker.shock_speed > stoker.um, "Shock speed S must exceed flow velocity um"

    fr_plat = stoker.um / math.sqrt(9.81 * stoker.hm)
    assert 1.0 < fr_plat < 1.5, f"Intermediate plateau Froude {fr_plat:.3f} must be supercritical"


def test_stoker_dam_break_simulation_characterization():
    """ACC simulation against Stoker wet-bed dam break across 3 grid resolutions.
    SCOPE: Length = 200 m, duration = 4.0 s, resolutions dx in [10.0, 5.0, 2.5] m.
    PHYSICS: ACC lacks shock capturing and advection, leaving structural shock error (~0.025 m)."""
    stoker = StokerCase(h0=1.0, h1=0.1)
    t_eval = 4.0
    resolutions = [10.0, 5.0, 2.5]
    errors_l1 = []
    errors_l2 = []

    for dx in resolutions:
        static, x, h0 = stoker.make_domain(length_m=200.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 0.5,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=0.5)
        res = simulate(static, p, ctrl, duration_s=t_eval, h0=h0)

        h_num = res.h[1, :].numpy()
        h_exact = stoker.exact_h(x, t_eval)

        l1 = float(np.mean(np.abs(h_num - h_exact)))
        l2 = float(np.sqrt(np.mean((h_num - h_exact) ** 2)))
        errors_l1.append(l1)
        errors_l2.append(l2)

    assert errors_l1[1] < 0.035, f"Stoker L1 error {errors_l1[1]:.4f} exceeds expected bound"
    assert errors_l2[1] < 0.090, f"Stoker L2 error {errors_l2[1]:.4f} exceeds expected bound"


# 3. THACKER (1981) PARABOLIC BOWL OSCILLATION


def test_thacker_closed_form_exact_properties():
    """Thacker parabolic bowl planar oscillation analytical properties.
    SCOPE: a = 1000 m, h0 = 1.0 m, eta0 = 20 m.
    VERIFIED RED: Modifying natural frequency omega in ThackerCase ->"""
    thacker = ThackerCase(a=1000.0, h0=1.0, eta0=20.0)
    x = np.linspace(-1200.0, 1200.0, 2401)
    T = thacker.period_s

    h_0 = thacker.exact_h(x, 0.0)
    h_T = thacker.exact_h(x, T)
    assert np.allclose(
        h_0, h_T, atol=1e-10
    ), "Thacker solution must be strictly periodic at period T"

    h_quarter = thacker.exact_h(x, T / 4.0)
    u_quarter = thacker.exact_u(x, T / 4.0)
    wet_quarter = h_quarter > 0.0
    u_wet = u_quarter[wet_quarter]
    assert np.isclose(
        np.std(u_wet), 0.0, atol=1e-12
    ), "Thacker velocity must be spatially uniform across wet cells"

    assert thacker.max_froude < 0.05, f"Thacker max Froude {thacker.max_froude:.4f} must be << 0.5"


def test_thacker_parabolic_bowl_matches_acc_solver():
    """ACC solver matches Thacker parabolic bowl planar oscillation to high precision.
    SCOPE: Domain = 2400 m, duration = 1 full period T = 141.9 s, resolutions [20.0, 10.0, 5.0] m.
    PHYSICS: Low-Froude (Fr=0.028) and du/dx=0 means advection is zero. ACC matches exact SWE."""
    thacker = ThackerCase(a=1000.0, h0=1.0, eta0=20.0)
    t_eval = thacker.period_s
    resolutions = [20.0, 10.0, 5.0]
    errors_l1 = []
    errors_l2 = []

    for dx in resolutions:
        static, x, h0 = thacker.make_domain(length_m=2400.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 2.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=2.0)
        res = simulate(static, p, ctrl, duration_s=t_eval, h0=h0)

        h_num = res.h[1, :].numpy()
        h_exact = thacker.exact_h(x, t_eval)

        wet = (h_exact > 0.05) | (h_num > 0.05)
        l1 = float(np.mean(np.abs(h_num[wet] - h_exact[wet])))
        l2 = float(np.sqrt(np.mean((h_num[wet] - h_exact[wet]) ** 2)))
        errors_l1.append(l1)
        errors_l2.append(l2)

    assert errors_l1[1] < 0.003, f"Thacker L1 error {errors_l1[1]:.5f} m exceeds 3 mm threshold"
    assert errors_l2[1] < 0.004, f"Thacker L2 error {errors_l2[1]:.5f} m exceeds 4 mm threshold"

    assert errors_l1[2] < errors_l1[0], "Error must decrease with grid refinement"


# 4. MACDONALD (1997) STEADY FLOW WITH FRICTION


def test_macdonald_closed_form_exact_properties():
    """MacDonald (1997) steady subcritical channel flow analytical bed slope relations.
    SCOPE: Length = 1000 m, q0 = 1.0 m2/s, n = 0.03, h_mid = 1.0 m, delta_h = 0.1 m.
    VERIFIED RED: Setting discharge q0 = 10.0 m2/s in MacDonaldCase ->"""
    mac = MacDonaldCase(length_m=1000.0, q0=1.0, manning_n=0.03, h_mid=1.0, delta_h=0.1)
    x = np.linspace(0.0, 1000.0, 501)

    h = mac.exact_h(x)
    fr = mac.froude(x)
    z = mac.exact_z(x)

    assert (fr < 0.5).all(), f"MacDonald flow must be subcritical (max Fr={float(np.max(fr)):.3f})"
    assert (h > 0.0).all(), "Depth must be strictly positive"

    assert z[0] > z[-1], "Bed must slope downwards towards outlet"


def test_macdonald_steady_channel_flow_simulation_characterization():
    """ACC simulation against MacDonald subcritical steady channel flow.
    SCOPE: Length = 1000 m, duration = 100.0 s, resolutions dx in [20.0, 10.0, 5.0] m.
    PHYSICS: Low Froude (Fr ~ 0.32 < 0.5) subcritical channel flow under Manning friction."""
    mac = MacDonaldCase(length_m=1000.0, q0=1.0, manning_n=0.03, h_mid=1.0, delta_h=0.1)
    resolutions = [20.0, 10.0, 5.0]
    errors_l1 = []
    errors_l2 = []

    for dx in resolutions:
        static, x, h0 = mac.make_domain(dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "free",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.7,
                "timestep.max_dt_s": 2.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.7, max_dt_s=2.0)
        res = simulate(static, p, ctrl, duration_s=100.0, h0=h0)

        h_num = res.h[1, :].numpy()
        h_exact = mac.exact_h(x)

        sel = (x >= 100.0) & (x <= 900.0)
        l1 = float(np.mean(np.abs(h_num[sel] - h_exact[sel])))
        l2 = float(np.sqrt(np.mean((h_num[sel] - h_exact[sel]) ** 2)))
        errors_l1.append(l1)
        errors_l2.append(l2)

    assert errors_l1[1] < 0.10, f"MacDonald L1 error {errors_l1[1]:.4f} exceeds expected bound"
    assert errors_l2[1] < 0.12, f"MacDonald L2 error {errors_l2[1]:.4f} exceeds expected bound"


def test_wetdry_front_trajectory_and_speed_characterization():
    """Wetting front error against Ritter trajectory x_front(t) = 2*t*sqrt(g*h0).
    SCOPE: Length = 200 m, duration = 4.0 s, resolutions dx in [10.0, 5.0, 2.5] m.
    PHYSICS: In full SWE, dry-bed front speed is 2*c0 = 6.264 m/s (x_front = 25.06 m at t=4s)."""
    ritter = RitterCase(h0=1.0)
    c0 = ritter.c0
    t_eval = 4.0
    resolutions = [10.0, 5.0, 2.5]

    for dx in resolutions:
        static, x, h0 = ritter.make_domain(length_m=200.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 10.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=10.0)
        res = simulate(static, p, ctrl, duration_s=t_eval, h0=h0)

        h_num = res.h[1, :].numpy()
        wet = h_num > 0.001
        x_num = float(x[wet].max()) if np.any(wet) else 0.0
        x_exact = 2.0 * c0 * t_eval

        assert 10.0 <= x_num <= 18.0, f"Front position {x_num:.2f}m outside expected ACC envelope"
        assert (
            x_num < x_exact
        ), f"ACC front {x_num:.2f}m unexpectedly reached or exceeded SWE front {x_exact:.2f}m"


def test_wetdry_front_mass_conservation_under_hard_and_ramp_modes():
    """Exact mass conservation across advancing dry-bed front under both 'hard' and 'ramp' gating.
    SCOPE: Length = 200 m, duration = 4.0 s, dx = 2.5 m, modes in ['hard', 'ramp'].
    PHYSICS: Face-weighted flux is conservative: whatever leaves cell i enters cell j.
    """
    ritter = RitterCase(h0=1.0)
    dx = 2.5

    for mode in ["hard", "ramp"]:
        static, x, h0 = ritter.make_domain(length_m=200.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": mode,
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 10.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=10.0)
        res = simulate(static, p, ctrl, duration_s=4.0, h0=h0)

        v0 = float(h0[1, :].sum() * dx)
        vt = float(res.h[1, :].sum() * dx)
        rel_residual = abs(vt - v0) / v0

        assert (
            rel_residual < 1e-6
        ), f"Mode {mode}: relative mass residual {rel_residual:.2e} exceeds 1e-6 tolerance"


def test_wetdry_depth_threshold_sensitivity_invariance():
    """SCOPE: Length = 200 m, duration = 4.0 s, dx = 2.5 m, thresholds in [1e-4, 1e-3, 1e-2] m.
    PHYSICS: The numerical depth threshold must NOT act as a physical tuning knob.
    VERIFIED RED: Asserting front position changes with threshold ->"""
    ritter = RitterCase(h0=1.0)
    dx = 2.5
    thresholds = [0.0001, 0.001, 0.01]
    front_positions = []
    l1_errors = []

    for thresh in thresholds:
        static, x, h0 = ritter.make_domain(length_m=200.0, dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "physics.depth_threshold_m": thresh,
                "physics.hf_floor_m": thresh,
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 10.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=10.0)
        res = simulate(static, p, ctrl, duration_s=4.0, h0=h0)

        h_num = res.h[1, :].numpy()
        wet = h_num > thresh
        x_num = float(x[wet].max()) if np.any(wet) else 0.0
        h_exact = ritter.exact_h(x, 4.0)
        l1 = float(np.mean(np.abs(h_num - h_exact)))

        front_positions.append(x_num)
        l1_errors.append(l1)

    # Front position is invariant to numerical threshold
    assert all(
        pos == front_positions[0] for pos in front_positions
    ), f"Front positions varied with threshold: {front_positions}"

    assert (
        max(l1_errors) - min(l1_errors) < 1e-5
    ), f"L1 error varied by {max(l1_errors) - min(l1_errors):.2e} m across threshold sweep"
