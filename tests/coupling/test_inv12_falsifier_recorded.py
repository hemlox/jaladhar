"""Invariant #12 ``falsifier-recorded`` (spec §12 row #12; §11.2; R5 discipline).

V2: "if this were broken I would observe ___" — a coupled run completing WITHOUT
any preregistration block in its manifest (or with counts that do not reproduce
from the preregistered file's bytes on disk), or a missing/corrupt prediction
set being silently skipped instead of refusing the start.

CLAIM under test: "The preregistered prediction set is loaded at coupled-run
start from ``runs/wf2_falsifier_preregistration/surcharge_prediction_set.json``
(n_predicted_edges=70, 116 predicted node ids asserted AGAINST DISK); absence
or corruption REFUSES the run (status ``refused_falsifier_missing``, NOT
``refused_with_D_G``); the run manifest carries the predicted-vs-actual
comparison recorded NEVER tuned."

REALIZED CONTRACT (inspected 2026-08-26 by probe, encoded honestly):

- At START the driver (``solver_hook._load_falsifier_header``) reads ONLY the
  set's header counts off disk or refuses: absent/unreadable/count-less =>
  :class:`~jaladhar.coupling.router.RefuseLoadError`, manifest status
  ``refused_falsifier_missing``, verbatim message kept, distinct from the D-G
  graph-reader seam. DEEP schema defects (duplicate/unsorted ids, count/list
  disagreement, bad hex sha) are the DIAGNOSTICS unit's refusal
  (:class:`~jaladhar.coupling.diagnostics.DiagnosticsRefusal` via
  ``load_falsifier_set``) before any comparison is computed — two layers, both
  tested here, boundary named.
- At COMPLETION the driver writes ``falsifier_comparison`` = the preregistration
  RECORD (path, loaded_at_start, n_predicted_edges, n_predicted_nodes,
  source_gpkg_sha256) with ``status: comparison_deferred_to_diagnostics_unit``.
  Spec §11.2's four OVERLAP counts (predicted_nodes_surcharged,
  flagged_edges_whose_downstream_node_surcharged, flagged_edges_not_surcharged,
  unflagged_surcharging_nodes + sha cross-check) are computed by the diagnostics
  unit (:func:`~jaladhar.coupling.diagnostics.compare_falsifier`) over the run's
  OWN realized ``surcharge_events.csv`` product at scoring time. The invariant
  checker accepts EITHER §11.2 compliance form on the overlap: the four counts
  carried INLINE (a manifest literally complying with the spec text) OR the
  explicit deferral record — NEITHER present is a violation (arm f pins the
  inline form clean; arms e1/e2 pin the fire side). This module
  pins BOTH halves and proves the full comparison block materializes, with all
  four count fields arithmetically consistent, from a completed micro-run's
  products on disk.
- RED CONDITION LIVE TODAY (config surface, no code mutation needed): flipping
  ``diagnostics.refuse_start_on_missing_falsifier`` to false lets a coupled run
  COMPLETE with ``loaded_at_start: false`` and NO counts — the escape-hatch
  world. Per the honest contract this module treats absence-of-preregistration-
  on-a-completed-run as INVARIANT FAILURE: ``_inv12_completed_violations``
  below re-reads the preregistered artefact and the gpkg BYTES independently
  and lists every violation; the red demos capture its output verbatim.

RED DEMOS (V5), mutations recorded beside each test:

- (e1) CONFIG FLIP (the real escape hatch, primary): refuse=false + absent path
  => run completes, comparison skipped => the invariant checker MUST fire;
  captured verbatim. This is the world the invariant exists to catch.
- (e2) FORGED HEADER (code surface): ``solver_hook._load_falsifier_header``
  monkeypatched to return fabricated counts (999/999, bogus sha) while the
  pristine set sits untouched on disk => run completes with a lying manifest =>
  the checker, which trusts only fresh disk bytes, MUST fire; captured
  verbatim. (Pre-fix history for the refusal LABEL — falsifier refusals once
  wore ``refused_with_D_G`` — is recorded red->green in
  ``tests/coupling/test_solver_hook.py::TestRefusalSourceLabels``; this module
  re-asserts the discriminating negatives.)

Every count assertion is made AGAINST DISK: the JSON is re-parsed here and the
gpkg sha256 recomputed by THIS module's own chunked-hash loop — never via the
code under test's helpers (V2 non-mirror).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import pytest
import torch

from jaladhar.coupling.config import CouplingConfig, resolve_config
from jaladhar.coupling.diagnostics import (
    DiagnosticsRefusal,
    compare_falsifier,
    load_falsifier_set,
)
from jaladhar.coupling.router import RefuseLoadError, build_drain_graph
from jaladhar.coupling.solver_hook import simulate_coupled
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config

REPO = Path(__file__).resolve().parents[2]

PREREG_PATH = REPO / "runs" / "wf2_falsifier_preregistration" / "surcharge_prediction_set.json"
PREREG_MANIFEST_PATH = REPO / "runs" / "wf2_falsifier_preregistration" / "manifest.json"
GPKG_PATH = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"

# Preregistered facts [F] — the owner-recorded prediction set. Literals pinned so
# a re-preregistration fails loudly HERE instead of silently shifting baselines.
EXPECTED_N_PREDICTED_EDGES = 70
EXPECTED_N_PREDICTED_NODES = 116


# ---------------------------------------------------------------------------
# Independent observables — recomputed from raw bytes, sharing no code with the
# modules under test (V2 non-mirror).
# ---------------------------------------------------------------------------


def _sha256_of_file(path: Path) -> str:
    """This module's OWN chunked sha256 — deliberately NOT diagnostics.sha256_file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prereg_from_disk() -> dict[str, Any]:
    """Fresh re-parse of the preregistered set's bytes (never cached, never trusted)."""
    return json.loads(PREREG_PATH.read_text())


# Spec §11.2's four OVERLAP count fields — one of the TWO accepted compliance forms when
# carried inline (the other being the explicit deferral record).
_OVERLAP_COUNT_FIELDS = (
    "predicted_nodes_surcharged",
    "flagged_edges_whose_downstream_node_surcharged",
    "flagged_edges_not_surcharged",
    "unflagged_surcharging_nodes",
)


def _inv12_completed_violations(
    manifest: dict[str, Any], prereg_disk: dict[str, Any], gpkg_sha: str
) -> list[str]:
    """Invariant #12 checker over a COMPLETED coupled-run manifest's REALIZED bytes.

    Pure function of (manifest dict, fresh preregistration disk parse, test-side
    gpkg sha). Returns every violation with an empty list meaning clean — the
    GREEN arms assert silence on honest manifests, the RED arms assert the
    listed violations on the escape-hatch and forged-header worlds.

    §11.2 compliance on the overlap is EITHER form: the four OVERLAP counts inline
    OR an explicit deferral record (status ``comparison_deferred_to_diagnostics_unit``)
    — a manifest literally complying via inline counts must NOT be flagged. A
    manifest carrying NEITHER violates.
    """
    if manifest.get("status") != "completed":
        return [f"not a completed manifest (status={manifest.get('status')!r}); checker N/A"]
    violations: list[str] = []
    fc = manifest.get("falsifier_comparison")
    if not isinstance(fc, dict):
        return [
            "completed manifest carries NO falsifier_comparison block — preregistration unrecorded"
        ]
    if fc.get("loaded_at_start") is not True:
        violations.append(
            f"falsifier_comparison.loaded_at_start={fc.get('loaded_at_start')!r} — the "
            "preregistered set was NEVER loaded (skipped_by_config / silent skip); a "
            "completed run without the preregistration violates invariant #12"
        )
    n_edges = fc.get("n_predicted_edges")
    if n_edges != prereg_disk["n_predicted_edges"] or n_edges != EXPECTED_N_PREDICTED_EDGES:
        violations.append(
            f"falsifier_comparison.n_predicted_edges={n_edges!r} != disk-preregistered "
            f"{prereg_disk['n_predicted_edges']} (pinned {EXPECTED_N_PREDICTED_EDGES})"
        )
    n_nodes = fc.get("n_predicted_nodes")
    disk_ids = prereg_disk["predicted_node_ids"]
    disk_n_unique_sorted = (
        len(set(disk_ids)) if isinstance(disk_ids, list) and disk_ids == sorted(disk_ids) else -1
    )
    if n_nodes != disk_n_unique_sorted or n_nodes != EXPECTED_N_PREDICTED_NODES:
        violations.append(
            f"falsifier_comparison.n_predicted_nodes={n_nodes!r} != disk-unique-sorted "
            f"{disk_n_unique_sorted} (pinned {EXPECTED_N_PREDICTED_NODES})"
        )
    if fc.get("source_gpkg_sha256") != gpkg_sha:
        violations.append(
            f"falsifier_comparison.source_gpkg_sha256={fc.get('source_gpkg_sha256')!r} != "
            f"sha256 recomputed from gpkg bytes {gpkg_sha[:16]}..."
        )
    counts_inline = all(field in fc for field in _OVERLAP_COUNT_FIELDS)
    explicit_deferral = fc.get("status") == "comparison_deferred_to_diagnostics_unit"
    if not counts_inline and not explicit_deferral:
        missing = [f for f in _OVERLAP_COUNT_FIELDS if f not in fc]
        violations.append(
            f"falsifier_comparison records NEITHER the four §11.2 overlap counts inline "
            f"(missing {missing}) NOR an explicit deferral record "
            f"(status={fc.get('status')!r}) — spec §11.2 compliance is one form or the other"
        )
    return violations


# ---------------------------------------------------------------------------
# Fixtures & micro-run machinery (self-contained copies of the toy-tier pattern
# from tests/coupling/test_solver_hook.py — declared-synthetic, CPU-only).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prereg_disk() -> dict[str, Any]:
    return _prereg_from_disk()


@pytest.fixture(scope="module")
def gpkg_sha() -> str:
    return _sha256_of_file(GPKG_PATH)


def _static(shape: tuple[int, int] = (64, 64)) -> StaticFields:
    """Flat CLOSED micro-domain carrying a LIVE legacy drain prior (zeroing matters)."""
    hh, ww = shape
    z = lambda *s: torch.zeros(*s, dtype=torch.float32)  # noqa: E731
    f = lambda *s, v=0.0: torch.full((*s,), v, dtype=torch.float32)  # noqa: E731
    return StaticFields(
        dz_x=z(hh, ww - 1),
        dz_y=z(hh - 1, ww),
        n_x=f(hh, ww - 1, v=0.03),
        n_y=f(hh - 1, ww, v=0.03),
        c_x=torch.ones(hh, ww - 1, dtype=torch.float32),
        c_y=torch.ones(hh - 1, ww, dtype=torch.float32),
        drain_cap_m_s=f(hh, ww, v=3.2e-6),
        infil_rate_m_s=z(hh, ww),
        edge_w_n=f(hh, v=0.03),
        edge_e_n=f(hh, v=0.03),
        edge_n_n=f(ww, v=0.03),
        edge_s_n=f(ww, v=0.03),
        edge_w_s=f(hh, v=1e-4),
        edge_e_s=f(hh, v=1e-4),
        edge_n_s=f(ww, v=1e-4),
        edge_s_s=f(ww, v=1e-4),
        edge_open=(0.0, 0.0, 0.0, 0.0),
        shape=shape,
    )


def _chain_graph(rows: int = 64, cols: int = 64, n_nodes: int = 8, cap: float = 0.05):
    """Capacity-bearing chain across distinct cells; terminal node is an outfall."""
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
        width_mean_m=torch.full((n_nodes,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
    )


def _coupling_cfg(tmp_path: Path, *, falsifier_set: Path | None = None, refuse: bool = True):
    """Hermetic coupling cfg: tmp outputs; diagnostics defaults to the REAL
    preregistered set with the refuse gate ON unless a variant overrides."""
    base: CouplingConfig = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    outs = dc_replace(
        base.outputs,
        run_dir=tmp_path / "run",
        manifest=tmp_path / "run" / "manifest.json",
        surcharge_events_csv=tmp_path / "run" / "products" / "surcharge_events.csv",
        event_continuity_csv=tmp_path / "run" / "products" / "event_continuity.csv",
        depth_series_dir=tmp_path / "run" / "depth",
    )
    diag = dc_replace(base.diagnostics, refuse_start_on_missing_falsifier=refuse)
    if falsifier_set is not None:
        diag = dc_replace(diag, falsifier_set=falsifier_set)
    return dc_replace(base, outputs=outs, diagnostics=diag)


def _solver_cfg() -> dict[str, Any]:
    scfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    scfg = copy.deepcopy(scfg)
    scfg["boundaries"]["mode"] = "closed"
    return scfg


def _wet_run(cfg: CouplingConfig, *, duration_s: float = 2400.0, max_steps: int = 200):
    """Micro coupled run known to surcharge (terminal chain node accumulates, D-A)."""
    return simulate_coupled(
        cfg,
        _solver_cfg(),
        REPO,
        graph=_chain_graph(),
        static=_static(),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=duration_s,
        max_steps=max_steps,
        mass_check_every=50,
        snapshot_every_s=900.0,
        smoke=True,
    )


# ---------------------------------------------------------------------------
# Arm (a): pristine set — counts asserted AGAINST DISK BYTES
# ---------------------------------------------------------------------------


def test_a_preregistered_set_reproduces_from_disk_bytes(prereg_disk, gpkg_sha) -> None:
    """GREEN arm (a): the preregistered set's declared counts equal what its own
    bytes and the gpkg bytes on disk actually contain — 70 flagged edges, 116
    unique ascending node ids, sha256(gpkg) recomputed BY THIS TEST.

    If this were broken I would observe: a set whose recorded counts drift from
    its own payload, or a sha naming different bytes than the realized graph —
    i.e., a baseline nobody could reproduce (V3 moving-baseline refusal)."""
    disk = prereg_disk
    assert disk["n_predicted_edges"] == EXPECTED_N_PREDICTED_EDGES
    assert len(disk["edges"]) == EXPECTED_N_PREDICTED_EDGES
    ids = disk["predicted_node_ids"]
    assert len(ids) == EXPECTED_N_PREDICTED_NODES
    assert len(set(ids)) == EXPECTED_N_PREDICTED_NODES, "ids must be unique on disk"
    assert ids == sorted(ids), "recorded set must be canonical ascending on disk"
    assert disk["source_gpkg_sha256"] == gpkg_sha, (
        "set's recorded source sha != sha256 recomputed here from "
        f"{GPKG_PATH} bytes — the set was NOT taken from these bytes"
    )

    # Loader agreement (same disk, validated instance) + edge-endpoint closure.
    fs = load_falsifier_set(PREREG_PATH)
    assert fs.n_predicted_edges == EXPECTED_N_PREDICTED_EDGES
    assert fs.n_predicted_nodes == EXPECTED_N_PREDICTED_NODES
    assert fs.source_gpkg_sha256 == gpkg_sha
    id_set = set(fs.predicted_node_ids)
    assert all(e["to_node"] in id_set and e["from_node"] in id_set for e in fs.edges)

    # Producer-side [F] reconciliation block agrees with the bytes (V8 producer half).
    pman = json.loads(PREREG_MANIFEST_PATH.read_text())
    rec = pman["reconciliation"]
    assert rec["bytes_vs_manifest"] == "ok"
    assert rec["n_predicted_edges"] == EXPECTED_N_PREDICTED_EDGES
    assert rec["n_predicted_nodes_distinct"] == EXPECTED_N_PREDICTED_NODES
    print(f"\n[inv12-a] disk bytes reproduce: 70 edges / 116 nodes / sha {gpkg_sha[:16]}...")


# ---------------------------------------------------------------------------
# Arm (b): ABSENT set refuses the start — distinct from the D-G seam
# ---------------------------------------------------------------------------


def test_b_absent_set_refuses_start_as_refused_falsifier_missing(tmp_path) -> None:
    """GREEN arm (b): refuse gate ON + absent path => RefuseLoadError BEFORE any
    simulation work; manifest lands at status ``refused_falsifier_missing`` —
    NEVER ``refused_with_D_G`` (a missing prediction set says nothing about the
    drain-graph artefact seam), no D-G notice, verbatim message kept.

    Positive-side pin for the LABEL discrimination: the red->green history of this
    refusal source split is recorded in
    ``tests/coupling/test_solver_hook.py::TestRefusalSourceLabels`` (falsifier-missing
    once wore ``refused_with_D_G``), and
    ``tests/coupling/test_solver_hook.py::test_real_artefact_refusal_propagates_refused_with_D_G``
    pins the OTHER side — a genuine D-G artefact-seam refusal DOES land at
    ``refused_with_D_G``. Between them the two labels are pinned from both directions.

    If this were broken I would observe: the run proceeding to completion, or a
    manifest wearing D-G's refusal label for a falsifier-source problem."""
    absent = tmp_path / "nowhere" / "prediction_set.json"
    cfg = _coupling_cfg(tmp_path, falsifier_set=absent, refuse=True)

    with pytest.raises(RefuseLoadError, match="falsifier prediction set missing") as ei:
        simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),  # injected: graph seam cannot mask this
            static=_static((16, 16)),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
            smoke=True,
        )
    print(f"\n[inv12-b] verbatim refusal: {ei.value}")

    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "refused_falsifier_missing"
    assert man["status"] != "refused_with_D_G"
    assert man["refusal_verbatim"].startswith("[wf2_coupled_run] falsifier prediction set missing")
    assert str(absent) in man["refusal_verbatim"], "absent path named in the refusal"
    assert man["refusal_source"] == "diagnostics.falsifier_set"
    assert "d_g_notice" not in man, "falsifier refusal must not carry D-G's notice"
    assert "cells" not in man and "grid_shape" not in man, "refusal preceded ALL simulation work"


# ---------------------------------------------------------------------------
# Arm (c): CORRUPT sets refuse — driver layer AND diagnostics layer, boundary named
# ---------------------------------------------------------------------------


_DRIVER_CORRUPTIONS = {
    "truncated_json": '{"n_predicted_edges": ',
    "empty_object_wrong_schema": "{}",
    "null_typed_required_keys": json.dumps(
        {"n_predicted_edges": None, "predicted_node_ids": None, "source_gpkg_sha256": None}
    ),
    "non_numeric_count": json.dumps(
        {"n_predicted_edges": "seventy", "predicted_node_ids": [3], "source_gpkg_sha256": "a" * 64}
    ),
}


@pytest.mark.parametrize("variant", sorted(_DRIVER_CORRUPTIONS))
def test_c_driver_layer_corrupt_set_refuses_start(tmp_path, variant) -> None:
    """Driver-layer corruption (present but unreadable / lacking usable counts)
    gets the SAME refuse-to-start treatment as absence: RefuseLoadError, manifest
    ``refused_falsifier_missing``.

    If this were broken I would observe a completed run whose preregistration
    block was built from garbage bytes."""
    blob = _DRIVER_CORRUPTIONS[variant]
    p = tmp_path / f"{variant}.json"
    p.write_text(blob)
    cfg = _coupling_cfg(tmp_path, falsifier_set=p, refuse=True)
    with pytest.raises(RefuseLoadError, match="unreadable/lacking counts"):
        simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
            smoke=True,
        )
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "refused_falsifier_missing"
    assert "unreadable/lacking counts" in man["refusal_verbatim"]


@pytest.mark.parametrize(
    "mutator, keyword",
    [
        (
            lambda d: d.__setitem__(
                "predicted_node_ids", d["predicted_node_ids"][:-1] + [d["predicted_node_ids"][0]]
            ),
            "duplicates",
        ),
        (
            lambda d: d.__setitem__("predicted_node_ids", list(reversed(d["predicted_node_ids"]))),
            "ascending",
        ),
        (lambda d: d.__setitem__("n_predicted_edges", 69), "disagrees"),
        (lambda d: d.__setitem__("source_gpkg_sha256", "nothex"), "source_gpkg_sha256"),
    ],
)
def test_c_diagnostics_layer_deep_schema_corruption_refuses(tmp_path, mutator, keyword) -> None:
    """Deep-schema corruption passes the driver's HEADER-only start gate by
    design (it reads counts only) and is refused by the diagnostics unit's
    ``load_falsifier_set`` before ANY comparison could be scored — the second,
    deeper layer of invariant #12. Boundary recorded: driver = presence +
    readable + typed counts; diagnostics = full schema.

    If this were broken I would observe a mutated set flowing into a comparison
    whose denominators (70/116) were silently wrong."""
    disk = _prereg_from_disk()
    mutator(disk)
    p = tmp_path / "mutated_prediction_set.json"
    p.write_text(json.dumps(disk))
    with pytest.raises(DiagnosticsRefusal, match=keyword) as ei:
        load_falsifier_set(p)
    print(f"\n[inv12-c-diag/{keyword}] verbatim refusal head: {str(ei.value).splitlines()[0]}")


# ---------------------------------------------------------------------------
# Arm (d): completed wet micro-run — preregistration RECORDED, comparison
# MATERIALIZES from the run's own products with all four count fields
# ---------------------------------------------------------------------------


def test_d_completed_run_records_prereg_and_comparison_materializes(
    tmp_path, prereg_disk, gpkg_sha
) -> None:
    """GREEN arm (d): a wet injected-graph micro-run (storm => terminal surcharge)
    completes; its realized manifest on disk records the preregistration (counts
    + sha matching THIS test's fresh disk reads; invariant checker SILENT), and
    running the diagnostics unit's compare_falsifier over the RUN'S OWN
    surcharge_events.csv yields the full §11.2 comparison with all four count
    fields present and arithmetically consistent, sha cross-check ``match``.

    Toy nodes are not Bengaluru predicted ids, so the honest overlap outcome here
    is 0 hits / all events unflagged / all 70 flagged edges un-surcharged —
    recorded, never tuned (diagnostic either way, §11.2).

    V7 scope: <=200 steps x 4096 cells x ~2000 simulated seconds, closed toy
    domain, declared-synthetic graph; scope claimed: preregistration recording +
    comparison materialization semantics. Full-overlap-on-real-surcharge-
    populations stays BLOCKED pending D-G (named in the scope statement)."""
    cfg = _coupling_cfg(tmp_path)  # real set, refuse gate ON (defaults)
    res = _wet_run(cfg)
    assert res.ledger.total_surcharging_steps > 0, "wet control must surcharge for events to exist"
    assert len(res.ledger.events) >= 1

    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "completed"

    # --- recording half: manifest bytes vs THIS test's fresh disk reads -------
    fc = man["falsifier_comparison"]
    assert fc["loaded_at_start"] is True
    assert fc["n_predicted_edges"] == prereg_disk["n_predicted_edges"] == EXPECTED_N_PREDICTED_EDGES
    assert (
        fc["n_predicted_nodes"]
        == len(set(prereg_disk["predicted_node_ids"]))
        == (EXPECTED_N_PREDICTED_NODES)
    )
    assert fc["source_gpkg_sha256"] == gpkg_sha
    assert fc["path"] == str(PREREG_PATH)
    violations = _inv12_completed_violations(man, prereg_disk, gpkg_sha)
    assert violations == [], f"honest run must be violation-free, got: {violations}"

    # --- comparison half: materialized over the run's OWN realized product ----
    csv_on_disk = Path(man["surcharge_events_csv"])
    assert csv_on_disk.exists()
    block = compare_falsifier(csv_on_disk, load_falsifier_set(PREREG_PATH), gpkg_sha)
    for field in (
        "predicted_nodes_surcharged",
        "flagged_edges_whose_downstream_node_surcharged",
        "flagged_edges_not_surcharged",
        "unflagged_surcharging_nodes",
        "source_gpkg_sha256_crosscheck",
    ):
        assert field in block, f"§11.2 comparison field {field!r} missing"
    n_rows = man["surcharge_events_rows"]
    assert block["n_surcharging_events_total"] == n_rows >= 1
    assert block["predicted_nodes_surcharged"] + len(block["unflagged_surcharging_nodes"]) == n_rows
    assert (
        len(block["flagged_edge_ids_whose_downstream_node_surcharged"])
        + len(block["flagged_edges_not_surcharged"])
        == EXPECTED_N_PREDICTED_EDGES
    ), "flagged edges partition into hit/miss with no residue"
    x = block["source_gpkg_sha256_crosscheck"]
    assert x["status"] == "match" and x["loaded_graph_gpkg_sha256"] == gpkg_sha
    assert (
        block["predicted_nodes_surcharged"] == 0
    ), "toy chain nodes are not Bengaluru predicted ids — disjoint is the HONEST outcome"
    assert sorted(block["unflagged_surcharging_nodes"]) == [
        r["node_id"] for r in res.ledger.flush_open_events()
    ] or block["unflagged_surcharging_nodes"] == sorted(
        r["node_id"] for r in json.loads(json.dumps(res.ledger.events))
    ), "every toy event node must appear as unflagged"
    assert "never tuned" in block["note"]
    print(
        f"\n[inv12-d] events={n_rows} predicted_hits="
        f"{block['predicted_nodes_surcharged']} unflagged={block['unflagged_surcharging_nodes']} "
        f"flagged_miss={len(block['flagged_edges_not_surcharged'])}/70 crosscheck={x['status']}"
    )


# ---------------------------------------------------------------------------
# Arm (f): EITHER-FORM §11.2 compliance — inline overlap counts accepted
# ---------------------------------------------------------------------------


def test_f_literal_compliance_inline_counts_accepted_neither_form_still_fires(
    prereg_disk, gpkg_sha
) -> None:
    """GREEN arm (f) (post-fix pin): spec §11.2 compliance on the overlap is EITHER
    the four OVERLAP counts carried INLINE in ``falsifier_comparison`` OR an explicit
    deferral record. A manifest literally complying via inline counts (NO deferral
    tag) must be judged CLEAN by ``_inv12_completed_violations`` — the pre-fix
    hard-coded ``status == 'comparison_deferred_to_diagnostics_unit'`` requirement
    flagged exactly such a manifest VIOLATING (recorded verbatim in the fix round:
    1 violation, 'recording half must declare the overlap deferral explicitly').
    The deferral form's clean verdict is pinned on REALIZED bytes by arm d; this
    arm pins the inline form and guards the branch against over-accepting: a
    manifest with NEITHER form still fires.

    V5 mutation record: the pre-fix checker itself was the mutant — its output on
    this exact dict is quoted in this docstring; no code mutation needed."""
    good = {
        "status": "completed",
        "falsifier_comparison": {
            "loaded_at_start": True,
            "path": str(PREREG_PATH),
            "n_predicted_edges": EXPECTED_N_PREDICTED_EDGES,
            "n_predicted_nodes": EXPECTED_N_PREDICTED_NODES,
            "source_gpkg_sha256": gpkg_sha,
            # The LITERAL §11.2 form: counts inline, NO deferral tag.
            "predicted_nodes_surcharged": 0,
            "flagged_edges_whose_downstream_node_surcharged": 0,
            "flagged_edges_not_surcharged": EXPECTED_N_PREDICTED_EDGES,
            "unflagged_surcharging_nodes": [11, 12],
        },
    }
    violations = _inv12_completed_violations(good, prereg_disk, gpkg_sha)
    assert violations == [], f"literal-compliance manifest must be ACCEPTED, got: {violations}"

    # Guard the branch: strip ALL four inline counts WITHOUT adding a deferral tag
    # => neither form present => MUST fire again.
    neither = {
        "status": "completed",
        "falsifier_comparison": {
            k: v for k, v in good["falsifier_comparison"].items() if k not in _OVERLAP_COUNT_FIELDS
        },
    }
    violations_n = _inv12_completed_violations(neither, prereg_disk, gpkg_sha)
    print(
        f"\n[inv12-f] neither-form manifest violations ({len(violations_n)}):\n  "
        + "\n  ".join(violations_n)
    )
    assert any(
        "NEITHER the four §11.2 overlap counts" in v and "NOR an explicit deferral record" in v
        for v in violations_n
    ), violations_n


# ---------------------------------------------------------------------------
# Arms (e1)/(e2): RED demos — the worlds invariant #12 exists to catch
# ---------------------------------------------------------------------------


def test_red_e1_escape_hatch_flip_completes_without_comparison_invariant_catches(
    tmp_path, prereg_disk, gpkg_sha
) -> None:
    """RED ARM e1 (primary; CONFIG-SURFACE mutation, live TODAY): flip
    ``diagnostics.refuse_start_on_missing_falsifier`` to FALSE with the set
    ABSENT => the run COMPLETES having skipped the preregistration entirely
    (``loaded_at_start: false``, no counts, no sha). The invariant checker MUST
    treat this completed-but-unpreregistered manifest as FAILURE; its output is
    captured verbatim below. This is the honest contract: the escape hatch may
    exist, but a run that used it is flagged by invariant #12, not waved through.

    Mutation record (V5): the config flip itself — a real config surface, not a
    synthetic patch. Pre-fix behaviour vs post-fix: with the gate ON the same
    absent path refuses as ``refused_falsifier_missing`` (arm b); with it OFF the
    hole is live and only this invariant stands between the run and a silent
    skip."""
    absent = tmp_path / "deleted" / "prediction_set.json"  # the deleted/renamed set
    cfg = _coupling_cfg(tmp_path, falsifier_set=absent, refuse=False)  # THE ESCAPE HATCH
    res = _wet_run(cfg, duration_s=600.0, max_steps=100)
    assert res.steps > 0
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "completed", "the escape-hatch world completes today — that IS the hole"
    fc = man["falsifier_comparison"]
    assert fc["loaded_at_start"] is False
    assert "n_predicted_edges" not in fc and "source_gpkg_sha256" not in fc

    violations = _inv12_completed_violations(man, prereg_disk, gpkg_sha)
    joined = "\n  ".join(violations)
    print(f"\n[inv12-red-e1] escape-hatch manifest violations ({len(violations)}):\n  {joined}")
    assert violations, "invariant #12 MUST fail a completed run that skipped its preregistration"
    assert any("NEVER loaded" in v for v in violations), violations
    assert any("n_predicted_edges" in v for v in violations), violations
    assert any("source_gpkg_sha256" in v for v in violations), violations
    # Either-form checker (post-fix): e1 carries NEITHER inline overlap counts NOR a valid
    # deferral record (no status tag at all) — so the new neither-form branch MUST fire too.
    assert any(
        "NEITHER the four §11.2 overlap counts" in v and "NOR an explicit deferral record" in v
        for v in violations
    ), violations


def test_red_e2_forged_header_counts_caught_by_disk_reread(
    tmp_path, prereg_disk, gpkg_sha, monkeypatch
) -> None:
    """RED ARM e2 (CODE-SURFACE mutation): ``solver_hook._load_falsifier_header``
    is monkeypatched to return FORGED counts (999/999, bogus sha) while the
    pristine set sits untouched on disk. The run completes with a lying
    manifest; the invariant checker — which trusts ONLY fresh bytes from disk —
    MUST fire on every forged field. Captured verbatim.

    Mutation record (V5): monkeypatch of the driver's falsifier header loader,
    installed for this test only and removed on teardown (pytest.MonkeyPatch).
    Demonstrates the checker detects code-level regressions in addition to e1's
    config-level hole."""
    import jaladhar.coupling.solver_hook as hook

    def _forged_header(path: Path, refuse_if_missing: bool) -> dict[str, Any]:
        return {
            "loaded_at_start": True,
            "path": str(path),
            "n_predicted_edges": 999,  # FORGED vs the disk truth of 70
            "n_predicted_nodes": 999,  # FORGED vs the disk truth of 116
            "source_gpkg_sha256": "f" * 64,  # FORGED vs the recomputed gpkg sha
            "status": "comparison_deferred_to_diagnostics_unit",
        }

    monkeypatch.setattr(hook, "_load_falsifier_header", _forged_header)
    cfg = _coupling_cfg(tmp_path)  # pristine set on disk, refuse gate ON
    res = _wet_run(cfg, duration_s=600.0, max_steps=100)
    monkeypatch.undo()
    assert res.steps > 0
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "completed"
    assert man["falsifier_comparison"]["n_predicted_edges"] == 999, "forgery reached the manifest"

    violations = _inv12_completed_violations(man, prereg_disk, gpkg_sha)
    joined = "\n  ".join(violations)
    print(f"\n[inv12-red-e2] forged-header manifest violations ({len(violations)}):\n  {joined}")
    assert violations, "invariant #12 MUST catch a manifest lying about the preregistration"
    assert any("999" in v for v in violations), violations
    assert any("source_gpkg_sha256" in v for v in violations), violations
    # Either-form checker (post-fix): the FORGED header carries the deferral tag, so e2 is
    # caught by the forged-field disk cross-checks above — never by the neither-form branch.
    # The checker accepting the deferral form therefore does not weaken this arm.


# ---------------------------------------------------------------------------
# Scope statement (V7) — machine-readable summary printed with -s
# ---------------------------------------------------------------------------


def test_scope_statement() -> None:
    """Prints what this module exercised against the full invariant claim, so
    the shortfall lives in test output rather than only in a status report."""
    exercised = [
        "loader/refusal: pristine set reproduces from disk bytes (70/116/sha; arm a)",
        "refusal: absent path + refuse=true => refused_falsifier_missing, never D-G (arm b)",
        "refusal: truncated/wrong-schema/null/non-numeric => driver-layer refuse (arm c)",
        "refusal: duplicate/unsorted/disagreeing-count/bad-sha => diagnostics-layer "
        "DiagnosticsRefusal (arm c)",
        "recording: completed wet run manifest carries preregistration matching disk; "
        "checker silent (arm d)",
        "comparison: all four §11.2 count fields materialize over the run's own CSV, "
        "arithmetic consistent, sha cross-check match (arm d)",
        "checker either-form: literal-compliance manifest with the four overlap counts "
        "INLINE and no deferral tag ACCEPTED; neither-form still fires (arm f)",
        "red e1: config escape hatch => completed-without-preregistration CAUGHT",
        "red e2: forged header counts CAUGHT by independent disk re-read",
    ]
    partial = [
        "PARTIAL: full-overlap semantics on REAL surcharge populations (predicted_hits>0 "
        "on the Bengaluru graph) BLOCKED pending D-G — the frozen WF-1 reader refuses the "
        "only realized artefact, so end-to-end real-graph coupled runs cannot execute yet "
        "(same blocker as tests/coupling/test_inv07_graph_seam.py); hand-built overlap "
        "semantics are covered in tests/coupling/test_diagnostics.py",
        "PARTIAL: spec §11.2's 'at completion the manifest carries' is realized as the "
        "driver recording the preregistration HEADER at completion with the four overlap "
        "counts computed by the diagnostics unit over the run's products at scoring time "
        "(status: comparison_deferred_to_diagnostics_unit) — both halves pinned here",
        "PARTIAL: deep-schema corruptions pass the driver's header-only start gate BY "
        "DESIGN and are refused at scoring time by load_falsifier_set — the boundary is "
        "asserted, not hidden",
    ]
    print("\n[inv12-scope] exercised:\n  " + "\n  ".join(exercised))
    print("[inv12-scope] " + "\n             ".join(partial))
