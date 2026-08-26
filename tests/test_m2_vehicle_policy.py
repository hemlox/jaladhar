"""M2 cited-wading-policy + routing-unblock tests (new file; foreign lane files untouched).

Scope statement (V7): these tests exercise, over small fixture repositories plus
the REAL curated pair in ``data/curation`` —

  * the policy loader's optional-annotation surface (sensitivity band,
    provenance grade, interpretation), extra-key tolerance, verbatim
    ``blocked_classes`` pass-through, and the sha256 evidence-binding refusal
    paths;
  * the ``python -m jaladhar.routing.policy validate`` CLI exit code against
    the curated repository artifact;
  * the launcher's ROUTING_STATE derivation for the three M2 banner outcomes
    (all-green / G1 FAIL / missing policy) at the pure-function level;
  * the routing HTTP surface's opt-in CORS behaviour on a live ephemeral
    server with a stub service.

They do NOT exercise full-stack route execution against a realized depth
product (that is ``scripts/demo/smoke_route_around.py``) nor browser DOM
behaviour (the CDP harness run beside it; PARTIAL by construction).

V5 mutations demonstrated red before trusting green:
  MUT-1  source_evidence.sha256 byte tampered            -> loader refuses
         (observed: status unavailable_external_unknown, "does not match").
  MUT-2  evidence FILE byte tampered after hashing       -> loader refuses
         (same observable, independent side of the hash pair).
Threshold tamper (30 -> 31 in the policy file alone) is NOT red-testable
through the loader BY DESIGN: ``source_sha256`` is COMPUTED over realized
bytes at load time, never read from a declaration, so no internal
inconsistency exists for the loader to trip on. The detection path for a
threshold tamper is provenance comparison downstream (every /route response
embeds the computed policy sha256, so any recorded consumer can detect the
changed bytes). That semantic is pinned by
``test_threshold_tamper_stays_loadable_but_moves_the_computed_sha`` below,
which asserts the computed sha differs from the pristine bytes' sha.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATED_POLICY = REPO_ROOT / "data/curation/vehicle_wading_policy.json"
CURATED_EVIDENCE = REPO_ROOT / "data/curation/vehicle_wading_evidence.md"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _policy_payload() -> dict[str, Any]:
    """Minimal valid policy payload shaped like the curated one."""

    return {
        "policies": {
            "passenger_car": {
                "max_impassable_depth_cm": 30,
                "citation": {"title": "t", "locator": "l"},
                "sensitivity_band_cm": [15, 40],
                "provenance_grade": "B",
                "interpretation": "still-water float limit",
            },
        },
        "blocked_classes": {
            "bus_truck": {"reason": "no published stationary-depth wading limit exists"}
        },
        "source_evidence": {"path": "data/curation/evidence.md", "sha256": ""},
    }


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A tmp repository holding a self-consistent policy/evidence pair."""

    repo = tmp_path / "repo"
    evidence = repo / "data/curation/evidence.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("evidence-bytes-v1\n", encoding="utf-8")
    payload = _policy_payload()
    payload["source_evidence"]["sha256"] = _sha256_bytes(evidence.read_bytes())
    policy = repo / "data/curation/policy.json"
    _write_json(policy, payload)
    return repo, policy


# --------------------------------------------------------- 1. validate CLI


def test_validate_cli_exit_zero_surfaces_both_classes_and_annotations() -> None:
    """The planned acceptance command exits 0 and surfaces every curated field."""

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "jaladhar.routing.policy",
            "validate",
            str(CURATED_POLICY),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert proc.returncode == 0, f"validate failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(proc.stdout)
    classes = {entry["vehicle_class"]: entry for entry in payload["policies"]}
    assert set(classes) == {"passenger_car", "emergency_heavy"}
    assert classes["passenger_car"]["max_impassable_depth_cm"] == 30
    assert classes["emergency_heavy"]["max_impassable_depth_cm"] == 46
    assert classes["passenger_car"]["sensitivity_band_cm"] == [15, 40]
    assert classes["emergency_heavy"]["sensitivity_band_cm"] == [30, 107]
    assert classes["passenger_car"]["provenance_grade"] == "B"
    assert classes["emergency_heavy"]["provenance_grade"] == "C-industry"
    assert classes["passenger_car"]["interpretation"]
    assert classes["emergency_heavy"]["interpretation"]
    assert (
        payload["blocked_classes"]["bus_truck"]["reason"]
        == "no published stationary-depth wading limit exists (ARR P10 lists commercial "
        "vehicles unassessed); moving-water figures are different physics"
    )
    # The curated pair binds to the REALIZED evidence bytes on disk (V1).
    assert payload["source_evidence"]["sha256"] == _sha256_bytes(CURATED_EVIDENCE.read_bytes())
    assert payload["source_sha256"] == _sha256_bytes(CURATED_POLICY.read_bytes())


# --------------------------------------------- 2. V5 red-under-mutation cases


def test_mut1_tampered_source_evidence_hash_byte_refuses(tmp_path: Path) -> None:
    """MUT-1: flipping one byte of the DECLARED evidence hash must refuse."""

    repo, policy = _fixture_repo(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    digest = payload["source_evidence"]["sha256"]
    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]
    payload["source_evidence"]["sha256"] = flipped
    _write_json(policy, payload)

    from jaladhar.routing.policy import load_vehicle_policies

    catalog = load_vehicle_policies(policy, repo_root=repo)
    assert not catalog.available
    assert catalog.error is not None and "does not match" in catalog.error


def test_mut2_tampered_evidence_file_byte_refuses(tmp_path: Path) -> None:
    """MUT-2: editing the EVIDENCE FILE after hashing must refuse identically."""

    repo, policy = _fixture_repo(tmp_path)
    evidence = repo / "data/curation/evidence.md"
    evidence.write_text("evidence-bytes-TAMPERED\n", encoding="utf-8")

    from jaladhar.routing.policy import load_vehicle_policies

    catalog = load_vehicle_policies(policy, repo_root=repo)
    assert not catalog.available
    assert catalog.error is not None and "does not match" in catalog.error


def test_threshold_tamper_stays_loadable_but_moves_the_computed_sha(tmp_path: Path) -> None:
    """A 30 -> 31 threshold edit cannot redden the LOADER path by design.

    Stated explicitly (plan S7 item 2): ``source_sha256`` is computed-not-
    declared, so a policy-file tamper produces no internal inconsistency.  The
    red-testable provenance path is the EVIDENCE hash (MUT-1/MUT-2 above); the
    threshold tamper is detectable where the computed sha is consumed — every
    /route response embeds ``provenance.vehicle_policy.sha256``, so a recorded
    prior value betrays the change.  This test pins that semantic: loadable,
    yes; byte-identical, never.
    """

    repo, policy = _fixture_repo(tmp_path)
    from jaladhar.routing.policy import load_vehicle_policies

    pristine_bytes = policy.read_bytes()
    pristine_catalog = load_vehicle_policies(policy, repo_root=repo)
    assert pristine_catalog.available
    assert pristine_catalog.policies["passenger_car"].max_impassable_depth_cm == 30

    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["policies"]["passenger_car"]["max_impassable_depth_cm"] = 31
    _write_json(policy, payload)

    tampered_catalog = load_vehicle_policies(policy, repo_root=repo)
    assert tampered_catalog.available  # computed-not-declared: nothing to refuse on
    assert tampered_catalog.policies["passenger_car"].max_impassable_depth_cm == 31
    assert tampered_catalog.source_sha256 == _sha256_bytes(policy.read_bytes())
    assert tampered_catalog.source_sha256 != _sha256_bytes(pristine_bytes)


# ------------------------------- 3. extra-key tolerance + blocked_classes pass-through


def test_extra_keys_tolerated_and_blocked_classes_pass_through_verbatim(tmp_path: Path) -> None:
    """Unknown keys are tolerated (loader ignores them); blocked_classes passes verbatim."""

    repo, policy = _fixture_repo(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["future_unknown_top_level"] = {"anything": [1, 2, 3]}
    payload["policies"]["passenger_car"]["future_unknown_class_key"] = "ignored"
    blocked = {
        "bus_truck": {
            "reason": "r",
            "evidence_note": "n",
            "nested": {"kept": "verbatim"},
        }
    }
    payload["blocked_classes"] = blocked
    _write_json(policy, payload)

    from jaladhar.routing.policy import load_vehicle_policies

    catalog = load_vehicle_policies(policy, repo_root=repo)
    assert catalog.available
    summary = catalog.summary()
    assert summary["blocked_classes"] == blocked
    assert catalog.get("PASSENGER_CAR").vehicle_class == "passenger_car"
    # get() consults only the threshold/citation — blocked_classes never gates it.


def test_malformed_blocked_classes_shape_refuses(tmp_path: Path) -> None:
    """Shape-check only: a non-object blocked_classes is refused, contents unchecked."""

    repo, policy = _fixture_repo(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["blocked_classes"] = ["not", "an", "object"]
    _write_json(policy, payload)

    from jaladhar.routing.policy import load_vehicle_policies

    catalog = load_vehicle_policies(policy, repo_root=repo)
    assert not catalog.available
    assert catalog.error is not None and "object" in catalog.error


# ------------------------------------------- 4. banner outcomes i / ii / iii


def _launch_demo_module():
    scripts_dir = REPO_ROOT / "scripts" / "demo"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import launch_demo

    return launch_demo


_READY_PAYLOAD = {
    "status": "ready",
    "snap_policy": {"status": "available"},
    "vehicle_policies": {"status": "available"},
    "depth_product": {"status": "available"},
    "scientific_readiness": {"operational_ready": True},
}


def test_banner_outcome_i_all_green_is_ready() -> None:
    lines = _launch_demo_module()._routing_state_lines(_READY_PAYLOAD)
    assert lines[0] == "ROUTING_STATE=ready"
    assert lines[1] == "ROUTING_BLOCKERS=none"


def test_banner_outcome_ii_g1_fail_names_scientific_gate_blocker() -> None:
    """G1 FAIL => headline not_ready + scientific_gate=not_accepted blocker.

    Scope note (V7): this pins the DERIVED state lines (the decision logic).
    The headline must read ``ROUTING_STATE=not_ready`` — derived from the
    payload's OVERALL status, never from one gate's status string. The
    process-level ``API_SERVING_DEGRADED`` stdout line is exercised
    end-to-end by scripts/demo/smoke_route_around.py running the launcher
    --startup-only against the real stack; its output is recorded there.
    """

    payload = dict(_READY_PAYLOAD)
    payload["status"] = "not_ready"
    payload["scientific_readiness"] = {"status": "not_accepted", "operational_ready": False}
    text = "\n".join(_launch_demo_module()._routing_state_lines(payload))
    assert "ROUTING_STATE=not_ready" in text
    assert "ROUTING_STATE=ready" not in text
    assert "ROUTING_BLOCKERS=scientific_gate=not_accepted" in text
    assert "snap_policy=" not in text
    assert "vehicle_policy=" not in text
    assert "depth_product=" not in text


def test_banner_outcome_iii_missing_policy_names_vehicle_blocker() -> None:
    payload = dict(_READY_PAYLOAD)
    payload["status"] = "not_ready"
    payload["vehicle_policies"] = {"status": "unavailable_external_unknown"}
    lines = _launch_demo_module()._routing_state_lines(payload)
    text = "\n".join(lines)
    # Headline derives from the OVERALL payload status (fix F2): a not_ready
    # payload is never headlined with a gate status string like
    # "unavailable_owner_gate" or "available".
    assert lines[0] == "ROUTING_STATE=not_ready"
    assert "vehicle_policy=unavailable_external_unknown" in text
    assert "scientific_gate=" not in text.split("ROUTING_BLOCKERS=")[1].splitlines()[0]


# ------------------------------------------------------------ 5. CORS unit


class _StubService:
    """Only the attributes the handler touches for /health and /policies."""

    def health(self) -> dict[str, Any]:
        return {"status": "not_ready"}

    class _Policies:
        @staticmethod
        def summary() -> dict[str, Any]:
            return {"status": "available"}

    policies = _Policies()


@pytest.fixture()
def routing_server():
    from jaladhar.routing.api import RoutingHTTPServer, _RoutingHandler

    started: list[tuple[RoutingHTTPServer, threading.Thread]] = []

    def _start(cors_origin: str | None):
        server = RoutingHTTPServer(("127.0.0.1", 0), _RoutingHandler)
        server.service = _StubService()
        server.cors_origin = cors_origin
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _cors_headers(response) -> dict[str, str]:
    return {key.lower(): value for key, value in response.headers.items()}


def _request(url: str, *, method: str, origin: str | None) -> Any:
    headers = {"Origin": origin} if origin else {}
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        return urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:  # non-2xx answers carry headers too
        return exc


def test_options_preflight_answers_204_with_cors_headers_when_configured(
    routing_server,
) -> None:
    base = routing_server("http://127.0.0.1:8501")
    response = _request(f"{base}/health", method="OPTIONS", origin="http://127.0.0.1:8501")
    assert response.status == 204
    headers = _cors_headers(response)
    assert headers["access-control-allow-origin"] == "http://127.0.0.1:8501"
    assert "content-type" in headers["access-control-allow-headers"].lower()
    assert "POST" in headers["access-control-allow-methods"]
    assert "OPTIONS" in headers["access-control-allow-methods"]


def test_get_echoes_origin_when_configured_and_absent_without_request_origin(
    routing_server,
) -> None:
    base = routing_server("http://127.0.0.1:8501")
    with_origin = _request(f"{base}/policies", method="GET", origin="http://127.0.0.1:8501")
    headers = _cors_headers(with_origin)
    assert headers["origin"] == "http://127.0.0.1:8501"
    assert headers["access-control-allow-origin"] == "http://127.0.0.1:8501"

    no_origin = _request(f"{base}/policies", method="GET", origin=None)
    assert "access-control-allow-origin" not in _cors_headers(no_origin)


def test_no_cors_headers_when_cors_origin_unset(routing_server) -> None:
    base = routing_server(None)
    response = _request(f"{base}/policies", method="GET", origin="http://127.0.0.1:8501")
    headers = _cors_headers(response)
    assert "access-control-allow-origin" not in headers
    assert "origin" not in headers


def test_mismatched_origin_gets_no_cors_headers(routing_server) -> None:
    """Allow-list semantics (fix F3): an Origin that is not exactly the configured
    value receives NO CORS headers — no ACAO, no echoed Origin, on GET and preflight."""

    base = routing_server("http://127.0.0.1:8501")
    for method in ("GET", "OPTIONS"):
        response = _request(f"{base}/policies", method=method, origin="http://evil.example:9999")
        headers = _cors_headers(response)
        assert "access-control-allow-origin" not in headers, (method, headers)
        assert "origin" not in headers, (method, headers)
        assert "access-control-allow-headers" not in headers
        assert "access-control-allow-methods" not in headers


def test_origin_differing_only_in_trailing_slash_is_mismatched(routing_server) -> None:
    """Exact-string matching: a near-miss origin must not be echoed as allowed."""

    base = routing_server("http://127.0.0.1:8501")
    response = _request(f"{base}/health", method="GET", origin="http://127.0.0.1:8501/")
    headers = _cors_headers(response)
    assert "access-control-allow-origin" not in headers
