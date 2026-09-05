"""WF-2 pre-registered falsifier (owner directive 2026-08-25, R5).

Records, BEFORE any coupled run exists, the set of drain edges that SHOULD
surcharge under the cited design storm: the observed edges whose capacity_basis
JSON carries demand_exceeds_capacity - rational DEMAND exceeding what the
bounded SUPPLY section conveys.

Everything is recounted from gpkg BYTES (V1), then reconciled against the WF-1
manifest counters; any mismatch fails the run loudly instead of writing a
prediction set nobody could trust.

Output: runs/wf2_falsifier_preregistration/surcharge_prediction_set.json
Rule-6 manifest written at run start, updated in place. CPU-only.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import typer

REPO = Path(__file__).resolve().parents[1]
RUN_DIR = REPO / "runs" / "wf2_falsifier_preregistration"
GPKG = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"
WF1_MANIFEST = REPO / "runs" / "drain_graph_build" / "manifest.json"

SYNTHETIC_PREFIX = "synthetic connector"
ZERO_SLOPE_PREFIX = "zero measured slope"
AREA_CAPPED_PREFIX = "contributing area"


class ReconciliationError(RuntimeError):
    """Byte-level recount disagrees with the producer manifest."""


app = typer.Typer()


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    )
    return sha, dirty


def _classify_basis(basis: str) -> str:
    """Partition an edge's capacity_basis text into exactly one class."""
    if basis.startswith(SYNTHETIC_PREFIX):
        return "synthetic"
    if basis.startswith(ZERO_SLOPE_PREFIX):
        return "zero_slope"
    if basis.startswith(AREA_CAPPED_PREFIX):
        return "area_capped"
    return "capacity_bearing"


def _basis_flag(basis: str) -> bool:
    """True iff the capacity_basis JSON carries a truthy demand_exceeds_capacity."""
    try:
        parsed = json.loads(basis)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    flag = parsed.get("demand_exceeds_capacity")
    if isinstance(flag, str):
        return flag.lower().startswith("true")
    return bool(flag)


@app.command()
def main() -> None:
    t0 = time.perf_counter()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    man_path = RUN_DIR / "manifest.json"

    for required in (GPKG, WF1_MANIFEST):
        if not required.exists():
            raise FileNotFoundError(f"required input missing: {required}")

    git_sha, git_dirty = _git()
    manifest: dict = {
        "stage": "wf2_preregister_falsifier",
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "cpu_only": True,
        "inputs": {
            "drain_graph_gpkg": str(GPKG),
            "gpkg_sha256": _sha(GPKG),
            "wf1_manifest": str(WF1_MANIFEST),
            "wf1_manifest_sha256": _sha(WF1_MANIFEST),
            "classification_prefixes": {
                "synthetic": SYNTHETIC_PREFIX,
                "zero_slope": ZERO_SLOPE_PREFIX,
                "area_capped": AREA_CAPPED_PREFIX,
                "else": "capacity_bearing",
            },
        },
        "outputs": {"prediction_set": str(RUN_DIR / "surcharge_prediction_set.json")},
    }
    # Rule 6: written AT RUN START with status running, updated in place below.
    man_path.write_text(json.dumps(manifest, indent=2))

    edges = gpd.read_file(GPKG, layer="drain_edges")
    edges["basis_class"] = edges["capacity_basis"].astype(str).map(_classify_basis)
    counts = edges["basis_class"].value_counts().to_dict()

    # --- reconcile against the producer manifest BEFORE trusting anything ---
    wf1 = json.loads(WF1_MANIFEST.read_text())
    cm = wf1["capacity_metrics"]
    expected = {
        "capacity_bearing": int(cm["n_edges_capacity_written"]),
        "synthetic": len(edges)
        - int(cm["n_edges_capacity_written"])
        - int(cm["n_edges_zero_slope_skipped"])
        - int(cm["n_edges_area_exceeds_validity"]),
        "zero_slope": int(cm["n_edges_zero_slope_skipped"]),
        "area_capped": int(cm["n_edges_area_exceeds_validity"]),
    }
    mismatches = [
        f"{k}: bytes={counts.get(k, 0)} vs manifest={v}"
        for k, v in expected.items()
        if counts.get(k, 0) != v
    ]
    if sum(counts.values()) != len(edges) or mismatches:
        raise ReconciliationError(
            "byte recount does not reconcile with WF-1 manifest counters; "
            f"total_bytes={len(edges)}; mismatches={mismatches}"
        )

    demand_mask = edges["basis_class"].eq("capacity_bearing") & edges["capacity_basis"].map(
        _basis_flag
    )
    predicted = edges.loc[demand_mask].copy()

    n_demand_manifest = int(cm["n_edges_demand_exceeds_capacity"])
    if len(predicted) != n_demand_manifest:
        raise ReconciliationError(
            f"demand_exceeds_capacity recount from bytes ({len(predicted)}) != "
            f"manifest counter ({n_demand_manifest})"
        )

    records = []
    for _, r in predicted.iterrows():
        basis_json = json.loads(str(r["capacity_basis"]))
        records.append(
            {
                "edge_id": int(r["edge_id"]),
                "from_node": int(r["from_node"]),
                "to_node": int(r["to_node"]),
                "order": str(r["order"]),
                "length_m": float(r["length_m"]),
                "q_capacity_nom_m3s": (
                    None if r["q_capacity_nom_m3s"] is None else float(r["q_capacity_nom_m3s"])
                ),
                "demand_m3s": basis_json.get("rational_demand_m3s"),
                "capacity_basis": str(r["capacity_basis"]),
            }
        )

    node_ids = sorted({rec["to_node"] for rec in records} | {rec["from_node"] for rec in records})
    prediction_set = {
        "stage": "wf2_surcharge_prediction_set",
        "created_utc": datetime.now(UTC).isoformat(),
        "source_gpkg": str(GPKG),
        "source_gpkg_sha256": _sha(GPKG),
        "definition": (
            "observed edges whose capacity_basis JSON carries demand_exceeds_capacity: "
            "rational demand under the cited design storm exceeds conveyance of the "
            "bounded section; these are the drains that SHOULD surcharge. Recorded "
            "before any coupled run exists (owner directive, R5). Comparison against "
            "actual surcharge is recorded into coupled-run manifests and is never tuned."
        ),
        "partition_from_bytes": {k: int(counts.get(k, 0)) for k in sorted(counts)},
        "partition_total": int(len(edges)),
        "n_predicted_edges": len(records),
        "predicted_node_ids": node_ids,
        "edges": records,
    }
    out_path = RUN_DIR / "surcharge_prediction_set.json"
    out_path.write_text(json.dumps(prediction_set, indent=2))

    manifest["finished_utc"] = datetime.now(UTC).isoformat()
    manifest["wall_seconds"] = round(time.perf_counter() - t0, 3)
    manifest["status"] = "completed"
    manifest["reconciliation"] = {
        "bytes_vs_manifest": "ok",
        "expected": expected,
        "observed": {k: int(counts.get(k, 0)) for k in sorted(counts)},
        "n_predicted_edges": len(records),
        "manifest_n_edges_demand_exceeds_capacity": n_demand_manifest,
        "n_predicted_nodes_distinct": len(node_ids),
    }
    man_path.write_text(json.dumps(manifest, indent=2))

    typer.echo(
        f"preregistered {len(records)} should-surcharging edges over "
        f"{len(node_ids)} distinct nodes -> {out_path}"
    )
    typer.echo(f"partition from bytes: {counts} total={len(edges)}")
    typer.echo(f"reconciled against WF-1 manifest counters: OK")


if __name__ == "__main__":
    app()
