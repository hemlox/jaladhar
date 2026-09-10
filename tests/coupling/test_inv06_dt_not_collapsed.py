"""Invariant #6 ``dt-not-collapsed`` — WF-2 coupling (spec §12 row #6; §10.5–§10.6).

Falsifiable claim (spec §12 #6, verbatim): "Coupling does not force dt below
50% of the uncoupled schedule over the same window (coupled steps <= 2x
uncoupled steps, identical forcing)" — enforced by the K1 kill threshold
(spec §10.6, HALT): ``KillThresholdHalt`` against the MEASURED
uncoupled-equivalent twin schedule (host ``jaladhar.solver.run.simulate``
imported unmodified; no analytic proxy).

V2 INDEPENDENT OBSERVABLE — if this were broken I would observe a coupled run
that COMPLETES (manifest status ``"completed"``, ``kill_thresholds.K1.fired``
is false) while its realized step count VIOLATES the K1 predicate over the
same simulated window: ``coupled_steps > floor(1/min_fraction_of_uncoupled *
uncoupled_equivalent_steps_at_final_tau)`` — i.e. dt collapsed below 50% of
the twin schedule and NOTHING halted. Both sides of that observation are
realized state: the twin denominator is measured by the unmodified host
simulator and recorded by the driver into the manifest bytes; the numerator
is the committed controller schedule length. This test reads both back from
the written manifest and recomputes the predicate itself.

GREEN tier asserts, on ONE full driver-loop toy run:
  1. THE RATIO PROPERTY: ``steps <= floor(1/min_frac * n_unc_final)``,
     replicated verbatim from the driver's K1 arithmetic, evaluated on the
     manifest's own recorded fields (V1: realized bytes, not return objects);
  2. K1 ARMED AND QUIET: status "completed"; ``fired is false``; >= 4 coupled
     steps (K1_MIN_EVAL_STEPS parity, hand-typed) and >= 4 twin steps covering
     the final tau — so the comparison genuinely ran, not skipped by the
     sub-signal guard; the twin's role line names it the K1 denominator;
  3. THE §10.5 MONITOR RECORDED: manifest ``indirect_cfl_monitor`` agrees with
     the result object field-for-field — ``steps_observed == steps``,
     ``len(factors) == steps``, ``min_factor == min(factors)``,
     ``alarm_steps == count(factor < alarm_factor)`` recomputed HERE — with
     ``0 < min_factor < 1`` and ``captured_to_drains_m3 > 0``: the monitored
     dH was real exchange, not the all-quiet factor==1.0 default.

V5 RED DEMO (standalone, /tmp copies ONLY — the repo tree is never touched;
verbatim failing output captured in the RedTest report). TWO mutations,
applied together to a byte-copy of ``src/jaladhar`` under
``/tmp/opencode/inv06_red/pkg``:
  (a) ``jaladhar/coupling/solver_hook.py`` — K1 raise-on-fire disabled:
      ``if steps > allowed:``  ->  ``if False and steps > allowed:``
  (b) ``jaladhar/coupling/exchange.py`` — couple_step return injects depth:
      ``h_new = h - captured_full + returned_full``
        ->  ``h_new = h - captured_full + returned_full + 0.5``
      0.5 m added to EVERY cell EVERY step, so h_max inflates monotonically,
      ``dt_for`` collapses toward ``min_dt_s`` while the dry twin strides at
      ``max_dt_s`` — sustained dt collapse by injection (task-named form).
Both together because EITHER alone leaves the other defence armed: (a) alone
=> K1 halts the injected collapse (production works; nothing to detect);
(b) alone => same. The pair produces the exact V2 world — breach WITHOUT
halt — and the committed helper must go RED on it. Driver:
``/tmp/opencode/inv06_red/red_driver.py`` (shadow-installs the mutant package
via ``sys.path[0]``; the helper's LAZY ``from jaladhar.coupling.solver_hook
import simulate_coupled`` then binds to the mutant). Two demo-calibration
facts recorded from failed drafts: with the judged-ledger cadence left at the
green tier's 60 steps the run died at CouplingMassBreach (invariant #1's
defence firing on the invented volume — a DIFFERENT invariant), so the
driver passes ``mass_check_every=10_000_000_000`` (TestK1Halt's own recipe)
to isolate #6's world; and a +2.0 m draft collapsed dt so hard the window
reached only ~28 s (< 4 twin steps), which would have reddened the ARMING
premise rather than the ratio property — +0.5 m lands the window at ~100 s
(10 twin steps, ratio ~= 25) so the RATIO assertion itself goes red.

V7 SCOPE — **PARTIAL** (toy tier). Realized: 1 coupled run + 1 measured twin
through the REAL driver loop; 64x64 = 4096 cells; <= 250 steps; 2400 s
simulated window; CLOSED boundaries; declared-synthetic 8-node chain graph;
uniform DESIGN storm (synthetic by declaration, solver.yaml). Scope claimed:
the K1 ratio property + its manifest realization + §10.5 monitor recording AT
TOY SCALE. Gaps, stated: (a) the city-scale twin pair (full-domain coupled vs
uncoupled over the 6 h design storm) — **BLOCKED companion**: closes when the
D-G artefact seam closes (WF-1 reader consumer_assertion_3,
zero_length_dropped_count=42) and a GPU slot is authorized; until then the
city-scale reading of this claim is NOT verified; (b) one topology, one
storm — the quantifier runs over a single declared scenario, not a sweep;
(c) windows shorter than the arming signal are excluded BY THE MACHINERY'S
OWN constant (twin shares the coupled run's first selection), not tested;
(d) **LATE-COLLAPSE MASKING HOLE — NAMED, NOT FIXED HERE**: both the runtime
K1 check and this file's committed assertion evaluate the ratio
GLOBALLY-CUMULATIVELY (total coupled steps vs twin steps covered at FINAL tau),
so a sustained dt collapse confined to the LATE window sits behind a healthy
prefix and leaves every defence quiet — coupled_steps accumulates slowly toward
the allowed ceiling while each late step crawls at min_dt_s. Closing this needs
windowed-ratio machinery (per-window step budgets), deliberately NOT built in
this fix round; per V7 the shortfall lives beside the verdict in this file's
test output so it cannot hide in a status report.

Fixtures are DECLARED-SYNTHETIC with this file's OWN values — builder style
reused from tests/coupling/test_solver_hook.py (TestK1Halt's run recipe:
injected node-state presets are NOT reused; the green tier is storm-driven),
numbers not.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace as dc_replace
from pathlib import Path

import torch

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.router import build_drain_graph
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config

REPO = Path(__file__).resolve().parents[2]

GRID = (64, 64)
N_NODES = 8
STORM_MM = 110.0
STORM_S = 7200.0
DURATION_S = 2400.0
MAX_STEPS = 250
MASS_CHECK_EVERY = 60


def _static64() -> StaticFields:
    hh, ww = GRID

    def z(*s):
        return torch.zeros(*s, dtype=torch.float32)

    def f(*s, v=0.0):
        return torch.full((*s,), v, dtype=torch.float32)

    return StaticFields(
        dz_x=z(hh, ww - 1),
        dz_y=z(hh - 1, ww),
        n_x=f(hh, ww - 1, v=0.028),
        n_y=f(hh - 1, ww, v=0.028),
        c_x=torch.ones(hh, ww - 1, dtype=torch.float32),
        c_y=torch.ones(hh - 1, ww, dtype=torch.float32),
        drain_cap_m_s=f(hh, ww, v=4.1e-6),
        infil_rate_m_s=z(hh, ww),
        edge_w_n=f(hh, v=0.028),
        edge_e_n=f(hh, v=0.028),
        edge_n_n=f(ww, v=0.028),
        edge_s_n=f(ww, v=0.028),
        edge_w_s=f(hh, v=1.3e-4),
        edge_e_s=f(hh, v=1.3e-4),
        edge_n_s=f(ww, v=1.3e-4),
        edge_s_s=f(ww, v=1.3e-4),
        edge_open=(0.0, 0.0, 0.0, 0.0),
        shape=GRID,
    )


def _chain8() -> object:
    rows, cols = GRID
    base_r, base_c = rows // 6, cols // 6
    cells = [(base_r + i, base_c) for i in range(N_NODES)]
    assert cells[-1][0] < rows and cells[-1][1] < cols
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, 0.055) for i in range(1, N_NODES)]
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.ones(len(edges), dtype=torch.bool)
    q_cap = torch.tensor([float(e[2]) for e in edges], dtype=torch.float64)
    outfall = torch.zeros(N_NODES, dtype=torch.bool)
    outfall[N_NODES - 1] = True
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=torch.tensor([7.4] * N_NODES, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(11.0, 0.0, N_NODES, dtype=torch.float64),
        contrib_area_m2=torch.zeros(N_NODES, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(N_NODES, dtype=torch.int64),
    )


def _coupling_cfg(out_dir: Path):
    base = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    outs = dc_replace(
        base.outputs,
        run_dir=out_dir,
        manifest=out_dir / "manifest.json",
        surcharge_events_csv=out_dir / "products" / "surcharge_events.csv",
        event_continuity_csv=out_dir / "products" / "event_continuity.csv",
        depth_series_dir=out_dir / "depth",
    )
    return dc_replace(base, outputs=outs)


def _solver_cfg() -> dict:
    scfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    scfg = copy.deepcopy(scfg)
    scfg["boundaries"]["mode"] = "closed"
    return scfg


def run_and_assert_dt_not_collapsed(
    out_dir: Path, *, mass_check_every: int = MASS_CHECK_EVERY
) -> dict:
    """ONE coupled run (real OR mutant module per sys.path state) + the
    invariant-#6 assertions. Returns an evidence dict on success; raises
    AssertionError with an INV06-prefixed message on any violation.

    ``mass_check_every`` exists for the V5 red driver only: the mutant world
    invents volume, which trips the JUDGED ledger cadence (invariant #1's
    defence) before K1's window matters — bypassing that cadence isolates
    invariant #6's breach-without-halt world (TestK1Halt's recipe). The green
    tier keeps the default cadence and exercises it."""
    from jaladhar.coupling.solver_hook import simulate_coupled

    out_dir = Path(out_dir)
    cfg = _coupling_cfg(out_dir)

    res = simulate_coupled(
        cfg,
        _solver_cfg(),
        REPO,
        graph=_chain8(),
        static=_static64(),
        rain=uniform_storm(STORM_MM, STORM_S),
        duration_s=DURATION_S,
        max_steps=MAX_STEPS,
        mass_check_every=mass_check_every,
        snapshot_every_s=None,
        smoke=True,
    )

    man = json.loads(cfg.outputs.manifest.read_text())
    k1 = man["kill_thresholds"]["K1"]
    steps = int(k1["coupled_steps"])
    n_unc = int(k1["uncoupled_equivalent_steps_at_final_tau"])
    min_frac = float(k1["min_fraction_of_uncoupled"])

    allowed = math.floor(1.0 / min_frac * n_unc)
    led = res.ledger

    assert man["status"] == "completed", (
        f"INV06: expected a completed run, got status={man['status']!r} "
        f"(halt_reason={k1['halt_reason']!r})"
    )
    assert k1["fired"] is False, f"INV06: K1 fired on the HEALTHY run: {k1['halt_reason']!r}"
    assert steps >= 4, "K1 never armed (< 4 coupled steps) — comparison did not run"
    assert n_unc >= 4, "twin signal < 4 steps at final tau — denominator is noise"
    assert k1["uncoupled_twin"]["role"].startswith("K1 denominator")
    assert int(k1["uncoupled_twin"]["steps"]) >= 1

    assert steps <= allowed, (
        f"INV06 RED: dt collapsed below {100 * min_frac:.0f}% of the uncoupled "
        f"schedule with NO halt — coupled_steps={steps}, uncoupled_equivalent="
        f"{n_unc} over tau={res.sim_time_s:.3f}s, allowed<={allowed} "
        f"(ratio {steps / max(n_unc, 1):.2f}), status={man['status']!r}, "
        f"K1.fired={k1['fired']}"
    )

    md = man["indirect_cfl_monitor"]
    mon = res.cfl_monitor
    assert md["steps_observed"] == res.steps > 0
    assert len(mon.factors) == res.steps
    assert md["min_factor"] == mon.min_factor
    assert md["min_factor"] == min(mon.factors)
    recomputed_alarms = sum(1 for f in mon.factors if f < md["alarm_factor"])
    assert md["alarm_steps"] == recomputed_alarms
    assert 0.0 < md["min_factor"] <= 1.0
    assert md["min_factor"] < 1.0, (
        "every observed factor was exactly 1.0 — no exchange ever perturbed "
        "h_max; the monitor verdict would be vacuous"
    )
    assert led.captured_to_drains_m3 > 0.0, "no capture occurred — coupling inactive"
    assert man["dt_schedule"]["n_steps"] == res.steps

    return {
        "steps": steps,
        "sim_time_s": res.sim_time_s,
        "twin_steps_total": int(k1["uncoupled_twin"]["steps"]),
        "n_unc_final": n_unc,
        "allowed": allowed,
        "ratio": steps / max(n_unc, 1),
        "min_factor": md["min_factor"],
        "alarm_steps": md["alarm_steps"],
        "captured_m3": led.captured_to_drains_m3,
        "returned_m3": led.surcharge_returned_m3,
    }


class TestInv06DtNotCollapsed:
    def test_coupled_run_ratio_within_k1_and_monitor_records(self, tmp_path, capsys):
        """GREEN INVARIANT: one storm-driven coupled toy run completes with the
        K1 ratio property satisfied on realized manifest fields and the
        indirect-CFL monitor recording consistently.

        V7 scope: see module docstring — PARTIAL (toy tier); the BLOCKED
        companion (city-scale twin pair post-D-G/GPU) is named there."""
        ev = run_and_assert_dt_not_collapsed(tmp_path / "run")
        print(
            f"[inv06] scope: 1 coupled run + 1 measured twin, {GRID[0]}x{GRID[1]}="
            f"{GRID[0] * GRID[1]} cells, {ev['steps']} coupled steps over "
            f"{ev['sim_time_s']:.1f}s, twin total {ev['twin_steps_total']} steps, "
            f"tau-final denominator {ev['n_unc_final']} (allowed<={ev['allowed']}), "
            f"ratio={ev['ratio']:.3f} <= 2; min_factor={ev['min_factor']:.6f}, "
            f"alarm_steps={ev['alarm_steps']}, captured={ev['captured_m3']!r} m3, "
            f"returned={ev['returned_m3']!r} m3; storm {STORM_MM}mm/{STORM_S}s "
            f"DESIGN (synthetic), CLOSED bounds — PARTIAL toy tier; BLOCKED "
            f"companion: full-domain twin pair post D-G closure + GPU slot; "
            f"KNOWN HOLE (V7 gap d, named not fixed): the ratio is evaluated "
            f"GLOBALLY-CUMULATIVELY at final tau, so a sustained LATE-WINDOW dt "
            f"collapse hides behind a healthy prefix — neither runtime K1 nor this "
            f"assertion would fire; needs windowed-ratio machinery (not built here)"
        )
        assert "[inv06]" in capsys.readouterr().out

    def test_committed_dt_policy_pins(self):
        """The committed coupling config pins the contract values this
        invariant is stated against — guards against a mutation demo (or any
        edit) landing in the tree (V1: realized config state, monkeypatch-free
        scope): dt.min_fraction_of_uncoupled = 0.5 (K1's 2x) and
        dt.indirect_cfl_alarm_factor = 0.902 (sqrt(1/1.23))."""
        base = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
        assert base.dt_policy.min_fraction_of_uncoupled == 0.5
        assert base.dt_policy.indirect_cfl_alarm_factor == 0.902
