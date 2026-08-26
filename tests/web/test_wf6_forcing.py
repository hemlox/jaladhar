"""WF-6 build lane L2 — forcing hyetograph series (requirement N3).

Verification scope is stated beside every check (V7). Independent observables
(V2) are named per test:

* identity — the derived matrix's SHA-256 must equal the value frozen in the
  goal-D run manifest, read DIRECTLY from that file inside this test. If any
  step of the recipe drifted (granule set, window, factors, dtype), the hash
  moves; nothing else in this repo can restore it.
* totals — areal-mean event total and peak hourly rate were recorded by the
  goal-D run through a DIFFERENT code path (canonical-grid expansion); they
  are recomputed here from the matrix and compared bit-exactly.
* alignment arithmetic — 96 x 1800 s == 172800 s == the LAST frame offset of
  the realised frames manifest, read directly.
* rule-1 line — with no granules under the directory, derivation raises
  ForcingUnavailable carrying closes_how; NOTHING is emitted in place of the
  series (no climatology, no synthetic rainfall).
* rule-6 lifecycle — the CLI writes its manifest at run START and updates the
  same file to a terminal status; the end-to-end run here uses tmp outputs.

The full-window derivation runs once per module (~10 s, warm cache); the
end-to-end CLI run adds one more on purpose: it is the acceptance path (V4).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from jaladhar.web import forcing as fmod
from jaladhar.web.forcing import (
    ForcingUnavailable,
    app,
    derive_interval_rates,
    load_goal_d_identity,
)

REPO = REPO_ROOT = fmod.REPO_ROOT
GOAL_D_MANIFEST = REPO / "runs" / "goal_d_replay2_final_config" / "manifest.json"
FRAMES_MANIFEST = REPO / "runs" / "wf3_replay2_uncoupled_baseline_frames_v1" / "manifest.json"


@pytest.fixture(scope="module")
def identity() -> dict:
    return load_goal_d_identity(REPO)


@pytest.fixture(scope="module")
def derived() -> dict:
    return derive_interval_rates(repo_root=REPO)


class TestIdentity:
    def test_matrix_sha_equals_frozen_identity(self, derived: dict, identity: dict) -> None:
        """V2 observable: sha256(matrix bytes) vs the producer-frozen hash.

        Any drift in granules, window, cell mapping or factor application
        changes the bytes and therefore the hash — hard fail, no substitute.
        """
        assert derived["sha256"] == identity["interval_rates_mm_per_halfhour_sha256"]
        assert derived["sha256"] == (
            "a6cc7831b0da695bc2733329e2a2529ad55a9a498307a075890d2bd1d89ab39c"
        )  # the goal-prompt-recorded value; equality above is the binding check

    def test_matrix_shape_and_dtype(self, derived: dict, identity: dict) -> None:
        matrix: np.ndarray = derived["matrix"]
        assert matrix.dtype == np.float32
        assert list(matrix.shape) == identity["interval_matrix_shape"] == [96, 16]
        assert derived["n_intervals"] == identity["n_intervals"] == 96

    def test_totals_via_independent_path(self, derived: dict, identity: dict) -> None:
        """Totals recomputed through canonical-grid expansion (the run's own
        path), NOT by re-reading module outputs."""
        matrix: np.ndarray = derived["matrix"]
        from jaladhar.web.forcing import _grid_native_mapping

        _pairs, native_ids, _n = _grid_native_mapping(REPO)
        cumulative = matrix.sum(axis=0)[native_ids.astype(np.int64)]
        areal_mean_total = float(cumulative.mean())
        peak_rate = float(matrix.max()) * 60.0 / 30
        assert areal_mean_total == identity["areal_mean_total_mm"]
        assert peak_rate == identity["peak_hourly_rate_mm_hr"]
        # And the module's own cross-check block agrees:
        cc = derived["cross_check"]
        assert cc["areal_mean_total_mm_realized"] == cc["areal_mean_total_mm_manifest"]
        assert cc["peak_hourly_rate_mm_hr_realized"] == cc["peak_hourly_rate_mm_hr_manifest"]

    def test_window_matches_consumed_window(self, derived: dict, identity: dict) -> None:
        consumed = identity["ksndmc_alert_anchor"]["consumed_window"]
        t0, t1 = derived["window_utc"]
        assert t0 == consumed["start"] and t1 == consumed["end"]
        assert derived["interval_minutes"] * derived["n_intervals"] == 96 * 30


class TestSeriesEmission:
    def test_schema_and_alignment(self, derived: dict, tmp_path: Path) -> None:
        series = fmod.emit_series(derived, out=tmp_path / "fs.json")
        for key in (
            "window",
            "interval_minutes",
            "n_intervals",
            "areal_mean_mm_hr",
            "peak_cell_mm_hr",
            "sha256",
            "source_manifest_path",
            "derived_by",
            "generated_utc",
            "frame_alignment",
        ):
            assert key in series, f"forcing_series.json missing {key}"
        assert series["interval_minutes"] == 30
        assert series["n_intervals"] == 96
        assert len(series["areal_mean_mm_hr"]) == 96
        assert len(series["peak_cell_mm_hr"]) == 96
        assert all(v >= 0.0 for v in series["areal_mean_mm_hr"])
        assert all(series["peak_cell_mm_hr"][i] >= series["areal_mean_mm_hr"][i] for i in range(96))
        assert series["source_manifest_path"].endswith("goal_d_replay2_final_config/manifest.json")

        # Alignment arithmetic (V2): read the last frame offset DIRECTLY from
        # the frames manifest; if the series' claim drifts, this fails.
        raw = json.loads(FRAMES_MANIFEST.read_text())
        last_offset = int(raw["frames"][-1]["offset_seconds"])
        align = series["frame_alignment"]
        assert 96 * 1800 == 172800 == last_offset
        assert align["last_frame_offset_seconds"] == last_offset
        assert align["expected_last_frame_offset_seconds"] == 172800
        assert align["n_frames"] == len(raw["frames"]) == 97

    def test_emitted_json_round_trips(self, derived: dict, tmp_path: Path) -> None:
        path = tmp_path / "fs.json"
        series = fmod.emit_series(derived, out=path)
        reloaded = json.loads(path.read_text())
        assert reloaded == json.loads(json.dumps(series))


class TestRuleOneHardLine:
    def test_missing_granules_raise_forcing_unavailable(self, tmp_path: Path) -> None:
        empty = tmp_path / "no-granules"
        empty.mkdir()
        with pytest.raises(ForcingUnavailable) as excinfo:
            derive_interval_rates(repo_root=REPO, granules_dir=empty)
        exc = excinfo.value
        assert getattr(exc, "closes_how", ""), "blocker must state how it closes"
        assert "fetch_imerg" in exc.closes_how or "acquire" in exc.closes_how.lower()
        assert exc.inventory["n_hdf5"] == 0

    def test_no_series_file_written_on_failure(self, tmp_path: Path) -> None:
        empty = tmp_path / "no-granules"
        empty.mkdir()
        out = tmp_path / "should-not-exist.json"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "derive",
                "--out",
                str(out),
                "--manifest-out",
                str(tmp_path / "m.json"),
                "--granules-dir",
                str(empty),
            ],
            catch_exceptions=True,
        )
        assert result.exit_code != 0
        assert not out.exists(), "rule 1: nothing may be emitted without real inputs"
        failed = json.loads((tmp_path / "m.json").read_text())
        assert failed["status"] == "failed"
        assert failed["closes_how"]


class TestRuleSixLifecycle:
    def test_cli_end_to_end_start_then_completed(self, tmp_path: Path) -> None:
        """V4 acceptance path: full CLI derivation into tmp artefacts.

        The rule-6 manifest must reach a terminal 'completed' status carrying
        the realized SHA, written by updating ONE file (start -> completed).
        """
        out = tmp_path / "series.json"
        man = tmp_path / "manifest.json"
        runner = CliRunner()
        result = runner.invoke(app, ["derive", "--out", str(out), "--manifest-out", str(man)])
        assert result.exit_code == 0, result.output
        payload = json.loads(out.read_text())
        manifest = json.loads(man.read_text())
        assert manifest["status"] == "completed"
        assert manifest["git_sha"], "provenance requires a git SHA"
        assert manifest["start_time_iso"] < manifest["end_time_iso"]
        assert manifest["sha256"] == payload["sha256"]
        assert manifest["n_intervals"] == 96
        assert manifest["series_written"] is True
        identity_frozen = json.loads(GOAL_D_MANIFEST.read_text())["forcing_identity"]
        assert payload["sha256"] == identity_frozen["interval_rates_mm_per_halfhour_sha256"]
