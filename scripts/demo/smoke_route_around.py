#!/usr/bin/env python3
"""M2 headless smoke — cited wading policy + routing unblock, route-around flow.

Stdlib-only (argparse/json/subprocess/urllib): starts the real background stack
(dashboard + routing API with policy and snap configured), snapshots /health
before/after into ``runs/m2_smoke_<ts>/``, chooses a flooded-street pair from
the live dashboard watchlist, resolves origin/destination from already-served
segment geometry (the segment's own endpoints — graph nodes are built from
segment endpoints, so snap distance is 0), then POSTs /route per configured
vehicle class asserting EITHER ok-with-avoided_segments OR the exact honest
refusal code expected under the realized gate state. Both branches are printed
and which-branch-exercised is stated per class in summary.json.

Gate-report binding: ``runs/wf3_replay2_gates_v7_excluded/g1_score.json`` is
used when it exists; otherwise the stack runs in absent-state (no
--gate-report-file), where every route honestly refuses
scientific_gate_not_accepted.

Everything this script starts, it kills.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_REPORT_CANDIDATE = REPO_ROOT / "runs/wf3_replay2_gates_v7_excluded/g1_score.json"
DEFAULT_POLICY = REPO_ROOT / "data/curation/vehicle_wading_policy.json"
GEOMETRY_ARG = REPO_ROOT / "data/interim/terrain/roads_centrelines.gpkg"


def log(message: str) -> None:
    print(f"[m2-smoke] {message}", flush=True)


def http_json(
    url: str, *, method: str = "GET", payload: Any | None = None, timeout: float = 10.0
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {"_raw": raw[:400].decode("utf-8", errors="replace")}


def wait_ready(
    url: str, process: subprocess.Popen[Any], name: str, *, timeout_s: float = 120.0
) -> None:
    deadline = time.monotonic() + timeout_s
    last = "no response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"{name} exited rc={process.returncode} before readiness")
        try:
            status, _ = http_json(url, timeout=2.0)
            if 200 <= status < 400:
                return
            last = f"HTTP {status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = str(exc)
        time.sleep(0.25)
    raise SystemExit(f"{name} never became ready at {url}: {last}")


def _servable_product_path(entry: Path, payload: dict[str, Any]) -> Path | None:
    """The product twin this run can actually serve (flat CSV or frame-0 CSV)."""

    if payload.get("series_kind"):
        frames = payload.get("frames")
        if isinstance(frames, list) and frames and isinstance(frames[0], dict):
            relative = str(frames[0].get("csv", ""))
            if relative:
                candidate = REPO_ROOT / relative
                if candidate.is_file():
                    return candidate
        return None
    candidate = entry / "products/segment_status.csv"
    return candidate if candidate.is_file() else None


def pick_run_dir(explicit: Path | None) -> tuple[Path, dict[str, Any]]:
    """Newest completed FLAT run preferred; series only when no flat run exists.

    Rationale (measured, not stylistic): the routing API binds a FROZEN
    FLAT-contract depth product; a frame-series twin is refused by design
    (see ``_demo_common.depth_product_file_for``), so exercising the route
    gate stack requires the flat shape.  Explicit ``--run-dir`` always wins.
    """

    if explicit is not None:
        manifest_path = explicit / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return explicit, payload
    candidates: list[tuple[str, str, bool, Path, dict[str, Any]]] = []
    for entry in sorted((REPO_ROOT / "runs").iterdir()):
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") != "completed":
            continue
        if _servable_product_path(entry, payload) is None:
            continue
        stamp = str(payload.get("end_time_iso") or payload.get("start_time_iso") or "")
        candidates.append((stamp, entry.name, bool(payload.get("series_kind")), entry, payload))
    flat = [item for item in candidates if not item[2]]
    pool = flat or candidates
    if not pool:
        raise SystemExit("no completed run under runs/ to serve; nothing invented")
    chosen = max(pool, key=lambda item: (item[0], item[1]))
    return chosen[3], chosen[4]


def depth_product_csv(run_dir: Path, manifest: dict[str, Any]) -> Path:
    product = _servable_product_path(run_dir, manifest)
    if product is None:
        raise SystemExit(f"run {run_dir.name} has no servable product twin")
    return product


def realized_forecast_lead(product_csv: Path) -> int | None:
    """Read the product's OWN realized forecast lead from its bytes (rule 3)."""

    import csv

    with product_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get("forecast_lead_minutes")
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def segment_endpoints_from_dashboard(
    dashboard: str, segment_id: int, source_block: dict[str, Any] | None
) -> list[list[float]]:
    if (
        source_block
        and source_block.get("kind") == "frame"
        and isinstance(source_block.get("frame_index"), int)
    ):
        url = (
            f"{dashboard}/api/segments?index={source_block['frame_index']}"
            f"&segment_id={segment_id}"
        )
    else:
        url = f"{dashboard}/api/segments?segment_id={segment_id}"
    status, payload = http_json(url)
    if status != 200:
        raise SystemExit(f"/api/segments refused segment {segment_id}: HTTP {status}")
    features = payload.get("features") or []
    geometry = features[0].get("geometry") if features else None
    coords = None
    if isinstance(geometry, dict):
        if geometry.get("type") == "LineString":
            coords = geometry.get("coordinates")
        elif geometry.get("type") == "MultiLineString" and geometry.get("coordinates"):
            coords = geometry["coordinates"][0]
    if not coords or len(coords) < 2:
        raise SystemExit(f"segment {segment_id} carries no served endpoint geometry")
    return [
        [float(coords[0][0]), float(coords[0][1])],
        [float(coords[-1][0]), float(coords[-1][1])],
    ]


def stop(processes: list[subprocess.Popen[Any]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--dashboard-port", type=int, default=8531)
    parser.add_argument("--routing-port", type=int, default=8532)
    parser.add_argument(
        "--skip-launcher-banner",
        action="store_true",
        help="skip the launch_demo --startup-only banner pass",
    )
    args = parser.parse_args()

    python = sys.executable
    out_dir = REPO_ROOT / f"runs/m2_smoke_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir, manifest = pick_run_dir(args.run_dir)
    product_csv = depth_product_csv(run_dir, manifest)
    dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
    routing_url = f"http://127.0.0.1:{args.routing_port}"

    gate_report = GATE_REPORT_CANDIDATE if GATE_REPORT_CANDIDATE.is_file() else None
    gate_mode = str(gate_report) if gate_report else "ABSENT_STATE (no v7 gate report)"

    log(f"RUN_DIR={run_dir.relative_to(REPO_ROOT)}")
    log(f"DEPTH_PRODUCT={product_csv.relative_to(REPO_ROOT)}")
    log(f"GATE_REPORT={gate_mode}")
    log(f"OUT_DIR={out_dir.relative_to(REPO_ROOT)}")

    processes: list[subprocess.Popen[Any]] = []

    # ---- launcher banner pass (real ROUTING_STATE lines from launch_demo)
    launcher_banner: dict[str, Any] = {"skipped": args.skip_launcher_banner}
    if not args.skip_launcher_banner:
        launcher_argv = [
            python,
            str(REPO_ROOT / "scripts/demo/launch_demo.py"),
            "--run-dir",
            str(run_dir),
            "--host",
            "127.0.0.1",
            "--dashboard-port",
            str(args.dashboard_port + 20),
            "--api-port",
            str(args.routing_port + 20),
            "--startup-only",
        ]
        try:
            done = subprocess.run(
                launcher_argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=420
            )
            banner_lines = [
                line
                for line in (done.stdout + done.stderr).splitlines()
                if line.startswith(
                    (
                        "ROUTING_",
                        "API_SERVING_DEGRADED",
                        "API_READY",
                        "VEHICLE_POLICY_FILE",
                        "SERVER_MAX_SNAP_DISTANCE_M",
                        "ROUTING_CONFIG",
                        "CORS_ORIGIN",
                        "DEMO_BLOCKED",
                    )
                )
            ]
            launcher_banner = {"returncode": done.returncode, "lines": banner_lines}
            for line in banner_lines:
                log(f"launcher| {line}")
        except subprocess.TimeoutExpired:
            launcher_banner = {"error": "launch_demo --startup-only timed out after 420s"}
            log("launcher| TIMEOUT after 420s")
    (out_dir / "launcher_banner.json").write_text(
        json.dumps(launcher_banner, indent=2), encoding="utf-8"
    )

    # ---- background stack
    api_argv = [
        python,
        "-m",
        "jaladhar.routing.api",
        "serve",
        "--depth-product-file",
        str(product_csv),
        "--vehicle-policy-file",
        str(DEFAULT_POLICY),
        "--server-max-snap-distance-m",
        "25.0",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.routing_port),
    ]
    if gate_report is not None:
        api_argv += ["--gate-report-file", str(gate_report)]
    dash_argv = [
        python,
        "-m",
        "jaladhar.web.app",
        "serve",
        "--product",
        str(run_dir / "products"),
        "--geometry",
        str(GEOMETRY_ARG),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.dashboard_port),
    ]
    api_log_path = out_dir / "routing_api.log"
    dash_log_path = out_dir / "dashboard.log"
    api_log = api_log_path.open("w", encoding="utf-8")
    dash_log = dash_log_path.open("w", encoding="utf-8")
    try:
        api = subprocess.Popen(api_argv, cwd=REPO_ROOT, stdout=api_log, stderr=subprocess.STDOUT)
        dash = subprocess.Popen(dash_argv, cwd=REPO_ROOT, stdout=dash_log, stderr=subprocess.STDOUT)
        processes += [dash, api]

        wait_ready(f"{routing_url}/health", api, "routing-api")
        dashboard_up = True
        try:
            wait_ready(f"{dashboard_url}/api/state", dash, "dashboard", timeout_s=180.0)
        except SystemExit as exc:
            dashboard_up = False
            log(f"DASHBOARD_DOWN honest state: {exc}")

        health_before = http_json(f"{routing_url}/health")[1]
        (out_dir / "health_before.json").write_text(
            json.dumps(health_before, indent=2, sort_keys=True), encoding="utf-8"
        )
        policies = health_before.get("vehicle_policies", {})
        scientific = health_before.get("scientific_readiness", {})
        snap_policy = health_before.get("snap_policy", {})
        gate_operational = scientific.get("operational_ready") is True
        log(
            f"policies.status={policies.get('status')} "
            f"snap.status={snap_policy.get('status')} "
            f"snap_cap={snap_policy.get('server_max_snap_distance_m')}"
        )
        log(
            f"scientific_readiness.operational_ready={scientific.get('operational_ready')} "
            f"(status={scientific.get('status')})"
        )

        # ---- choose a flooded-street pair from the live dashboard watchlist
        pair_choice: dict[str, Any] = {"dashboard_up": dashboard_up}
        origin: list[float] | None = None
        destination: list[float] | None = None
        source_block: dict[str, Any] | None = None
        if dashboard_up:
            frame_tag = "held"
            status, watch = http_json(
                f"{dashboard_url}/api/watchlist?frame={frame_tag}", timeout=60.0
            )
            rows = watch.get("rows") if status == 200 else None
            if not rows:
                pair_choice["note"] = f"watchlist gave no rows (HTTP {status})"
                log(f"WATCHLIST no rows: {pair_choice['note']}")
            else:
                flooded = [row for row in rows if row.get("status") == "flooded"]
                target_row = flooded[0] if flooded else rows[0]
                segment_id = target_row.get(
                    "worst_segment_id",
                    (target_row.get("segment_ids") or [None])[0],
                )
                source_block = watch.get("source")
                endpoints = segment_endpoints_from_dashboard(
                    dashboard_url, int(segment_id), source_block
                )
                origin, destination = endpoints
                pair_choice.update(
                    {
                        "street_name": target_row.get("street_name"),
                        "segment_id": segment_id,
                        "origin": origin,
                        "destination": destination,
                        "lead_kind": (source_block or {}).get("lead_kind"),
                    }
                )
                log(
                    f"PAIR street='{target_row.get('street_name')}' "
                    f"segment={segment_id} origin={origin} destination={destination}"
                )
        (out_dir / "pair_choice.json").write_text(
            json.dumps(pair_choice, indent=2), encoding="utf-8"
        )
        if origin is None or destination is None:
            log("BLOCKED: no usable origin/destination pair; refusing to invent one")
            (out_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "verdict": "PARTIAL",
                        "reason": "no usable origin/destination pair; route branch unexercised",
                        "pair_choice": pair_choice,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return 1

        # ---- POST /route per configured class
        policy_payload = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
        classes = sorted(policy_payload.get("policies", {}))
        blocked_classes = sorted(policy_payload.get("blocked_classes", {}))
        cap_m = snap_policy.get("server_max_snap_distance_m")
        realized_lead = realized_forecast_lead(product_csv)
        log(f"product realized forecast_lead_minutes={realized_lead} (read from product bytes)")
        results: list[dict[str, Any]] = []
        all_matched = True

        def expected_outcome(vehicle_class: str, *, with_lead: bool) -> tuple[str, str]:
            """(expected, rationale) under the REALIZED gate/product state."""

            if not with_lead:
                # Field omitted because the displayed payload realizes no
                # forecast lead (hindcast offsets are never sent as leads):
                # field validation refuses BEFORE any gate — honest 400.
                return "refusal_400_invalid_request_missing_field", (
                    "no forecast lead in the served frame payload; the required "
                    "field is honestly absent and validation refuses first"
                )
            if not gate_operational:
                return "refusal_503_scientific_gate_not_accepted", (
                    "scientific_readiness.operational_ready=false; the gate "
                    "refuses before product/policy resolution"
                )
            if vehicle_class in blocked_classes:
                return "refusal_503_vehicle_policy_unavailable", (
                    "gate operational but the class is deliberately unconfigured"
                )
            return "ok_with_avoided_segments_or_disconnected", (
                "gate operational, cited class: full route stack executes"
            )

        probes: list[tuple[str, dict[str, Any], bool]] = []
        for vehicle_class in classes + blocked_classes:
            full_body: dict[str, Any] = {
                "origin": origin,
                "destination": destination,
                "vehicle_class": vehicle_class,
                "max_snap_distance_m": cap_m,
            }
            if realized_lead is not None:
                full_body["forecast_lead_minutes"] = realized_lead
            probes.append((f"{vehicle_class}/with-realized-lead", full_body, True))
            if realized_lead is None:
                # UI-honesty probe: exactly what routing.js sends when the
                # displayed payload carries no forecast lead.
                probes.append(
                    (
                        f"{vehicle_class}/omitted-field",
                        {
                            key: value
                            for key, value in full_body.items()
                            if key != "forecast_lead_minutes"
                        },
                        False,
                    )
                )

        for label, request_body, with_lead in probes:
            vehicle_class = str(request_body["vehicle_class"])
            expected, rationale = expected_outcome(vehicle_class, with_lead=with_lead)
            started = time.monotonic()
            status, payload = http_json(
                f"{routing_url}/v1/route", method="POST", payload=request_body
            )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            code = (payload or {}).get("error", {}).get("code")
            route_status = (payload or {}).get("status")
            branch_ok = {
                "refusal_400_invalid_request_missing_field": status == 400
                and code == "invalid_request",
                "refusal_503_scientific_gate_not_accepted": status == 503
                and code == "scientific_gate_not_accepted",
                "refusal_503_vehicle_policy_unavailable": status == 503
                and code == "vehicle_policy_unavailable",
                "ok_with_avoided_segments_or_disconnected": route_status in {"ok", "disconnected"}
                and (
                    route_status != "ok"
                    or isinstance((payload or {}).get("avoided_segments"), list)
                ),
            }[expected]
            all_matched = all_matched and branch_ok
            exercised = (
                "OK_BRANCH realized"
                if route_status == "ok"
                else (
                    "DISCONNECTED_BRANCH realized"
                    if route_status == "disconnected"
                    else f"REFUSAL_BRANCH realized ({code})"
                )
            )
            results.append(
                {
                    "probe": label,
                    "http_status": status,
                    "route_status": route_status,
                    "error_code": code,
                    "expected_under_realized_gate": expected,
                    "rationale": rationale,
                    "branch_matched_expectation": branch_ok,
                    "which_branch_exercised": exercised,
                    "elapsed_ms": elapsed_ms,
                    "request": request_body,
                    "response_excerpt": {
                        key: (payload or {}).get(key)
                        for key in ("avoided_segments", "route", "reason", "vehicle_limit_cm")
                    },
                }
            )
            log(
                f"{label}: HTTP {status} code={code} expected={expected} "
                f"-> {'MATCH' if branch_ok else 'MISMATCH'} | {exercised}"
            )
        (out_dir / "route_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

        health_after = http_json(f"{routing_url}/health")[1]
        (out_dir / "health_after.json").write_text(
            json.dumps(health_after, indent=2, sort_keys=True), encoding="utf-8"
        )

        verdict = "PASS" if all_matched else "FAIL"
        (out_dir / "summary.json").write_text(
            json.dumps(
                {
                    "unit": "M2-route-around-smoke",
                    "verdict": verdict,
                    "run_dir": str(run_dir.relative_to(REPO_ROOT)),
                    "depth_product": str(product_csv.relative_to(REPO_ROOT)),
                    "gate_report": gate_mode,
                    "realized_gate_operational": gate_operational,
                    "realized_forecast_lead_minutes": realized_lead,
                    "classes_expected_vs_exercised": [
                        {
                            key: item[key]
                            for key in (
                                "probe",
                                "expected_under_realized_gate",
                                "branch_matched_expectation",
                                "which_branch_exercised",
                            )
                        }
                        for item in results
                    ],
                    "pair_choice": pair_choice,
                    "launcher_banner": launcher_banner,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        detail = "all branches matched realized gate state" if all_matched else "branch mismatch"
        log(f"VERDICT {verdict} ({detail})")
        return 0 if all_matched else 1
    finally:
        stop(processes)
        api_log.close()
        dash_log.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    raise SystemExit(main())
