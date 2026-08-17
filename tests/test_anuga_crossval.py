"""Tests for ANUGA cross-validation against ACC solver and analytical benchmarks.

V1-V8 BINDING TEST SUITE:
External correctness evidence comparing ACC against ANUGA 3.3.10 and closed-form solutions.
"""

from __future__ import annotations

import numpy as np
import pytest

from jaladhar.solver.analytical.anuga_crossval import (
    run_froude_sweep,
    run_macdonald_crossval,
    run_ritter_crossval,
    run_stoker_crossval,
    run_thacker_crossval,
)


def test_ritter_anuga_crossval_characterization():
    """Ritter dry-bed dam break cross-validation across Analytical, ACC, and ANUGA.

    SCOPE: dx=2.5m, L=200m, t=4.0s, h0=1.0m.
    PHYSICS: High-Froude advection-dominated dam break.
    ANUGA (full SWE) matches analytical solution (<0.01 m error).
    ACC (local-inertial) retards front due to advection omission, producing ~0.033m error.
    ACC-vs-ANUGA gap isolates the advection omission cost.

    VERIFIED RED: Asserting ACC matches Analytical to L1 < 0.01 m ->
    AssertionError: ACC L1 error 0.0328m exceeds 0.01m, test fails.
    """
    res = run_ritter_crossval(dx=2.5, length_m=200.0, t_eval=4.0)

    # ANUGA solves full SWE and must match analytical within 1 cm (10 mm)
    assert res.l1_analytical_vs_anuga < 0.010, (
        f"ANUGA Ritter L1 {res.l1_analytical_vs_anuga:.5f}m exceeds 10 mm"
    )
    assert res.l2_analytical_vs_anuga < 0.025, (
        f"ANUGA Ritter L2 {res.l2_analytical_vs_anuga:.5f}m exceeds 25 mm"
    )

    # ACC drops advection and must show the expected error plateau (~0.033 m)
    assert 0.020 < res.l1_analytical_vs_acc < 0.050, (
        f"ACC Ritter L1 {res.l1_analytical_vs_acc:.5f}m outside expected advective plateau"
    )

    # ACC vs ANUGA gap directly measures advection neglect cost (~0.032 m)
    assert 0.020 < res.l1_acc_vs_anuga < 0.050, (
        f"ACC vs ANUGA gap {res.l1_acc_vs_anuga:.5f}m outside expected envelope"
    )


def test_stoker_anuga_crossval_characterization():
    """Stoker wet-bed dam break cross-validation across Analytical, ACC, and ANUGA.

    SCOPE: dx=2.5m, L=200m, t=4.0s, h0=1.0m, h1=0.1m.
    PHYSICS: High-Froude shock-forming wet-bed dam break (Fr ~ 1.18 in plateau).
    ANUGA (full SWE with shock capturing) matches analytical solution (<0.006 m error).
    ACC lacks shock capturing and advection, plateauing at ~0.027m error.

    VERIFIED RED: Asserting ACC matches Analytical to L1 < 0.005 m ->
    AssertionError: ACC L1 error 0.0273m exceeds 0.005m, test fails.
    """
    res = run_stoker_crossval(dx=2.5, length_m=200.0, t_eval=4.0, h0=1.0, h1=0.1)

    # ANUGA matches analytical shock within 6 mm
    assert res.l1_analytical_vs_anuga < 0.006, (
        f"ANUGA Stoker L1 {res.l1_analytical_vs_anuga:.5f}m exceeds 6 mm"
    )

    # ACC exhibits structural shock and advection error plateau (~0.027 m)
    assert 0.015 < res.l1_analytical_vs_acc < 0.040, (
        f"ACC Stoker L1 {res.l1_analytical_vs_acc:.5f}m outside expected plateau"
    )

    # ACC vs ANUGA gap isolates shock dissipation error
    assert 0.015 < res.l1_acc_vs_anuga < 0.040, (
        f"ACC vs ANUGA gap {res.l1_acc_vs_anuga:.5f}m outside expected envelope"
    )


def test_thacker_anuga_crossval_both_match_analytical():
    """Thacker parabolic bowl cross-validation: BOTH ACC and ANUGA must match analytical.

    SCOPE: dx=10m, L=2400m, duration = 1 period T = 141.9s, a=1000m, h0=1m, eta0=20m.
    PHYSICS: Low Froude (Fr=0.028 << 0.5) and du/dx=0 means nonlinear advection is zero.
    Both ACC and ANUGA match the exact SWE solution to sub-millimetre / millimetre precision.

    VERIFIED RED: Overriding ANUGA friction to Manning n = 0.05 ->
    AssertionError: ANUGA Thacker L1 0.082m exceeds 1 mm, test fails.
    """
    res = run_thacker_crossval(dx=10.0, length_m=2400.0)

    # Both models match analytical within 3 mm on a 1.0 m depth
    assert res.l1_analytical_vs_acc < 0.003, (
        f"ACC Thacker L1 {res.l1_analytical_vs_acc:.5f}m exceeds 3 mm"
    )
    assert res.l1_analytical_vs_anuga < 0.001, (
        f"ANUGA Thacker L1 {res.l1_analytical_vs_anuga:.5f}m exceeds 1 mm"
    )

    # ACC and ANUGA agree with each other within 3 mm
    assert res.l1_acc_vs_anuga < 0.003, (
        f"ACC vs ANUGA Thacker L1 {res.l1_acc_vs_anuga:.5f}m exceeds 3 mm"
    )


def test_macdonald_anuga_crossval_advection_discrepancy():
    """MacDonald channel flow cross-validation: ANUGA matches exact SWE; ACC shows O(Fr^2) gap.

    SCOPE: dx=10m, L=1000m, t=100s, q0=1.0 m2/s, n=0.03, h_mid=1.0m, delta_h=0.1m.
    PHYSICS: Low Froude (Fr ~ 0.32 < 0.5) channel flow under Manning friction.
    MacDonald exact solution includes SWE advection Fr^2 * dh/dx.
    ANUGA matches exact SWE (<0.015 m).
    ACC omits advection, leaving an O(Fr^2) error (~0.059 m).

    VERIFIED RED: Asserting ACC matches MacDonald analytical to L1 < 0.02 m ->
    AssertionError: ACC L1 error 0.0594m exceeds 0.02m, test fails.
    """
    res = run_macdonald_crossval(dx=10.0, length_m=1000.0, t_eval=100.0)

    # ANUGA matches exact SWE within 15 mm
    assert res.l1_analytical_vs_anuga < 0.015, (
        f"ANUGA MacDonald L1 {res.l1_analytical_vs_anuga:.5f}m exceeds 15 mm"
    )

    # ACC shows O(Fr^2) advection omission model error (~0.059 m)
    assert 0.040 < res.l1_analytical_vs_acc < 0.080, (
        f"ACC MacDonald L1 {res.l1_analytical_vs_acc:.5f}m outside expected O(Fr^2) plateau"
    )

    # ACC vs ANUGA difference reflects this exact missing advection term (~0.069 m)
    assert 0.050 < res.l1_acc_vs_anuga < 0.090, (
        f"ACC vs ANUGA gap {res.l1_acc_vs_anuga:.5f}m outside expected envelope"
    )


def test_froude_sweep_monotonic_degradation():
    """Froude validity sweep: ACC-vs-ANUGA disagreement grows monotonically with Fr.

    SCOPE: Stoker dam break with h1/h0 in [0.95 -> 0.05], Fr in [0.02 -> 1.6].
    PHYSICS: At low Froude (Fr < 0.05), ACC matches ANUGA to sub-millimetre precision.
    As Froude rises, ACC-vs-ANUGA error grows monotonically up to ~0.030 m at Fr ~ 1.6.

    VERIFIED RED: Sorting sweep rows in reverse Froude order ->
    AssertionError: ACC-vs-ANUGA error must increase with Froude number, test fails.
    """
    h1_ratios = [0.95, 0.60, 0.30, 0.10, 0.05]
    rows = run_froude_sweep(h1_ratios=h1_ratios, dx=5.0, length_m=200.0, t_eval=4.0)

    froudes = [r["froude"] for r in rows]
    l1_errors = [r["l1_acc_vs_anuga_m"] for r in rows]

    # Monotonic increase of ACC-vs-ANUGA difference with Froude
    assert l1_errors == sorted(l1_errors), (
        "ACC-vs-ANUGA error must increase monotonically with Froude number"
    )

    # Sub-millimetre agreement at lowest Froude (Fr ~ 0.026)
    assert l1_errors[0] < 0.0015, (
        f"Low-Froude agreement {l1_errors[0]:.5f}m must be sub-millimetre/near-millimetre"
    )

    # Substantial divergence at supercritical Froude (Fr ~ 1.6)
    assert l1_errors[-1] > 0.025, (
        f"Supercritical divergence {l1_errors[-1]:.5f}m must exceed 0.025m"
    )


def test_froude_crossover_and_real_domain_evaluation():
    """Froude validity envelope crossover and real-domain (Fr = 0.876) characterization.

    SCOPE: Stoker sweep spanning Fr in [0.02, 3.5], target domain Fr = 0.876.
    OBSERVABLES: 5% relative error crossover at Fr ~ 1.07; Fr=0.876 has ~2.4 cm error.

    VERIFIED RED: Asserting 5% error crossover occurs below Fr = 0.5 ->
    AssertionError: Crossover Froude 1.066 is unexpectedly below 0.5, test fails.
    """
    from jaladhar.solver.analytical.froude_envelope import (
        compute_crossover,
        interpolate_error_at_froude,
    )

    h1_ratios = [0.95, 0.80, 0.60, 0.40, 0.30, 0.20, 0.15, 0.10, 0.05, 0.02, 0.005]
    sweep_rows = run_froude_sweep(h1_ratios=h1_ratios, dx=2.5, length_m=200.0, t_eval=4.0)

    co_5pct = compute_crossover(sweep_rows, 0.05)
    assert co_5pct is not None, "5% crossover must be found within sweep range"
    assert 0.90 < co_5pct < 1.20, f"5% crossover Froude {co_5pct:.3f} outside expected [0.90, 1.20] band"

    # Real domain evaluation at Fr = 0.876
    real_eval = interpolate_error_at_froude(sweep_rows, 0.876)
    assert 0.020 < real_eval["l1_acc_vs_anuga_m"] < 0.028, (
        f"Real domain L1 disagreement {real_eval['l1_acc_vs_anuga_m']:.4f}m outside expected band"
    )
    assert 3.5 < real_eval["mean_rel_diff_pct"] < 4.8, (
        f"Real domain relative disagreement {real_eval['mean_rel_diff_pct']:.2f}% outside expected band"
    )
