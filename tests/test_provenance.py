"""Shared provenance contract tests.

Scope: temporary one-commit repositories and one manifest lifecycle each. These
checks establish the infrastructure contract; they do not claim every runner is
already migrated. Red mutation recorded: changing ``--porcelain`` to
``--porcelain --untracked-files=no`` makes the dirty-tree test fail.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jaladhar.provenance import DirtyTreeError, RunManifest
from jaladhar.validation import event_replay


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "provenance-test@example.invalid")
    _git(repo, "config", "user.name", "Provenance Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def test_dirty_tree_refuses_before_manifest_write(clean_repo: Path) -> None:
    """If broken: an uncommitted source file exists and a run manifest appears."""
    (clean_repo / "new_source.py").write_text("raise SystemExit\n")
    path = clean_repo / "ignored-output" / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={"steps": 4})

    with pytest.raises(DirtyTreeError, match="new_source.py"):
        run.start()

    assert not path.exists()


def test_realized_manifest_lifecycle_and_snapshot(clean_repo: Path) -> None:
    """If broken: on-disk state lacks immutable start provenance or terminal state."""
    path = clean_repo / "manifest.json"
    _git(clean_repo, "status", "--porcelain")  # independently confirm clean fixture
    cfg = {"solver": {"steps": 8}}
    budget = {"cells": 16, "timesteps": 8, "scenarios": 1}
    run = RunManifest(
        path,
        stage="test",
        repo_root=clean_repo,
        resolved_config=cfg,
        config_paths={"solver": "configs/solver.yaml"},
        compute_budget=budget,
    )
    start = run.start()
    on_disk_start = json.loads(path.read_text())
    assert on_disk_start == start
    assert on_disk_start["status"] == "running"
    assert on_disk_start["git_sha"] == _git(clean_repo, "rev-parse", "HEAD")
    assert "end_time_iso" not in on_disk_start

    cfg["solver"]["steps"] = 999
    run.complete({"realized_steps": 8})
    completed = json.loads(path.read_text())
    assert completed["status"] == "completed"
    assert completed["resolved_config"]["solver"]["steps"] == 8
    assert completed["compute_budget"] == budget
    assert completed["git_sha"] == on_disk_start["git_sha"]
    assert completed["start_time_iso"] == on_disk_start["start_time_iso"]
    assert completed["end_time_iso"]
    assert completed["realized_steps"] == 8


def test_failure_updates_started_manifest(clean_repo: Path) -> None:
    """If broken: an exception leaves only a running manifest with no failure reason."""
    path = clean_repo / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={})
    run.start()
    run.fail(ValueError("deliberate failure"))

    failed = json.loads(path.read_text())
    assert failed["status"] == "failed"
    assert failed["error_type"] == "ValueError"
    assert failed["error"] == "deliberate failure"
    assert failed["start_time_iso"]
    assert failed["end_time_iso"]


def test_terminal_manifest_is_immutable(clean_repo: Path) -> None:
    """If broken: a completed run can be relabelled as a later terminal state."""
    path = clean_repo / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={})
    run.start()
    run.complete()
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="only a running manifest"):
        run.complete()

    assert path.read_bytes() == before


def test_running_update_preserves_start_provenance(clean_repo: Path) -> None:
    """If broken: adding the compute budget replaces start identity or status."""
    path = clean_repo / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={})
    started = run.start()

    run.update_running({"compute_budget": {"cells": 16}})

    updated = json.loads(path.read_text())
    assert updated["status"] == "running"
    assert updated["git_sha"] == started["git_sha"]
    assert updated["start_time_iso"] == started["start_time_iso"]
    assert updated["compute_budget"] == {"cells": 16}
    assert "end_time_iso" not in updated


def test_manifest_owner_detects_another_writer(clean_repo: Path) -> None:
    """If broken: a second writer can replace realized running provenance.

    V5 red state is the deliberate external replacement below. Completion
    must observe it rather than accepting the in-memory mirror as realized
    state. Scope: one temporary manifest lifecycle.
    """
    path = clean_repo / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={})
    run.start()
    path.write_text('{"stage": "unauthorized-replacement"}\n')

    with pytest.raises(RuntimeError, match="overwritten by another writer"):
        run.complete()


def test_failure_preserves_foreign_manifest_and_writes_sidecar(clean_repo: Path) -> None:
    """If broken: failure handling destroys the unauthorized writer's evidence.

    Scope: one deliberately replaced manifest. The independent observable is
    the exact foreign bytes, which must remain unchanged after ``fail``.
    """
    path = clean_repo / "manifest.json"
    run = RunManifest(path, stage="test", repo_root=clean_repo, resolved_config={})
    run.start()
    foreign = b'{"stage":"foreign-writer","status":"running"}\n'
    path.write_bytes(foreign)

    with pytest.raises(RuntimeError, match="manifest ownership was lost"):
        run.fail(ValueError("original run failure"))

    assert path.read_bytes() == foreign
    sidecar = json.loads((clean_repo / "manifest.failure.json").read_text())
    assert sidecar["status"] == "manifest_ownership_lost"
    assert sidecar["error_type"] == "ValueError"
    assert sidecar["error"] == "original run failure"
    assert "overwritten by another writer" in sidecar["ownership_error"]


def test_phase3_gate_is_single_manifest_owner(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If broken: simulation/scoring replaces the lifecycle manifest on disk."""
    config_paths: dict[str, Path] = {}
    for name in ("solver", "forcing", "validation", "compute"):
        path = clean_repo / f"{name}.yaml"
        path.write_text("fixture: true\n")
        config_paths[name] = path
    _git(clean_repo, "add", ".")
    _git(clean_repo, "commit", "-qm", "add configs")

    monkeypatch.setattr(event_replay, "load_solver_config", lambda *_: {"solver": True})
    monkeypatch.setattr(event_replay, "resolve_phase3_config", lambda *args: {})

    def fake_simulation(*, lifecycle: RunManifest, **_: object) -> object:
        before = json.loads(lifecycle.path.read_text())
        assert before["status"] == "running"
        assert before["resolved_config"]["solver"]["solver"] is True
        lifecycle.update_running({"compute_budget": {"cells": 16, "timesteps": 8}})
        return SimpleNamespace(
            mass_dict={"rain_in_m3": 100.0, "residual_m3": 0.0},
            final_depth_path="run/final_depth.tif",
            cumulative_transport_depth_path="run/transport.tif",
            cumulative_drain_depth_path="run/drain.tif",
            cumulative_infiltration_depth_path="run/infiltration.tif",
            final_depth_buffered_path="run/final_depth_buffered.tif",
        )

    def fake_scoring(**_: object) -> dict[str, object]:
        during = json.loads((clean_repo / "run" / "manifest.json").read_text())
        assert during["status"] == "running"
        assert during["compute_budget"]["timesteps"] == 8
        return {
            "exclusions": {"domain_total_cells": 16, "excluded_cell_count": 3},
            "score": "realized-by-test-double",
        }

    monkeypatch.setattr(event_replay, "run_phase3_simulation", fake_simulation)
    monkeypatch.setattr(event_replay, "score_phase3_validation", fake_scoring)

    report = event_replay.run_phase3_gate(
        solver_cfg_path=config_paths["solver"],
        forcing_cfg_path=config_paths["forcing"],
        val_cfg_path=config_paths["validation"],
        compute_cfg_path=config_paths["compute"],
        out_dir=clean_repo / "run",
        repo_root=clean_repo,
    )

    completed = json.loads((clean_repo / "run" / "manifest.json").read_text())
    assert completed["status"] == "completed"
    assert completed["compute_budget"] == {"cells": 16, "timesteps": 8}
    assert completed["scored_domain_cell_count"] == 13
    assert completed["mass_balance"]["rain_in_m3"] == 100.0
    assert completed["artifacts"]["cumulative_drain_depth"] == "run/drain.tif"
    assert completed["results"] == report
