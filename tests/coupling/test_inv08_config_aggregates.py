"""V2 — if aggregation were broken (``problem()`` raising fail-fast on the FIRST defect) I would
observe: the three-missing-keys fixture below returning a ConfigError whose message contains only
the FIRST missing key in schema order — 'budget.judged_bar_relative' — while
'diagnostics.attribution_radius_m' and 'outputs.surcharge_events_csv' are absent from BOTH the
message text and ``.problems``, with ``len(.problems) == 1`` instead of 3; the typo fixture would
carry only its unknown-key half; the cross-class fixture would surface exactly one of its four
defects. The observable is independent of the assertion: error text comes back from
``resolve_config`` and is searched for key names chosen a priori (they are deleted from MY fixture
before the call, never read back from production state).

==============================================================================
INVARIANT #8 ``config-aggregates`` — sprint-level re-proof (WF-2, spec §12 row #8)
==============================================================================
Claim under test (spec §12 row #8 = CLAUDE.md rule 7): a run missing THREE config keys fails in the
FIRST SECOND naming ALL THREE, not the first one; unknown/typo'd keys are flagged alongside their
intended key; cross-key violations — ``exchange.hf_floor_m`` != solver ``physics.hf_floor_m``, and
solver ``sinks.infiltration.uniform_rate_mm_per_hr`` != 0.0 (deviation D-F refusal) — are refused at
resolve time. The FIRST-SECOND half of the claim is MEASURED here: every failure-path resolve is
wrapped in ``time.perf_counter()`` and asserted < 1.0 s wall clock.

RELATION TO THE UNIT TIER (independence statement): ``tests/coupling/test_config.py`` (17 tests,
verified independently of this module) owns per-key breadth and shares fixtures with
``configs/coupling.yaml``. THIS module is deliberately self-contained: the coupling YAML below is a
complete 57-key document TYPED INLINE from spec §5 + WF-2 M1's
graph.terminal_definition leaf + WF-2 M2's diagnostics.attribution_mode /
diagnostics.attribution_radius_basis leaves (ruling D-GT 2026-08-26) + the
round-3 C2/D2 diagnostics.attribution_radius_node_m leaf (V6 record at the
fixture below — shares no code and no file with
configs/coupling.yaml or with the unit tier's fixtures), the host solver chain is a MINIMAL inline
YAML carrying exactly the two subtrees resolve_config cross-keys read, and the missing keys are
DIFFERENT from every prior demo's (unit tier used outputs.run_dir / exchange.cd /
diagnostics.falsifier_set; this suite deletes diagnostics.attribution_radius_m,
budget.judged_bar_relative, outputs.surcharge_events_csv). If both suites stay green, invariant #8
holds on two disjoint fixture sets.

Fixture-integrity duty (loud drift): the green control asserts the inline fixture declares EXACTLY
the schema's leaf set (``set(LEAF_KEYS)``, length asserted == 57 per spec §5 [F]
as grown by WF-2 M1, WF-2 M2's ruling D-GT 2026-08-26 and the round-3 C2/D2 leaf). A schema change
without a conscious fixture update reds HERE, naming the drifted key — rule-7 discipline applied to
the test's own fixture. Placeholder files staged for existence-checked paths (graph artefacts,
falsifier set, ground-truth manifest) are NEVER READ by the code under test — resolve_config only
stats them — so they carry no fabricated measurements (rule 1 untouched).

V5 RED DEMO — /tmp COPY ONLY of config.py (repo source never modified). Pinned mutation site: the
single collector closure line ``problems.append(msg)`` inside ``resolve_config`` (asserted unique
before patching; a stale pin fails LOUD and must be re-pointed consciously). Mutation: replace that
line with ``raise ConfigError([msg], config_path=path)`` — raise immediately on the FIRST problem.
Executed evidence 2026-08-26 (.venv/bin/python 3.11.15, pytest 9.1.1):
  - GREEN control: pristine fixture resolves through unmutated resolve_config (0 problems).
  - RED under mutant: the SAME three-missing-keys fixture yields ConfigError with EXACTLY ONE
    problem — "missing key 'budget.judged_bar_relative'" (first in _SCHEMA order); both other keys
    absent from message and .problems. Replicating THIS suite's headline assertion against the
    mutant raises AssertionError("aggregation broken: 'diagnostics.attribution_radius_m' absent")
    — the invariant REDDENS, exactly as V2 predicted. Verbatim output archived in the RedTest unit
    report; the demo also runs embedded below on every suite execution, so it cannot silently rot.
Both attempts asserted, not narrated: if the mutant did NOT redden the replicated assertion, the
demo test itself fails ("mutant did NOT redden").

V7 SCOPE STATEMENT: Scope run: full 57-key schema resolved END-TO-END green through resolve_config
on the inline fixture (100% of declared keys touched by the resolver under test), plus deliberate
defects concentrated on 4/57 coupling keys (7.0%) across three defect classes — missing ×3 keys,
typo→unknown+intended ×2 keys, cross-key ×1 key — plus 2 solver-side keys outside the coupling
schema (physics.hf_floor_m, sinks.infiltration.uniform_rate_mm_per_hr); latency measured on all 5
failure paths + 1 green path. Scope CLAIMED by #8 is AGGREGATION SEMANTICS + FIRST-SECOND LATENCY
at resolve time, NOT per-key kind/range/pin breadth — that breadth is the unit tier's coverage by
design. Gap ⇒ PARTIAL: a downstream reader bypassing resolve_config is invisible here (BugHunt
'config' lens scope, not this invariant). CPU-only: no torch import anywhere in the path under test;
no CUDA allocation; device pin asserted "cpu".
"""

from __future__ import annotations

import importlib.util
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from jaladhar.coupling import config as config_mod
from jaladhar.coupling.config import (
    LEAF_KEYS,
    ConfigError,
    CouplingConfig,
    resolve_config,
)

REPO = Path(__file__).resolve().parents[2]

THE_MISSING: tuple[str, ...] = (
    "budget.judged_bar_relative",
    "diagnostics.attribution_radius_m",
    "outputs.surcharge_events_csv",
)
FIRST_MISSING_IN_SCHEMA_ORDER = THE_MISSING[0]

SOLVER_YAML: dict[str, Any] = {
    "physics": {"hf_floor_m": 0.001},
    "sinks": {"infiltration": {"uniform_rate_mm_per_hr": 0.0}},
}

FIRST_SECOND_S = 1.0


def _fresh_fixture() -> dict[str, Any]:
    return {
        "coupling": {
            "enabled": True,
            "coupling_version": "wf2-coupling-v1.0.0",
            "device": "cpu",
            "legacy_sink_replacement": True,
        },
        "solver_ref": {"config": "configs/solver.yaml"},
        "graph": {
            "gpkg": "runs/drain_graph_build/drain_graph.gpkg",
            "adjacency": "runs/drain_graph_build/drain_graph_adjacency.json",
            "manifest": "runs/drain_graph_build/manifest.json",
            "terminal_definition": "configs/terminal_definition.yaml",
            "require_dag": True,
            "expected_counts": {"nodes": 1721, "edges": 1587},
            "expected_partition": {
                "capacity_bearing": 1208,
                "zero_slope": 61,
                "area_capped": 32,
                "synthetic": 286,
            },
        },
        "exchange": {
            "hf_floor_m": 0.001,
            "regime_switch_m": 0.1,
            "cw": 1.7,
            "cd": 0.65,
            "capture_cap_fraction": 0.9,
            "return_cap_fraction": 0.9,
            "capture_radius_m": 20.0,
            "a_open_width_fraction": 0.1,
        },
        "router": {
            "null_edge_policy": "q_zero_always",
            "denominator": "capacity_bearing_only",
            "no_substepping": True,
        },
        "storage": {
            "assumed_freeboard_m": 1.5,
            "isolated_node_width_m": 2.285,
            "shaft_length_proxy_m": 1.0,
        },
        "budget": {
            "relative_tolerance": 1.0e-3,
            "judged_bar_relative": 1.0e-4,
            "realized_reference_residual": 1.79e-05,
            "total_water_judged": True,
        },
        "dt": {
            "min_fraction_of_uncoupled": 0.5,
            "indirect_cfl_alarm_factor": 0.902,
        },
        "diagnostics": {
            "falsifier_set": "runs/wf2_falsifier_preregistration/surcharge_prediction_set.json",
            "refuse_start_on_missing_falsifier": True,
            "suspicious_deadend_share": 0.5,
            "ground_truth_manifest": "runs/groundtruth/manifest.json",
            "attribution_mode": "edge",
            "attribution_radius_m": 100.0,
            "attribution_radius_node_m": 100.0,
            "attribution_radius_basis": "D-GT owner ruling 2026-08-26 (inline fixture value; "
            "the REAL configs/coupling.yaml carries its own measured basis string)",
            "g2_min_returned_m3": 1.0,
        },
        "outputs": {
            "run_dir": "runs/wf2_coupled",
            "manifest": "runs/wf2_coupled/manifest.json",
            "surcharge_events_csv": "runs/wf2_coupled/products/surcharge_events.csv",
            "event_continuity_csv": "runs/wf2_coupled/products/event_continuity.csv",
            "depth_series_dir": "runs/wf2_coupled/depth",
            "write_every_s": 900.0,
        },
        "smoke": {
            "window_pad_cells": 50,
            "min_predicted_nodes_in_window": 10,
            "max_window_cells": 16384,
            "duration_s": 3600.0,
            "max_steps": 20000,
            "cpu_budget_wall_clock_min": 30.0,
        },
    }


def _dotted_leaves(node: dict[str, Any], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in node.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _dotted_leaves(v, full)
        else:
            out.add(full)
    return out


def _has(raw: dict[str, Any], dotted: str) -> bool:
    node: Any = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _delete(raw: dict[str, Any], dotted: str) -> None:
    sect, key = dotted.split(".", 1)
    del raw[sect][key]


_STAGED_EXISTENCE_PATHS: tuple[str, ...] = (
    "runs/drain_graph_build/drain_graph.gpkg",
    "runs/drain_graph_build/drain_graph_adjacency.json",
    "runs/drain_graph_build/manifest.json",
    "runs/groundtruth/manifest.json",
    "runs/wf2_falsifier_preregistration/surcharge_prediction_set.json",
)


def _stage(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "solver.yaml").write_text(yaml.safe_dump(SOLVER_YAML))

    (tmp_path / "configs" / "terminal_definition.yaml").write_text(
        (REPO / "configs" / "terminal_definition.yaml").read_text()
    )
    for rel in _STAGED_EXISTENCE_PATHS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    coupling_yaml = tmp_path / "coupling_fixture.yaml"
    coupling_yaml.write_text(yaml.safe_dump(_fresh_fixture(), sort_keys=False))
    return coupling_yaml


def _resolve_ok(path: Path, repo_root: Path) -> tuple[CouplingConfig, float]:
    t0 = time.perf_counter()
    cfg = resolve_config(path, repo_root)
    return cfg, time.perf_counter() - t0


def _resolve_err(path: Path, repo_root: Path) -> tuple[ConfigError, float]:
    t0 = time.perf_counter()
    with pytest.raises(ConfigError) as ei:
        resolve_config(path, repo_root)
    return ei.value, time.perf_counter() - t0


# skipped ``_stage`` and reddened with 6-9 SPURIOUS file-not-found problems.


def test_inv08_green_control_pristine_fixture_resolves_full_schema(tmp_path: Path) -> None:
    raw = _fresh_fixture()
    leaves = _dotted_leaves(raw)
    assert leaves == set(LEAF_KEYS), (
        "fixture/schema drift — fix the fixture CONSCIOUSLY, not silently: "
        f"fixture-only={sorted(leaves - set(LEAF_KEYS))} "
        f"schema-only={sorted(set(LEAF_KEYS) - leaves)}"
    )

    assert len(LEAF_KEYS) == 57, "schema count drifted from spec §5 [F]; re-prove §5 then refixture"

    coupling_yaml = _stage(tmp_path)
    cfg, elapsed = _resolve_ok(coupling_yaml, tmp_path)

    assert isinstance(cfg, CouplingConfig)
    assert cfg.device == "cpu"
    assert cfg.coupling_version == "wf2-coupling-v1.0.0"
    assert cfg.exchange.hf_floor_m == 0.001
    assert cfg.budget.judged_bar_relative == 1.0e-4
    assert cfg.diagnostics.attribution_radius_m == 100.0
    assert cfg.diagnostics.attribution_radius_node_m == 100.0
    assert cfg.outputs.surcharge_events_csv == (
        tmp_path / "runs/wf2_coupled/products/surcharge_events.csv"
    )
    assert cfg.graph.expected_counts.nodes == 1721
    assert cfg.smoke.cpu_budget_wall_clock_min == 30.0

    print(
        f"\n[inv08-green-control] resolved {len(LEAF_KEYS)}/{len(LEAF_KEYS)} keys in {elapsed * 1e3:.1f} ms"  # noqa: E501
    )


def test_inv08_three_missing_keys_all_named_within_first_second(tmp_path: Path) -> None:
    raw = _fresh_fixture()
    for dotted in THE_MISSING:
        _delete(raw, dotted)

    for dotted in THE_MISSING:
        assert not _has(raw, f"{dotted}")

    _stage(tmp_path)
    exc, elapsed = _resolve_err(_write_raw(tmp_path, raw), tmp_path)

    msg = str(exc)
    for dotted in THE_MISSING:
        assert dotted in msg, f"aggregation broken: {dotted!r} absent from error:\n{msg}"
        assert any(dotted in p for p in exc.problems), f"{dotted!r} absent from .problems"
    assert len(exc.problems) == 3, f"expected exactly the 3 deletions, got: {exc.problems}"
    assert elapsed < FIRST_SECOND_S, f"aggregated refusal took {elapsed:.3f}s — claim is <1.0s"
    print(
        f"\n[inv08-headline] 3 problems aggregated in {elapsed * 1e3:.1f} ms "
        f"(<{FIRST_SECOND_S}s): {exc.problems}"
    )
    print(
        f"[inv08-scope] see module docstring V7 statement ({len(LEAF_KEYS)}-key green / 5-key defect fraction)."  # noqa: E501
    )


def _write_raw(tmp_path: Path, raw: dict[str, Any]) -> Path:
    p = tmp_path / f"fixture_{uuid.uuid4().hex[:8]}.yaml"
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    return p


def test_inv08_typo_flagged_alongside_intended_key_within_first_second(tmp_path: Path) -> None:
    raw = _fresh_fixture()
    del raw["budget"]["judged_bar_relative"]
    raw["budget"]["judged_bar_realtive"] = 1.0e-4
    assert not _has(raw, "budget.judged_bar_relative")
    assert _has(raw, "budget.judged_bar_realtive")

    _stage(tmp_path)
    exc, elapsed = _resolve_err(_write_raw(tmp_path, raw), tmp_path)

    msg = str(exc)
    assert "unknown key 'budget.judged_bar_realtive'" in msg
    assert "missing key 'budget.judged_bar_relative'" in msg
    assert len(exc.problems) == 2, f"expected exactly the typo pair, got: {exc.problems}"
    assert elapsed < FIRST_SECOND_S, f"took {elapsed:.3f}s"
    print(f"[inv08-typo] both halves flagged in {elapsed * 1e3:.1f} ms: {exc.problems}")


def test_inv08_hf_floor_mismatch_refuses_at_resolve_time(tmp_path: Path) -> None:
    raw = _fresh_fixture()
    raw["exchange"]["hf_floor_m"] = 0.002

    _stage(tmp_path)
    exc, elapsed = _resolve_err(_write_raw(tmp_path, raw), tmp_path)

    msg = str(exc)
    assert "exchange.hf_floor_m" in msg and "physics.hf_floor_m" in msg
    assert len(exc.problems) == 1, f"expected exactly the mismatch, got: {exc.problems}"
    assert elapsed < FIRST_SECOND_S
    print(f"[inv08-hf-floor] refused at resolve time in {elapsed * 1e3:.1f} ms: {exc.problems}")


def test_inv08_infiltration_nonzero_df_refusal_at_resolve_time(tmp_path: Path) -> None:
    solver = yaml.safe_load(yaml.safe_dump(SOLVER_YAML))
    solver["sinks"]["infiltration"]["uniform_rate_mm_per_hr"] = 2.5
    spath = tmp_path / "df_solver.yaml"
    spath.write_text(yaml.safe_dump(solver))
    raw = _fresh_fixture()
    raw["solver_ref"]["config"] = str(spath)

    _stage(tmp_path)
    exc, elapsed = _resolve_err(_write_raw(tmp_path, raw), tmp_path)

    msg = str(exc)
    assert "uniform_rate_mm_per_hr" in msg and "D-F" in msg
    assert len(exc.problems) == 1, f"expected exactly the D-F refusal, got: {exc.problems}"
    assert elapsed < FIRST_SECOND_S
    print(f"[inv08-df] D-F refusal at resolve time in {elapsed * 1e3:.1f} ms: {exc.problems}")


def test_inv08_cross_class_aggregation_missing_typo_crosskey_one_error(tmp_path: Path) -> None:
    raw = _fresh_fixture()
    del raw["budget"]["judged_bar_relative"]
    del raw["diagnostics"]["attribution_radius_m"]
    raw["diagnostics"]["attribution_radus_m"] = 100.0
    raw["exchange"]["hf_floor_m"] = 0.002

    _stage(tmp_path)
    exc, elapsed = _resolve_err(_write_raw(tmp_path, raw), tmp_path)

    msg = str(exc)
    expected = [
        "missing key 'budget.judged_bar_relative'",
        "unknown key 'diagnostics.attribution_radus_m'",
        "missing key 'diagnostics.attribution_radius_m'",
        "exchange.hf_floor_m",
    ]
    for fragment in expected:
        assert fragment in msg, f"cross-class aggregation broken: {fragment!r} absent:\n{msg}"
    assert "physics.hf_floor_m" in msg
    assert len(exc.problems) == 4, f"expected exactly 4 cross-class problems, got: {exc.problems}"
    assert elapsed < FIRST_SECOND_S
    print(f"[inv08-cross-class] 4 problems / 3 classes in {elapsed * 1e3:.1f} ms: {exc.problems}")


# V5 RED DEMO — fail-fast mutant on a /tmp COPY of config.py reddens the claim.

FAIL_FAST_NEEDLE = "problems.append(msg)"
FAIL_FAST_REPLACEMENT = "raise ConfigError([msg], config_path=path)"


def _materialise_fail_fast_mutant(root: Path) -> Any:
    src = Path(config_mod.__file__).read_text()
    n_hits = src.count(FAIL_FAST_NEEDLE)
    assert n_hits == 1, f"mutation-site pin stale ({n_hits} matches for {FAIL_FAST_NEEDLE!r})"
    mutant_path = root / f"config_mut_failfast_{uuid.uuid4().hex[:8]}.py"
    mutant_path.write_text(src.replace(FAIL_FAST_NEEDLE, FAIL_FAST_REPLACEMENT))
    spec = importlib.util.spec_from_file_location(
        f"inv08_mutant_{uuid.uuid4().hex[:8]}", mutant_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_v5_red_demo_fail_fast_mutant_yields_single_key_error(tmp_path: Path) -> None:
    """The named mutation must redden THIS suite's headline assertion.

    Order: green control on pristine code → mutant → same fixture → observe
    single-key error → replicate the invariant assertion → it MUST raise
    AssertionError naming the second key. Outcomes asserted, not narrated.
    """
    coupling_yaml = _stage(tmp_path)
    _, green_elapsed = _resolve_ok(coupling_yaml, tmp_path)

    mutant = _materialise_fail_fast_mutant(tmp_path)
    raw = _fresh_fixture()
    for dotted in THE_MISSING:
        _delete(raw, dotted)

    mut_exc, mut_elapsed = _resolve_err_mutant(mutant, _write_raw(tmp_path, raw), tmp_path)

    # Observed mutant behaviour — must match the V2 prediction exactly.
    assert len(mut_exc.problems) == 1, f"fail-fast mutant carried {len(mut_exc.problems)} problems"
    assert mut_exc.problems[0] == f"missing key '{FIRST_MISSING_IN_SCHEMA_ORDER}'"
    later_keys = [d for d in THE_MISSING if d != FIRST_MISSING_IN_SCHEMA_ORDER]
    mut_msg = str(mut_exc)
    for dotted in later_keys:
        assert dotted not in mut_msg, f"{dotted!r} unexpectedly present under fail-fast mutant"

    # Replicate THIS suite's headline assertion verbatim against the mutant error.
    reddened: str | None = None
    try:
        for dotted in THE_MISSING:
            assert (
                dotted in mut_msg
            ), f"aggregation broken: {dotted!r} absent from error:\n{mut_msg}"
        raise SystemError(
            "fail-fast mutant did NOT redden the invariant — demo vacuous, fix the harness"
        )
    except AssertionError as e:
        reddened = str(e)
    assert reddened is not None and later_keys[0] in reddened, "red capture malformed"
    print(
        f"\n[inv08-red-demo] GREEN control {green_elapsed * 1e3:.1f} ms; MUTANT "
        f"(problem() raises on first defect) returned 1 problem in {mut_elapsed * 1e3:.1f} ms: "
        f"{mut_exc.problems!r}; replicated headline assertion REDDENED with: "
        f"{reddened.splitlines()[0]}"
    )


def _resolve_err_mutant(mutant: Any, path: Path, repo_root: Path) -> tuple[ConfigError, float]:
    t0 = time.perf_counter()
    with pytest.raises(mutant.ConfigError) as ei:
        mutant.resolve_config(path, repo_root)
    err = ei.value
    assert isinstance(err.problems, list)
    return err, time.perf_counter() - t0
