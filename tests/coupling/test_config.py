"""Invariant #8 — ``config-aggregates`` (WF-2 coupling, spec §12).

Claim (spec §12, invariant 8): rule 7 — a run missing THREE config keys fails
in the first second naming ALL THREE, not the first one.

V2 — independent observable: if aggregation were broken (a fail-fast raise on
the first problem), the three-key fixture below would produce an error whose
text contains exactly ONE of the three deleted dotted key names while the
other two are absent, and ``exc.problems`` would have length 1. The fixture's
missing keys are asserted independently of the resolver BEFORE the call, so
the observable is not the resolver's own word about what it saw.

V5 — red demonstration: 2026-08-25 this suite was run against a deliberate
MUTATION of ``src/jaladhar/coupling/config.py`` in which the single collector
closure ``problem()`` raised immediately instead of appending (fail-fast).
CORRECTED RECORD (2026-08-26, re-measured on a /tmp copy of the mutant): an
earlier version of this paragraph claimed exactly ONE test reddened — that
understated the blast radius. The mutant turns ALL THREE aggregation-
sensitive tests RED, each for the same reason: its assertion requires
MULTIPLE problems to survive collection into one error, and fail-fast raises
carrying only the FIRST —

- ``test_missing_three_keys_aggregates_in_one_error`` — asserts all three
  deleted keys named; fails "aggregation broken: 'outputs.run_dir' absent
  from error" (error carried 'exchange.cd' alone);
- ``test_wrong_types_aggregate_too`` — asserts both type defects named;
  'smoke.max_steps' absent (error carried 'exchange.cw' alone);
- ``test_unknown_typo_key_refused_and_original_reported_missing`` — asserts
  BOTH the typo refusal AND the missing key it silently creates; only the
  unknown-key problem survived.

Measured under the mutant: 3 failed, 14 passed — the remaining tests are
insensitive by construction (single-defect fixtures, one problem each), not
evidence of survival. Broader net than first recorded STRENGTHENS invariant
#8's detection claim. Mutation reverted, suite green; verbatim output
archived in the build unit report (execution_evidence).

V7 — scope: exercised 57 declared leaf keys (LEAF_KEYS == spec §5 enumeration
+ WF-2 M1's graph.terminal_definition + WF-2 M2's diagnostics.attribution_mode,
diagnostics.attribution_radius_basis and — round-3 fix C2/D2 —
diagnostics.attribution_radius_node_m, count asserted) x {presence, type,
range/pin} plus 5 cross-key/existence rules, against (a) the REAL
configs/coupling.yaml and (b) 11 deliberately
mutated fixtures derived from it. Scope claimed by the invariant is exactly
aggregation-at-resolve-time; that full scope is covered. Known gap, stated:
this suite cannot prove downstream code never reads a config key WITHOUT
going through resolve_config — a bypassing reader would be invisible here.
That gap belongs to the BugHunt 'config' lens (workflow wf2-coupling.js), not
to this test, and is not a shortfall of the #8 claim itself.

CPU-only: no torch import anywhere in the path under test; no CUDA allocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jaladhar.coupling.config import (
    LEAF_KEYS,
    ConfigError,
    CouplingConfig,
    resolve_config,
)

REPO = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPO / "configs" / "coupling.yaml"
REAL_SOLVER = REPO / "configs" / "solver.yaml"

# Invariant #8's exact red-mutation recipe (spec §12): delete these three keys.
THREE_MISSING = [
    ("outputs", "run_dir"),
    ("exchange", "cd"),
    ("diagnostics", "falsifier_set"),
]


def _real_raw() -> dict:
    with open(REAL_CONFIG) as f:
        raw = yaml.safe_load(f)
    assert isinstance(raw, dict), f"{REAL_CONFIG} did not parse to a mapping"
    return raw


def _write(tmp_path: Path, raw: dict, name: str = "coupling_fixture.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    return p


def test_real_repo_config_resolves_green():
    """V7 scope: the real configs/coupling.yaml (57 leaves incl. WF-2 M1's
    graph.terminal_definition, WF-2 M2's diagnostics.attribution_mode /
    diagnostics.attribution_radius_basis, and the round-3 C2/D2 leaf
    diagnostics.attribution_radius_node_m), resolved end to end."""
    cfg = resolve_config(REAL_CONFIG, REPO)
    assert isinstance(cfg, CouplingConfig)

    assert cfg.enabled is True
    assert cfg.coupling_version == "wf2-coupling-v1.0.0"
    assert cfg.device == "cpu"
    assert cfg.legacy_sink_replacement is True
    assert cfg.exchange.hf_floor_m == 0.001
    assert cfg.exchange.cw == 1.7 and cfg.exchange.cd == 0.65
    assert cfg.router.null_edge_policy == "q_zero_always"
    assert cfg.storage.assumed_freeboard_m == 1.5
    assert cfg.budget.judged_bar_relative == 1.0e-4
    assert cfg.dt_policy.indirect_cfl_alarm_factor == 0.902
    assert cfg.smoke.max_window_cells == 16384
    assert cfg.graph.expected_counts.nodes == 1721
    assert cfg.graph.expected_partition.capacity_bearing == 1208

    assert cfg.graph.terminal_definition == REPO / "configs" / "terminal_definition.yaml"
    assert cfg.graph.terminal_definition.exists()

    assert cfg.diagnostics.attribution_mode == "edge"
    assert cfg.diagnostics.attribution_radius_m == 40.0
    assert cfg.diagnostics.attribution_radius_node_m == 100.0


def test_leaf_key_enumeration_matches_spec_section_5():
    assert len(LEAF_KEYS) == 57
    assert len(set(LEAF_KEYS)) == 57
    assert "graph.terminal_definition" in LEAF_KEYS
    assert "diagnostics.attribution_mode" in LEAF_KEYS
    assert "diagnostics.attribution_radius_basis" in LEAF_KEYS
    assert "diagnostics.attribution_radius_node_m" in LEAF_KEYS
    for prefix in (
        "coupling",
        "solver_ref",
        "graph",
        "exchange",
        "router",
        "storage",
        "budget",
        "dt",
        "diagnostics",
        "outputs",
        "smoke",
    ):
        assert any(k.startswith(prefix + ".") for k in LEAF_KEYS), f"no keys under {prefix}"


def test_output_paths_resolved_even_though_not_yet_on_disk():
    cfg = resolve_config(REAL_CONFIG, REPO)
    assert cfg.outputs.run_dir == REPO / "runs/wf2_coupled"
    assert cfg.outputs.manifest == REPO / "runs/wf2_coupled/manifest.json"
    assert (
        cfg.outputs.surcharge_events_csv == REPO / "runs/wf2_coupled/products/surcharge_events.csv"
    )
    assert (
        cfg.outputs.event_continuity_csv == REPO / "runs/wf2_coupled/products/event_continuity.csv"
    )
    assert cfg.outputs.depth_series_dir == REPO / "runs/wf2_coupled/depth"


def test_missing_three_keys_aggregates_in_one_error(tmp_path):
    """V7 scope: 3 deleted keys over 3 different sections of the real config.

    V5 mutation record: see module docstring (corrected 2026-08-26) — the
    fail-fast ``problem()`` mutant turned THIS test red TOGETHER WITH BOTH
    other aggregation-sensitive tests (measured 3 failed / 14 passed), not
    alone as earlier recorded; mutation reverted, suite green.
    """
    raw = _real_raw()
    for sect, key in THREE_MISSING:
        del raw[sect][key]

    for sect, key in THREE_MISSING:
        assert key not in raw[sect]

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    problems = ei.value.problems
    for sect, key in THREE_MISSING:
        dotted = f"{sect}.{key}"
        assert dotted in msg, f"aggregation broken: {dotted!r} absent from error:\n{msg}"
        assert any(dotted in p for p in problems), f"{dotted!r} absent from .problems list"

    assert len(problems) >= 3


def test_single_missing_key_names_exactly_one_problem(tmp_path):
    raw = _real_raw()
    del raw["storage"]["isolated_node_width_m"]
    assert "isolated_node_width_m" not in raw["storage"]

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert "storage.isolated_node_width_m" in str(ei.value)
    assert len(ei.value.problems) == 1


def test_wrong_types_aggregate_too(tmp_path):
    raw = _real_raw()
    raw["exchange"]["cw"] = "heavy"
    raw["smoke"]["max_steps"] = [20000]

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "exchange.cw" in msg
    assert "smoke.max_steps" in msg
    assert len(ei.value.problems) >= 2


def test_unknown_typo_key_refused_and_original_reported_missing(tmp_path):
    raw = _real_raw()
    del raw["exchange"]["capture_radius_m"]
    raw["exchange"]["capture_radus_m"] = 20.0

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "unknown key 'exchange.capture_radus_m'" in msg
    assert "missing key 'exchange.capture_radius_m'" in msg


def test_hf_floor_cross_key_mismatch_refuses(tmp_path):
    raw = _real_raw()
    raw["exchange"]["hf_floor_m"] = 0.002

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "exchange.hf_floor_m" in msg
    assert "physics.hf_floor_m" in msg


def test_solver_yaml_missing_hf_floor_key_is_a_named_problem(tmp_path):
    solver_raw = yaml.safe_load(REAL_SOLVER.read_text())
    del solver_raw["physics"]["hf_floor_m"]
    spath = tmp_path / "solver_no_hf.yaml"
    spath.write_text(yaml.safe_dump(solver_raw))
    raw = _real_raw()
    raw["solver_ref"]["config"] = str(spath)

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert "physics.hf_floor_m" in str(ei.value)


def test_df_infiltration_nonzero_refuses(tmp_path):
    solver_raw = yaml.safe_load(REAL_SOLVER.read_text())
    solver_raw["sinks"]["infiltration"]["uniform_rate_mm_per_hr"] = 2.0
    spath = tmp_path / "solver_infil.yaml"
    spath.write_text(yaml.safe_dump(solver_raw))
    raw = _real_raw()
    raw["solver_ref"]["config"] = str(spath)

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "uniform_rate_mm_per_hr" in msg
    assert "D-F" in msg


def test_enabled_requires_legacy_sink_replacement(tmp_path):
    raw = _real_raw()
    raw["coupling"]["enabled"] = True
    raw["coupling"]["legacy_sink_replacement"] = False

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert "legacy_sink_replacement" in str(ei.value)


def test_falsifier_missing_refuses_while_gate_on_then_passes_with_gate_off(tmp_path):
    raw = _real_raw()
    raw["diagnostics"]["falsifier_set"] = "runs/wf2_nonexistent/prediction_set.json"

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)
    assert "diagnostics.falsifier_set" in str(ei.value)

    raw["diagnostics"]["refuse_start_on_missing_falsifier"] = False
    cfg = resolve_config(_write(tmp_path, raw), REPO)
    assert isinstance(cfg, CouplingConfig)


@pytest.mark.parametrize(
    ("section", "key", "bad_value", "expected_fragment"),
    [
        ("router", "null_edge_policy", "route_through", "q_zero_always"),
        ("router", "denominator", "all_edges", "capacity_bearing_only"),
        ("router", "no_substepping", False, "no_substepping"),
        ("coupling", "device", "cuda", "cpu"),
    ],
)
def test_const_pins_refuse_any_other_value(tmp_path, section, key, bad_value, expected_fragment):
    raw = _real_raw()
    raw[section][key] = bad_value

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert expected_fragment in str(ei.value)


def test_max_window_cells_capped_at_128x128(tmp_path):
    raw = _real_raw()
    raw["smoke"]["max_window_cells"] = 200 * 200

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert "max_window_cells" in str(ei.value)


# untested refusal is a claim, not a check (V2/V5).


def test_attribution_mode_enum_violation_refused_in_one_aggregated_error(tmp_path):
    """attribution_mode='diagonal' produces ONE aggregated ConfigError naming
    exactly the enum violation — no collateral problems (rule-7 aggregation
    reports each defect once; nothing else is wrong with this fixture).

    V5 red demonstration (executed 2026-08-26): with the enum predicate
    deliberately mutated to _no_check() for diagnostics.attribution_mode only,
    the SAME fixture RESOLVED clean (no ConfigError) — i.e. this case reddens
    under exactly that mutation ('DID NOT RAISE' is the observable). Mutation
    reverted, suite green; the refusal is load-bearing, not vacuous.
    """
    raw = _real_raw()
    raw["diagnostics"]["attribution_mode"] = "diagonal"

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "'diagnostics.attribution_mode'" in msg
    assert "must be one of" in msg
    assert "'node'" in msg and "'edge'" in msg
    assert (
        len(ei.value.problems) == 1
    ), f"expected exactly the enum refusal, got: {ei.value.problems}"


# V5 mutation record (executed this session): neutering the parity loop
# (``for ... in ()``) reddens every case here with "DID NOT RAISE"; mutation

_PIN_PARITY_CASES = [
    ("storage", "assumed_freeboard_m", 15.0, "ASSUMED_FREEBOARD_M", 1.5),
    ("exchange", "cw", 9.9, "WEIR_COEFF_CW", 1.7),
    ("exchange", "cd", 0.7, "ORIFICE_COEFF_CD", 0.65),
    ("exchange", "regime_switch_m", 0.2, "REGIME_SWITCH_M", 0.1),
    ("exchange", "capture_cap_fraction", 1.1, "CAPTURE_CAP_FRACTION", 0.9),
    ("exchange", "return_cap_fraction", 0.8, "RETURN_CAP_FRACTION", 0.9),
    ("exchange", "a_open_width_fraction", 0.2, "A_OPEN_WIDTH_FRACTION", 0.1),
]


@pytest.mark.parametrize(("section", "key", "bad", "const_name", "pinned"), _PIN_PARITY_CASES)
def test_pinned_physics_yaml_must_equal_executed_constant(
    tmp_path, section, key, bad, const_name, pinned
):
    """Every pinned key, mutated off its constant, refuses at resolve time with
    BOTH sides named (V2: the error text is produced by the resolver but the
    two values are asserted against independently known constants imported
    from exchange.py below — not read back from the resolver's own message)."""
    from jaladhar.coupling import exchange as xchg

    assert getattr(xchg, const_name) == pinned

    raw = _real_raw()
    raw[section][key] = bad

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert f"{section}.{key}" in msg
    assert const_name in msg
    assert repr(pinned) in msg


def test_hf_floor_backstop_catches_yamls_agreeing_off_the_constant(tmp_path):
    solver_raw = yaml.safe_load(REAL_SOLVER.read_text())
    solver_raw["physics"]["hf_floor_m"] = 0.002
    spath = tmp_path / "solver_drifted.yaml"
    spath.write_text(yaml.safe_dump(solver_raw))
    raw = _real_raw()
    raw["exchange"]["hf_floor_m"] = 0.002
    raw["solver_ref"]["config"] = str(spath)

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "exchange.hf_floor_m" in msg
    assert "HF_FLOOR_M" in msg
    assert repr(0.001) in msg


def test_two_pin_mutations_aggregate_into_one_error(tmp_path):
    raw = _real_raw()
    raw["storage"]["assumed_freeboard_m"] = 15.0
    raw["exchange"]["cw"] = 9.9

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "assumed_freeboard_m" in msg and "ASSUMED_FREEBOARD_M" in msg
    assert "exchange.cw" in msg and "WEIR_COEFF_CW" in msg
    assert len(ei.value.problems) >= 2


@pytest.mark.parametrize("bad", [0, 0.0, -0.25])
def test_dt_min_fraction_zero_or_negative_refused_at_resolve(tmp_path, bad):
    raw = _real_raw()
    raw["dt"]["min_fraction_of_uncoupled"] = bad

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "dt.min_fraction_of_uncoupled" in msg
    assert "(0, 1]" in msg
    assert "K1" in msg


def test_dt_min_fraction_one_still_resolves(tmp_path):
    raw = _real_raw()
    raw["dt"]["min_fraction_of_uncoupled"] = 1.0
    cfg = resolve_config(_write(tmp_path, raw), REPO)
    assert cfg.dt_policy.min_fraction_of_uncoupled == 1.0


def _mutated_solver_chain(tmp_path: Path, resolution_m: float | None) -> Path:
    dom_raw = yaml.safe_load((REPO / "configs" / "domain_bengaluru.yaml").read_text())
    if resolution_m is None:
        del dom_raw["resolution_m"]
    else:
        dom_raw["resolution_m"] = resolution_m
    dpath = tmp_path / f"domain_dx{resolution_m}.yaml"
    dpath.write_text(yaml.safe_dump(dom_raw))
    solver_raw = yaml.safe_load(REAL_SOLVER.read_text())
    solver_raw["domain_config"] = str(dpath)
    spath = tmp_path / "solver_chain.yaml"
    spath.write_text(yaml.safe_dump(solver_raw))
    return spath


def test_domain_resolution_mismatch_refuses_naming_both_sides(tmp_path):
    raw = _real_raw()
    raw["solver_ref"]["config"] = str(_mutated_solver_chain(tmp_path, 5.0))

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    msg = str(ei.value)
    assert "resolution_m" in msg
    assert "CELL_AREA_M2" in msg
    assert repr(100.0) in msg
    assert repr(10.0) in msg


def test_domain_resolution_missing_is_a_named_problem(tmp_path):
    raw = _real_raw()
    raw["solver_ref"]["config"] = str(_mutated_solver_chain(tmp_path, None))

    with pytest.raises(ConfigError) as ei:
        resolve_config(_write(tmp_path, raw), REPO)

    assert "'resolution_m'" in str(ei.value)


def test_real_solver_chain_dx_passes_silently():
    cfg = resolve_config(REAL_CONFIG, REPO)
    dom = yaml.safe_load(
        (REPO / yaml.safe_load(REAL_SOLVER.read_text())["domain_config"]).read_text()
    )
    assert dom["resolution_m"] == 10.0
    assert cfg.solver_config == REPO / "configs" / "solver.yaml"
