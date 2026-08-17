"""Measure where ACC's approximation starts to cost — the degradation curve.

WHAT THIS REPLACES
------------------
"Published local-inertial work shows degradation for Fr >~ 0.5" is a citation:
someone else's number, on someone else's grid, for someone else's
implementation. Per CLAUDE.md V1 a citation is a DECLARATION. This script
produces the realized state: a measured curve for THIS solver on THIS grid.

WHY THE MEASUREMENT IS POSSIBLE AT ALL
--------------------------------------
The kinematic reference and the ACC scheme have validity ranges that run in
OPPOSITE directions with slope:

  * kinematic validity RISES with slope (k = S*L/(h0*F0^2) grows monotonically,
    and is >> 10 across the whole practical range, so the reference never binds)
  * ACC validity FALLS with slope (its low-Froude assumption degrades)

So the reference stays trustworthy exactly where ACC degrades, which means the
departure from the analytical solution is ATTRIBUTABLE to ACC rather than
shared between the two. That is what turns a comparison into a measurement.

CROSS-CHECK ON THE ANUGA COMPARISON
-----------------------------------
The ANUGA check (invariant 22) asserts disagreement above some Froude — a
model-to-model comparison. This one measures the same degradation
analytically. If both locate the same Froude limit, two independent methods
agree and tau_hi becomes our own number. If they disagree, one of the two
comparisons is broken, and that is far cheaper to learn here than in Phase 3.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import typer

from jaladhar.solver.acc import SolverParams
from jaladhar.solver.analytical import make_plane
from jaladhar.solver.analytical.kinematic import (
    DEFAULT_SLOPES,
    LOCAL_VALIDITY_CEILING,
    PlaneCase,
)
from jaladhar.solver.run import simulate
from jaladhar.solver.state import load_solver_config
from jaladhar.solver.timestep import TimestepController

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def run_one(
    slope: float, *, rain_mm_hr: float, manning_n: float, length_m: float, dx: float, cfg: dict
) -> dict:
    rain_m_s = rain_mm_hr / 1000.0 / 3600.0
    case = PlaneCase(slope=slope, rain_m_s=rain_m_s, manning_n=manning_n, length_m=length_m)
    static, x = make_plane(
        length_m=length_m, width_m=6 * dx, dx=dx, slope=slope, manning_n=manning_n
    )
    p = SolverParams.from_config(cfg, dx)
    ctrl = TimestepController(dx=dx, alpha=0.7, max_dt_s=5.0)
    res = simulate(static, p, ctrl, duration_s=4.0 * case.t_eq_s, rain=lambda t: rain_m_s)

    h_num = res.h[res.h.shape[0] // 2, :].numpy()
    h_exact = case.depth_profile(x)
    sel = x >= 0.1 * length_m  # dh/dx -> inf at the divide; exclude it
    rel = np.abs(h_num[sel] - h_exact[sel]) / h_exact[sel]
    return {
        "slope": slope,
        "froude": case.froude,
        "kinematic_number": case.kinematic_number,
        "local_validity_ratio": case.local_validity_ratio(0.1 * length_m),
        "reference_trustworthy": case.local_validity_ratio(0.1 * length_m) < LOCAL_VALIDITY_CEILING,
        "t_eq_s": case.t_eq_s,
        "steps": res.steps,
        "sim_time_s": res.sim_time_s,
        "h_outlet_analytical_m": float(h_exact[-1]),
        "h_outlet_numerical_m": float(h_num[-1]),
        "median_rel_error": float(np.median(rel)),
        "max_rel_error": float(np.max(rel)),
        "mean_abs_error_m": float(np.mean(np.abs(h_num[sel] - h_exact[sel]))),
    }


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs" / "solver.yaml"),
    rain_mm_hr: float = typer.Option(55.0),
    manning_n: float = typer.Option(0.03),
    length_m: float = typer.Option(1000.0),
    dx: float = typer.Option(5.0),
    out: Path = typer.Option(REPO / "runs" / "solver_kinematic_sweep"),
) -> None:
    cfg = load_solver_config(config, REPO)
    typer.echo(
        f"R={rain_mm_hr} mm/hr  n={manning_n}  L={length_m:.0f} m  dx={dx} m  "
        f"mode={cfg['wetdry']['mode']}\n"
    )
    typer.echo(
        f"{'S':>8} {'Froude':>7} {'|dh/dx|/S':>10} {'ref?':>5} {'steps':>7} "
        f"{'h_exact':>9} {'h_num':>9} {'med.err':>8}"
    )

    rows = []
    for s in DEFAULT_SLOPES:
        r = run_one(
            s, rain_mm_hr=rain_mm_hr, manning_n=manning_n, length_m=length_m, dx=dx, cfg=cfg
        )
        rows.append(r)
        typer.echo(
            f"{r['slope']:>8.1e} {r['froude']:>7.3f} {r['local_validity_ratio']:>10.3f} "
            f"{'OK' if r['reference_trustworthy'] else 'BAD':>5} "
            f"{r['steps']:>7,} {r['h_outlet_analytical_m']:>9.4f} "
            f"{r['h_outlet_numerical_m']:>9.4f} {r['median_rel_error']:>8.3f}"
        )

    # Where does degradation actually begin? First slope whose median relative
    # error crosses 10%, interpolated in Froude.
    # The error curve is U-SHAPED, not monotonic: at LOW slope the kinematic
    # REFERENCE fails (|dh/dx| is no longer negligible against S), at HIGH slope
    # ACC fails (Froude). Only the rows where the reference is trustworthy can
    # attribute error to ACC at all, and the onset must be sought walking UP
    # from the minimum rather than from the first row.
    threshold = 0.10
    usable = [r for r in rows if r["reference_trustworthy"]]
    onset_froude = None
    if usable:
        i_min = min(range(len(usable)), key=lambda i: usable[i]["median_rel_error"])
        for a, b in zip(usable[i_min:], usable[i_min + 1 :], strict=False):
            if a["median_rel_error"] < threshold <= b["median_rel_error"]:
                f = (threshold - a["median_rel_error"]) / (
                    b["median_rel_error"] - a["median_rel_error"]
                )
                onset_froude = a["froude"] + f * (b["froude"] - a["froude"])
                break

    typer.echo("")
    if onset_froude is not None:
        typer.echo(
            f"MEASURED degradation onset: Fr = {onset_froude:.3f} "
            f"(at the {threshold:.0%} median-relative-error level)"
        )
        typer.echo("  literature prior was Fr ~ 0.5 — this is our own number, on our own grid.")
        typer.echo(
            "  NOTE: low-slope rows marked BAD are excluded — there the kinematic\n"
            "  REFERENCE is invalid (|dh/dx| not small against S), so disagreement\n"
            "  there is the reference's failure, not ACC's."
        )
    else:
        worst = max(r["median_rel_error"] for r in rows)
        typer.echo(
            f"No {threshold:.0%} crossing within the swept range; worst median "
            f"relative error {worst:.3f}. Widen the slope range to locate the onset."
        )

    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "stage": "phase2_kinematic_degradation_sweep",
                "kind": "measurement",
                "git_sha": git_sha(),
                "rain_mm_hr": rain_mm_hr,
                "manning_n": manning_n,
                "length_m": length_m,
                "dx_m": dx,
                "wetdry_mode": cfg["wetdry"]["mode"],
                "error_threshold": threshold,
                "measured_onset_froude": onset_froude,
                "literature_prior_froude": 0.5,
                "rows": rows,
            },
            indent=2,
        )
    )
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
