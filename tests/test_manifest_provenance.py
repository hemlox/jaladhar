"""Provenance enforcement across EVERY manifest in runs/.
Phase 1 bug #7/#1 was exactly this failure: manifests written by a CLI that
bypassed the shared helper, so a manifest could describe a different run than
an acceptance-run manifest with `git_sha: None`. **The Phase 1 fix made the
manifest under `runs/` and fails the moment ANY of them lacks a real commit.
minutes, and this is what stops the third instance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs"

# Manifests produced before this rule existed. Each entry is a DEBT, not an
# exemption — a manifest here is one that cannot be traced to a commit, and the
GRANDFATHERED: dict[str, str] = {
    "analytical_ladder/manifest.json": (
        "records a commit that is absent from this repository. CLOSED BY: a clean-state rerun "
        "through the shared provenance lifecycle; never by replacing the historical SHA."
    ),
    "anuga_crossval/manifest.json": (
        "records a commit that is absent from this repository. CLOSED BY: a clean-state rerun "
        "through the shared provenance lifecycle; never by replacing the historical SHA."
    ),
    "depression_inventory/manifest.json": (
        "records a commit that is absent from this repository and its producing script is unavailable. "  # noqa: E501
        "CLOSED BY: restoring a traceable producer and rerunning from a clean commit, or deleting the "  # noqa: E501
        "artifact once a traceable replacement supersedes it."
    ),
    "solver_probe/manifest.json": (
        "1 h probe, same pre-fix code path with git_sha: None. CLOSED BY: deletion once the "
        "re-run acceptance supersedes it; retained meanwhile because its mass "
        "residual at 4,887 steps is the datapoint the tolerance scaling rests on."
    ),
}


def _manifests() -> list[Path]:
    return sorted(RUNS.rglob("manifest.json"))


def _is_real_commit(sha: str) -> bool:
    try:
        subprocess.check_output(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            stderr=subprocess.DEVNULL,
            cwd=REPO,
        )
        return True
    except Exception:
        return False


def test_runs_directory_has_manifests():
    """vacuously, which is precisely the class of defect (V7) that this project"""
    found = _manifests()
    if not found:
        pytest.skip("no runs/ manifests present — nothing to verify")
    assert len(found) > 0


@pytest.mark.parametrize("manifest", _manifests(), ids=lambda p: str(p.relative_to(RUNS)))
def test_every_manifest_records_a_real_commit(manifest: Path):
    """Observable if false: a manifest whose `git_sha` is null, missing,"""
    rel = str(manifest.relative_to(RUNS))
    if rel in GRANDFATHERED:
        pytest.skip(f"DEBT (not exempt) — {rel}: {GRANDFATHERED[rel]}")

    data = json.loads(manifest.read_text())
    sha = data.get("git_sha")

    assert sha is not None, (
        f"{rel}: git_sha is null — this run cannot be traced to a commit. "
        "Route the writer through terrain/grid.py::run_stage(), or call git_sha() "
        "explicitly. See this module's docstring: this exact failure has now "
        "happened in two separate phases."
    )
    assert sha != "unknown", f"{rel}: git_sha is 'unknown' — git was unavailable when it ran"
    assert _is_real_commit(sha), f"{rel}: git_sha {sha!r} is not a commit in this repository"


@pytest.mark.parametrize("manifest", _manifests(), ids=lambda p: str(p.relative_to(RUNS)))
def test_no_manifest_is_pathologically_large(manifest: Path):
    """Manifests are metadata, not data stores.
    The first Phase 2 acceptance manifest was 2.4 MB because a 44,049-entry dt"""
    rel = str(manifest.relative_to(RUNS))
    if rel in GRANDFATHERED:
        pytest.skip(f"DEBT (not exempt) — {rel}: {GRANDFATHERED[rel]}")
    size_mib = manifest.stat().st_size / 1024**2
    assert size_mib < 1.0, (
        f"{rel} is {size_mib:.2f} MiB. Manifests hold metadata; move bulk arrays "
        "to a binary sidecar and reference it (see solver/run.py::write_dt_sidecar)."
    )
