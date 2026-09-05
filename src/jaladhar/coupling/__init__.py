"""WF-2 surface<->drain coupling (weir-orifice-exchange).

Spec: ``runs/wf2_design_phase/spec.md``; frozen contract
``configs/contracts/coupling_iface.json`` v1.1.0. Modules land unit by unit
(config, router, exchange, ledger, solver_hook, diagnostics); each build unit
extends these exports. Only the public API is re-exported here.
"""

from jaladhar.coupling.config import ConfigError, CouplingConfig, resolve_config
from jaladhar.coupling.diagnostics import (
    DiagnosticsRefusal,
    FalsifierSet,
    attribute_ground_truth,
    compare_falsifier,
    component_split,
    g2_verdict,
    load_falsifier_set,
    read_surcharge_events_csv,
    write_surcharge_events_csv,
)
from jaladhar.coupling.exchange import (
    CoupleResult,
    NodeState,
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.ledger import (
    AntiDoubleCountError,
    CouplingMassBreach,
    CouplingMassLedger,
    IndirectCflMonitor,
)
from jaladhar.coupling.router import DrainGraph, RoutePlan, load_drain_graph, route
from jaladhar.coupling.solver_hook import (
    ComputeBudgetExceeded,
    CoupledRunResult,
    KillThresholdHalt,
    coupled_parameter_set,
    simulate_coupled,
)

__all__ = [
    "AntiDoubleCountError",
    "ComputeBudgetExceeded",
    "ConfigError",
    "CoupleResult",
    "CoupledRunResult",
    "CouplingConfig",
    "CouplingMassBreach",
    "CouplingMassLedger",
    "DiagnosticsRefusal",
    "DrainGraph",
    "FalsifierSet",
    "IndirectCflMonitor",
    "KillThresholdHalt",
    "NodeState",
    "RoutePlan",
    "attribute_ground_truth",
    "build_node_state",
    "compare_falsifier",
    "component_split",
    "coupled_parameter_set",
    "couple_step",
    "g2_verdict",
    "load_drain_graph",
    "load_falsifier_set",
    "read_surcharge_events_csv",
    "resolve_config",
    "route",
    "simulate_coupled",
    "write_surcharge_events_csv",
    "zero_drain_cap_out_of_place",
]
