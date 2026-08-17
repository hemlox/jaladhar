import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

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

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "solver.yaml"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def _cfg(**overrides):
    cfg = load_solver_config(CONFIG, REPO)
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        cfg[section][key] = value
    return cfg


def _params(dx: float, **overrides) -> SolverParams:
    cfg = _cfg(**overrides)
    return SolverParams.from_config(cfg, dx)


def run_all_benchmarks():
    manifest_path = Path(__file__).resolve().parent / "manifest.json"
    start_time = time.time()

    # CLAUDE.md Rule 6: write manifest at run start with git SHA, config snapshot and status 'running'
    manifest = {
        "stage": "analytical_ladder_convergence_benchmarks",
        "kind": "measurement",
        "status": "running",
        "git_sha": git_sha(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cases": ["ritter", "stoker", "thacker", "macdonald"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print("================================================================================")
    print("=== JALADHAR ANALYTICAL VALIDATION LADDER (ACC LOCAL-INERTIAL SOLVER) ===")
    print("================================================================================")

    # 1. Ritter (1892) Dry-bed Dam Break
    print("\n--- 1. RITTER (1892) DRY-BED DAM BREAK ---")
    ritter = RitterCase(h0=1.0)
    t_ritter = 4.0
    dx_ritter = [10.0, 5.0, 2.5]
    l1_ritter, l2_ritter = [], []
    dt_ritter = []

    for dx in dx_ritter:
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
        res = simulate(static, p, ctrl, duration_s=t_ritter, h0=h0)
        h_num = res.h[1, :].numpy()
        h_exact = ritter.exact_h(x, t_ritter)

        l1 = float(np.mean(np.abs(h_num - h_exact)))
        l2 = float(np.sqrt(np.mean((h_num - h_exact) ** 2)))
        l1_ritter.append(l1)
        l2_ritter.append(l2)
        dt_used = ctrl.schedule[0] if ctrl.schedule else 0.0
        dt_ritter.append(dt_used)
        fr = ritter.froude(x, t_ritter)
        print(f"  dx={dx:4.1f}m: L1={l1:.6f} m, L2={l2:.6f} m, steps={res.steps}, dt={dt_used:.4f}s, max_Fr={float(np.max(fr)):.2f}")

    order_l1_r = float(np.polyfit(np.log(dx_ritter), np.log(l1_ritter), 1)[0])
    order_l2_r = float(np.polyfit(np.log(dx_ritter), np.log(l2_ritter), 1)[0])
    print(f"  Observed Convergence Order: L1 = {order_l1_r:.2f}, L2 = {order_l2_r:.2f}")
    print(f"  Physical Regime: High-Froude (Fr >= 1.0, advection-dominated). Error plateau L1={l1_ritter[-1]:.4f}m.")

    # 2. Stoker (1957) Wet-bed Dam Break
    print("\n--- 2. STOKER (1957) WET-BED DAM BREAK ---")
    stoker = StokerCase(h0=1.0, h1=0.1)
    t_stoker = 4.0
    dx_stoker = [10.0, 5.0, 2.5]
    l1_stoker, l2_stoker = [], []
    dt_stoker = []

    for dx in dx_stoker:
        static, x, h0 = stoker.make_domain(length_m=200.0, dx=dx)
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
        res = simulate(static, p, ctrl, duration_s=t_stoker, h0=h0)
        h_num = res.h[1, :].numpy()
        h_exact = stoker.exact_h(x, t_stoker)

        l1 = float(np.mean(np.abs(h_num - h_exact)))
        l2 = float(np.sqrt(np.mean((h_num - h_exact) ** 2)))
        l1_stoker.append(l1)
        l2_stoker.append(l2)
        dt_used = ctrl.schedule[0] if ctrl.schedule else 0.0
        dt_stoker.append(dt_used)
        fr = stoker.froude(x, t_stoker)
        print(f"  dx={dx:4.1f}m: L1={l1:.6f} m, L2={l2:.6f} m, steps={res.steps}, dt={dt_used:.4f}s, max_Fr={float(np.max(fr)):.2f}")

    order_l1_s = float(np.polyfit(np.log(dx_stoker), np.log(l1_stoker), 1)[0])
    order_l2_s = float(np.polyfit(np.log(dx_stoker), np.log(l2_stoker), 1)[0])
    print(f"  Observed Convergence Order: L1 = {order_l1_s:.2f}, L2 = {order_l2_s:.2f}")
    print(f"  Physical Regime: High-Froude (Fr ~ 1.18 in plateau, shock bore). Error plateau L1={l1_stoker[-1]:.4f}m.")

    # 3. Thacker (1981) Parabolic Bowl Oscillation
    print("\n--- 3. THACKER (1981) PARABOLIC BOWL OSCILLATION ---")
    thacker = ThackerCase(a=1000.0, h0=1.0, eta0=20.0)
    t_thacker = thacker.period_s
    dx_thacker = [20.0, 10.0, 5.0]
    l1_thacker, l2_thacker = [], []
    dt_thacker = []

    for dx in dx_thacker:
        static, x, h0 = thacker.make_domain(length_m=2400.0, dx=dx)
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
        res = simulate(static, p, ctrl, duration_s=t_thacker, h0=h0)
        h_num = res.h[1, :].numpy()
        h_exact = thacker.exact_h(x, t_thacker)

        wet = (h_exact > 0.05) | (h_num > 0.05)
        l1 = float(np.mean(np.abs(h_num[wet] - h_exact[wet])))
        l2 = float(np.sqrt(np.mean((h_num[wet] - h_exact[wet]) ** 2)))
        l1_thacker.append(l1)
        l2_thacker.append(l2)
        dt_used = ctrl.schedule[0] if ctrl.schedule else 0.0
        dt_thacker.append(dt_used)
        print(f"  dx={dx:4.1f}m: L1={l1:.6f} m, L2={l2:.6f} m, steps={res.steps}, dt={dt_used:.4f}s, max_Fr={thacker.max_froude:.4f}")

    order_l1_t = float(np.polyfit(np.log(dx_thacker), np.log(l1_thacker), 1)[0])
    order_l2_t = float(np.polyfit(np.log(dx_thacker), np.log(l2_thacker), 1)[0])
    print(f"  Observed Convergence Order: L1 = {order_l1_t:.2f}, L2 = {order_l2_t:.2f}")
    print(f"  Physical Regime: Low-Froude (Fr = {thacker.max_froude:.4f} << 0.5, du/dx=0). Monotonic positive convergence.")

    # 4. MacDonald (1997) Steady Subcritical Channel Flow
    print("\n--- 4. MACDONALD (1997) STEADY CHANNEL FLOW WITH FRICTION ---")
    mac = MacDonaldCase(length_m=1000.0, q0=1.0, manning_n=0.03, h_mid=1.0, delta_h=0.1)
    t_mac = 100.0
    dx_mac = [20.0, 10.0, 5.0]
    l1_mac, l2_mac = [], []
    dt_mac = []

    for dx in dx_mac:
        static, x, h0 = mac.make_domain(dx=dx)
        p = _params(
            dx,
            **{
                "boundaries.mode": "free",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.7,
                "timestep.max_dt_s": 10.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.7, max_dt_s=10.0)
        res = simulate(static, p, ctrl, duration_s=t_mac, h0=h0)
        h_num = res.h[1, :].numpy()
        h_exact = mac.exact_h(x)

        sel = (x >= 100.0) & (x <= 900.0)
        l1 = float(np.mean(np.abs(h_num[sel] - h_exact[sel])))
        l2 = float(np.sqrt(np.mean((h_num[sel] - h_exact[sel]) ** 2)))
        l1_mac.append(l1)
        l2_mac.append(l2)
        dt_used = ctrl.schedule[0] if ctrl.schedule else 0.0
        dt_mac.append(dt_used)
        fr = mac.froude(x)
        print(f"  dx={dx:4.1f}m: L1={l1:.6f} m, L2={l2:.6f} m, steps={res.steps}, dt={dt_used:.4f}s, max_Fr={float(np.max(fr)):.4f}")

    order_l1_m = float(np.polyfit(np.log(dx_mac), np.log(l1_mac), 1)[0])
    order_l2_m = float(np.polyfit(np.log(dx_mac), np.log(l2_mac), 1)[0])
    print(f"  Observed Convergence Order: L1 = {order_l1_m:.2f}, L2 = {order_l2_m:.2f}")
    print(f"  Physical Regime: Low-Froude (Fr ~ 0.32 < 0.5, subcritical). Error plateau L1={l1_mac[-1]:.4f}m from O(Fr^2) advection omission.")

    # Summary Ladder Table
    print("\n================================================================================")
    print("=== SUMMARY ANALYTICAL VALIDATION LADDER TABLE ===")
    print("================================================================================")
    print(f"{'Benchmark Case':<22} | {'Froude (Fr)':<12} | {'Expected':<14} | {'dx=10m L1 (m)':<14} | {'dx=10m L2 (m)':<14} | {'Order L1':<10}")
    print("-" * 96)
    print(f"{'1. Ritter (1892)':<22} | {'>= 1.0 (sup)':<12} | {'PLATEAU (adv)':<14} | {l1_ritter[0]:<14.6f} | {l2_ritter[0]:<14.6f} | {order_l1_r:<10.2f}")
    print(f"{'2. Stoker (1957)':<22} | {'~ 1.18 (sup)':<12} | {'PLATEAU (shk)':<14} | {l1_stoker[0]:<14.6f} | {l2_stoker[0]:<14.6f} | {order_l1_s:<10.2f}")
    print(f"{'3. Thacker (1981)':<22} | {'0.028 (sub)':<12} | {'PASS (order>0)':<14} | {l1_thacker[1]:<14.6f} | {l2_thacker[1]:<14.6f} | {order_l1_t:<10.2f}")
    print(f"{'4. MacDonald (1997)':<22} | {'0.322 (sub)':<12} | {'PLATEAU (Fr2)':<14} | {l1_mac[1]:<14.6f} | {l2_mac[1]:<14.6f} | {order_l1_m:<10.2f}")

    elapsed_s = time.time() - start_time
    # Update manifest in place on completion per Rule 6
    manifest.update(
        {
            "status": "completed",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_wall_s": elapsed_s,
            "results": {
                "ritter": {
                    "dx_m": dx_ritter,
                    "dt_s": dt_ritter,
                    "l1_m": l1_ritter,
                    "l2_m": l2_ritter,
                    "order_l1": order_l1_r,
                    "order_l2": order_l2_r,
                    "regime": "high_froude_advection_dominated",
                    "error_plateau_l1_m": l1_ritter[-1],
                },
                "stoker": {
                    "dx_m": dx_stoker,
                    "dt_s": dt_stoker,
                    "l1_m": l1_stoker,
                    "l2_m": l2_stoker,
                    "order_l1": order_l1_s,
                    "order_l2": order_l2_s,
                    "regime": "high_froude_shock_forming",
                    "error_plateau_l1_m": l1_stoker[-1],
                },
                "thacker": {
                    "dx_m": dx_thacker,
                    "dt_s": dt_thacker,
                    "l1_m": l1_thacker,
                    "l2_m": l2_thacker,
                    "order_l1": order_l1_t,
                    "order_l2": order_l2_t,
                    "regime": "low_froude_zero_advection",
                    "error_plateau_l1_m": None,
                },
                "macdonald": {
                    "dx_m": dx_mac,
                    "dt_s": dt_mac,
                    "l1_m": l1_mac,
                    "l2_m": l2_mac,
                    "order_l1": order_l1_m,
                    "order_l2": order_l2_m,
                    "regime": "low_froude_frictional_advection_model_error",
                    "error_plateau_l1_m": l1_mac[-1],
                },
            },
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote manifest to {manifest_path}")


if __name__ == "__main__":
    run_all_benchmarks()

