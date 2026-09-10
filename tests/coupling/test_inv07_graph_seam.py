"""Invariant #7 ``graph-seam`` (spec §12 row #7) — V8 at the realized WF-1/WF-2 seam.

V2: "if this were broken I would observe ___" — a mutated drain-graph manifest
or graph copy would LOAD SILENTLY and every downstream number (counts, capacity
partition, routing plan, falsifier overlap) would shift with no error anywhere.

CLAIM under test: "The coupling asserts the drain-graph manifest guarantees AT
LOAD and REJECTS a mutated manifest: DAG flag, node/edge counts, edge partition
{capacity_bearing:1208, zero_slope:61, area_capped:32, synthetic:286},
falsifier-set sha cross-check; mutations are refused loudly, never silently
accepted."

D-G CLOSED (contract v1.2.0, owner amendment 2026-08-26 — see
configs/contracts/drain_graph.json amendment_history): the FROZEN WF-1 reader
(``jaladhar.drainage.graph_io.read_artefact``) now ACCEPTS the ONLY realized
artefact — loads clean at (1721 nodes, 1587 edges) returning the amendment's
RECORDED properties beside the frames: dropped_zero_length_count=42,
dropped_self_loop_count=11 (M5b-sanctioned), null_capacity_edge_count=93.
The production path runs THROUGH ``read_artefact`` again
(``router.load_drain_graph`` no longer needs the byte-assembly bypass), so this
module exercises BOTH tiers: the reader tier LIVE against the realized
artefact paths (section below), and the consumer-tier assertions via the /tmp
copy harness retained for the red demos — the bypass is still the only way to
feed MUTATED bytes through ``load_drain_graph`` without ever touching runs/.

RED DEMO (V5): mutations are applied to /tmp/opencode/redtest_seam/ COPIES of
DATA only — never to live data/ or runs/. Per variant the exact refusal is
captured verbatim (printed for evidence) and asserted to name its specific
gate:

  (a) manifest ``edge_count`` off-by-one (1587 -> 1588)
      -> "[consumer] manifest node_count/edge_count disagree with gpkg"
  (b) manifest ``graph_is_dag`` flipped to False
      -> "[consumer] manifest.graph_is_dag=False"
  (c) one capacity-bearing edge's ``q_capacity_nom_m3s`` set NULL in the gpkg
      -> "[consumer] edge partition mismatch: ... capacity_bearing 1207 != 1208"
  (d) NaN sentinel coerced to 0.0 on a NON-capacity (synthetic) edge
      -> "[assemble] non-capacity-bearing edges must carry NaN sentinels ..."
  (e) falsifier-set sha cross-check: any gpkg-mutating variant changes the
      file bytes, so ``compare_falsifier``'s ``source_gpkg_sha256_crosscheck``
      flips match -> MISMATCH while pristine bytes reproduce "match".
  (f) reader tier, contract v1.2.0 guard: a DECLARED-null-capacity edge given
      q_capacity_nom_m3s=0.0 in a /tmp gpkg copy -> "[null_capacity_class] ...
      NOT loadable as zero" (the null class is routing conduit; the new guard
      is demonstrated red-capable, mutation recorded beside its test).

Pristine copies must LOAD CLEAN through the same path — a seam that refuses
everything proves nothing.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import torch

from jaladhar.coupling.config import CouplingConfig, resolve_config
from jaladhar.coupling.diagnostics import (
    compare_falsifier,
    load_falsifier_set,
    sha256_file,
)
from jaladhar.coupling.router import OWNER_ADJUDICATION_REF_M5B, load_drain_graph
from jaladhar.drainage.graph_io import RefuseLoadError, read_artefact

REPO = Path(__file__).resolve().parents[2]
SRC_DIR = REPO / "runs" / "drain_graph_build"
TMP_ROOT = Path("/tmp/opencode/redtest_seam")
ARTEFACT_FILES = ("drain_graph.gpkg", "drain_graph_adjacency.json", "manifest.json")

EXPECTED_NODES = 1721
EXPECTED_EDGES = 1587
EXPECTED_PARTITION = {
    "capacity_bearing": 1208,
    "zero_slope": 61,
    "area_capped": 32,
    "synthetic": 286,
}

_ZERO_PREFIX = "zero measured slope"
_AREA_PREFIX = "contributing area"


def _variant_dir(seam_root: Path, name: str) -> Path:
    dst = seam_root / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(seam_root / "pristine", dst)
    return dst


def _mutate_manifest(variant: Path, **updates: Any) -> None:
    mpath = variant / "manifest.json"
    man = json.loads(mpath.read_text())
    man.update(updates)
    mpath.write_text(json.dumps(man))


def _mutate_edge_capacity(variant: Path, predicate, value: float | None) -> int:
    """Rewrite the COPY's drain_edges layer, setting ``q_capacity_nom_m3s``
    where ``predicate(edges_frame)`` selects; returns the mutated edge_id.

    The copy's gpkg is DELETED first, then both layers rewritten (nodes create,
    edges append): appending onto the pre-existing file would duplicate the
    edges layer (caught live: 3174 rows -> "[consumer] realized graph size
    ... edges=3174 != expected (1721, 1587)" — the counts gate refusing the
    botched rewrite, not the mutation under test)."""
    gpkg = variant / "drain_graph.gpkg"
    nodes = gpd.read_file(gpkg, layer="drain_nodes")
    edges = gpd.read_file(gpkg, layer="drain_edges")
    gpkg.unlink()
    sel = predicate(edges)
    assert int(sel.sum()) >= 1, "mutation predicate selected nothing — test bug"
    idx = int(np.nonzero(sel.to_numpy())[0][0])
    edges.loc[idx, "q_capacity_nom_m3s"] = value
    nodes.to_file(gpkg, layer="drain_nodes", driver="GPKG")
    edges.to_file(gpkg, layer="drain_edges", driver="GPKG", mode="a")
    return int(edges.loc[idx, "edge_id"])


def _assemble_from(art_dir: Path) -> dict[str, Any]:
    return {
        "nodes_gdf": gpd.read_file(art_dir / "drain_graph.gpkg", layer="drain_nodes"),
        "edges_gdf": gpd.read_file(art_dir / "drain_graph.gpkg", layer="drain_edges"),
        "adjacency": json.loads((art_dir / "drain_graph_adjacency.json").read_text()),
        "manifest": json.loads((art_dir / "manifest.json").read_text()),
    }


def _load_variant(monkeypatch: pytest.MonkeyPatch, cfg: CouplingConfig, art_dir: Path):
    from jaladhar.coupling import router as rmod

    monkeypatch.setattr(rmod, "read_artefact", lambda *a, **k: _assemble_from(art_dir))
    return load_drain_graph(cfg, REPO)


# shares no code with load_drain_graph's internal recount (V2 non-mirror).


def _partition_from_bytes(edges: gpd.GeoDataFrame) -> dict[str, int]:
    src = edges["edge_source"].astype(str)
    basis = edges["capacity_basis"].astype(str)
    q_notna = edges["q_capacity_nom_m3s"].notna().to_numpy()
    is_syn = (src == "synthesised").to_numpy()
    obs_null = (src == "observed").to_numpy() & ~q_notna
    return {
        "capacity_bearing": int(((src == "observed").to_numpy() & q_notna).sum()),
        "zero_slope": int((obs_null & basis.str.startswith(_ZERO_PREFIX).to_numpy()).sum()),
        "area_capped": int((obs_null & basis.str.startswith(_AREA_PREFIX).to_numpy()).sum()),
        "synthetic": int(is_syn.sum()),
    }


@pytest.fixture(scope="session")
def seam_root() -> Path:
    if TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT)
    TMP_ROOT.mkdir(parents=True)
    pristine = TMP_ROOT / "pristine"
    pristine.mkdir()
    for name in ARTEFACT_FILES:
        shutil.copyfile(SRC_DIR / name, pristine / name)
    return TMP_ROOT


@pytest.fixture(scope="session")
def cfg() -> CouplingConfig:
    return resolve_config(REPO / "configs" / "coupling.yaml", REPO)


@pytest.fixture(scope="session")
def fs(cfg: CouplingConfig):
    return load_falsifier_set(cfg.diagnostics.falsifier_set)


def test_pristine_copies_load_clean_with_realized_guarantees(
    seam_root: Path, cfg: CouplingConfig, monkeypatch: pytest.MonkeyPatch, fs
) -> None:
    """The unmutated /tmp copies pass EVERY gate on the load path — proving the
    red refusals below fire on the mutation, not on the harness.

    V2 observables are all REALIZED state: tensor sizes, an independent
    partition recount from gpkg bytes, DAG flag read from the copied manifest
    TEXT, topo_sha256 recomputed from the copied adjacency FILE bytes, NaN
    sentinels inspected on the loaded tensor, and the falsifier-set sha
    reproducing from the copied gpkg BYTES."""
    g = _load_variant(monkeypatch, cfg, seam_root / "pristine")

    assert g.num_nodes == EXPECTED_NODES and g.num_edges == EXPECTED_EDGES
    man_text = json.loads((seam_root / "pristine" / "manifest.json").read_text())
    assert man_text["graph_is_dag"] is True and man_text["edge_count"] == EXPECTED_EDGES

    counted = _partition_from_bytes(
        gpd.read_file(seam_root / "pristine" / "drain_graph.gpkg", layer="drain_edges")
    )
    assert counted == EXPECTED_PARTITION
    assert counted["capacity_bearing"] == int(g.capacity_bearing.sum().item())
    assert int((~g.capacity_bearing).sum().item()) == (
        EXPECTED_PARTITION["zero_slope"]
        + EXPECTED_PARTITION["area_capped"]
        + EXPECTED_PARTITION["synthetic"]
    )

    assert bool(torch.isnan(g.q_cap_nom_m3s[~g.capacity_bearing]).all())
    cb_q = g.q_cap_nom_m3s[g.capacity_bearing]
    assert bool(torch.isfinite(cb_q).all() and (cb_q > 0).all())

    adj = json.loads((seam_root / "pristine" / "drain_graph_adjacency.json").read_text())
    adj_order = [int(i) for i in adj["topo_order"]]
    expect_sha = hashlib.sha256(
        json.dumps(adj_order, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert g.topo_sha256 == expect_sha and g.topo_order == adj_order

    assert g.n_active_predicted_targets == len(fs.predicted_node_ids)
    assert g.n_inactive_predicted_targets == 0

    pristine_sha = sha256_file(seam_root / "pristine" / "drain_graph.gpkg")
    assert pristine_sha == fs.source_gpkg_sha256
    block = compare_falsifier([], fs, pristine_sha)
    assert block["source_gpkg_sha256_crosscheck"]["status"] == "match"


# RED DEMOS (V5) — each mutation refused LOUDLY by its specific gate


def test_red_a_manifest_edge_count_off_by_one_refused(
    seam_root: Path, cfg: CouplingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = _variant_dir(seam_root, "mut_a_edge_count")
    _mutate_manifest(variant, edge_count=EXPECTED_EDGES + 1)
    with pytest.raises(
        RefuseLoadError, match=r"\[consumer\] manifest node_count/edge_count disagree with gpkg"
    ) as ei:
        _load_variant(monkeypatch, cfg, variant)
    print(f"\n[inv07-red-a] verbatim refusal: {ei.value}")


def test_red_b_manifest_dag_flag_false_refused(
    seam_root: Path, cfg: CouplingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) graph_is_dag flipped to False: spec §12 row #7's own named mutation.
    A DAG flag is a producer DECLARATION (V1); the consumer refuses unless it
    reads True — a cyclic graph must never enter level-synchronous routing."""
    variant = _variant_dir(seam_root, "mut_b_not_dag")
    _mutate_manifest(variant, graph_is_dag=False)
    with pytest.raises(RefuseLoadError, match=r"\[consumer\] manifest\.graph_is_dag=False") as ei:
        _load_variant(monkeypatch, cfg, variant)
    print(f"\n[inv07-red-b] verbatim refusal: {ei.value}")


def test_red_c_capacity_edge_nulled_partition_mismatch_refused(
    seam_root: Path, cfg: CouplingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = _variant_dir(seam_root, "mut_c_qcap_null")
    eid = _mutate_edge_capacity(variant, lambda e: e["q_capacity_nom_m3s"].notna(), None)
    print(f"\n[inv07-red-c] mutated edge_id={eid} q_capacity_nom_m3s -> NULL in {variant}")
    with pytest.raises(RefuseLoadError, match=r"\[consumer\] edge partition mismatch") as ei:
        _load_variant(monkeypatch, cfg, variant)
    msg = str(ei.value)
    assert "capacity_bearing 1207 != 1208" in msg, msg
    assert "edges outside all four partition classes" in msg, msg
    print(f"[inv07-red-c] verbatim refusal: {ei.value}")


def test_red_d_nan_sentinel_coerced_to_zero_refused(
    seam_root: Path, cfg: CouplingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = _variant_dir(seam_root, "mut_d_nan_to_zero")
    eid = _mutate_edge_capacity(
        variant, lambda e: e["edge_source"].astype(str) == "synthesised", 0.0
    )
    print(f"\n[inv07-red-d] mutated edge_id={eid} (synthesised) q NaN -> 0.0 in {variant}")
    with pytest.raises(
        RefuseLoadError, match=r"non-capacity-bearing edges must carry NaN sentinels"
    ) as ei:
        _load_variant(monkeypatch, cfg, variant)
    print(f"[inv07-red-d] verbatim refusal: {ei.value}")


def test_red_e_falsifier_sha_crosscheck_moves_with_graph_bytes(
    seam_root: Path, cfg: CouplingConfig, fs
) -> None:
    """(e) The falsifier-set ``source_gpkg_sha256`` cross-check must MOVE when
    the loaded graph's bytes change (V5 observable): MISMATCH on the mutated
    copy, never a silent comparison against a different graph than the one the
    predictions were preregistered from. ``None`` stays explicitly unchecked,
    not silently skipped.

    ORDER-INDEPENDENT (adjudicated): this test builds its OWN gpkg-mutated
    variant locally instead of depending on test_red_c's ``mut_c_qcap_null``
    directory, so it passes under any subset/ordering of this module."""
    pristine_sha = sha256_file(seam_root / "pristine" / "drain_graph.gpkg")
    mut_dir = _variant_dir(seam_root, "mut_e_qcap_null")
    _mutate_edge_capacity(mut_dir, lambda e: e["q_capacity_nom_m3s"].notna(), None)
    mutated_sha = sha256_file(mut_dir / "drain_graph.gpkg")
    assert pristine_sha != mutated_sha, "mutation did not change file bytes — demo void"

    matched = compare_falsifier([], fs, pristine_sha)
    mismatched = compare_falsifier([], fs, mutated_sha)
    unchecked = compare_falsifier([], fs, None)
    assert matched["source_gpkg_sha256_crosscheck"]["status"] == "match"
    assert mismatched["source_gpkg_sha256_crosscheck"]["status"] == "MISMATCH"
    assert (
        unchecked["source_gpkg_sha256_crosscheck"]["status"]
        == "not_checked_no_loaded_graph_sha_provided"
    )
    print(
        f"\n[inv07-red-e] pristine={matched['source_gpkg_sha256_crosscheck']['status']} "
        f"mutated={mismatched['source_gpkg_sha256_crosscheck']['status']}"
    )


def _null_capacity_recount(edges: gpd.GeoDataFrame) -> int:
    """Independent recount of the v1.2.0 null-capacity class straight from edge
    frames — shares no code with read_artefact's per-edge loop (V2 non-mirror):
    observed + declared 'no capacity claim' basis under the two realized
    prefixes + EVERY numeric capacity field NULL."""
    numeric_cols = [
        "width_m",
        "width_low_m",
        "width_high_m",
        "n_manning",
        "depth_m_solved",
        "q_capacity_nom_m3s",
        "q_capacity_low_m3s",
        "q_capacity_high_m3s",
    ]
    src = edges["edge_source"].astype(str)
    basis = edges["capacity_basis"].astype(str)
    declared = (
        basis.str.contains("no capacity claim", case=False, regex=False)
        & (basis.str.startswith(_ZERO_PREFIX) | basis.str.startswith(_AREA_PREFIX))
    ).to_numpy()
    all_null = edges[numeric_cols].isna().all(axis=1).to_numpy()
    return int(((src == "observed").to_numpy() & declared & all_null).sum())


def test_reader_tier_realized_artefact_loads_clean_with_recorded_d_g_properties() -> None:
    """(a)+(b) D-G CLOSED — the frozen reader tier is LIVE coverage now, not a
    skip. ``read_artefact`` on the REALIZED artefact paths (runs/
    drain_graph_build, read-only) must LOAD CLEAN and return the contract
    v1.2.0 recorded properties, asserted against DISK-DERIVED expectations:
    the manifest counts block and an independent gpkg recount (V1: realized
    bytes; V3: no baseline produced by the code under test).

    V2 observable: if the amendment's recorded-property plumbing broke (fields
    dropped, counts mis-echoed), the returned dict would disagree with the
    manifest text / gpkg recount pinned here.

    V7 scope: one full reader pass over 1721 nodes x 1587 edges incl. the
    elevation-sha binding rehash; scope claimed: reader-tier acceptance +
    recorded-property fidelity at the seam, full WF-1 build behaviour remains
    tests/drainage's."""
    art = read_artefact(
        str(SRC_DIR / "drain_graph.gpkg"),
        str(SRC_DIR / "drain_graph_adjacency.json"),
        str(SRC_DIR / "manifest.json"),
        owner_adjudication_ref=OWNER_ADJUDICATION_REF_M5B,
    )
    man_text = json.loads((SRC_DIR / "manifest.json").read_text())

    assert len(art["nodes_gdf"]) == EXPECTED_NODES
    assert len(art["edges_gdf"]) == EXPECTED_EDGES

    assert man_text["counts"]["zero_length_dropped_count"] == 42
    assert art["dropped_zero_length_count"] == man_text["counts"]["zero_length_dropped_count"]
    assert man_text["counts"]["self_loop_dropped_count"] == 11
    assert art["dropped_self_loop_count"] == man_text["counts"]["self_loop_dropped_count"]
    assert art["null_capacity_edge_count"] == 93

    assert _null_capacity_recount(art["edges_gdf"]) == art["null_capacity_edge_count"]
    adj = art["adjacency"]
    assert adj["dropped"]["zero_length"] == ["count=42"]
    assert len(adj["dropped"]["self_loops"]) == 11


def test_red_f_declared_null_edge_given_numeric_capacity_refused_at_reader_tier(
    seam_root: Path,
) -> None:
    """(f) V5 RED DEMO at the reader tier for the NEW v1.2.0 guard (mutation
    recorded here): one DECLARED-null-capacity observed edge given
    q_capacity_nom_m3s=0.0 in a /tmp gpkg copy. The null class is routing
    conduit, NOT loadable as zero — read_artefact itself must refuse naming
    [null_capacity_class] and the smuggled field, else the amendment would have
    opened a silent zero-capacity hole. Pristine bytes load clean (test above),
    so this refusal fires on the mutation, not the harness."""

    def _declared_null(e: gpd.GeoDataFrame):
        b = e["capacity_basis"].astype(str)
        return (
            (e["edge_source"].astype(str) == "observed")
            & e["q_capacity_nom_m3s"].isna()
            & (b.str.startswith(_ZERO_PREFIX) | b.str.startswith(_AREA_PREFIX))
        )

    variant = _variant_dir(seam_root, "mut_f_declared_null_given_qnom")
    eid = _mutate_edge_capacity(variant, _declared_null, 0.0)
    print(f"\n[inv07-red-f] mutated edge_id={eid} (declared-null class) q -> 0.0 in {variant}")
    with pytest.raises(RefuseLoadError, match=r"\[null_capacity_class\]") as ei:
        read_artefact(
            str(variant / "drain_graph.gpkg"),
            str(variant / "drain_graph_adjacency.json"),
            str(variant / "manifest.json"),
            owner_adjudication_ref=OWNER_ADJUDICATION_REF_M5B,
        )
    msg = str(ei.value)
    assert "q_capacity_nom_m3s" in msg, msg
    print(f"[inv07-red-f] verbatim refusal: {ei.value}")


# Scope statement (V7) — machine-readable summary printed with -s


def test_scope_statement(fs) -> None:
    exercised = [
        "reader: read_artefact LOADS the realized artefact end-to-end, recorded D-G "
        "properties 42/11/93 vs manifest counts + independent gpkg recount (D-G closed)",
        "reader: [null_capacity_class] guard red-capable — declared-null edge given a "
        "numeric capacity refused (red f)",
        "consumer: manifest.graph_is_dag is True (red b)",
        "consumer: expected_counts + manifest node_count/edge_count vs gpkg (red a)",
        "consumer: edge partition 1208/61/32/286 + zero remainder (red c; green recount)",
        "assemble: CB finite>0 AND non-CB NaN sentinels intact (red d; green)",
        "loader: full-domain falsifier activity gate, all targets active (green)",
        "diagnostics: falsifier-set source_gpkg_sha256 crosscheck match/MISMATCH (red e)",
        "topo_order sha256 recomputed from adjacency bytes (green)",
    ]
    partial = [
        "PARTIAL: consumer-tier reds run through the /tmp byte-assembly bypass "
        "(mutated bytes must never be written into runs/); the production path now "
        "runs THROUGH read_artefact, exercised live above; solver-side window gates "
        "and falsifier-gate statuses live in test_solver_hook.py; falsifier set "
        f"n_nodes={fs.n_predicted_nodes}"
    ]
    print("\n[inv07-scope] exercised:\n  " + "\n  ".join(exercised))
    print("[inv07-scope] " + "\n             ".join(partial))
