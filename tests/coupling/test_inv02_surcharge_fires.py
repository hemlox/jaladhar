"""Invariant #2 ``surcharge-fires`` — the G2 ANTI-VACUITY gate (spec §11.1, §12 row #2).

V2 OBSERVABLES (stated before the checks, one per arm):
- Arm (a) physics — if surcharge were broken I would observe total_surcharging_steps == 0,
  surcharge_returned_m3 == 0.0, an empty events list, and a header-only surcharge_events.csv
  under a WET over-capacity storm. Those are exactly the numbers the Phase-1 precedent
  (#15, "outfall flux is outward or zero") made vacuous by passing on a sealed boundary;
  here the wet control MUST show them non-zero, and Mutation 1 below must move them.
- Arm (b) detection/reporting — if the vacuous branch were vacuous I would observe
  g2_anti_vacuity_fail=False / verdict pass / CLI exit 0 on an empty event set. THAT EXACT
  DEFECT EXISTED UNTIL 2026-08-26 (fix commit pending): a completed dry coupled run printed
  the ``G2 FAIL: NO NODE EVER SURCHARGED`` headline yet exited 0 — recorded verbatim in the
  red->green note of tests/coupling/test_solver_hook.py::TestG2VacuousExit::
  test_cli_exits_nonzero_on_g2_vacuous_window. This invariant therefore guards against a
  demonstrated-real regression, not a hypothetical one.

CLAIM (spec §12 row #2): under a storm exceeding network capacity, >= 1 node surcharges with
total_returned_m3 >= diagnostics.g2_min_returned_m3 (= 1.0 m3), AND a run where nothing
surcharges is DETECTED (result flag + manifest ``g2`` block, library and disk bytes) and
REPORTED AS FAILURE (solver_hook CLI exit 1 with the G2 FAIL headline; gate reporter
diagnostics ``score-g2`` exit 1) — designed so a never-surcharging coupling cannot pass.

RED DEMOS (V5; /tmp copies only, repo tree untouched; both mutations recorded here):
- MUTATION 1 (breaks the mechanism): in a full /tmp mirror of ``src/jaladhar``, set
  exchange.py ``ASSUMED_FREEBOARD_M`` from ``1.5`` to ``float("inf")`` AND rewrite
  storage.assumed_freeboard_m to ``.inf`` in a mirrored copy of configs/coupling.yaml —
  BOTH surfaces, because config.py pass 4d refuses any coupling.yaml whose freeboard
  differs from the executed constant (adjudicated provenance-parity guard); mutating
  only the code would kill the mutant at resolve_config before physics. With parity
  holding, the activation threshold is +inf so the freeboard comparison
  ``(h_excess > 0)`` is never true, the return branch is dead, r_node == 0 every step.
  Expected red: the wet-control assertions A1/A2/A3 fail on zero events while the run
  itself COMPLETES — and the detector flags it (g2_anti_vacuity_fail=True),
  demonstrating "a never-surcharging coupling cannot pass".
- MUTATION 2 (breaks the reporting): in a second /tmp mirror, revert the vacuous-exit in
  solver_hook.py by deleting the ``raise typer.Exit(code=1)`` under the CLI's ``if
  vacuous:`` tail. Expected red: the arm-(b) CLI assertion ``exit_code == 1`` observes
  exit_code 0 while the headline still prints and the manifest still records the fail —
  precisely the pre-2026-08-26 defect class.
Both failures are captured verbatim by the subprocess drivers and printed into the pytest
log; a mutation that stops reddening fails THIS test loudly (MUTATION-DID-NOT-REDDEN).

V7 SCOPE (each scope stated beside its claim; overall label: PARTIAL):
- Arm (a): <= 200 steps x 4,096 cells x 2,000 s realized (step-capped before the
  2,400 s requested), flat CLOSED declared-synthetic
  toy domain, uniform DESIGN storm (synthetic by declaration, never presented as observed),
  8-node chain graph, terminal-node surcharge via the D-A no-export consequence. Scope
  claimed: couple_step return branch + ledger event tracking + G2 aggregation + CSV product
  at TOY scale. NOT claimed: city-scale behaviour.
- Arm (b): <= 3 steps x 256 cells, dry closed toy domain. Scope claimed: verdict
  propagation (result object, returned manifest, on-disk manifest) and both CLI reporting
  paths. NOT claimed: any physics beyond "nothing fired".
- BLOCKED COMPANION (what closes the gap to the city-scale claim): one coupled run over the
  real buffered-terrain window (> max_window_cells) forced by the solver.yaml design storm
  through the D-G-cleared graph loader (post adjudication, CPU/GPU slot), scored by
  ``score-g2`` against its manifest — until that run exists, the city-scale reading of this
  invariant is BLOCKED, and this file says so rather than implying it.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import torch
import yaml
from conftest import chain_graph, closed_solver_config, hermetic_config, static_fields
from typer.testing import CliRunner

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.diagnostics import app as diagnostics_app
from jaladhar.coupling.router import RefuseLoadError, load_drain_graph
from jaladhar.coupling.solver_hook import simulate_coupled
from jaladhar.solver.run import uniform_storm

REPO = Path(__file__).resolve().parents[2]
G2_HEADLINE = "G2 FAIL: NO NODE EVER SURCHARGED"
CSV_HEADER = "node_id,total_returned_m3,first_step,last_step,max_head_m"

_static = static_fields
_chain_graph = chain_graph


def _solver_cfg():
    return closed_solver_config(REPO)


def _hermetic_cfg(tmp_path: Path, subdir: str = "run"):
    return hermetic_config(REPO, tmp_path, subdir=subdir)


def _install_cli_fixtures(monkeypatch, cfg, *, n_nodes: int, shape: tuple[int, int]):
    import jaladhar.coupling.solver_hook as hook

    monkeypatch.setattr(hook, "resolve_config", lambda p, r: cfg)
    monkeypatch.setattr(
        hook, "load_drain_graph", lambda c, r, window=None: _chain_graph(*shape, n_nodes=n_nodes)
    )
    monkeypatch.setattr(hook, "load_domain", lambda *a, **k: _static(shape))


class TestArmAWetStormSurcharges:
    @pytest.mark.parametrize("subdir", ["wet"])
    def test_wet_over_capacity_storm_surcharges_with_volume_above_g2_floor(self, tmp_path, subdir):
        """Injected 8-node chain, uniform 110 mm/7200 s DESIGN storm, max_steps=200, CLOSED
        boundaries: at least one node surcharges, total_returned_m3 clears the contract G2
        floor (1.0 m3), events land in the ledger AND as realized CSV bytes, the detector
        stays quiet, and the manifest reconciles with the ledger counters.

        Realized envelope (measured, this fixture): 136 surcharging steps, ~67 m3 returned
        at node 8 — comfortably above the floor, so the floor assertion is not knife-edge.

        V7 scope: <= 200 steps x 4,096 cells x 2,000 s realized (step-capped before the
        2,400 s requested), flat closed synthetic toy domain,
        uniform design storm; scope claimed: return branch + ledger + G2 aggregation + CSV
        at toy scale. City-scale claim remains BLOCKED (module docstring companion)."""
        cfg = _hermetic_cfg(tmp_path, subdir)
        static_before = _static((64, 64))
        drain_before = static_before.drain_cap_m_s.clone()

        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(64, 64),
            static=static_before,
            rain=uniform_storm(110.0, 7200.0),
            duration_s=2400.0,
            max_steps=200,
            mass_check_every=50,
            snapshot_every_s=900.0,
            smoke=True,
        )

        floor = cfg.diagnostics.g2_min_returned_m3
        led = res.ledger
        assert led.total_surcharging_steps >= 1, (
            f"total_surcharging_steps={led.total_surcharging_steps}: wet over-capacity "
            "storm produced NO surcharging step (V2 observable: the Phase-1 #15 vacuity)"
        )
        assert led.surcharge_returned_m3 >= floor, (
            f"surcharge_returned_m3={led.surcharge_returned_m3!r} cleared the G2 floor "
            f"{floor!r} nowhere"
        )
        events = led.flush_open_events()
        assert len(events) >= 1 and all(e["total_returned_m3"] > 0.0 for e in events)

        man = json.loads(cfg.outputs.manifest.read_text())
        lines = Path(man["surcharge_events_csv"]).read_text().splitlines()
        assert lines[0] == CSV_HEADER
        rows = lines[1:]
        assert len(rows) >= 1
        csv_vol = sum(float(r.split(",")[1]) for r in rows)

        assert abs(csv_vol - led.surcharge_returned_m3) <= 1e-9 * max(
            1.0, led.surcharge_returned_m3
        ), (
            f"surcharge_events.csv volume {csv_vol!r} != ledger surcharge_returned_m3 "
            f"{led.surcharge_returned_m3!r} — the CSV is not the realized ledger's own "
            "event partition"
        )

        assert res.g2_anti_vacuity_fail is False
        assert man["status"] == "completed"
        assert man["g2"]["anti_vacuity_fail"] is False
        assert man["total_surcharging_steps"] == led.total_surcharging_steps
        assert man["total_returned_m3"] == led.surcharge_returned_m3

        assert torch.equal(static_before.drain_cap_m_s, drain_before)

    def test_wet_control_cli_exits_zero_without_headline(self, tmp_path, monkeypatch):
        """Contrast arm: through the SAME CLI path, a surcharging run exits 0 and prints no
        G2 FAIL headline — the non-zero exit tracks the verdict, it is not blanket-on.

        V7 scope: <= 200 steps x 4,096 cells synthetic storm via the full typer command."""
        cfg = _hermetic_cfg(tmp_path, "wet_cli")
        cfg = dc_replace(cfg, smoke=dc_replace(cfg.smoke, duration_s=2400.0, max_steps=200))
        _install_cli_fixtures(monkeypatch, cfg, n_nodes=8, shape=(64, 64))

        import jaladhar.coupling.solver_hook as hook

        result = CliRunner().invoke(hook.app, ["--config", "unused.yaml", "--smoke"])
        assert result.exit_code == 0, result.output
        assert not any(line.startswith("G2 FAIL") for line in result.output.splitlines())
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        assert man["total_surcharging_steps"] > 0
        assert man["total_returned_m3"] >= cfg.diagnostics.g2_min_returned_m3
        assert man["g2"]["anti_vacuity_fail"] is False


class TestArmBDryRunReportedAsFailure:
    def test_dry_run_detected_in_result_manifest_and_reported_by_both_clis(
        self, tmp_path, monkeypatch
    ):
        """<=3-step dry closed micro-run completes normally (the library does not raise)
        yet must be flagged g2_anti_vacuity_fail=True on the RESULT object, in the RETURNED
        final manifest, in the REALIZED on-disk manifest bytes, and REPORTED by the
        solver_hook CLI (exit 1, G2 FAIL headline first) and by the diagnostics gate
        reporter ``score-g2`` on those same manifest bytes (exit 1, same headline).

        V2: until 2026-08-26 (fix pending) the CLI half of this exited 0 — see module
        docstring; this test pins the fixed behaviour permanently.

        V7 scope: <= 3 steps x 256 cells dry toy domain; scope claimed: verdict propagation
        + CLI reporting only. City-scale claim BLOCKED (module docstring companion)."""
        cfg = _hermetic_cfg(tmp_path, "dry")
        floor = cfg.diagnostics.g2_min_returned_m3

        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )

        assert res.steps == 3
        assert res.ledger.total_surcharging_steps == 0
        assert res.ledger.surcharge_returned_m3 < floor

        assert res.g2_anti_vacuity_fail is True
        assert res.final_manifest["g2"]["anti_vacuity_fail"] is True
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        assert man["g2"]["anti_vacuity_fail"] is True
        assert man["total_surcharging_steps"] == 0
        assert man["total_returned_m3"] == res.ledger.surcharge_returned_m3 < floor

        assert Path(man["surcharge_events_csv"]).read_text().splitlines()[0] == CSV_HEADER
        assert len(Path(man["surcharge_events_csv"]).read_text().splitlines()) == 1

        cli_cfg = _hermetic_cfg(tmp_path, "dry_cli")
        cli_cfg = dc_replace(cli_cfg, smoke=dc_replace(cli_cfg.smoke, duration_s=30.0, max_steps=3))
        _install_cli_fixtures(monkeypatch, cli_cfg, n_nodes=2, shape=(16, 16))
        import jaladhar.coupling.solver_hook as hook

        result = CliRunner().invoke(hook.app, ["--config", "unused.yaml", "--smoke"])
        assert result.exit_code == 1, result.output
        out_lines = result.output.splitlines()
        assert out_lines[0].startswith("G2 FAIL"), result.output
        assert G2_HEADLINE in out_lines[0]
        cli_man = json.loads(cli_cfg.outputs.manifest.read_text())
        assert cli_man["status"] == "completed"
        assert cli_man["total_surcharging_steps"] == 0
        assert cli_man["g2"]["anti_vacuity_fail"] is True

        gate = CliRunner().invoke(diagnostics_app, ["score-g2", str(cfg.outputs.manifest)])
        assert gate.exit_code == 1, gate.output
        assert G2_HEADLINE in gate.output.splitlines()[0]


# RED DEMOS (V5) — /tmp mirrors only; the repo tree is never modified

_DRIVER_SOURCE = '''
"""INV02 red-demo driver — runs INSIDE a subprocess against a mirrored src tree.

Usage: python red_driver.py MODE WORK_DIR REPO MIRROR_SRC [CFG_YAML]
MODE: control_a | mutant_a | control_b | mutant_b
Exit codes: 0 protocol satisfied; 3 mutation did not redden; 4 control not green;
5 harness error. Every assertion message is printed VERBATIM for the pytest log.
"""
import copy
import json
import sys
import traceback
from dataclasses import replace as dc_replace
from pathlib import Path

MODE = sys.argv[1]
WORK = Path(sys.argv[2])
REPO = Path(sys.argv[3])
MIRROR_SRC = Path(sys.argv[4])
# Optional coupling-yaml override (mutation 1 only): the mutant resolves a mirrored
# copy whose storage.assumed_freeboard_m carries the SAME .inf the mutated exchange.py
# executes, so pass-4d provenance parity passes and the mutant reaches couple_step.
# Control arms get no argv[5] and resolve the pristine repo yaml (1.5 both sides).
CFG_YAML = Path(sys.argv[5]) if len(sys.argv) > 5 else REPO / "configs" / "coupling.yaml"
sys.path.insert(0, str(MIRROR_SRC))

import torch  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import jaladhar.coupling.exchange as ex_mod  # noqa: E402
import jaladhar.coupling.solver_hook as hook_mod  # noqa: E402
from jaladhar.coupling.config import resolve_config  # noqa: E402
from jaladhar.coupling.router import build_drain_graph  # noqa: E402
from jaladhar.solver.run import uniform_storm  # noqa: E402
from jaladhar.solver.state import StaticFields, load_solver_config  # noqa: E402

CSV_HEADER = "node_id,total_returned_m3,first_step,last_step,max_head_m"
G2_HEADLINE = "G2 FAIL: NO NODE EVER SURCHARGED"

def log(*parts) -> None:
    print("[inv02][" + MODE + "]", *parts, flush=True)

def bail(code: int, msg: str) -> None:
    log(msg)
    raise SystemExit(code)

def hard(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)

# --- provenance: WHICH bytes are actually executing (V1) ----------------------
log("exchange module:", ex_mod.__file__)
log("solver_hook module:", hook_mod.__file__)
log("resolve_config source:", CFG_YAML)
log("ASSUMED_FREEBOARD_M =", repr(ex_mod.ASSUMED_FREEBOARD_M))
if not (
    str(ex_mod.__file__).startswith(str(MIRROR_SRC))
    and str(hook_mod.__file__).startswith(str(MIRROR_SRC))
):
    bail(5, "HARNESS ERROR: pristine repo modules leaked into the demo process")
expected_fb = float("inf") if MODE == "mutant_a" else 1.5
if ex_mod.ASSUMED_FREEBOARD_M != expected_fb:
    bail(5, "HARNESS ERROR: %s running with ASSUMED_FREEBOARD_M=%r (expected %r) — "
            "wrong mirror handed to this mode" % (MODE, ex_mod.ASSUMED_FREEBOARD_M, expected_fb))

# --- fixtures: byte-equivalent to tests/coupling/test_solver_hook.py ----------
def _static(shape, drain_cap_m_s=3.2e-6):
    hh, ww = shape
    z = lambda *s: torch.zeros(*s, dtype=torch.float32)  # noqa: E731
    f = lambda *s, v=0.0: torch.full((*s,), v, dtype=torch.float32)  # noqa: E731
    return StaticFields(
        dz_x=z(hh, ww - 1), dz_y=z(hh - 1, ww),
        n_x=f(hh, ww - 1, v=0.03), n_y=f(hh - 1, ww, v=0.03),
        c_x=torch.ones(hh, ww - 1, dtype=torch.float32),
        c_y=torch.ones(hh - 1, ww, dtype=torch.float32),
        drain_cap_m_s=f(hh, ww, v=drain_cap_m_s), infil_rate_m_s=z(hh, ww),
        edge_w_n=f(hh, v=0.03), edge_e_n=f(hh, v=0.03),
        edge_n_n=f(ww, v=0.03), edge_s_n=f(ww, v=0.03),
        edge_w_s=f(hh, v=1e-4), edge_e_s=f(hh, v=1e-4),
        edge_n_s=f(ww, v=1e-4), edge_s_s=f(ww, v=1e-4),
        edge_open=(0.0, 0.0, 0.0, 0.0), shape=shape,
    )

def _chain_graph(rows, cols, n_nodes=8, cap=0.05):
    base_r, base_c = max(1, rows // 8), max(1, cols // 8)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, cap) for i in range(1, n_nodes)]
    outfall = torch.zeros(n_nodes, dtype=torch.bool)
    outfall[n_nodes - 1] = True
    return build_drain_graph(
        edge_from=torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64),
        capacity_bearing=torch.ones(len(edges), dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([float(e[2]) for e in edges], dtype=torch.float64),
        width_mean_m=torch.tensor([6.71] * n_nodes, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
    )

def hermetic_cfg(run_dir: Path):
    fset = run_dir.parent / ("falsifier_" + run_dir.name + ".json")
    fset.parent.mkdir(parents=True, exist_ok=True)
    fset.write_text(json.dumps(
        {"n_predicted_edges": 1, "predicted_node_ids": [1], "source_gpkg_sha256": "0" * 64}))
    base = resolve_config(CFG_YAML, REPO)
    log("resolved storage.assumed_freeboard_m =",
        repr(base.storage.assumed_freeboard_m))
    outs = dc_replace(
        base.outputs, run_dir=run_dir, manifest=run_dir / "manifest.json",
        surcharge_events_csv=run_dir / "products" / "surcharge_events.csv",
        event_continuity_csv=run_dir / "products" / "event_continuity.csv",
        depth_series_dir=run_dir / "depth")
    return dc_replace(base, outputs=outs,
                      diagnostics=dc_replace(base.diagnostics, falsifier_set=fset))

def solver_cfg():
    cfg = copy.deepcopy(load_solver_config(REPO / "configs" / "solver.yaml", REPO))
    cfg["boundaries"]["mode"] = "closed"
    return cfg

def wet_run(run_dir: Path):
    cfg = hermetic_cfg(run_dir)
    res = hook_mod.simulate_coupled(
        cfg, solver_cfg(), REPO,
        graph=_chain_graph(64, 64), static=_static((64, 64)),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=2400.0, max_steps=200, mass_check_every=50,
        snapshot_every_s=900.0, smoke=True)
    return cfg, res

def dry_run(run_dir: Path):
    cfg = hermetic_cfg(run_dir)
    res = hook_mod.simulate_coupled(
        cfg, solver_cfg(), REPO,
        graph=_chain_graph(16, 16, n_nodes=2), static=_static((16, 16)),
        duration_s=30.0, max_steps=3, mass_check_every=10_000_000)
    return cfg, res

def run_checks(checks):
    results = {}
    for name, fn in checks.items():
        try:
            fn()
            results[name] = None
            log("PASS", name)
        except AssertionError as exc:
            results[name] = str(exc)
            log("FAILED", name, "::", exc)
    return results

def install_cli_fixtures(cfg, n_nodes, shape, duration_s, max_steps):
    # hook_mod.REPO derives REPO from __file__, which inside the mirror points at the
    # mirror root (no configs/) — repoint it at the real repo so the CLI's
    # load_solver_config/compute-config reads hit the real configs/ tree.
    cfg = dc_replace(cfg, smoke=dc_replace(cfg.smoke, duration_s=duration_s, max_steps=max_steps))
    hook_mod.REPO = REPO
    hook_mod.resolve_config = lambda p, r: cfg
    hook_mod.load_drain_graph = lambda c, r, window=None: _chain_graph(*shape, n_nodes=n_nodes)
    hook_mod.load_domain = lambda *a, **k: _static(shape)
    return cfg

def cli_invoke(cfg):
    return CliRunner().invoke(hook_mod.app, ["--config", "unused.yaml", "--smoke"])

# --- ARM (a) checks: the GREEN wet-storm expectations ------------------------
def arm_a_checks(cfg, res):
    led = res.ledger
    floor = cfg.diagnostics.g2_min_returned_m3
    evs = led.flush_open_events()
    man = json.loads(cfg.outputs.manifest.read_text())
    lines = Path(man["surcharge_events_csv"]).read_text().splitlines()
    rows = lines[1:] if lines else []
    csv_vol = sum(float(r.split(",")[1]) for r in rows)

    def c1():
        hard(led.total_surcharging_steps >= 1,
             "total_surcharging_steps=%d (expected >= 1): wet over-capacity storm "
             "produced NO surcharging step" % led.total_surcharging_steps)

    def c2():
        hard(led.surcharge_returned_m3 >= floor,
             "surcharge_returned_m3=%r < G2 floor %r" % (led.surcharge_returned_m3, floor))

    def c3():
        hard(bool(evs) and all(e["total_returned_m3"] > 0.0 for e in evs),
             "surcharge events=%d (expected >= 1 with positive volume)" % len(evs))

    def c4():
        hard(bool(lines) and lines[0] == CSV_HEADER and len(rows) >= 1 and csv_vol >= floor,
             "surcharge_events.csv rows=%d vol=%r (schema ok=%s)"
             % (len(rows), csv_vol, bool(lines) and lines[0] == CSV_HEADER))

    def c5():
        hard(res.g2_anti_vacuity_fail is False,
             "g2_anti_vacuity_fail=%r on a WET run" % (res.g2_anti_vacuity_fail,))

    def c6():
        hard(man["status"] == "completed"
             and man["total_surcharging_steps"] == led.total_surcharging_steps
             and man["total_returned_m3"] == led.surcharge_returned_m3
             and man["g2"]["anti_vacuity_fail"] is False,
             "manifest status=%r steps=%r returned=%r g2=%r"
             % (man.get("status"), man.get("total_surcharging_steps"),
                man.get("total_returned_m3"), man.get("g2")))

    return {
        "A1_wet_total_surcharging_steps>=1": c1,
        "A2_wet_total_returned_m3>=floor": c2,
        "A3_wet_events>=1_positive_volume": c3,
        "A4_wet_surcharge_csv_rows>=1": c4,
        "A5_wet_detector_quiet(g2_fail_False)": c5,
        "A6_manifest_reconciles_with_ledger": c6,
    }

PHYSICS_ARM = ("A1_wet_total_surcharging_steps>=1", "A2_wet_total_returned_m3>=floor",
               "A3_wet_events>=1_positive_volume")

def mode_control_a() -> None:
    cfg, res = wet_run(WORK / "control_a")
    results = run_checks(arm_a_checks(cfg, res))
    bad = {k: v for k, v in results.items() if v is not None}
    if bad:
        bail(4, "CONTROL-NOT-GREEN: pristine-mirror wet run failed its own checks: %r" % bad)
    log("CONTROL-GREEN: pristine mirror surcharges (%d steps, %r m3)"
        % (res.ledger.total_surcharging_steps, res.ledger.surcharge_returned_m3))

def mode_mutant_a() -> None:
    cfg, res = wet_run(WORK / "mutant_a")
    results = run_checks(arm_a_checks(cfg, res))
    reddened = [k for k in PHYSICS_ARM if results[k] is not None]
    if len(reddened) < len(PHYSICS_ARM):
        bail(3, "MUTATION-DID-NOT-REDDEN: physics-arm assertions still passing: %r"
             % (sorted(set(PHYSICS_ARM) - set(reddened)),))
    man = json.loads(cfg.outputs.manifest.read_text())
    flagged = (res.g2_anti_vacuity_fail is True
               and man.get("g2", {}).get("anti_vacuity_fail") is True
               and man.get("status") == "completed")
    log("detector observation under mutation: result_flag=%r manifest_flag=%r status=%r "
        "(run COMPLETED but cannot pass: flagged vacuous)"
        % (res.g2_anti_vacuity_fail, man.get("g2", {}).get("anti_vacuity_fail"),
           man.get("status")))
    if not flagged:
        bail(3, "MUTATION-DID-NOT-REDDEN: a zero-event wet run was NOT flagged "
                "g2_anti_vacuity_fail=True — a never-surcharging coupling could pass")
    log("RED-CONFIRMED (mutation 1, freeboard->inf): wet-physics assertions A1/A2/A3 "
        "failed verbatim above; detector flagged the vacuous window (cannot pass)")

# --- ARM (b) checks: the GREEN dry-run detection/reporting expectations ------
def mode_control_b() -> None:
    cfg, res = dry_run(WORK / "control_b")
    floor = cfg.diagnostics.g2_min_returned_m3
    man = json.loads(cfg.outputs.manifest.read_text())

    def b1():
        hard(res.steps == 3 and res.ledger.total_surcharging_steps == 0,
             "dry run unexpectedly surcharged")

    def b2():
        hard(res.g2_anti_vacuity_fail is True,
             "result flag g2_anti_vacuity_fail=%r" % (res.g2_anti_vacuity_fail,))

    def b3():
        hard(res.final_manifest["g2"]["anti_vacuity_fail"] is True
             and man["g2"]["anti_vacuity_fail"] is True
             and man["status"] == "completed"
             and man["total_surcharging_steps"] == 0
             and man["total_returned_m3"] < floor,
             "manifest detection surfaces disagree")

    def b4():
        result = cli_invoke(install_cli_fixtures(
            hermetic_cfg(WORK / "control_b_cli"), 2, (16, 16), 30.0, 3))
        lines = result.output.splitlines()
        hard(result.exit_code == 1,
             "CLI exit_code=%r (expected 1); output=%r" % (result.exit_code, result.output))
        headline_ok = bool(lines) and lines[0].startswith("G2 FAIL") and G2_HEADLINE in lines[0]
        hard(headline_ok,
             "CLI first line=%r lacks the G2 FAIL headline" % (lines[:1],))
        cli_man = json.loads((WORK / "control_b_cli" / "manifest.json").read_text())
        hard(cli_man["status"] == "completed" and cli_man["g2"]["anti_vacuity_fail"] is True
             and cli_man["total_surcharging_steps"] == 0,
             "CLI-run manifest disagrees with the vacuous window")

    def b5():
        from jaladhar.coupling.diagnostics import app as diag_app
        gate = CliRunner().invoke(diag_app, ["score-g2", str(cfg.outputs.manifest)])
        hard(gate.exit_code == 1 and "NO NODE EVER SURCHARGED" in gate.output.splitlines()[0],
             "score-g2 exit=%r output=%r" % (gate.exit_code, gate.output))

    results = run_checks({"B1_dry_zero_events": b1, "B2_result_flag_True": b2,
                          "B3_manifest_flags_True_both_surfaces": b3,
                          "B4_cli_exit_1_with_headline": b4,
                          "B5_score_g2_exit_1_with_headline": b5})
    bad = {k: v for k, v in results.items() if v is not None}
    if bad:
        bail(4, "CONTROL-NOT-GREEN: pristine-mirror dry run failed its own checks: %r" % bad)
    log("CONTROL-GREEN: pristine mirror detects the vacuous window and exits 1")

def mode_mutant_b() -> None:
    cli_cfg = install_cli_fixtures(hermetic_cfg(WORK / "mutant_b_cli"), 2, (16, 16), 30.0, 3)
    result = cli_invoke(cli_cfg)
    lines = result.output.splitlines()
    headline = bool(lines) and lines[0].startswith("G2 FAIL") and G2_HEADLINE in lines[0]
    log("observed exit_code=%r headline_printed=%r output_verbatim=%r"
        % (result.exit_code, headline, result.output))
    if result.exit_code == 1:
        bail(3, "MUTATION-DID-NOT-REDDEN: CLI still exits 1 on a vacuous window")
    if result.exit_code != 0 or not headline:
        bail(3, "MUTATION-DID-NOT-REDDEN: expected the pre-fix signature (exit 0 WITH "
                "headline); got exit_code=%r headline=%r" % (result.exit_code, headline))
    cli_man = json.loads(cli_cfg.outputs.manifest.read_text())
    if cli_man.get("g2", {}).get("anti_vacuity_fail") is not True:
        bail(3, "MUTATION-DID-NOT-REDDEN cleanly: the library detection surface ALSO moved, "
                "so the failure cannot be attributed to the reverted exit alone")
    log("RED-CONFIRMED (mutation 2, typer.Exit removed): exit_code=0 while the G2 FAIL "
        "headline prints and the manifest still records the fail — the exact "
        "pre-2026-08-26 defect restored")

def main() -> None:
    try:
        {"control_a": mode_control_a, "mutant_a": mode_mutant_a,
         "control_b": mode_control_b, "mutant_b": mode_mutant_b}[MODE]()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — harness errors must be loud and attributed
        traceback.print_exc()
        bail(5, "HARNESS ERROR: unexpected exception in mode %r (traceback above)" % MODE)

if __name__ == "__main__":
    main()
'''

# drifts, the mirror builder REFUSES — an unapplied mutation is an invalid demo.
_MUTATION_ANCHORS: dict[str, tuple[str, str]] = {
    "exchange": (
        "ASSUMED_FREEBOARD_M: float = 1.5  # ‡ declared assumption v1: surcharge activation head",
        'ASSUMED_FREEBOARD_M: float = float("inf")  # INV02 RED MUTATION 1: activation '
        "threshold -> +inf; the freeboard comparison (h_excess > 0) is never true",
    ),
    "solver_hook": (
        '    typer.echo(f"wrote {cfg.outputs.manifest}")\n'
        "    if vacuous:\n"
        "        raise typer.Exit(code=1)",
        '    typer.echo(f"wrote {cfg.outputs.manifest}")\n'
        "    if vacuous:\n"
        "        pass  # INV02 RED MUTATION 2: vacuous-exit reverted (pre-2026-08-26 defect)",
    ),
}

_FREEBOARD_YAML_ANCHOR = (
    # targeted line replace for the mutation-1 PARITY surface: the mirrored
    # or config.py pass 4d refuses the mutant at resolve_config (declared/executed
    "assumed_freeboard_m:",
    "  assumed_freeboard_m: .inf"
    "  # INV02 RED MUTATION 1 (parity surface): mirrors exchange.py\n"
    "                                      #   ASSUMED_FREEBOARD_M -> +inf so pass-4d "
    "provenance parity holds and the mutant reaches couple_step\n",
)


def _mutated_parity_cfg_path(work: Path, target: str) -> Path:
    """Where _build_mirror wrote the mutation-1 parity coupling.yaml."""
    return work / f"mirror_mutant_{target}" / "configs" / "coupling.yaml"


def _write_parity_cfg(work: Path, target: str) -> None:
    """Mutation-1 parity surface: copy configs/coupling.yaml into the mutant mirror
    with ONLY ``storage.assumed_freeboard_m`` rewritten 1.5 -> .inf, so resolve_config's
    pass-4d provenance-parity check passes against the mutated exchange.py constant and
    the mutant reaches couple_step. Targeted line replace, uniqueness asserted; every
    other key verified identical on the PARSED document (realized state, V1)."""
    anchor, replacement = _FREEBOARD_YAML_ANCHOR
    src_text = (REPO / "configs" / "coupling.yaml").read_text(encoding="utf-8")
    lines = src_text.splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(anchor)]
    assert len(hits) == 1, (
        f"freeboard yaml anchor {anchor!r} matched {len(hits)} lines in "
        "configs/coupling.yaml — drift"
    )
    lines[hits[0]] = replacement
    out = _mutated_parity_cfg_path(work, target)
    out.parent.mkdir(parents=True)
    out.write_text("".join(lines), encoding="utf-8")

    src_doc = yaml.safe_load(src_text)
    mut_doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    fb = mut_doc["storage"]["assumed_freeboard_m"]
    assert (
        isinstance(fb, float) and math.isinf(fb) and fb > 0
    ), f"parity yaml scalar parsed as {fb!r}, expected float('inf')"
    for sect, sval in src_doc.items():
        assert sect in mut_doc and set(mut_doc[sect]) == set(sval), f"section {sect} drifted"
        for key, val in sval.items():
            if (sect, key) == ("storage", "assumed_freeboard_m"):
                continue
            assert mut_doc[sect][key] == val, f"{sect}.{key} drifted in parity yaml"
    assert set(mut_doc) == set(src_doc)


def _build_mirror(work: Path, target: str | None) -> Path:
    """Full /tmp copy of ``src/jaladhar``; with ``target`` set, ONE textual mutation is
    applied (mutant mirror); with ``target=None`` the copy stays byte-pristine (control
    mirror). Controls must never share a mirror with mutants.

    Mutation 1 mutates BOTH surfaces the adjudicated provenance-parity guard reads:
    exchange.py's ASSUMED_FREEBOARD_M AND storage.assumed_freeboard_m in a mirrored
    copy of configs/coupling.yaml (written under the mirror root; every other key
    byte-identical). Mutating only one side is exactly the declared/executed
    divergence pass 4d exists to refuse — resolve_config would kill the mutant before
    physics and the demo would exercise the guard, not the surcharge branch."""
    role = "mutant_" + (target or "none")
    mirror_root = work / f"mirror_{role}"
    mirror_src = mirror_root / "src"
    shutil.copytree(
        REPO / "src" / "jaladhar",
        mirror_src / "jaladhar",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if target is None:
        return mirror_src
    if target == "exchange":
        _write_parity_cfg(work, target)
    victim = {
        "exchange": mirror_src / "jaladhar" / "coupling" / "exchange.py",
        "solver_hook": mirror_src / "jaladhar" / "coupling" / "solver_hook.py",
    }[target]
    old, new = _MUTATION_ANCHORS[target]
    text = victim.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"anchor for {target} matched {text.count(old)} times — drift"
    victim.write_text(text.replace(old, new), encoding="utf-8")
    mutated = victim.read_text(encoding="utf-8")
    assert old not in mutated and new in mutated, f"mutation not applied to {victim}"
    return mirror_src


def _run_demo(
    mode: str, work: Path, mirror_src: Path, cfg_override: Path | None = None
) -> subprocess.CompletedProcess[str]:
    argv = [
        sys.executable,
        str(work / "red_driver.py"),
        mode,
        str(work),
        str(REPO),
        str(mirror_src),
    ]
    if cfg_override is not None:
        argv.append(str(cfg_override))
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(work),
        env={**os.environ, "PYTHONPATH": str(mirror_src)},
    )
    print(f"\n===== [inv02] demo {mode}: rc={proc.returncode} =====\n{proc.stdout}")
    if proc.stderr:
        print(f"----- {mode} stderr -----\n{proc.stderr}")
    return proc


class TestRedDemos:
    def test_mutation1_freeboard_inf_kills_surcharge_and_run_cannot_pass(self):
        """V5 red demo, mutation 1 (recorded in module docstring): exchange.py freeboard ->
        +inf in a /tmp mirror — AND the mirrored coupling.yaml's
        storage.assumed_freeboard_m carries the SAME .inf (parity surface), so the
        adjudicated pass-4d provenance-parity guard passes and the mutant reaches
        couple_step instead of dying at resolve_config. Control (PRISTINE mirror,
        pristine repo yaml: 1.5 both sides) must be green on the SAME checks; the mutant
        mirror must fail A1/A2/A3 (zero events) while completing AND being flagged
        g2_anti_vacuity_fail=True — the never-surcharging-coupling-cannot-pass property.

        V7 scope: identical toy envelopes in-process and in-mirror; city scale BLOCKED."""
        work = Path(tempfile.mkdtemp(prefix="inv02_mut1_", dir="/tmp/opencode"))
        pristine = _build_mirror(work, None)
        mutated = _build_mirror(work, "exchange")
        parity_cfg = _mutated_parity_cfg_path(work, "exchange")
        assert parity_cfg.is_file(), f"mutation-1 parity yaml not written at {parity_cfg}"
        (work / "red_driver.py").write_text(_DRIVER_SOURCE)

        ctrl = _run_demo("control_a", work, pristine)
        assert ctrl.returncode == 0, f"pristine control collapsed: {ctrl.stdout[-4000:]}"
        assert "CONTROL-GREEN" in ctrl.stdout
        assert "ASSUMED_FREEBOARD_M = 1.5" in ctrl.stdout
        assert "resolved storage.assumed_freeboard_m = 1.5" in ctrl.stdout

        mut = _run_demo("mutant_a", work, mutated, cfg_override=parity_cfg)
        assert mut.returncode == 0, f"mutation-1 demo protocol error: {mut.stdout[-4000:]}"

        # the resolved yaml value — otherwise the mutant exercised the parity guard,

        assert "ASSUMED_FREEBOARD_M = inf" in mut.stdout
        assert "resolved storage.assumed_freeboard_m = inf" in mut.stdout
        assert "MUTATION-DID-NOT-REDDEN" not in mut.stdout
        assert "RED-CONFIRMED" in mut.stdout

    def test_mutation2_reverted_vacuous_exit_reddens_detector_arm(self):
        """V5 red demo, mutation 2 (recorded in module docstring): solver_hook.py's
        ``raise typer.Exit(code=1)`` removed in a /tmp mirror. Control (PRISTINE mirror)
        must show the full arm-(b) green chain; the mutant mirror must reproduce the
        pre-fix defect signature: CLI exit_code 0 WHILE the headline prints and the
        manifest still records anti_vacuity_fail=True (library detection intact, reporting
        broken).

        V7 scope: <= 3-step dry toy envelope through the full typer command in-mirror."""
        work = Path(tempfile.mkdtemp(prefix="inv02_mut2_", dir="/tmp/opencode"))
        pristine = _build_mirror(work, None)
        mutated = _build_mirror(work, "solver_hook")
        (work / "red_driver.py").write_text(_DRIVER_SOURCE)

        ctrl = _run_demo("control_b", work, pristine)
        assert ctrl.returncode == 0, f"pristine control collapsed: {ctrl.stdout[-4000:]}"
        assert "CONTROL-GREEN" in ctrl.stdout

        mut = _run_demo("mutant_b", work, mutated)
        assert mut.returncode == 0, f"mutation-2 demo protocol error: {mut.stdout[-4000:]}"
        assert "MUTATION-DID-NOT-REDDEN" not in mut.stdout
        assert "RED-CONFIRMED" in mut.stdout


# BLOCKED companion (V7): the city-scale G2 event population over the real graph


@pytest.mark.slow
def test_real_graph_companion_city_scale_g2_population_or_blocked() -> None:
    """Arm (a)'s claim at CITY scale — the leg the module docstring marks BLOCKED: one
    coupled run over the real buffered terrain + real 1,721-node graph, scored through
    the G2 aggregation on its manifest. While the frozen WF-1 reader refuses the only
    realized artefact (D-G seam, owner adjudication pending) this xfails LOUDLY naming
    the closure condition (house pattern from test_inv10/test_inv11). If the seam ever
    opens, a BOUNDED (<=2-step) realization runs: total_surcharging_steps and
    total_returned_m3 land in the completed manifest and the surcharge_events.csv
    product exists with the exact G2 schema."""
    cfg = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    try:
        g = load_drain_graph(cfg, REPO)
    except RefuseLoadError as exc:
        pytest.xfail(
            "BLOCKED (V7): city-scale G2 event population closes when the D-G seam "
            "clears (WF-1 republishes an artefact the frozen reader accepts) and the "
            f"coupled design-storm run over the real graph executes -> {exc}"
        )
    rows, cols = (int(v) for v in g.node_id_map.shape)
    res = simulate_coupled(
        cfg,
        _solver_cfg(),
        REPO,
        graph=g,
        static=_static((rows, cols)),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=60.0,
        max_steps=2,
        mass_check_every=10_000_000,
        smoke=True,
    )
    man = json.loads(cfg.outputs.manifest.read_text())
    assert res.steps <= 2
    assert man["status"] == "completed"
    assert Path(man["surcharge_events_csv"]).read_text().splitlines()[0] == CSV_HEADER
    print(
        f"\n[inv02-real] grid={rows}x{cols} nodes={g.num_nodes} steps={res.steps} "
        f"surcharging_steps={man['total_surcharging_steps']} "
        f"returned_m3={man['total_returned_m3']!r}"
    )
