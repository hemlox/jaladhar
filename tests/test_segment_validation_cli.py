from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jaladhar_segment_validation_cli", REPO / "scripts/run_segment_validation.py"
)
assert SPEC is not None and SPEC.loader is not None
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def test_positive_unlabelled_metrics_render_without_crashing() -> None:
    """Scope: the output conversion boundary, independent of a Phase 3 rerun."""
    assert CLI.format_metric(None) == "N/A"
    assert CLI.format_metric(float("nan")) == "N/A"
    assert CLI.format_metric(0.125, places=2) == "0.12"


def test_cli_passes_distinct_phase3_input_and_output(tmp_path: Path, monkeypatch) -> None:
    """Scope: CLI path ownership only; no scientific input or output is fabricated."""
    input_dir = tmp_path / "phase3"
    output_dir = tmp_path / "segment"
    config = tmp_path / "validation.yaml"
    config.write_text("{}\n")
    captured: dict[str, Any] = {}

    class FakeManifest:
        def __init__(self, path, **kwargs):
            captured["manifest_path"] = path

        def start(self):
            pass

        def complete(self, fields):
            pass

        def fail(self, exc):
            raise exc

    score = {
        "observed_flooded_segments": 0,
        "predicted_flooded_segments": 0,
        "hits": 0,
        "pod": None,
        "far": None,
        "csi": None,
    }
    report = {
        "headline_results": {
            "bbmp_validation": score,
            "groundtruth_validation_date_and_snap_eligible": score,
        },
        "stratification": {
            "arterial_trunk_alone": {"total_arterial_segments": 0, "bbmp_event_max": score}
        },
        "trunk_signal_evaluation": {"trunk_alone": score},
        "verdict_for_adjudication": {"status": "BLOCKED", "findings": []},
    }

    def fake_gate(**kwargs):
        captured.update(kwargs)
        return report

    monkeypatch.setattr(CLI, "RunManifest", FakeManifest)
    monkeypatch.setattr(CLI, "run_segment_validation_gate", fake_gate)
    CLI.main(
        runs_dir=input_dir,
        output_dir=output_dir,
        val_config=config,
        mc_draws=10000,
        seed=42,
    )

    assert captured["phase3_run_dir"] == input_dir
    assert captured["output_dir"] == output_dir
    assert captured["manifest_path"] == output_dir / "segment_validation_manifest.json"
