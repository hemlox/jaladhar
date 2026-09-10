"""Rule-7 configuration resolution for the WF-2 surface<->drain coupling.

CLAUDE.md rule 7 exists because a ``KeyError`` on ``buffer_m`` in
``write_depth_series()`` destroyed 37.8 minutes of GPU time: that line only
executes AFTER a complete simulation. This module is the structural fix for
the coupling workflow — :func:`resolve_config` touches EVERY key in spec §5
(``runs/wf2_design_phase/spec.md``), including post-simulation reporting and
output paths, validates types and ranges, and asserts the cross-keys against
the host solver config BEFORE anything expensive starts.

Aggregation contract (rule 7): ALL problems — missing keys, wrong types,
out-of-range values, refused pins, missing input files — are collected into
ONE :class:`ConfigError` listing every one of them, raised in the first
second. Never the first problem alone; a fail-fast raise here is the defect
invariant #8 (``config-aggregates``) exists to catch, demonstrated red in
``tests/coupling/test_config.py``.

Cross-keys asserted (spec §4.1):

- ``exchange.hf_floor_m`` == solver ``physics.hf_floor_m``
- solver ``sinks.infiltration.uniform_rate_mm_per_hr`` == 0.0 exactly — D-F
  refusal. Coupled mode applies capture BEFORE the sink stage (the pinned
  interior call site is unreachable inside monolithic ``acc_step``), so any
  non-zero infiltration would be applied at the wrong point in the step;
  enabling it requires owner adjudication of D-F, not a config edit.
- ``coupling.enabled: true`` requires ``legacy_sink_replacement: true``
  (guard b declaration).
- ``diagnostics.falsifier_set`` must exist on disk when
  ``diagnostics.refuse_start_on_missing_falsifier`` is true (default true) —
  the pre-registered falsifier is loaded at run START or the run refuses.
- ``router.null_edge_policy`` is the D-C pin ``"q_zero_always"`` — the ONLY
  legal value; ``device`` must be ``"cpu"`` (V12: this workflow spends no GPU
  hours).
- every pinned physics key (``storage.assumed_freeboard_m``, ``exchange.cw``,
  ``exchange.cd``, ``exchange.regime_switch_m``,
  ``exchange.capture_cap_fraction``, ``exchange.return_cap_fraction``,
  ``exchange.a_open_width_fraction``, ``exchange.hf_floor_m``) EQUALS the
  constant ``exchange.py`` executes (BugHunt round-1 item D — the manifest
  writes the YAML value as the producing physics, so declared/executed
  divergence is refused at resolve time). The ‡ declared-assumption keys stay
  declared-assumption but must equal the pinned constants; changing one means
  editing the exchange.py constant AND bumping ``coupling_version`` together.
- solver_ref domain ``resolution_m`` == sqrt(exchange.CELL_AREA_M2) = 10.0 m
  (the loader pins graph-manifest cell_area == 100 but nothing pinned dx;
  BugHunt round-1 item E-minor-2).
- ``dt.min_fraction_of_uncoupled`` lies in (0, 1] — K1 divides by it, so 0
  admitted by a closed-interval check was a deferred mid-run ZeroDivisionError
  (BugHunt round-1 item E-minor-1).

Deliberate non-checks, so no other unit's red test is blocked here:
capture/return cap fractions are validated > 0 but NOT capped at <= 1 —
invariant #4's red mutation raises ``capture_cap_fraction`` to 1.1 and needs
the violation to surface at RUNTIME (negative depths), not to be refused at
resolve time.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "BudgetParams",
    "ConfigError",
    "CouplingConfig",
    "DiagParams",
    "DtPolicy",
    "ExchangeParams",
    "ExpectedCounts",
    "ExpectedPartition",
    "GraphPaths",
    "LEAF_KEYS",
    "OutputPaths",
    "RouterParams",
    "SmokeParams",
    "StorageParams",
    "resolve_config",
]


class ConfigError(ValueError):

    def __init__(self, problems: list[str], config_path: Path | None = None) -> None:
        self.problems = list(problems)
        self.config_path = config_path
        header = (
            f"{config_path}: rule-7 pre-flight failed with {len(problems)} problem(s); "
            "fixing ALL of them before the next attempt (aggregated error, CLAUDE.md rule 7):"
        )
        body = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, start=1))
        super().__init__(f"{header}\n{body}")


@dataclass(frozen=True)
class ExpectedCounts:
    nodes: int
    edges: int


@dataclass(frozen=True)
class ExpectedPartition:
    capacity_bearing: int
    zero_slope: int
    area_capped: int
    synthetic: int


@dataclass(frozen=True)
class GraphPaths:
    gpkg: Path
    adjacency: Path
    manifest: Path

    terminal_definition: Path
    require_dag: bool
    expected_counts: ExpectedCounts
    expected_partition: ExpectedPartition


@dataclass(frozen=True)
class ExchangeParams:
    hf_floor_m: float
    regime_switch_m: float
    cw: float
    cd: float
    capture_cap_fraction: float
    return_cap_fraction: float
    capture_radius_m: float
    a_open_width_fraction: float


@dataclass(frozen=True)
class RouterParams:
    null_edge_policy: str
    denominator: str
    no_substepping: bool


@dataclass(frozen=True)
class StorageParams:
    assumed_freeboard_m: float
    isolated_node_width_m: float
    shaft_length_proxy_m: float


@dataclass(frozen=True)
class BudgetParams:
    relative_tolerance: float
    judged_bar_relative: float
    realized_reference_residual: float
    total_water_judged: bool


@dataclass(frozen=True)
class DtPolicy:
    min_fraction_of_uncoupled: float
    indirect_cfl_alarm_factor: float


@dataclass(frozen=True)
class DiagParams:
    falsifier_set: Path
    refuse_start_on_missing_falsifier: bool
    suspicious_deadend_share: float
    ground_truth_manifest: Path

    attribution_mode: str
    attribution_radius_m: float

    attribution_radius_node_m: float
    attribution_radius_basis: str
    g2_min_returned_m3: float


@dataclass(frozen=True)
class OutputPaths:
    run_dir: Path
    manifest: Path
    surcharge_events_csv: Path
    event_continuity_csv: Path
    depth_series_dir: Path
    write_every_s: float


@dataclass(frozen=True)
class SmokeParams:
    window_pad_cells: int
    min_predicted_nodes_in_window: int
    max_window_cells: int
    duration_s: float
    max_steps: int
    cpu_budget_wall_clock_min: float


@dataclass(frozen=True)
class CouplingConfig:

    enabled: bool
    coupling_version: str
    device: str
    legacy_sink_replacement: bool
    solver_config: Path
    graph: GraphPaths
    exchange: ExchangeParams
    router: RouterParams
    storage: StorageParams
    budget: BudgetParams
    dt_policy: DtPolicy
    diagnostics: DiagParams
    outputs: OutputPaths
    smoke: SmokeParams


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_int(v: Any) -> bool:

    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:

    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


Predicate = Callable[[Any], str | None]


def _no_check() -> Predicate:
    def chk(v: Any) -> str | None:
        return None

    return chk


def _nonempty() -> Predicate:
    def chk(v: Any) -> str | None:
        if isinstance(v, str) and v.strip():
            return None
        return f"must be a non-empty string, got {v!r}"

    return chk


def _positive() -> Predicate:
    def chk(v: Any) -> str | None:
        return None if v > 0 else f"must be > 0, got {v!r}"

    return chk


def _nonneg() -> Predicate:
    def chk(v: Any) -> str | None:
        return None if v >= 0 else f"must be >= 0, got {v!r}"

    return chk


def _unit_interval() -> Predicate:
    def chk(v: Any) -> str | None:
        return None if 0 <= v <= 1 else f"must lie in [0, 1], got {v!r}"

    return chk


def _open_unit_interval() -> Predicate:

    def chk(v: Any) -> str | None:
        if 0 < v <= 1:
            return None
        return (
            f"must lie in (0, 1] — K1 divides by it (1/fraction caps coupled steps), "
            f"a 0 would ZeroDivisionError mid-run, got {v!r}"
        )

    return chk


def _window_cells() -> Predicate:

    def chk(v: Any) -> str | None:
        if v <= 0:
            return f"must be > 0, got {v!r}"
        return None if v <= 128 * 128 else f"must be <= 16384 (<= 128x128 toy window), got {v!r}"

    return chk


def _const(expected: Any, why: str) -> Predicate:
    def chk(v: Any) -> str | None:
        return None if v == expected else f"must be {expected!r} ({why}); got {v!r} — refusing"

    return chk


def _one_of(allowed: tuple[str, ...]) -> Predicate:
    def chk(v: Any) -> str | None:
        return None if v in allowed else (f"must be one of {allowed}, got {v!r} — refusing")

    return chk


_KIND_NAME: dict[str, str] = {
    "bool": "a boolean",
    "int": "an integer",
    "num": "a number",
    "str": "a string",
    "path": "a path string",
}

_SCHEMA: list[tuple[str, str, Predicate]] = [
    ("coupling.enabled", "bool", _no_check()),
    ("coupling.coupling_version", "str", _nonempty()),
    (
        "coupling.device",
        "str",
        _const("cpu", "DEFAULT AND REQUIRED 'cpu' for WF-2 (V12: no GPU hours)"),
    ),
    ("coupling.legacy_sink_replacement", "bool", _no_check()),
    ("solver_ref.config", "path", _no_check()),
    ("graph.gpkg", "path", _no_check()),
    ("graph.adjacency", "path", _no_check()),
    ("graph.manifest", "path", _no_check()),
    ("graph.terminal_definition", "path", _no_check()),
    ("graph.require_dag", "bool", _no_check()),
    ("graph.expected_counts.nodes", "int", _positive()),
    ("graph.expected_counts.edges", "int", _positive()),
    ("graph.expected_partition.capacity_bearing", "int", _nonneg()),
    ("graph.expected_partition.zero_slope", "int", _nonneg()),
    ("graph.expected_partition.area_capped", "int", _nonneg()),
    ("graph.expected_partition.synthetic", "int", _nonneg()),
    ("exchange.hf_floor_m", "num", _positive()),
    ("exchange.regime_switch_m", "num", _positive()),
    ("exchange.cw", "num", _positive()),
    ("exchange.cd", "num", _positive()),
    # #4's red mutation sets capture_cap_fraction=1.1 and expects the cap
    ("exchange.capture_cap_fraction", "num", _positive()),
    ("exchange.return_cap_fraction", "num", _positive()),
    ("exchange.capture_radius_m", "num", _nonneg()),
    ("exchange.a_open_width_fraction", "num", _positive()),
    (
        "router.null_edge_policy",
        "str",
        _const("q_zero_always", "D-C pin: NULL-capacity edges carry Q_edge=0 ALWAYS"),
    ),
    (
        "router.denominator",
        "str",
        _const("capacity_bearing_only", "outgoing split excludes null/synthetic edges"),
    ),
    (
        "router.no_substepping",
        "bool",
        _const(True, "const: graph advances on host adaptive dt only"),
    ),
    ("storage.assumed_freeboard_m", "num", _positive()),
    ("storage.isolated_node_width_m", "num", _positive()),
    ("storage.shaft_length_proxy_m", "num", _positive()),
    ("budget.relative_tolerance", "num", _positive()),
    ("budget.judged_bar_relative", "num", _positive()),
    ("budget.realized_reference_residual", "num", _positive()),
    ("budget.total_water_judged", "bool", _no_check()),
    ("dt.min_fraction_of_uncoupled", "num", _open_unit_interval()),
    ("dt.indirect_cfl_alarm_factor", "num", _unit_interval()),
    ("diagnostics.falsifier_set", "path", _no_check()),
    ("diagnostics.refuse_start_on_missing_falsifier", "bool", _no_check()),
    ("diagnostics.suspicious_deadend_share", "num", _unit_interval()),
    ("diagnostics.ground_truth_manifest", "path", _no_check()),
    (
        "diagnostics.attribution_mode",
        "str",
        _one_of(("node", "edge")),
    ),
    ("diagnostics.attribution_radius_m", "num", _positive()),
    ("diagnostics.attribution_radius_node_m", "num", _positive()),
    ("diagnostics.attribution_radius_basis", "str", _nonempty()),
    ("diagnostics.g2_min_returned_m3", "num", _nonneg()),
    ("outputs.run_dir", "path", _no_check()),
    ("outputs.manifest", "path", _no_check()),
    ("outputs.surcharge_events_csv", "path", _no_check()),
    ("outputs.event_continuity_csv", "path", _no_check()),
    ("outputs.depth_series_dir", "path", _no_check()),
    ("outputs.write_every_s", "num", _positive()),
    ("smoke.window_pad_cells", "int", _nonneg()),
    ("smoke.min_predicted_nodes_in_window", "int", _positive()),
    ("smoke.max_window_cells", "int", _window_cells()),
    ("smoke.duration_s", "num", _positive()),
    ("smoke.max_steps", "int", _positive()),
    ("smoke.cpu_budget_wall_clock_min", "num", _positive()),
]

LEAF_KEYS: tuple[str, ...] = tuple(name for name, _, _ in _SCHEMA)

_NESTED_SECTIONS: dict[str, tuple[str, ...]] = {
    "graph.expected_counts": ("nodes", "edges"),
    "graph.expected_partition": ("capacity_bearing", "zero_slope", "area_capped", "synthetic"),
}

_REQUIRED_INPUT_PATHS: tuple[str, ...] = (
    "solver_ref.config",
    "graph.gpkg",
    "graph.adjacency",
    "graph.manifest",
    "graph.terminal_definition",
    "diagnostics.ground_truth_manifest",
)


def _kind_check(kind: str, v: Any) -> str | None:
    ok = {
        "bool": _is_bool,
        "int": _is_int,
        "num": _is_num,
        "str": _is_str,
        "path": _is_str,
    }[kind]
    if not ok(v):
        return f"must be {_KIND_NAME[kind]}, got {v!r} ({type(v).__name__})"
    return None


def _walk(raw: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = raw
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _abspath(v: Any, repo_root: Path) -> Path:
    p = Path(str(v))
    return p if p.is_absolute() else repo_root / p


def _load_yaml(path: Path, problems: list[str], what: str) -> Any:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except OSError as e:
        problems.append(f"{what}: cannot read {path}: {e}")
    except yaml.YAMLError as e:
        problems.append(f"{what}: invalid YAML in {path}: {e}")
    return None


def resolve_config(path: Path, repo_root: Path) -> CouplingConfig:
    """Resolve ``configs/coupling.yaml`` into a frozen :class:`CouplingConfig`.

    Touches EVERY leaf key in spec §5 — including post-simulation reporting
    and output paths — validates kinds and ranges, asserts cross-keys against
    the host solver config, and raises ONE aggregated :class:`ConfigError`
    naming every problem found. All problem reports funnel through a single
    collector so a fail-fast mutation is caught by invariant #8's red demo.

    Args:
        path: path to the coupling YAML (usually ``configs/coupling.yaml``).
        repo_root: repository root; relative paths in the YAML are resolved
            against it and stored absolute on the returned object.

    Returns:
        A fully-resolved, frozen :class:`CouplingConfig`.

    Raises:
        ConfigError: aggregated over ALL problems, in the first second.
    """
    path = Path(path)
    repo_root = Path(repo_root)
    problems: list[str] = []

    def problem(msg: str) -> None:

        problems.append(msg)

    raw = _load_yaml(path, problems, "coupling config")
    if raw is None:

        raise ConfigError(problems, config_path=path)
    if not isinstance(raw, Mapping):
        problem(f"top-level document must be a mapping, got {type(raw).__name__}")
        raise ConfigError(problems, config_path=path)

    values: dict[str, Any] = {}
    bad_parents: set[str] = set()

    known_tops = {name.split(".")[0] for name in LEAF_KEYS}
    for sect, sect_val in raw.items():
        if sect not in known_tops:
            problem(f"unknown section '{sect}' (not declared in spec §5) — typo? refusing")
            continue
        if not isinstance(sect_val, Mapping):
            problem(f"section '{sect}' must be a mapping, got {type(sect_val).__name__}")
            bad_parents.add(sect)
            continue
        for key, val in sect_val.items():
            full = f"{sect}.{key}"
            if full in _NESTED_SECTIONS:
                if not isinstance(val, Mapping):
                    allowed = ", ".join(_NESTED_SECTIONS[full])
                    problem(f"'{full}' must be a mapping of {{{allowed}}}, got {val!r}")
                    bad_parents.add(full)
                    continue
                for sub_key, sub_val in val.items():
                    sub_full = f"{full}.{sub_key}"
                    if sub_full not in LEAF_KEYS:
                        problem(f"unknown key '{sub_full}' — typo? refusing")
                        continue
                    values[sub_full] = sub_val
            elif full in LEAF_KEYS:
                values[full] = val
            else:
                problem(f"unknown key '{full}' — typo? refusing")

    for name, kind, pred in _SCHEMA:
        if any(name == bp or name.startswith(bp + ".") for bp in bad_parents):
            continue
        found, v = _walk(raw, name)
        if not found:
            problem(f"missing key '{name}'")
            continue
        type_err = _kind_check(kind, v)
        if type_err is not None:
            problem(f"'{name}' {type_err}")
            continue
        range_err = pred(v)
        if range_err is not None:
            problem(f"'{name}' {range_err}")
            continue
        values[name] = v

    for name in _REQUIRED_INPUT_PATHS:
        if name in values:
            p = _abspath(values[name], repo_root)
            if not p.exists():
                problem(f"'{name}': file not found: {p}")

    refuse = values.get("diagnostics.refuse_start_on_missing_falsifier")
    fs_val = values.get("diagnostics.falsifier_set")
    if refuse is True and fs_val is not None:
        fs_path = _abspath(fs_val, repo_root)
        if not fs_path.exists():
            problem(
                f"'diagnostics.falsifier_set': pre-registered prediction set not found at "
                f"{fs_path} and diagnostics.refuse_start_on_missing_falsifier=true — refusing "
                "to start (invariant #12)"
            )

    enabled = values.get("coupling.enabled")
    legacy = values.get("coupling.legacy_sink_replacement")
    if enabled is True and legacy is not True:
        problem(
            "'coupling.enabled'=true requires 'coupling.legacy_sink_replacement'=true "
            "(guard b declaration: the run manifest must declare legacy sink replacement)"
        )

    sc_val = values.get("solver_ref.config")
    solver_raw: Any = None
    if sc_val is not None:
        spath = _abspath(sc_val, repo_root)
        solver_raw = _load_yaml(spath, problems, "solver config")
        if isinstance(solver_raw, Mapping):
            physics = solver_raw.get("physics")
            s_hf = physics.get("hf_floor_m") if isinstance(physics, Mapping) else None
            c_hf = values.get("exchange.hf_floor_m")
            if s_hf is None:
                problem(
                    f"solver config {spath} missing key 'physics.hf_floor_m' "
                    "(needed for hf_floor cross-key)"
                )
            elif c_hf is not None and s_hf != c_hf:
                problem(
                    f"'exchange.hf_floor_m' ({c_hf!r}) MUST equal solver 'physics.hf_floor_m' "
                    f"({s_hf!r} in {spath})"
                )
            sinks = solver_raw.get("sinks")
            infil = sinks.get("infiltration") if isinstance(sinks, Mapping) else None
            rate = infil.get("uniform_rate_mm_per_hr") if isinstance(infil, Mapping) else None
            if rate is None:
                problem(
                    f"solver config {spath} missing key "
                    "'sinks.infiltration.uniform_rate_mm_per_hr' (needed for D-F assertion)"
                )
            elif rate != 0.0:
                problem(
                    f"D-F refusal: solver 'sinks.infiltration.uniform_rate_mm_per_hr' is {rate!r}; "
                    "coupled mode asserts it == 0.0 exactly (capture precedes the sink stage under "
                    "the v1 call-site realization — enabling infiltration in coupled mode requires "
                    "owner adjudication of deviation D-F, not a config edit)"
                )

    from jaladhar.coupling.exchange import (
        A_OPEN_WIDTH_FRACTION,
        ASSUMED_FREEBOARD_M,
        CAPTURE_CAP_FRACTION,
        CELL_AREA_M2,
        HF_FLOOR_M,
        ORIFICE_COEFF_CD,
        REGIME_SWITCH_M,
        RETURN_CAP_FRACTION,
        WEIR_COEFF_CW,
    )

    _EXECUTED_PIN_PARITY: tuple[tuple[str, str, float], ...] = (
        ("storage.assumed_freeboard_m", "ASSUMED_FREEBOARD_M", ASSUMED_FREEBOARD_M),
        ("exchange.cw", "WEIR_COEFF_CW", WEIR_COEFF_CW),
        ("exchange.cd", "ORIFICE_COEFF_CD", ORIFICE_COEFF_CD),
        ("exchange.regime_switch_m", "REGIME_SWITCH_M", REGIME_SWITCH_M),
        ("exchange.capture_cap_fraction", "CAPTURE_CAP_FRACTION", CAPTURE_CAP_FRACTION),
        ("exchange.return_cap_fraction", "RETURN_CAP_FRACTION", RETURN_CAP_FRACTION),
        ("exchange.a_open_width_fraction", "A_OPEN_WIDTH_FRACTION", A_OPEN_WIDTH_FRACTION),
    )
    for _key, _const_name, _const in _EXECUTED_PIN_PARITY:
        _val = values.get(_key)
        if _val is not None and _val != _const:
            problem(
                f"'{_key}' ({_val!r}) MUST equal the executed constant "
                f"jaladhar.coupling.exchange.{_const_name} ({_const!r}) — the run "
                "manifest records the YAML value as the producing physics while the runtime "
                "executes the module constant (declared/executed divergence, BugHunt round-1 "
                "item D); changing it requires editing the constant AND bumping "
                "coupling_version together"
            )

    c_hf = values.get("exchange.hf_floor_m")
    hf_solver_reported = any("exchange.hf_floor_m" in p for p in problems)
    if not hf_solver_reported and c_hf is not None and c_hf != HF_FLOOR_M:
        problem(
            f"'exchange.hf_floor_m' ({c_hf!r}) MUST equal the executed constant "
            f"jaladhar.coupling.exchange.HF_FLOOR_M ({HF_FLOOR_M!r}) — the run manifest "
            "records the YAML value as the producing physics while the runtime executes the "
            "module constant; changing it requires editing the constant AND bumping "
            "coupling_version together"
        )

    if isinstance(solver_raw, Mapping):
        dom_val = solver_raw.get("domain_config")
        if isinstance(dom_val, str):
            dpath = _abspath(dom_val, repo_root)
            dom_raw = _load_yaml(dpath, problems, "domain config")
            res = dom_raw.get("resolution_m") if isinstance(dom_raw, Mapping) else None
            expected_dx = math.sqrt(CELL_AREA_M2)
            if not _is_num(res):
                problem(
                    f"domain config {dpath} missing numeric 'resolution_m' (needed for the "
                    f"dx<->cell-area cross-key: must equal sqrt(exchange.CELL_AREA_M2) = "
                    f"{expected_dx!r} m)"
                )
            elif not math.isclose(float(res), expected_dx, rel_tol=1e-9, abs_tol=0.0):
                problem(
                    f"domain 'resolution_m' is {res!r} ({dpath}) but the exchange contract cell "
                    f"area jaladhar.coupling.exchange.CELL_AREA_M2 = {CELL_AREA_M2!r} m^2 implies "
                    f"dx = sqrt(cell area) = {expected_dx!r} m — the drain overlay would sit on a "
                    "different lattice than the cells exchange math assumes"
                )

    if problems:
        raise ConfigError(problems, config_path=path)

    cfg = CouplingConfig(
        enabled=values["coupling.enabled"],
        coupling_version=values["coupling.coupling_version"],
        device=values["coupling.device"],
        legacy_sink_replacement=values["coupling.legacy_sink_replacement"],
        solver_config=_abspath(values["solver_ref.config"], repo_root),
        graph=GraphPaths(
            gpkg=_abspath(values["graph.gpkg"], repo_root),
            adjacency=_abspath(values["graph.adjacency"], repo_root),
            manifest=_abspath(values["graph.manifest"], repo_root),
            terminal_definition=_abspath(values["graph.terminal_definition"], repo_root),
            require_dag=values["graph.require_dag"],
            expected_counts=ExpectedCounts(
                nodes=values["graph.expected_counts.nodes"],
                edges=values["graph.expected_counts.edges"],
            ),
            expected_partition=ExpectedPartition(
                capacity_bearing=values["graph.expected_partition.capacity_bearing"],
                zero_slope=values["graph.expected_partition.zero_slope"],
                area_capped=values["graph.expected_partition.area_capped"],
                synthetic=values["graph.expected_partition.synthetic"],
            ),
        ),
        exchange=ExchangeParams(
            hf_floor_m=values["exchange.hf_floor_m"],
            regime_switch_m=values["exchange.regime_switch_m"],
            cw=values["exchange.cw"],
            cd=values["exchange.cd"],
            capture_cap_fraction=values["exchange.capture_cap_fraction"],
            return_cap_fraction=values["exchange.return_cap_fraction"],
            capture_radius_m=values["exchange.capture_radius_m"],
            a_open_width_fraction=values["exchange.a_open_width_fraction"],
        ),
        router=RouterParams(
            null_edge_policy=values["router.null_edge_policy"],
            denominator=values["router.denominator"],
            no_substepping=values["router.no_substepping"],
        ),
        storage=StorageParams(
            assumed_freeboard_m=values["storage.assumed_freeboard_m"],
            isolated_node_width_m=values["storage.isolated_node_width_m"],
            shaft_length_proxy_m=values["storage.shaft_length_proxy_m"],
        ),
        budget=BudgetParams(
            relative_tolerance=values["budget.relative_tolerance"],
            judged_bar_relative=values["budget.judged_bar_relative"],
            realized_reference_residual=values["budget.realized_reference_residual"],
            total_water_judged=values["budget.total_water_judged"],
        ),
        dt_policy=DtPolicy(
            min_fraction_of_uncoupled=values["dt.min_fraction_of_uncoupled"],
            indirect_cfl_alarm_factor=values["dt.indirect_cfl_alarm_factor"],
        ),
        diagnostics=DiagParams(
            falsifier_set=_abspath(values["diagnostics.falsifier_set"], repo_root),
            refuse_start_on_missing_falsifier=values[
                "diagnostics.refuse_start_on_missing_falsifier"
            ],
            suspicious_deadend_share=values["diagnostics.suspicious_deadend_share"],
            ground_truth_manifest=_abspath(values["diagnostics.ground_truth_manifest"], repo_root),
            attribution_mode=str(values["diagnostics.attribution_mode"]),
            attribution_radius_m=values["diagnostics.attribution_radius_m"],
            attribution_radius_node_m=values["diagnostics.attribution_radius_node_m"],
            attribution_radius_basis=str(values["diagnostics.attribution_radius_basis"]),
            g2_min_returned_m3=values["diagnostics.g2_min_returned_m3"],
        ),
        outputs=OutputPaths(
            run_dir=_abspath(values["outputs.run_dir"], repo_root),
            manifest=_abspath(values["outputs.manifest"], repo_root),
            surcharge_events_csv=_abspath(values["outputs.surcharge_events_csv"], repo_root),
            event_continuity_csv=_abspath(values["outputs.event_continuity_csv"], repo_root),
            depth_series_dir=_abspath(values["outputs.depth_series_dir"], repo_root),
            write_every_s=values["outputs.write_every_s"],
        ),
        smoke=SmokeParams(
            window_pad_cells=values["smoke.window_pad_cells"],
            min_predicted_nodes_in_window=values["smoke.min_predicted_nodes_in_window"],
            max_window_cells=values["smoke.max_window_cells"],
            duration_s=values["smoke.duration_s"],
            max_steps=values["smoke.max_steps"],
            cpu_budget_wall_clock_min=values["smoke.cpu_budget_wall_clock_min"],
        ),
    )
    return cfg
