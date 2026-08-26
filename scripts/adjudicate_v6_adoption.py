"""Owner-adjudication records for the WF-6 storage-water exclusion chain.

Two additive, idempotent-refusing subcommands:

* ``adopt`` -- records the owner's adoption decision as ONE new top-level key
  ``owner_adjudication`` in ``runs/wf3_replay2_uncoupled_baseline_v6/manifest.json``
  together with the pre-adjudication SHA-256 of that file.  Nothing else in the
  manifest changes: the three measured blocks (``variant_summaries``,
  ``residual_anomaly``, ``storage_water_exclusion``) must serialize identically
  before and after, byte-for-byte at the canonical-JSON level, or the script
  refuses.
* ``annotate-gate`` -- after the gates_v7 rescore completes, adds ONE additive
  top-level key ``open_anomaly_carryover`` to its manifest carrying the
  unexplained 61-segment residual forward.  Every other top-level key must
  serialize identically pre/post.
* ``annotate-series-gate`` -- binds the frame-series manifest to the realized
  EVENT-MAXIMUM G1 report by SHA-256: ONE additive top-level key
  ``event_maximum_gate_reference``.  Owner directive 2026-08-26: G1 asks
  "did the model flood this location during the event" -- an event-level
  question -- so the frame series references the event-maximum score instead
  of being rescoring per frame (per-frame rescoring is prohibited).  The
  report must be byte-bound to the given event product or the command refuses.

All three commands refuse on a second run rather than overwrite: an
adjudication record that can be silently rewritten is not a record.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import typer

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from jaladhar.provenance import write_json_atomic  # noqa: E402

DEFAULT_V6_MANIFEST = REPO / "runs/wf3_replay2_uncoupled_baseline_v6/manifest.json"
DEFAULT_V7_MANIFEST = REPO / "runs/wf3_replay2_gates_v7_excluded/manifest.json"
DEFAULT_FRAMES_MANIFEST = REPO / "runs/wf3_replay2_uncoupled_baseline_frames_v2/manifest.json"
DEFAULT_SERIES_GATE_REPORT = REPO / "runs/wf3_replay2_gates_v7_excluded_r2/g1_score.json"
DEFAULT_EVENT_PRODUCT = REPO / "runs/wf3_replay2_uncoupled_baseline_v6/products/segment_status.csv"

RATIONALE_VERBATIM = (
    "excluded cells are INITIAL-CONDITION water, put there by the storage_at_spill IC to "
    "represent antecedent lake levels. That is not street flooding and must never have been "
    "reported as such."
)
OPEN_ANOMALY_POLICY = "never explained away or excluded without measured cause"
EXPECTED_RESIDUAL_N_GT300 = 61
EXPECTED_RESIDUAL_RANGE_CM = [301, 369]
EVENT_GATE_SCOPE = "event_maximum"
EVENT_GATE_ADJUDICATION = (
    "owner directive 2026-08-26 — G1 is an event-level question; the frame series "
    "references the event-maximum score; per-frame rescoring prohibited"
)

app = typer.Typer(add_completion=False)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2)


def _load_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.exists():
        raise typer.BadParameter(f"manifest is absent: {path}")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"manifest is not a JSON object: {path}")
    return raw, payload


def _sha256_path(path: Path) -> str:
    """SHA-256 of a file's CURRENT bytes; refuses absent inputs by name."""
    if not path.is_file():
        raise typer.BadParameter(f"file is absent: {path}")
    return _sha256_bytes(path.read_bytes())


def _repo_relative(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(REPO.resolve()):
        return resolved.relative_to(REPO.resolve()).as_posix()
    return str(resolved)


def _assert_residual_anchor(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    """Bind the OPEN ANOMALY carry to v6's realized residual bytes, fail closed."""
    residual = payload.get("residual_anomaly")
    if not isinstance(residual, dict):
        typer.echo(f"v6 manifest carries no residual_anomaly block: {path}", err=True)
        raise typer.Exit(code=1)
    n_gt300 = int(residual["n_flooded_gt300cm_post_exclusion"])
    max_cm = int(residual["max_band_high_cm_post_exclusion"])
    if n_gt300 != EXPECTED_RESIDUAL_N_GT300:
        typer.echo(
            f"residual anomaly does not match the adjudicated anchor: n_gt300={n_gt300} != "
            f"{EXPECTED_RESIDUAL_N_GT300}; refusing to record a decision against different bytes",
            err=True,
        )
        raise typer.Exit(code=1)
    return {
        "status": "OPEN",
        "carried_from": "residual_anomaly",
        "n_segments_gt300cm": n_gt300,
        "range_cm": [EXPECTED_RESIDUAL_RANGE_CM[0], max_cm],
        "policy": OPEN_ANOMALY_POLICY,
    }


@app.command()
def adopt(
    manifest_path: Path = typer.Option(DEFAULT_V6_MANIFEST, "--manifest-path"),
) -> None:
    """Record the owner's ADOPT decision additively in the v6 product manifest."""
    preimage, payload = _load_manifest(manifest_path)
    preimage_sha = _sha256_bytes(preimage)
    if "owner_adjudication" in payload:
        typer.echo(
            f"refusing second adjudication: owner_adjudication already present in {manifest_path} "
            "(idempotent-refuse; the recorded decision is never rewritten)",
            err=True,
        )
        raise typer.Exit(code=1)
    if payload.get("status") != "completed":
        typer.echo(
            f"refusing: product manifest status is {payload.get('status')!r}, not completed",
            err=True,
        )
        raise typer.Exit(code=1)

    guarded_blocks = {
        key: _canonical(payload[key])
        for key in ("variant_summaries", "residual_anomaly", "storage_water_exclusion")
        if key in payload
    }
    missing = [
        key
        for key in ("variant_summaries", "residual_anomaly", "storage_water_exclusion")
        if key not in payload
    ]
    if missing:
        typer.echo(f"refusing: v6 manifest lacks measured block(s): {missing}", err=True)
        raise typer.Exit(code=1)

    open_anomaly = _assert_residual_anchor(payload, manifest_path)
    payload["owner_adjudication"] = {
        "decision": "adopt",
        "date": "2026-08-26",
        "rationale": RATIONALE_VERBATIM,
        "sequence": ["adopt", "frames_v2", "rescore_g1"],
        "retirement": {
            "subject": "runs/wf3_replay2_gates_v6",
            "label_directive": (
                "the recorded G1 result -- 182/399 hits (hit_rate 0.4561) against a null mean "
                "lift of 1.41 -- is retired-not-deleted, and every future citation carries the "
                "label 'scored on the pre-exclusion product'"
            ),
            "superseded_by": "runs/wf3_replay2_gates_v7_excluded",
        },
        "reporting_rule_both_conditions": (
            "every G1 verdict reports BOTH signed conditions under unchanged thresholds: "
            "hit_rate against >=0.60 AND null lift against >=3.0; neither condition may be "
            "dropped from a report or summary"
        ),
        "no_tune_rule": (
            "thresholds stay frozen as signed 2026-08-24 and are never tuned toward the 60% "
            "anchor or toward any realized score"
        ),
        "open_anomaly": open_anomaly,
        "pre_adjudication_manifest_sha256": preimage_sha,
    }
    write_json_atomic(manifest_path, payload)

    # Realized-state verification: re-read from disk, guarded blocks unchanged.
    reread_raw, reread = _load_manifest(manifest_path)
    for key, before in guarded_blocks.items():
        if _canonical(reread[key]) != before:
            typer.echo(
                f"post-write check failed: {key} serialized differently after the additive "
                "write; refusing to leave the mutation in place",
                err=True,
            )
            raise typer.Exit(code=1)
    if reread["owner_adjudication"]["pre_adjudication_manifest_sha256"] != preimage_sha:
        typer.echo("post-write check failed: pre-adjudication sha mismatch", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"decision={reread['owner_adjudication']['decision']}")
    typer.echo(f"pre_adjudication_sha256={preimage_sha}")
    typer.echo(f"post_adjudication_sha256={_sha256_bytes(reread_raw)}")
    typer.echo("guarded_blocks=identical")


@app.command()
def annotate_gate(
    gate_manifest: Path = typer.Option(DEFAULT_V7_MANIFEST, "--gate-manifest"),
    product_manifest: Path = typer.Option(DEFAULT_V6_MANIFEST, "--product-manifest"),
) -> None:
    """Carry the OPEN ANOMALY additively into a completed gates run manifest."""
    _preimage, product_payload = _load_manifest(product_manifest)
    open_anomaly = _assert_residual_anchor(product_payload, product_manifest)
    open_anomaly["carried_from"] = {
        "path": (
            product_manifest.resolve().relative_to(REPO.resolve()).as_posix()
            if product_manifest.resolve().is_relative_to(REPO.resolve())
            else str(product_manifest.resolve())
        ),
        "field": "residual_anomaly",
    }

    preimage, payload = _load_manifest(gate_manifest)
    preimage_sha = _sha256_bytes(preimage)
    if "open_anomaly_carryover" in payload:
        typer.echo(
            f"refusing second annotation: open_anomaly_carryover already present in "
            f"{gate_manifest} (idempotent-refuse)",
            err=True,
        )
        raise typer.Exit(code=1)
    if payload.get("status") != "completed":
        typer.echo(
            f"refusing: gates manifest status is {payload.get('status')!r}, not completed",
            err=True,
        )
        raise typer.Exit(code=1)

    guarded = {key: _canonical(value) for key, value in payload.items()}
    payload["open_anomaly_carryover"] = {**open_anomaly, "annotated_date": "2026-08-26"}
    write_json_atomic(gate_manifest, payload)

    _reread_raw, reread = _load_manifest(gate_manifest)
    for key, before in guarded.items():
        if key not in reread or _canonical(reread[key]) != before:
            typer.echo(
                f"post-write check failed: key {key!r} changed beyond the additive annotation",
                err=True,
            )
            raise typer.Exit(code=1)
    typer.echo(f"open_anomaly_carryover.status={reread['open_anomaly_carryover']['status']}")
    typer.echo(f"pre_annotation_sha256={preimage_sha}")
    typer.echo(f"post_annotation_sha256={_sha256_bytes(_reread_raw)}")
    typer.echo("all_other_keys=identical")


@app.command()
def annotate_series_gate(
    series_manifest: Path = typer.Option(DEFAULT_FRAMES_MANIFEST, "--series-manifest"),
    gate_report: Path = typer.Option(DEFAULT_SERIES_GATE_REPORT, "--gate-report"),
    event_product: Path = typer.Option(DEFAULT_EVENT_PRODUCT, "--event-product"),
) -> None:
    """Bind a frame-series manifest to the realized EVENT-MAXIMUM G1 report.

    Writes ONE additive top-level key ``event_maximum_gate_reference`` carrying
    both files' paths and their CURRENT SHA-256 digests plus the owner
    directive.  Refuses unless the gate report is byte-bound to the given
    event product (``input_sha256.depth_product`` must equal the product's
    realized hash) -- recording an unbound reference would institutionalize a
    provenance lie.  Every pre-existing key must serialize identically after
    the write; a second run is refused outright.
    """
    _preimage, payload = _load_manifest(series_manifest)
    if "event_maximum_gate_reference" in payload:
        typer.echo(
            f"refusing second annotation: event_maximum_gate_reference already present in "
            f"{series_manifest} (idempotent-refuse; the recorded reference is never rewritten)",
            err=True,
        )
        raise typer.Exit(code=1)
    if payload.get("status") != "completed":
        typer.echo(
            f"refusing: series manifest status is {payload.get('status')!r}, not completed",
            err=True,
        )
        raise typer.Exit(code=1)

    gate_sha = _sha256_path(gate_report)
    product_sha = _sha256_path(event_product)
    report = json.loads(gate_report.read_text(encoding="utf-8"))
    bound = (
        report.get("input_sha256", {}).get("depth_product") if isinstance(report, dict) else None
    )
    if bound != product_sha:
        typer.echo(
            "refusing: gate report is not bound to the event product bytes "
            f"(report input_sha256.depth_product={bound!r} vs realized "
            f"{product_sha}); refusing to record an unbound reference",
            err=True,
        )
        raise typer.Exit(code=1)

    guarded = {key: _canonical(value) for key, value in payload.items()}
    payload["event_maximum_gate_reference"] = {
        "gate_report_path": _repo_relative(gate_report),
        "gate_report_sha256": gate_sha,
        "event_product_path": _repo_relative(event_product),
        "event_product_sha256": product_sha,
        "scope": EVENT_GATE_SCOPE,
        "adjudication": EVENT_GATE_ADJUDICATION,
    }
    write_json_atomic(series_manifest, payload)

    _reread_raw, reread = _load_manifest(series_manifest)
    for key, before in guarded.items():
        if key not in reread or _canonical(reread[key]) != before:
            typer.echo(
                f"post-write check failed: key {key!r} changed beyond the additive annotation",
                err=True,
            )
            raise typer.Exit(code=1)
    reference = reread["event_maximum_gate_reference"]
    if reference["gate_report_sha256"] != gate_sha or reference["event_product_sha256"] != (
        product_sha
    ):
        typer.echo("post-write check failed: recorded sha does not round-trip", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"event_maximum_gate_reference.scope={reference['scope']}")
    typer.echo(f"gate_report={reference['gate_report_path']}")
    typer.echo(f"gate_report_sha256={gate_sha}")
    typer.echo(f"event_product={reference['event_product_path']}")
    typer.echo(f"event_product_sha256={product_sha}")
    typer.echo(f"pre_annotation_sha256={_sha256_bytes(_preimage)}")
    typer.echo(f"post_annotation_sha256={_sha256_bytes(_reread_raw)}")
    typer.echo("all_other_keys=identical")


if __name__ == "__main__":
    app()
