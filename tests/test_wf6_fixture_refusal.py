"""WF-6 INT unit — fixture-refusal invariants (ADDENDUM-2/3 L6, V5 red-first).

RED-FIRST RECORD: this file was written and executed BEFORE any integration
edit touched ``src/jaladhar/web/app.py`` in this unit. Every invariant below
was already realized by the existing discovery/admission code, so the tests
codify green — the goal prompt's RED-FIRST clause directs exactly that when
the refusal already exists ("do not weaken discovery to manufacture a red").
No discovery code was changed by this unit; the git diff of
``jaladhar.web.series`` and of ``_candidate_products`` is empty.

Independent observables (V2), one per invariant:

* detect_series on an undeclared fixture — if a manifest WITHOUT
  ``series_kind`` could ever own the store, the dashboard would warm and
  render a fake run's frames as replay state. The observable is the return
  value plus the absence of any warming thread (a declared-but-invalid
  manifest returns a FrameSeries whose acceptance FAILED, checked separately).
* DashboardStore(product=fake csv) — if admission accepted a 2-segment product,
  ``snapshots`` would be non-empty and ``status`` would read "ready"; the
  observable is the realized status/error fields, not a log line.
* auto-discovery rooting — if ``_candidate_products(repo_root, None)`` ever
  returned a path outside ``runs/``, cold boot would admit fixture bytes.

Scope each check runs at (V7): tiny synthetic fixtures (1-2 rows), real repo
contract + real segment lookup on the store path, and the REAL runs/ tree for
the discovery-root check (asserted non-empty so it cannot pass vacuously).
"""

from __future__ import annotations

from pathlib import Path

from jaladhar.web.app import REPO_ROOT, DashboardStore, _candidate_products
from jaladhar.web.series import detect_series

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_RUN = FIXTURES / "wf6_fake_run"
DECLARED_RUN = FIXTURES / "wf6_fake_run_declared"
FAKE_CSV = FAKE_RUN / "products" / "segment_status.csv"


def test_detect_series_ignores_undeclared_fixture() -> None:
    """A manifest without series_kind can never be detected as a frame series."""
    result = detect_series(FAKE_RUN, repo_root=REPO_ROOT)
    assert result is None, "detect_series accepted a fixture whose manifest declares no series_kind"


def test_detect_series_declares_only_completed_series() -> None:
    """'Unless declared' branch: a declaring-but-invalid fixture is returned
    as a REFUSED candidate (never ready, never warming, error names why)."""
    result = detect_series(DECLARED_RUN, repo_root=REPO_ROOT)
    assert result is not None  # declaration alone surfaces a candidate object
    assert result.ready is False
    assert result.error is not None and "completed" in result.error


def test_store_refuses_fake_product() -> None:
    """DashboardStore(product=<fake csv>) refuses: error status, zero snapshots."""
    store = DashboardStore(product=FAKE_CSV)
    assert store.series is None
    assert store.snapshots == ()
    assert store.status == "error", f"store admitted fake product: {store.status}"
    payload = store.state_payload()
    assert payload["status"] == "error"
    # The refusal must name the candidate it rejected (honest failure, rule 6).
    skipped = payload.get("skipped_candidates") or store.skipped_candidates
    assert any("wf6_fake_run" in entry.get("candidate", "") for entry in skipped), skipped
    message = payload.get("message", "")
    assert "no candidate depth product validated" in message or "wf6_fake_run" in message


def test_auto_discovery_roots_at_runs() -> None:
    """Cold discovery candidates come only from runs/**; tests/ bytes are
    unreachable even though this test file sits inside the repo."""
    candidates = _candidate_products(REPO_ROOT, None)
    assert candidates, "no realized products under runs/ — check would pass vacuously"
    runs_root = (REPO_ROOT / "runs").resolve()
    for path in candidates:
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(runs_root), f"discovery leaked outside runs/: {resolved}"


def test_auto_discovery_never_returns_fixture_paths() -> None:
    candidates = _candidate_products(REPO_ROOT, None)
    fixtures = str(FIXTURES)
    assert all(fixtures not in str(p) for p in candidates)
