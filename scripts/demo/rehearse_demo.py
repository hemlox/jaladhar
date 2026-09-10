#!/usr/bin/env python3
"Run the CPU-only demo rehearsal against both product services. The rehearsal checks the realized run before starting children, probes the dashboard and API, sends one route request using persisted road geometry, and rejects numeric JSON values without a resolvable source manifest. It does not claim browser-pixel coverage; the dashboard HTML and configured data endpoint are the observable surface available without a browser dependency. The default route request uses endpoints read from the persisted road centreline."  # noqa: E501

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from _demo_common import (
    DEFAULT_API_COMMAND,
    DEFAULT_DASHBOARD_COMMAND,
    REPO_ROOT,
    DemoError,
    RunningService,
    depth_product_file_for,
    http_request,
    parse_json_response,
    resolve_command,
    start_service,
    stop_services,
    validate_run_dir,
    verify_dashboard_state_provenance,
    verify_json_number_provenance,
    verify_manifest_reference,
    verify_route_response_provenance,
    wait_for_http,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _url(host: str, port: int, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def _route_payload(
    rows: tuple[dict[str, Any], ...],
    vehicle_class: str,
    override: str | None,
    *,
    road_file: Path,
    max_snap_distance_m: float,
) -> Any:
    if override is not None:
        try:
            payload = json.loads(override)
        except json.JSONDecodeError as exc:
            raise DemoError(f"--route-request is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise DemoError("--route-request must be a JSON object")
        return payload
    try:
        import geopandas as gpd

        roads = gpd.read_file(road_file)
    except Exception as exc:
        raise DemoError(
            f"cannot derive a route request from realized road geometry {road_file}: {exc}; "
            "pass --route-request to use a known presentation pair"
        ) from exc
    if roads.empty or roads.geometry.is_empty.all():
        raise DemoError(
            f"realized road geometry is empty: {road_file}; pass --route-request explicitly"
        )

    def endpoint(geometry: Any, *, first: bool) -> tuple[float, float] | None:
        if geometry is None or geometry.is_empty:
            return None
        if geometry.geom_type == "LineString":
            points = list(geometry.coords)
            if points:
                point = points[0] if first else points[-1]
                return float(point[0]), float(point[1])
        if geometry.geom_type == "MultiLineString":
            parts = list(geometry.geoms)
            if parts:
                return endpoint(parts[0] if first else parts[-1], first=first)
        return None

    origin = next((endpoint(geometry, first=True) for geometry in roads.geometry), None)
    destination = next(
        (endpoint(geometry, first=False) for geometry in reversed(roads.geometry)), None
    )
    if origin is None or destination is None:
        raise DemoError(
            f"realized road geometry has no line endpoints: {road_file}; "
            "pass --route-request explicitly"
        )
    crs = str(roads.crs) if roads.crs is not None else "EPSG:32643"
    return {
        "origin": {"coordinates": list(origin), "crs": crs},
        "destination": {"coordinates": list(destination), "crs": crs},
        "vehicle_class": vehicle_class,
        "forecast_lead_minutes": rows[0]["forecast_lead_minutes"],
        "max_snap_distance_m": max_snap_distance_m,
    }


@app.command()
def main(
    run_dir: Path = typer.Option(..., "--run-dir", help="Completed runs/<run_id> to rehearse"),
    host: str = typer.Option("127.0.0.1", help="Bind address for both child services"),
    dashboard_port: int = typer.Option(8501, help="Dashboard TCP port"),
    api_port: int = typer.Option(8502, help="Routing API TCP port"),
    dashboard_command: str = typer.Option(
        DEFAULT_DASHBOARD_COMMAND,
        help="Dashboard argv template; placeholders: {python} {repo_root} {run_dir} {host} {port}",
    ),
    api_command: str = typer.Option(
        DEFAULT_API_COMMAND,
        help="Routing API argv template; placeholders: {python} {repo_root} {run_dir} "
        "{host} {port}",
    ),
    dashboard_path: str = typer.Option("/", help="Dashboard HTML endpoint"),
    dashboard_state_path: str = typer.Option(
        "/api/state", help="Dashboard catalog endpoint whose manifest reference is checked"
    ),
    dashboard_data_path: str = typer.Option(
        "/api/segments?segment_id=1",
        help="Bounded dashboard segment endpoint whose numeric payload is checked",
    ),
    api_health_path: str = typer.Option("/health", help="Routing API health endpoint"),
    api_policies_path: str = typer.Option(
        "/policies", help="Routing API cited vehicle-policy endpoint"
    ),
    api_route_path: str = typer.Option("/route", help="Routing API route endpoint"),
    route_method: str = typer.Option("POST", help="HTTP method for the route endpoint"),
    route_request: str | None = typer.Option(
        None, help="JSON route request override; default uses persisted road endpoints"
    ),
    vehicle_class: str = typer.Option(
        "emergency", help="Vehicle class for the derived route request"
    ),
    road_file: Path = typer.Option(
        REPO_ROOT / "data/interim/terrain/roads_centrelines.gpkg",
        help="Realized road geometry used to derive the default route request",
    ),
    max_snap_distance_m: float = typer.Option(
        ...,
        help=(
            "Owner-supplied maximum coordinate-to-graph snap distance in metres; "
            "no default is invented"
        ),
    ),
    startup_timeout_s: float = typer.Option(
        120.0,
        help=(
            "Seconds to wait for each service; the realized full-scale product twins take "
            "a measured ~26 s to validate on this repository's CPU-only check"
        ),
    ),
    request_timeout_s: float = typer.Option(5.0, help="Seconds per endpoint request"),
) -> None:
    services: list[RunningService] = []
    try:
        if dashboard_port == api_port:
            raise DemoError("dashboard and API ports must be distinct")
        evidence = validate_run_dir(run_dir)
        if evidence.kind == "frame_series":
            raise DemoError(
                "rehearsal verifies flat event-maximum rows against route/state payloads; "
                "a frame-series run has no single row set to verify -- rehearse its "
                "flat fallback or extend the rehearsal for series mode explicitly"
            )
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
            depth_product_file=depth_product_file_for(evidence),
        )
        if evidence.gate_report is not None:
            dashboard_argv.extend(("--gate-report", str(evidence.gate_report)))
            api_argv.extend(("--gate-report-file", str(evidence.gate_report)))
        api_argv.extend(("--server-max-snap-distance-m", str(max_snap_distance_m)))
        services.append(start_service("dashboard", dashboard_argv))
        services.append(start_service("routing-api", api_argv))
        typer.echo("REHEARSAL_CPU_ONLY=1 CUDA_VISIBLE_DEVICES='' JALADHAR_DEVICE=cpu")
        typer.echo(f"REHEARSAL_RUN_DIR={evidence.run_dir}")
        typer.echo(f"REHEARSAL_MANIFEST={evidence.manifest_relative_path}")
        typer.echo(f"REHEARSAL_PRODUCT_LABEL={evidence.manifest.get('product_label')}")

        dashboard_url = _url(host, dashboard_port, dashboard_path)
        dashboard = wait_for_http(services[0], dashboard_url, timeout=startup_timeout_s)
        if not 200 <= dashboard.status < 400:
            raise DemoError(f"dashboard {dashboard_url} returned HTTP {dashboard.status}")
        typer.echo(f"PASS dashboard status={dashboard.status}")

        dashboard_state_url = _url(host, dashboard_port, dashboard_state_path)
        dashboard_state_result = http_request(dashboard_state_url, timeout=request_timeout_s)
        if not 200 <= dashboard_state_result.status < 400:
            raise DemoError(
                f"dashboard state {dashboard_state_url} returned HTTP "
                f"{dashboard_state_result.status}"
            )
        dashboard_state_payload = parse_json_response(dashboard_state_result, dashboard_state_url)
        verify_manifest_reference(
            dashboard_state_result.body,
            manifest_relative_paths=(
                evidence.manifest_relative_path,
                *evidence.source_manifest_relative_paths,
            ),
            endpoint=dashboard_state_url,
        )
        dashboard_state_numbers = verify_dashboard_state_provenance(
            dashboard_state_payload,
            repo_root=REPO_ROOT,
            expected_manifests=(evidence.manifest_path, *evidence.source_manifest_paths),
        )
        typer.echo(
            f"PASS dashboard_state status={dashboard_state_result.status} "
            "manifest_reference=1"
            f" numeric_values={dashboard_state_numbers}"
        )

        dashboard_data_url = _url(host, dashboard_port, dashboard_data_path)
        dashboard_data_result = http_request(dashboard_data_url, timeout=request_timeout_s)
        if not 200 <= dashboard_data_result.status < 400:
            raise DemoError(
                f"dashboard data {dashboard_data_url} returned HTTP {dashboard_data_result.status}"
            )
        dashboard_payload = parse_json_response(dashboard_data_result, dashboard_data_url)
        dashboard_numbers = verify_json_number_provenance(
            dashboard_payload,
            repo_root=REPO_ROOT,
            expected_manifests=(evidence.manifest_path, *evidence.source_manifest_paths),
        )
        typer.echo(
            f"PASS dashboard_data status={dashboard_data_result.status} "
            f"numeric_values={dashboard_numbers}"
        )

        health_url = _url(host, api_port, api_health_path)
        health = wait_for_http(services[1], health_url, timeout=startup_timeout_s)
        health_payload = parse_json_response(health, health_url)
        if health_payload.get("status") != "ready":
            raise DemoError(
                f"API health is not ready: status={health_payload.get('status')!r}; "
                "the API must fail closed until its product and cited policy are loaded"
            )
        typer.echo(f"PASS api_health status={health.status} state=ready")

        policies_url = _url(host, api_port, api_policies_path)
        policies_result = http_request(policies_url, timeout=request_timeout_s)
        if not 200 <= policies_result.status < 400:
            raise DemoError(f"API policies {policies_url} returned HTTP {policies_result.status}")
        policies_payload = parse_json_response(policies_result, policies_url)
        typer.echo(
            f"PASS api_policies status={policies_result.status} "
            f"state={policies_payload.get('status')!r}"
        )

        request_payload = _route_payload(
            evidence.rows,
            vehicle_class,
            route_request,
            road_file=road_file,
            max_snap_distance_m=max_snap_distance_m,
        )
        route_url = _url(host, api_port, api_route_path)
        route_result = http_request(
            route_url, method=route_method, payload=request_payload, timeout=request_timeout_s
        )
        if not 200 <= route_result.status < 400:
            raise DemoError(f"API route {route_url} returned HTTP {route_result.status}")
        route_payload = parse_json_response(route_result, route_url)
        route_numbers = verify_route_response_provenance(
            route_payload,
            repo_root=REPO_ROOT,
            expected_manifest=evidence.manifest_path,
            expected_products=(evidence.product_csv, evidence.product_json),
            expected_rows=evidence.rows,
        )
        typer.echo(f"PASS api_route status={route_result.status} numeric_values={route_numbers}")
        typer.echo("REHEARSAL_PASS=1 scope=service_startup+HTTP+JSON_manifest_provenance")
        typer.echo(
            "REHEARSAL_NOTE=browser_pixel_scope_not_verified; dashboard data endpoint was checked"
        )
    except (DemoError, OSError) as exc:
        typer.echo(f"REHEARSAL_FAIL={exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        stop_services(services)


if __name__ == "__main__":
    app()
