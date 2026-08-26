"""WF-6 M2+M3 — event-maximum G1 binding and the OPEN ANOMALY card payload.

M2 (owner directive 2026-08-26 verbatim): G1 asks "did the model flood this
location during the event" — an event-level question. The frame series
REFERENCES the event-maximum score via ``event_maximum_gate_reference``
(written only by ``scripts/adjudicate_v6_adoption.py annotate-series-gate``);
per-frame rescoring is prohibited; the chip reads the event-level score with
an explicit "· event maximum" label and never UNAVAILABLE while an event-level
score exists.

Verification scope statements (V7), stated beside each check:

* annotation tests run against a TMP COPY of the realized frames_v2 manifest
  plus the REAL gate-report/event-product bytes read read-only;
* integration tests construct the real ``DashboardStore`` on the full
  frames_v2 series manifest (97 frames accepted at manifest level; background
  frame warm-up threads are irrelevant to every asserted field, which are all
  resolved before any frame byte is parsed) and on the full flat v5/v6 twins
  (176,171 rows per twin pair);
* red-under-mutation (V5) corrupts the recorded ``gate_report_sha256`` IN
  MEMORY on a live store rather than on disk: the loader
  (``app._safe_repo_path``) constrains annotated paths to the repository and
  this unit is forbidden to mutate realized ``runs/`` manifests except through
  its own annotation subcommand, so a tmp-copy disk mutation could not be
  served through the real resolution path anyway. The mutated call exercises
  exactly the code path the running server executes.

Independent observables (V2): recorded SHAs are recomputed from CURRENT file
bytes (never compared to another declaration), and the exposed G1 status is
tied back to the gate-report JSON read independently inside the test.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from jaladhar.validation.depth_product_contract import sha256_file  # noqa: E402
from jaladhar.web.app import DashboardStore  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
try:
    from adjudicate_v6_adoption import EVENT_GATE_ADJUDICATION
    from adjudicate_v6_adoption import app as adjudicate_app
finally:
    sys.path.remove(str(REPO / "scripts"))

FRAMES_RUN = REPO / "runs/wf3_replay2_uncoupled_baseline_frames_v2"
FRAMES_MANIFEST = FRAMES_RUN / "manifest.json"
GATE_REPORT = REPO / "runs/wf3_replay2_gates_v7_excluded_r2/g1_score.json"
V6_PRODUCTS = REPO / "runs/wf3_replay2_uncoupled_baseline_v6/products"
V6_CSV = V6_PRODUCTS / "segment_status.csv"
V6_MANIFEST = REPO / "runs/wf3_replay2_uncoupled_baseline_v6/manifest.json"
V5_PRODUCTS = REPO / "runs/wf3_replay2_uncoupled_baseline_v5/products"

ANNOTATION_INPUTS_PRESENT = all(path.exists() for path in (FRAMES_MANIFEST, GATE_REPORT, V6_CSV))

runner = CliRunner()


def _pre_annotation_copy(tmp_path: Path) -> tuple[Path, dict]:
    """Copy of the realized series manifest with the annotation key removed.

    The realized frames_v2 manifest is already annotated (this unit's own
    subcommand wrote it); tests needing the PRE-annotation state reconstruct
    it on a tmp copy -- the realized file itself is only ever touched by the
    subcommand.
    """
    copy = tmp_path / "manifest.json"
    shutil.copy2(FRAMES_MANIFEST, copy)
    payload = json.loads(copy.read_text(encoding="utf-8"))
    payload.pop("event_maximum_gate_reference", None)
    copy.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return copy, payload


# ------------------------------------------------------- annotate-series-gate


@pytest.mark.skipif(
    not ANNOTATION_INPUTS_PRESENT,
    reason="BLOCKED: needs the realized frames_v2 manifest, r2 gate report and v6 csv",
)
class TestAnnotateSeriesGate:
    def test_writes_additive_reference_and_refuses_second_run(self, tmp_path: Path) -> None:
        """Additive write onto a COPY; every pre-existing key survives byte-equal.

        Independent observable: the recorded SHAs equal sha256 of the CURRENT
        report/product bytes recomputed here; idempotence is the second run's
        non-zero exit naming the key it refuses to rewrite.
        """
        copy, original = _pre_annotation_copy(tmp_path)

        result = runner.invoke(
            adjudicate_app,
            [
                "annotate-series-gate",
                "--series-manifest",
                str(copy),
                "--gate-report",
                str(GATE_REPORT),
                "--event-product",
                str(V6_CSV),
            ],
        )
        assert result.exit_code == 0, result.output
        annotated = json.loads(copy.read_text(encoding="utf-8"))
        ref = annotated["event_maximum_gate_reference"]
        assert set(ref) == {
            "gate_report_path",
            "gate_report_sha256",
            "event_product_path",
            "event_product_sha256",
            "scope",
            "adjudication",
        }
        assert ref["scope"] == "event_maximum"
        assert ref["gate_report_path"].endswith(
            "runs/wf3_replay2_gates_v7_excluded_r2/g1_score.json"
        )
        assert ref["event_product_path"].endswith(
            "runs/wf3_replay2_uncoupled_baseline_v6/products/segment_status.csv"
        )
        # V1: digests checked against CURRENT bytes, not against declarations.
        assert ref["gate_report_sha256"] == sha256_file(GATE_REPORT)
        assert ref["event_product_sha256"] == sha256_file(V6_CSV)
        assert ref["adjudication"] == EVENT_GATE_ADJUDICATION
        # Guarded blocks: everything except the new key is identical.
        stripped = {k: v for k, v in annotated.items() if k != "event_maximum_gate_reference"}
        assert stripped == original

        second = runner.invoke(
            adjudicate_app,
            [
                "annotate-series-gate",
                "--series-manifest",
                str(copy),
                "--gate-report",
                str(GATE_REPORT),
                "--event-product",
                str(V6_CSV),
            ],
        )
        assert second.exit_code != 0
        assert "refusing second annotation" in second.output
        assert json.loads(copy.read_text(encoding="utf-8")) == annotated

    @pytest.mark.parametrize("missing", ["series-manifest", "gate-report", "event-product"])
    def test_refuses_missing_input_files(self, tmp_path: Path, missing: str) -> None:
        """Fail closed when any named input does not exist."""
        copy, _original = _pre_annotation_copy(tmp_path)
        args = ["annotate-series-gate", "--series-manifest", str(copy)]
        if missing != "gate-report":
            args += ["--gate-report", str(GATE_REPORT)]
        if missing != "event-product":
            args += ["--event-product", str(V6_CSV)]
        if missing == "gate-report":
            args += ["--gate-report", str(tmp_path / "absent_g1_score.json")]
        if missing == "event-product":
            args += ["--event-product", str(tmp_path / "absent_product.csv")]
        if missing == "series-manifest":
            args[args.index("--series-manifest") + 1] = str(tmp_path / "absent_manifest.json")
        result = runner.invoke(adjudicate_app, args)
        assert result.exit_code != 0, result.output
        untouched = json.loads(copy.read_text(encoding="utf-8")) if copy.exists() else None
        assert untouched is None or "event_maximum_gate_reference" not in untouched

    def test_refuses_report_not_bound_to_event_product(self, tmp_path: Path) -> None:
        """Recording an unbound reference would institutionalize a provenance lie."""
        copy, _original = _pre_annotation_copy(tmp_path)
        tampered_report = tmp_path / "tampered_g1_score.json"
        payload = json.loads(GATE_REPORT.read_text(encoding="utf-8"))
        payload["input_sha256"]["depth_product"] = "0" * 64
        tampered_report.write_text(json.dumps(payload), encoding="utf-8")
        result = runner.invoke(
            adjudicate_app,
            [
                "annotate-series-gate",
                "--series-manifest",
                str(copy),
                "--gate-report",
                str(tampered_report),
                "--event-product",
                str(V6_CSV),
            ],
        )
        assert result.exit_code != 0
        assert "not bound" in result.output
        assert "event_maximum_gate_reference" not in json.loads(copy.read_text(encoding="utf-8"))


# --------------------------------------------- app resolution order (repo-real)


@pytest.fixture(scope="module")
def frames_store() -> DashboardStore:
    """One real store on the frames_v2 run; shared across integration checks."""
    if not FRAMES_RUN.is_dir():
        pytest.skip("frames_v2 run absent")
    return DashboardStore(product=FRAMES_RUN)


@pytest.mark.skipif(
    not (FRAMES_MANIFEST.exists() and GATE_REPORT.exists()),
    reason="BLOCKED: needs the annotated frames_v2 manifest and the realized r2 report",
)
class TestSeriesEventGateReference:
    def test_state_payload_event_maximum_score_and_open_anomaly(
        self, frames_store: DashboardStore
    ) -> None:
        """Cold-open (no --gate-report): the chip still reads the event-level score.

        Scope: full frames_v2 manifest acceptance; asserted fields resolve from
        the manifest + referenced report bytes only. If the M2 resolution broke,
        g1_status would revert to UNAVAILABLE and this reddens.
        """
        payload = frames_store.state_payload()
        gate = payload["scientific_gate"]
        assert gate["scope"] == "event_maximum"
        assert gate["g1_status"].startswith("FAIL")
        assert "event maximum" in gate["g1_status"]
        assert gate["report_path"].endswith("wf3_replay2_gates_v7_excluded_r2/g1_score.json")
        # V1: reported hash equals the report's CURRENT bytes, recomputed here.
        assert gate["report_sha256"] == sha256_file(REPO / gate["report_path"])
        assert gate["reference_stale"] is False
        # Tie the displayed verdict to the report's own realized bytes.
        report = json.loads((REPO / gate["report_path"]).read_text(encoding="utf-8"))
        assert gate["g1_status"].startswith(report["g1"]["status"])
        assert gate["signed_g3_status"] == report["g3"]["status"]

        anomaly = payload["open_anomaly"]
        assert anomaly["status"] == "OPEN"
        assert anomaly["n_segments_gt300cm"] == 61
        assert anomaly["range_cm"] == [301, 369]
        assert anomaly["policy"]
        assert anomaly["source_manifest"].endswith(
            "runs/wf3_replay2_uncoupled_baseline_frames_v2/manifest.json"
        )

    def test_stale_reference_marks_flag_but_still_exposes_score(
        self, frames_store: DashboardStore
    ) -> None:
        """V5 red-under-mutation, function-level (see module docstring why).

        Mutation: the recorded gate_report_sha256 becomes zeros in memory.
        Expected observation: reference_stale flips True while g1_status STILL
        starts with the raw FAIL verdict — if the staleness branch ever degraded
        to UNAVAILABLE, or stopped computing staleness, one assertion reddens.
        """
        green = frames_store._scientific_gate_summary()
        assert green["reference_stale"] is False
        ref = frames_store.series.manifest["event_maximum_gate_reference"]
        recorded = ref["gate_report_sha256"]
        try:
            ref["gate_report_sha256"] = "0" * 64  # MUTATED (in-memory only)
            mutated = frames_store._scientific_gate_summary()
            assert mutated["reference_stale"] is True
            assert mutated["status"] == "loaded_not_accepted"
            assert mutated["g1_status"].startswith("FAIL")
            assert mutated["g1_status"].endswith("· event maximum")
        finally:
            ref["gate_report_sha256"] = recorded
        assert frames_store._scientific_gate_summary()["reference_stale"] is False

    def test_open_anomaly_key_absent_when_manifest_carries_none(
        self, frames_store: DashboardStore
    ) -> None:
        """Designed absence: no flagged anomaly -> NO key, never an empty card."""
        removed = frames_store.series.manifest.pop("open_anomaly", None)
        try:
            assert "open_anomaly" not in frames_store.state_payload()
        finally:
            if removed is not None:
                frames_store.series.manifest["open_anomaly"] = removed


@pytest.mark.skipif(
    not (V6_PRODUCTS.is_dir() and GATE_REPORT.is_file()),
    reason="v6 product or r2 gate report absent",
)
class TestFlatDirectBindingStillLabelled:
    def test_flat_v6_direct_binding_and_anomaly(self) -> None:
        """Path (a) mechanics unchanged; the bound event-maximum product adds the label.

        Scope: both 176,171-row twins loaded and row-compared by the store.
        Observable: bound hash recomputed from CURRENT csv bytes here.
        """
        store = DashboardStore(product=V6_PRODUCTS, gate_report=GATE_REPORT)
        payload = store.state_payload()
        gate = payload["scientific_gate"]
        assert gate["status"] == "loaded_not_accepted"
        assert gate["scope"] == "event_maximum"
        assert gate["g1_status"].startswith("FAIL")
        assert gate["g1_status"].endswith("· event maximum")
        assert gate["bound_product_sha256"] == sha256_file(V6_CSV)
        assert gate["report_sha256"] == sha256_file(GATE_REPORT)
        anomaly = payload["open_anomaly"]
        assert anomaly["n_segments_gt300cm"] == 61
        assert anomaly["range_cm"] == [301, 369]
        assert anomaly["source_manifest"].endswith(
            "runs/wf3_replay2_uncoupled_baseline_v6/manifest.json"
        )


@pytest.mark.skipif(not V5_PRODUCTS.is_dir(), reason="v5 product absent")
def test_flat_v5_store_has_no_open_anomaly_key() -> None:
    """Designed absence on older products: the key is omitted entirely (M3.1)."""
    store = DashboardStore(product=V5_PRODUCTS)
    assert store.state_payload()["status"] == "ready"
    assert "open_anomaly" not in store.state_payload()
