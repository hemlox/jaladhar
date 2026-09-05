#!/usr/bin/env python3
"""Bring up the already-built dashboard and routing API for one realized run.

The launcher never generates a product and never invokes the solver.  It performs
the frozen depth-product checks, starts both child services with an explicit CPU
environment, probes their readiness endpoints, and keeps them alive until Ctrl-C.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import typer
import yaml
from _demo_common import (
    DEFAULT_API_COMMAND,
    DEFAULT_DASHBOARD_COMMAND,
    REPO_ROOT,
    DemoError,
    RunningService,
    depth_product_file_for,
    discover_fallback,
    parse_json_response,
    resolve_command,
    start_service,
    stop_services,
    validate_run_dir,
    wait_for_http,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)

ENV_ROUTING_PREFIX = "JALADHAR_ROUTING_"
DEFAULT_ROUTING_CONFIG = Path("configs/routing.yaml")


def append_snap_cap(argv: list[str], cap_m: float | None) -> list[str]:
    """Append the owner-supplied server snap cap to a routing-API argv (None = absent)."""

    if cap_m is None:
        return list(argv)
    return [*argv, "--server-max-snap-distance-m", str(cap_m)]


def append_vehicle_policy(argv: list[str], policy_file: Path | None) -> list[str]:
    """Append the cited vehicle-policy file to a routing-API argv (None = absent)."""

    if policy_file is None:
        return list(argv)
    return [*argv, "--vehicle-policy-file", str(policy_file)]


def append_cors_origin(argv: list[str], cors_origin: str | None) -> list[str]:
    """Append the demo browser origin to a routing-API argv (None = header absent)."""

    if cors_origin is None:
        return list(argv)
    return [*argv, "--cors-origin", str(cors_origin)]


def append_gate_report_file(argv: list[str], gate_report_file: Path | None) -> list[str]:
    """Append the config-bound scientific-gate report to a routing-API argv (None = absent)."""

    if gate_report_file is None:
        return list(argv)
    return [*argv, "--gate-report-file", str(gate_report_file)]


def _routing_env(env: Mapping[str, str], key: str) -> str | None:
    return env.get(ENV_ROUTING_PREFIX + key)


def resolve_routing_settings(
    *,
    flag_policy_file: Path | None = None,
    flag_snap_cap_m: float | None = None,
    flag_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    """Resolve vehicle policy + snap cap from flag > env ``JALADHAR_ROUTING_*`` > YAML.

    Precedence per key is explicit-flag first, then the environment, then the
    routing config file.  The config file itself resolves as: explicit
    ``--routing-config`` (must exist) > ``$JALADHAR_ROUTING_CONFIG`` (must
    exist) > the repo default ``configs/routing.yaml`` when present.  A missing
    default file means the feature is simply not configured — never invented.
    All resolution problems are aggregated into one error (repo rule 7).
    """

    environment = os.environ if env is None else env
    problems: list[str] = []

    config_source = "unset"
    config_path: Path | None = None
    if flag_config_path is not None:
        candidate = flag_config_path
        if not candidate.is_file():
            problems.append(f"--routing-config file does not exist: {candidate}")
        else:
            config_path, config_source = candidate, "flag"
    else:
        env_config = _routing_env(environment, "CONFIG")
        if env_config:
            candidate = Path(env_config)
            if not candidate.is_absolute():
                candidate = repo_root / candidate
            if not candidate.is_file():
                problems.append(f"$JALADHAR_ROUTING_CONFIG file does not exist: {env_config}")
            else:
                config_path, config_source = candidate, "env"
        elif (repo_root / DEFAULT_ROUTING_CONFIG).is_file():
            config_path, config_source = repo_root / DEFAULT_ROUTING_CONFIG, "default"

    yaml_policy: str | None = None
    yaml_snap_raw: str | None = None
    yaml_gate_report: str | None = None
    yaml_depth_override: str | None = None
    if config_path is not None and config_path.is_file():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"routing config {config_path} could not be parsed: {exc}")
            payload = None
        if payload is not None and not isinstance(payload, dict):
            problems.append(f"routing config {config_path} must be a YAML mapping")
        elif isinstance(payload, dict):
            value = payload.get("vehicle_policy_path")
            if value is not None and isinstance(value, str) and value.strip():
                yaml_policy = value.strip()
            value = payload.get("server_max_snap_distance_m")
            if (
                value is not None
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                yaml_snap_raw = str(value)
            value = payload.get("gate_report_path")
            if value is not None and isinstance(value, str) and value.strip():
                yaml_gate_report = value.strip()
            value = payload.get("depth_product_override_path")
            if value is not None and isinstance(value, str) and value.strip():
                yaml_depth_override = value.strip()
        snap_basis = payload.get("server_max_snap_distance_basis")
        if snap_basis is not None and not isinstance(snap_basis, str):
            problems.append(
                "routing config server_max_snap_distance_basis must be a string"
            )

    policy_file: Path | None = flag_policy_file
    policy_source = "flag" if policy_file is not None else "unset"
    if policy_file is None:
        env_policy = _routing_env(environment, "VEHICLE_POLICY_FILE")
        if env_policy:
            policy_file, policy_source = Path(env_policy), "env"
        elif yaml_policy:
            policy_file, policy_source = repo_root / yaml_policy, "config"

    snap_cap_m: float | None = flag_snap_cap_m
    snap_source = "flag" if snap_cap_m is not None else "unset"
    if snap_cap_m is None:
        env_snap = _routing_env(environment, "SERVER_MAX_SNAP_DISTANCE_M")
        if env_snap:
            try:
                snap_cap_m, snap_source = float(env_snap), "env"
            except ValueError:
                problems.append(
                    f"$JALADHAR_ROUTING_SERVER_MAX_SNAP_DISTANCE_M is not a number: {env_snap!r}"
                )
        elif yaml_snap_raw:
            try:
                snap_cap_m, snap_source = float(yaml_snap_raw), "config"
            except ValueError:
                problems.append(
                    f"routing config server_max_snap_distance_m is not a number: {yaml_snap_raw!r}"
                )

    gate_report_file: Path | None = None
    gate_report_source = "unset"
    if yaml_gate_report:
        candidate = Path(yaml_gate_report)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        if candidate.is_file():
            gate_report_file, gate_report_source = candidate, "config"
        else:
            problems.append(
                f"routing config gate_report_path file does not exist: {yaml_gate_report}"
            )

    depth_override_file: Path | None = None
    depth_override_source = "unset"
    if yaml_depth_override:
        candidate = Path(yaml_depth_override)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        if candidate.is_file():
            depth_override_file, depth_override_source = candidate, "config"
        else:
            problems.append(
                f"routing config depth_product_override_path file does not exist: "
                f"{yaml_depth_override}"
            )

    if problems:
        raise DemoError("routing settings resolution failed:\n- " + "\n- ".join(problems))
    return {
        "vehicle_policy_file": policy_file,
        "vehicle_policy_source": policy_source,
        "server_max_snap_distance_m": snap_cap_m,
        "snap_source": snap_source,
        "snap_basis": (snap_basis.strip() if isinstance(snap_basis, str) else None),
        "gate_report_file": gate_report_file,
        "gate_report_source": gate_report_source,
        "depth_override_file": depth_override_file,
        "depth_override_source": depth_override_source,
        "config_path": config_path,
        "config_source": config_source,
    }


def _routing_state_lines(payload: object) -> list[str]:
    """Derive honest ROUTING_STATE lines from the API's realized /health payload.

    The wading-threshold policy is owner-adjudicated; this launcher never
    invents a value and never fakes readiness.  Every line here is read from
    what the API itself reports.
    """

    if not isinstance(payload, dict):
        return ["ROUTING_STATE=unknown reason=health payload was not an object"]
    snap = payload.get("snap_policy") if isinstance(payload.get("snap_policy"), dict) else {}
    policies = (
        payload.get("vehicle_policies") if isinstance(payload.get("vehicle_policies"), dict) else {}
    )
    product = payload.get("depth_product") if isinstance(payload.get("depth_product"), dict) else {}
    scientific = (
        payload.get("scientific_readiness")
        if isinstance(payload.get("scientific_readiness"), dict)
        else {}
    )
    blockers: list[str] = []
    if snap.get("status") != "available":
        blockers.append(f"snap_policy={snap.get('status')}")
    if policies.get("status") != "available":
        blockers.append(f"vehicle_policy={policies.get('status')}")
    if product.get("status") != "available":
        blockers.append(f"depth_product={product.get('status')}")
    if scientific.get("operational_ready") is not True:
        blockers.append(f"scientific_gate={scientific.get('status')}")
    if payload.get("status") == "ready":
        state = "ROUTING_STATE=ready"
    else:
        # The headline derives from the payload's OVERALL status only; the
        # per-gate detail stays on ROUTING_BLOCKERS. A not_ready payload is
        # never headlined with one gate's status string.
        state = "ROUTING_STATE=not_ready"
    lines = [state, "ROUTING_BLOCKERS=" + (",".join(blockers) or "none")]
    if blockers:
        lines.append(
            "ROUTING_REASON=demo proceeds with the dashboard as presentation surface; "
            "routing awaits owner adjudication: " + ", ".join(blockers)
        )
    return lines


def _url(host: str, port: int, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def _require_ready_api_health(payload: object) -> None:
    """Reject HTTP-success responses whose realized API state is not ready."""

    health_status = payload.get("status") if isinstance(payload, dict) else None
    if health_status != "ready":
        raise DemoError(
            f"routing API answered HTTP successfully but is not ready: status={health_status!r}"
        )


def _require_ready_dashboard_state(payload: object) -> None:
    """Reject HTTP-success responses whose realized dashboard state is not ready."""

    state = payload.get("status") if isinstance(payload, dict) else None
    if state != "ready":
        raise DemoError(f"dashboard answered HTTP successfully but is not ready: status={state!r}")


@app.command()
def main(
    run_dir: Path | None = typer.Option(
        None, "--run-dir", help="Completed runs/<run_id> containing the frozen depth product"
    ),
    fallback: bool = typer.Option(
        False, "--fallback", help="Use an existing valid product run; never create a fallback"
    ),
    host: str = typer.Option("127.0.0.1", help="Bind address for both child services"),
    dashboard_port: int = typer.Option(8501, help="Dashboard TCP port"),
    api_port: int = typer.Option(8502, help="Routing API TCP port"),
    dashboard_path: str = typer.Option("/api/state", help="Dashboard JSON readiness path"),
    api_health_path: str = typer.Option("/health", help="Routing API readiness path"),
    dashboard_command: str = typer.Option(
        DEFAULT_DASHBOARD_COMMAND,
        help="Dashboard argv template; placeholders: {python} {repo_root} {run_dir} {host} {port}",
    ),
    api_command: str = typer.Option(
        DEFAULT_API_COMMAND,
        help="Routing API argv template; placeholders: {python} {repo_root} {run_dir} "
        "{host} {port}",
    ),
    startup_timeout_s: float = typer.Option(
        120.0,
        help=(
            "Seconds to wait for each readiness endpoint; the realized full-scale product "
            "twins take a measured ~26 s to validate on this repository's CPU-only check"
        ),
    ),
    startup_only: bool = typer.Option(
        False, help="Start and probe both services, then stop; useful for a mechanical smoke"
    ),
    server_max_snap_distance_m: float | None = typer.Option(
        None,
        help=(
            "Owner-supplied server-side maximum graph-snap distance passed to the routing "
            "API; without it the API refuses every route (fail-closed owner gate). "
            "Resolution order: this flag > $JALADHAR_ROUTING_SERVER_MAX_SNAP_DISTANCE_M > "
            "routing config file."
        ),
    ),
    vehicle_policy_file: Path | None = typer.Option(
        None,
        help=(
            "Cited vehicle-wading policy JSON passed to the routing API. Resolution order: "
            "this flag > $JALADHAR_ROUTING_VEHICLE_POLICY_FILE > routing config "
            "vehicle_policy_path."
        ),
    ),
    routing_config: Path | None = typer.Option(
        None,
        help=(
            "Routing YAML (keys vehicle_policy_path, server_max_snap_distance_m). Defaults "
            f"to {DEFAULT_ROUTING_CONFIG} when present; explicit paths must exist."
        ),
    ),
    cors_origin: str | None = typer.Option(
        None,
        help=(
            "Browser origin allowed to call the routing API cross-origin (e.g. "
            "http://127.0.0.1:8501). Default None emits no CORS header at all."
        ),
    ),
) -> None:
    """Start dashboard + routing API against a realized, manifest-backed run."""
    services: list[RunningService] = []
    try:
        if fallback and run_dir is not None:
            raise DemoError("choose either --run-dir or --fallback, not both")
        if dashboard_port == api_port:
            raise DemoError("dashboard and API ports must be distinct")
        if not (1 <= dashboard_port <= 65535 and 1 <= api_port <= 65535):
            raise DemoError("dashboard and API ports must be in 1..65535")
        if startup_timeout_s <= 0:
            raise DemoError("startup timeout must be positive")
        routing = resolve_routing_settings(
            flag_policy_file=vehicle_policy_file,
            flag_snap_cap_m=server_max_snap_distance_m,
            flag_config_path=routing_config,
            env=os.environ,
            repo_root=REPO_ROOT,
        )
        resolved_policy = routing["vehicle_policy_file"]
        assert resolved_policy is None or isinstance(resolved_policy, Path)
        resolved_snap = routing["server_max_snap_distance_m"]
        assert resolved_snap is None or isinstance(resolved_snap, (int, float))
        resolved_gate_report = routing["gate_report_file"]
        assert resolved_gate_report is None or isinstance(resolved_gate_report, Path)

        evidence = discover_fallback() if fallback else None
        if evidence is None and run_dir is None:
            # Unified cold discovery (same truth as the dashboard): newest
            # valid frame series first, then the designated flat fallback.
            evidence = discover_fallback()
            typer.echo("DISCOVERY=auto frame-series-preferred-then-flat-fallback")
        if evidence is None:
            if run_dir is None:
                raise DemoError("--run-dir is required unless --fallback is selected")
            evidence = validate_run_dir(run_dir)
        assert evidence is not None

        typer.echo("CPU_ONLY=1 CUDA_VISIBLE_DEVICES='' JALADHAR_DEVICE=cpu")
        typer.echo(f"RUN_DIR={evidence.run_dir}")
        typer.echo(f"RUN_MANIFEST={evidence.manifest_relative_path}")
        typer.echo(f"PRODUCT_LABEL={evidence.manifest.get('product_label')}")
        if evidence.kind == "frame_series":
            typer.echo(
                f"SERIES_FRAMES={evidence.expected_count} "
                f"(cadence {evidence.manifest.get('cadence_seconds')}s, manifest-backed)"
            )
            typer.echo(
                f"FRAME_ZERO={evidence.product_csv.relative_to(REPO_ROOT)} "
                "(anchors product references; per-frame SHA checks run in the dashboard)"
            )
        else:
            typer.echo(f"PRODUCT_ROWS={evidence.expected_count} (manifest-backed)")
        typer.echo(f"DASHBOARD_COMMAND_TEMPLATE={dashboard_command}")
        typer.echo(f"API_COMMAND_TEMPLATE={api_command}")

        dashboard_argv = resolve_command(
            dashboard_command,
            python=sys.executable,
            run_dir=evidence.run_dir,
            host=host,
            port=dashboard_port,
            repo_root=REPO_ROOT,
        )
        api_argv = resolve_command(
            api_command,
            python=sys.executable,
            run_dir=evidence.run_dir,
            host=host,
            port=api_port,
            repo_root=REPO_ROOT,
            depth_product_file=(
                routing.get("depth_override_file")
                or depth_product_file_for(evidence)
            ),
        )
        api_argv = append_snap_cap(api_argv, resolved_snap)
        api_argv = append_vehicle_policy(api_argv, resolved_policy)
        api_argv = append_cors_origin(api_argv, cors_origin)
        # Config-bound gate report is appended now and DEDUPED below: a
        # product-bound report discovered from the run wins (it binds the exact
        # served bytes), so never pass --gate-report-file twice.
        if evidence is not None and evidence.gate_report is None:
            api_argv = append_gate_report_file(api_argv, resolved_gate_report)
        if routing["config_source"] != "unset":
            config_path = routing["config_path"]
            shown = (
                config_path.relative_to(REPO_ROOT) if isinstance(config_path, Path) else config_path
            )
            typer.echo(f"ROUTING_CONFIG={shown} (source={routing['config_source']})")
        if resolved_policy is not None:
            typer.echo(
                f"VEHICLE_POLICY_FILE={resolved_policy} (source={routing['vehicle_policy_source']})"
            )
        else:
            typer.echo("VEHICLE_POLICY_FILE=UNAVAILABLE (no flag/env/config value)")
        if resolved_snap is not None:
            typer.echo(f"SERVER_MAX_SNAP_DISTANCE_M={resolved_snap}")
            snap_basis = routing.get("snap_basis")
            if snap_basis:
                typer.echo(f"SNAP_BASIS={snap_basis}")
        else:
            typer.echo("SERVER_MAX_SNAP_DISTANCE_M=UNAVAILABLE_OWNER_GATE")
        typer.echo(f"CORS_ORIGIN={'OFF' if cors_origin is None else cors_origin}")
        if evidence.gate_report is not None:
            dashboard_argv.extend(("--gate-report", str(evidence.gate_report)))
            api_argv.extend(("--gate-report-file", str(evidence.gate_report)))
            typer.echo(
                f"GATE_REPORT={evidence.gate_report.relative_to(REPO_ROOT)} "
                "(source=product-bound)"
            )
        elif resolved_gate_report is not None:
            # Already appended to the API argv above; the dashboard keeps its own
            # product-bound binding and is deliberately not handed this path.
            typer.echo(
                f"GATE_REPORT={resolved_gate_report.relative_to(REPO_ROOT)} "
                f"(source={routing['gate_report_source']}; API only)"
            )
        else:
            typer.echo("GATE_REPORT=UNAVAILABLE")
        services.append(start_service("dashboard", dashboard_argv))
        services.append(start_service("routing-api", api_argv))
        dashboard_result = wait_for_http(
            services[0], _url(host, dashboard_port, dashboard_path), timeout=startup_timeout_s
        )
        dashboard_state = parse_json_response(
            dashboard_result, _url(host, dashboard_port, dashboard_path)
        )
        _require_ready_dashboard_state(dashboard_state)
        typer.echo(
            f"DASHBOARD_READY status={dashboard_result.status} "
            f"url={_url(host, dashboard_port, dashboard_path)}"
        )
        # Degraded-routing start: the routing API's wading threshold is an
        # owner-adjudicated policy and its fail-closed gate is honest state,
        # not a launcher failure.  The API process keeps serving /health with
        # that gate status; only a DASHBOARD failure blocks the demo.
        api_health: object | None = None
        api_result = None
        try:
            api_result = wait_for_http(
                services[1], _url(host, api_port, api_health_path), timeout=startup_timeout_s
            )
            api_health = parse_json_response(api_result, _url(host, api_port, api_health_path))
        except DemoError as exc:
            typer.echo(f"ROUTING_STATE=unreachable reason={exc}", err=True)
        if isinstance(api_health, dict):
            for line in _routing_state_lines(api_health):
                typer.echo(line)
            if api_health.get("status") == "ready" and api_result is not None:
                _require_ready_api_health(api_health)
                typer.echo(
                    f"API_READY status={api_result.status} state=ready "
                    f"url={_url(host, api_port, api_health_path)}"
                )
            else:
                typer.echo(
                    f"API_SERVING_DEGRADED url={_url(host, api_port, api_health_path)} "
                    "(fail-closed owner gate; /health carries the realized reasons)"
                )
        typer.echo(f"DASHBOARD_LOG={services[0].log_path}")
        typer.echo(f"API_LOG={services[1].log_path}")
        if startup_only:
            typer.echo("STARTUP_ONLY=1 services probed and will now stop")
            return
        typer.echo("DEMO_READY=1 press Ctrl-C to stop both services")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        typer.echo("\nDEMO_STOP=keyboard_interrupt")
    except (DemoError, OSError) as exc:
        typer.echo(f"DEMO_BLOCKED={exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        stop_services(services)


if __name__ == "__main__":
    app()
