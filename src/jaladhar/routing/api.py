'''* ``product_age_seconds`` is derived from producer issue time. Physical routing
lead remains unavailable until a realized arrival-time series exists.
No route is returned until both a contract-valid realized depth product and a
service reports ``operational_routing_ready=false`` on ``/health`` and EVERY
``/route`` request is refused with 503 ``scientific_gate_not_accepted``
regardless of how many wading classes or snap configurations are unblocked.
visibly unblocked; routing execution awaits the scientific gate."'''

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import typer

from jaladhar.routing.graph import REPO, RoadGraphError, RoadNetwork, SnappedPoint
from jaladhar.routing.graph import (
    _resolve_routing_graph_config as _resolve_routing_graph_config_from_graph,
)
from jaladhar.routing.policy import VehiclePolicyCatalog, VehiclePolicyError, load_vehicle_policies
from jaladhar.routing.product import DepthProduct, DepthProductError, load_depth_product
from jaladhar.validation.depth_product_contract import MAX_FORECAST_LEAD_MINUTES, sha256_file

app = typer.Typer(add_completion=False)


def _runtime_source_provenance(repo_root: Path) -> dict[str, Any]:
    """Expose current source bytes and dirty state without claiming reproducibility."""

    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).rstrip("\r\n")
        dirty_paths = [line[3:] for line in porcelain.splitlines() if len(line) >= 4]
    except (OSError, subprocess.CalledProcessError):
        git_sha = None
        dirty_paths = []
    source_files = {}
    for name in ("api.py", "graph.py", "policy.py", "product.py"):
        path = Path(__file__).resolve().parent / name
        try:
            label = path.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            label = str(path)
        source_files[label] = sha256_file(path)
    return {
        "git_sha": git_sha,
        "git_tree_clean": git_sha is not None and not dirty_paths,
        "git_dirty_paths": dirty_paths,
        "source_files_sha256": source_files,
    }


def _resolve_routing_graph_config_for_service(
    explicit_tolerance: float | None = None,
    explicit_splitting: bool | None = None,
    config_path: Path | None = None,
) -> dict[str, object]:
    # validation single-sourced.
    if explicit_tolerance is not None or explicit_splitting is not None:
        problems: list[str] = []
        if explicit_tolerance is not None:
            import math

            if (
                isinstance(explicit_tolerance, bool)
                or not isinstance(explicit_tolerance, (int, float))
                or not math.isfinite(float(explicit_tolerance))
                or float(explicit_tolerance) < 0
            ):
                problems.append(
                    f"routing_graph.endpoint_snap_tolerance_m must be finite >=0 or null, got {explicit_tolerance!r}"  # noqa: E501
                )
        if explicit_splitting is not None and not isinstance(explicit_splitting, bool):
            problems.append(
                f"routing_graph.enable_intersection_splitting must be boolean, got {explicit_splitting!r}"  # noqa: E501
            )
        if problems:
            raise ValueError(
                "config resolution failed with "
                + str(len(problems))
                + " problem(s):\n"
                + "\n".join(f"- {p}" for p in problems)
            )
        if explicit_tolerance is None or explicit_splitting is None:
            try:
                cfg = _resolve_routing_graph_config_from_graph(config_path)
            except ValueError:
                raise
            tol = (
                explicit_tolerance
                if explicit_tolerance is not None
                else cfg.get("endpoint_snap_tolerance_m")
            )
            split = (
                explicit_splitting
                if explicit_splitting is not None
                else cfg.get("enable_intersection_splitting", False)
            )
            if tol is not None and float(tol) == 0.0:
                tol = None
            return {"endpoint_snap_tolerance_m": tol, "enable_intersection_splitting": bool(split)}
        tol = float(explicit_tolerance) if explicit_tolerance is not None else None
        if tol is not None and tol == 0.0:
            tol = None
        return {
            "endpoint_snap_tolerance_m": tol,
            "enable_intersection_splitting": bool(explicit_splitting),
        }
    # No explicit override: delegate fully to graph resolver (single source)
    return _resolve_routing_graph_config_from_graph(config_path)


def _scientific_gate_from_report(
    gate_report_file: Path | None,
    product: DepthProduct | None,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    if gate_report_file is None:
        return {
            "status": "unavailable",
            "operational_ready": False,
            "reason": "no product-bound scientific gate report configured",
        }
    try:
        path = gate_report_file.resolve()
        path.relative_to(repo_root.resolve())
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("g1"), dict):
            raise ValueError("gate report lacks g1 object")
        if product is None:
            raise ValueError("gate report cannot be bound without a depth product")
        expected_hash = payload.get("input_sha256", {}).get("depth_product")
        if expected_hash != sha256_file(product.source_path):
            raise ValueError("gate report depth-product hash differs from loaded bytes")
        g1_status = payload["g1"].get("status")
        signed_g3_status = payload.get("g3", {}).get("status")
        operational_ready = g1_status == "PASS" and signed_g3_status == "PASS"
        return {
            "status": "accepted" if operational_ready else "not_accepted",
            "operational_ready": operational_ready,
            "g1_status": g1_status,
            "signed_g3_status": signed_g3_status,
            "report_path": path.relative_to(repo_root.resolve()).as_posix(),
            "report_sha256": sha256_file(path),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "operational_ready": False,
            "reason": str(exc),
        }


class RoutingAPIError(RuntimeError):
    """An honest API-level refusal with an HTTP status and machine code."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "error": {"code": self.code, "detail": self.detail},
        }


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RoutingAPIError(400, "invalid_request", f"{field} must be an integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise RoutingAPIError(400, "invalid_request", f"{field} must be an integer") from exc
    if str(value).strip() != str(parsed):
        raise RoutingAPIError(400, "invalid_request", f"{field} must be an integer")
    return parsed


def _positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise RoutingAPIError(400, "invalid_request", f"{field} must be a positive number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RoutingAPIError(400, "invalid_request", f"{field} must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise RoutingAPIError(400, "invalid_request", f"{field} must be a positive number")
    return parsed


def _snap_dict(point: SnappedPoint) -> dict[str, Any]:
    return {
        "node_id": point.node_id,
        "x_m": point.x_m,
        "y_m": point.y_m,
        "distance_m": point.distance_m,
        "input_crs": point.input_crs,
    }


class RoutingService:

    def __init__(
        self,
        network: RoadNetwork,
        product: DepthProduct | None,
        product_error: str | None,
        policies: VehiclePolicyCatalog,
        repo_root: Path = REPO,
        *,
        server_max_snap_distance_m: float | None = None,
        scientific_readiness: dict[str, Any] | None = None,
    ) -> None:
        self.network = network
        self.product = product
        self.product_error = product_error
        self.policies = policies
        self.repo_root = repo_root.resolve()
        self.server_max_snap_distance_m = server_max_snap_distance_m
        self.scientific_readiness = scientific_readiness or {
            "status": "unavailable",
            "operational_ready": False,
            "reason": "no product-bound scientific gate report configured",
        }
        self.runtime_provenance = _runtime_source_provenance(self.repo_root)
        self._blocked_cache: dict[str, tuple[set[int], list[dict[str, Any]]]] = {}

    @classmethod
    def from_paths(
        cls,
        road_file: Path,
        lookup_file: Path,
        depth_product_file: Path | None,
        vehicle_policy_file: Path | None,
        gate_report_file: Path | None = None,
        server_max_snap_distance_m: float | None = None,
        repo_root: Path = REPO,
        endpoint_snap_tolerance_m: float | None = None,
        enable_intersection_splitting: bool | None = None,
        routing_config_path: Path | None = None,
    ) -> RoutingService:
        try:
            cfg = _resolve_routing_graph_config_for_service(
                explicit_tolerance=endpoint_snap_tolerance_m,
                explicit_splitting=enable_intersection_splitting,
                config_path=routing_config_path,
            )
        except ValueError as exc:
            raise RoadGraphError(str(exc)) from exc
        resolved_tolerance = cfg.get("endpoint_snap_tolerance_m")
        resolved_splitting = bool(cfg.get("enable_intersection_splitting", False))
        network = RoadNetwork.from_files(
            road_file,
            lookup_file,
            endpoint_snap_tolerance_m=resolved_tolerance,
            enable_intersection_splitting=resolved_splitting,
        )
        product: DepthProduct | None = None
        product_error: str | None = None
        if depth_product_file is None:
            product_error = (
                "no depth product path configured; the API will not invent flood state "
                "or return an unscored route"
            )
        else:
            try:
                product = load_depth_product(
                    depth_product_file,
                    repo_root=repo_root,
                    lookup_path=lookup_file,
                )
            except DepthProductError as exc:
                product_error = str(exc)
        policies = load_vehicle_policies(vehicle_policy_file, repo_root=repo_root)
        scientific_readiness = _scientific_gate_from_report(
            gate_report_file, product, repo_root=repo_root
        )
        return cls(
            network,
            product,
            product_error,
            policies,
            repo_root,
            server_max_snap_distance_m=server_max_snap_distance_m,
            scientific_readiness=scientific_readiness,
        )

    def _repo_relative(self, path: Path, field: str) -> str:
        try:
            return path.resolve().relative_to(self.repo_root).as_posix()
        except ValueError as exc:
            raise RoutingAPIError(
                503,
                "provenance_unavailable",
                f"{field} is outside the repository and cannot be exposed as traceable provenance",
            ) from exc

    def health(self) -> dict[str, Any]:
        """Return realized input state, including why routing is unavailable."""

        product_summary = (
            self.product.summary()
            if self.product is not None
            else {
                "status": "unavailable",
                "error": self.product_error,
            }
        )
        artifact_ready = self.product is not None
        snap_ready = (
            self.server_max_snap_distance_m is not None
            and math.isfinite(self.server_max_snap_distance_m)
            and self.server_max_snap_distance_m > 0.0
        )
        operational_ready = (
            artifact_ready
            and self.policies.available
            and snap_ready
            and self.scientific_readiness.get("operational_ready") is True
        )
        return {
            "status": "ready" if operational_ready else "not_ready",
            "artifact_readiness": "ready" if artifact_ready else "unavailable",
            "service_readiness": "serving_read_only",
            "operational_routing_ready": operational_ready,
            "cpu_only": True,
            "road_graph": self.network.summary(),
            "depth_product": product_summary,
            "vehicle_policies": self.policies.summary(),
            "snap_policy": {
                "status": "available" if snap_ready else "unavailable_owner_gate",
                "server_max_snap_distance_m": (
                    self.server_max_snap_distance_m if snap_ready else None
                ),
                "reason": (
                    None if snap_ready else "no owner-configured server-side maximum snap distance"
                ),
            },
            "scientific_readiness": self.scientific_readiness,
            "runtime_software_provenance": self.runtime_provenance,
            "contract_gaps": [
                (
                    "The frozen depth-product contract defines one product file per run but "
                    "does not define a multi-lead index; this process selects exactly one "
                    "configured product and rejects a different requested lead."
                ),
                (
                    "The realized OSM centreline artefact has no one-way, turn-restriction, "
                    "speed, or access fields; routing is undirected topology plus flood safety."
                ),
                (
                    "The raw OSM inventory has no node/edge graph; this service derives a "
                    "conservative endpoint graph from the persisted centreline geometry "
                    "with endpoint snapping at routing_graph.endpoint_snap_tolerance_m when "
                    "configured (2.0 m per configs/routing.yaml; null/absent => legacy exact match) "  # noqa: E501
                    "and without inventing intersections unless routing_graph.enable_intersection_splitting is true."  # noqa: E501
                ),
                (
                    "No verified published vehicle-wading threshold is present in the "
                    "repository; a cited policy file is required."
                ),
                (
                    "The frozen product contract does not define a freshness/expiry tolerance; "
                    "the API exposes product age and does not invent freshness or routing lead."
                ),
            ],
        }

    def _blocked_for_policy(
        self, vehicle_class: str, max_depth_cm: int
    ) -> tuple[set[int], list[dict[str, Any]]]:
        cache_key = f"{vehicle_class}:{max_depth_cm}"
        if cache_key in self._blocked_cache:
            return self._blocked_cache[cache_key]
        if self.product is None:
            raise RoutingAPIError(
                503, "depth_product_unavailable", self.product_error or "depth product unavailable"
            )
        blocked: set[int] = set()
        avoided: list[dict[str, Any]] = []
        for segment_id in sorted(self.product.rows):
            state = self.product.rows[segment_id]
            if state.flood_status == "unknown":
                blocked.add(segment_id)
                reason = "unknown_flood_state_no_data"
            elif state.band_high_cm >= max_depth_cm:
                blocked.add(segment_id)
                reason = "depth_band_high_cm_at_or_above_vehicle_limit"
            else:
                continue
            avoided.append(
                {
                    "segment_id": segment_id,
                    "flood_status": state.flood_status,
                    "confidence": state.confidence,
                    "depth_band_cm": [state.band_low_cm, state.band_high_cm],
                    "reason": reason,
                    "vehicle_class": vehicle_class,
                    "vehicle_limit_cm": max_depth_cm,
                }
            )
        result = (blocked, avoided)
        self._blocked_cache[cache_key] = result
        return result

    def _require_product_and_policy(
        self, vehicle_class: str
    ) -> tuple[DepthProduct, Any, set[int], list[dict[str, Any]]]:
        if self.product is None:
            raise RoutingAPIError(
                503,
                "depth_product_unavailable",
                self.product_error or "no realized depth product is loaded",
            )
        try:
            policy = self.policies.get(vehicle_class)
        except VehiclePolicyError as exc:
            raise RoutingAPIError(503, "vehicle_policy_unavailable", str(exc)) from exc
        blocked, avoided = self._blocked_for_policy(
            policy.vehicle_class, policy.max_impassable_depth_cm
        )
        return self.product, policy, blocked, avoided

    def route(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise RoutingAPIError(400, "invalid_request", "request body must be a JSON object")
        for field in (
            "origin",
            "destination",
            "vehicle_class",
            "forecast_lead_minutes",
            "max_snap_distance_m",
        ):
            if field not in request:
                raise RoutingAPIError(400, "invalid_request", f"missing required field {field!r}")
        vehicle_class = str(request["vehicle_class"]).strip().lower()
        if not vehicle_class:
            raise RoutingAPIError(400, "invalid_request", "vehicle_class must not be empty")
        forecast_lead = _integer(request["forecast_lead_minutes"], "forecast_lead_minutes")
        max_snap_distance_m = _positive_float(request["max_snap_distance_m"], "max_snap_distance_m")
        cap = self.server_max_snap_distance_m
        cap_usable = (
            not isinstance(cap, bool)
            and isinstance(cap, (int, float))
            and math.isfinite(float(cap))
            and float(cap) > 0.0
        )
        if not cap_usable:
            raise RoutingAPIError(
                503,
                "snap_policy_unavailable",
                "no owner-configured server-side maximum snap distance is available",
            )
        assert cap is not None
        if max_snap_distance_m > float(cap):
            raise RoutingAPIError(
                400,
                "snap_limit_exceeds_server_policy",
                "requested max_snap_distance_m exceeds the owner-configured server maximum",
            )
        if self.scientific_readiness.get("operational_ready") is not True:
            raise RoutingAPIError(
                503,
                "scientific_gate_not_accepted",
                "operational routing is refused until the product-bound scientific gate passes",
            )
        if not 0 <= forecast_lead <= MAX_FORECAST_LEAD_MINUTES:
            raise RoutingAPIError(
                400,
                "invalid_request",
                "forecast_lead_minutes must be within the fixed 0-180 minute horizon",
            )

        product, policy, blocked, avoided = self._require_product_and_policy(vehicle_class)
        if forecast_lead != product.forecast_lead_minutes:
            raise RoutingAPIError(
                409,
                "forecast_lead_not_available",
                "the configured depth product has "
                f"forecast_lead_minutes={product.forecast_lead_minutes}, not {forecast_lead}; "
                "configure a realized product for the requested lead",
            )
        try:
            origin = self.network.snap_point(request["origin"], max_distance_m=max_snap_distance_m)
            destination = self.network.snap_point(
                request["destination"], max_distance_m=max_snap_distance_m
            )
        except RoadGraphError as exc:
            raise RoutingAPIError(400, "invalid_point", str(exc)) from exc

        baseline_result = self.network.shortest_path(origin.node_id, destination.node_id, set())
        path_result = self.network.shortest_path(origin.node_id, destination.node_id, blocked)
        baseline_segment_ids = (
            {int(attributes["segment_id"]) for *_, attributes in baseline_result[1]}
            if baseline_result is not None
            else set()
        )
        safe_segment_ids = (
            {int(attributes["segment_id"]) for *_, attributes in path_result[1]}
            if path_result is not None
            else set()
        )
        changed_blocked_ids = baseline_segment_ids & blocked - safe_segment_ids
        avoided = [item for item in avoided if int(item["segment_id"]) in changed_blocked_ids]
        now = datetime.now(UTC)
        product_age_seconds = (now - product.issue_time_utc).total_seconds()
        if self.policies.path is None or self.policies.source_sha256 is None:
            raise RoutingAPIError(
                503,
                "vehicle_policy_provenance_unavailable",
                "vehicle policy bytes are not bound to a repository artifact",
            )
        policy_evidence_path = (
            self._repo_relative(self.policies.evidence_path, "vehicle-policy source evidence")
            if self.policies.evidence_path is not None
            else None
        )
        provenance = {
            "road_graph": {
                "path": self._repo_relative(self.network.source_path, "road graph"),
                "sha256": self.network.summary()["source_sha256"],
            },
            "road_lookup": {
                "path": self._repo_relative(self.network.lookup_path, "road lookup"),
                "sha256": self.network.summary()["lookup_sha256"],
            },
            "depth_product": {
                "path": self._repo_relative(product.source_path, "depth product"),
                "sha256": sha256_file(product.source_path),
                "manifest_path": self._repo_relative(
                    product.source_manifest_path, "depth-product manifest"
                ),
                "manifest_sha256": sha256_file(product.source_manifest_path),
            },
            "vehicle_policy": {
                "path": self._repo_relative(self.policies.path, "vehicle policy"),
                "sha256": self.policies.source_sha256,
                "source_evidence": {
                    "path": policy_evidence_path,
                    "sha256": self.policies.evidence_sha256,
                },
                "citation": policy.summary()["citation"],
            },
            "runtime_software": self.runtime_provenance,
        }
        forcing_kind = product.manifest.get("forcing_kind")
        temporal_aggregation = product.manifest.get("temporal_aggregation")
        lead_context = {
            "forecast_lead_minutes": forecast_lead,
            "forcing_kind": forcing_kind,
            "temporal_aggregation": temporal_aggregation,
            "forecast_condition": (
                "historical replay event maximum"
                if forcing_kind == "historical_replay"
                else "conditional forecast rainfall"
            ),
            "product_valid_time_utc": product.valid_time_utc.isoformat(),
            "product_issue_time_utc": product.issue_time_utc.isoformat(),
            "product_age_seconds": product_age_seconds,
            "routing_lead_seconds": None,
            "routing_lead_status": "unavailable_no_realized_arrival_time_series",
            "routing_observed_at_utc": now.isoformat(),
        }
        base = {
            "origin": _snap_dict(origin),
            "destination": _snap_dict(destination),
            "vehicle_class": policy.vehicle_class,
            "vehicle_limit_cm": policy.max_impassable_depth_cm,
            "max_snap_distance_m": max_snap_distance_m,
            "server_max_snap_distance_m": self.server_max_snap_distance_m,
            "product_label": product.manifest.get("product_label"),
            "lead_times": lead_context,
            "provenance": provenance,
            "avoided_segments": avoided,
            "routing_basis": (
                "segment flood_status plus contract band_high_cm; no per-cell depth is used"
            ),
        }
        if path_result is None:
            same_source_component = self.network.component_id(
                origin.node_id
            ) == self.network.component_id(destination.node_id)
            base.update(
                {
                    "status": "disconnected",
                    "reason": (
                        "no_flood_safe_route"
                        if same_source_component
                        else "source_graph_disconnected"
                    ),
                    "route": None,
                }
            )
            return base

        nodes, edges = path_result
        route_segments: list[dict[str, Any]] = []
        route_distance_m = 0.0
        for _from_node, _to_node, _edge_key, attributes in edges:
            segment_id = int(attributes["segment_id"])
            metadata = self.network.metadata[segment_id]
            state = product.state(segment_id)
            length_m = float(attributes["length_m"])
            route_distance_m += length_m
            route_segments.append(
                {
                    "segment_id": segment_id,
                    "osm_id": metadata.osm_id,
                    "highway": metadata.highway,
                    "name": metadata.name,
                    "length_m": length_m,
                    "flood_status": state.flood_status,
                    "confidence": state.confidence,
                    "depth_band_cm": [state.band_low_cm, state.band_high_cm],
                }
            )
        base.update(
            {
                "status": "ok",
                "reason": "flood_safe_route_found",
                "route": {
                    "distance_m": route_distance_m,
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "segments": route_segments,
                },
            }
        )
        return base


class RoutingHTTPServer(ThreadingHTTPServer):

    service: RoutingService
    cors_origin: str | None = None


class _RoutingHandler(BaseHTTPRequestHandler):
    server: RoutingHTTPServer

    def _apply_cors(self, request_origin: str | None) -> None:

        configured = getattr(self.server, "cors_origin", None)
        if not configured or not request_origin:
            return
        allowed = {origin.strip() for origin in str(configured).split(",") if origin.strip()}
        if request_origin not in allowed:
            return
        self.send_header("Origin", request_origin)
        self.send_header("Access-Control-Allow-Origin", request_origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Vary", "Origin")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._apply_cors(self.headers.get("Origin"))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._apply_cors(self.headers.get("Origin"))
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/health", "/v1/health"}:
            self._send(HTTPStatus.OK, self.server.service.health())
            return
        if path in {"/policies", "/v1/policies"}:
            self._send(HTTPStatus.OK, self.server.service.policies.summary())
            return
        self._send(HTTPStatus.NOT_FOUND, {"status": "not_found", "error": {"code": "not_found"}})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/route", "/v1/route"}:
            self._send(
                HTTPStatus.NOT_FOUND, {"status": "not_found", "error": {"code": "not_found"}}
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            request = json.loads(raw.decode("utf-8"))
            payload = self.server.service.route(request)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(
                HTTPStatus.BAD_REQUEST, RoutingAPIError(400, "invalid_json", str(exc)).as_dict()
            )
            return
        except RoutingAPIError as exc:
            self._send(exc.status_code, exc.as_dict())
            return
        self._send(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _server_for(
    service: RoutingService, host: str, port: int, cors_origin: str | None = None
) -> RoutingHTTPServer:
    server = RoutingHTTPServer((host, port), _RoutingHandler)
    server.service = service
    server.cors_origin = cors_origin
    return server


def _common_service(
    road_file: Path,
    lookup_file: Path,
    depth_product_file: Path | None,
    vehicle_policy_file: Path | None,
    gate_report_file: Path | None,
    server_max_snap_distance_m: float | None,
    endpoint_snap_tolerance_m: float | None = None,
    enable_intersection_splitting: bool | None = None,
    routing_config_path: Path | None = None,
) -> RoutingService:
    try:
        return RoutingService.from_paths(
            road_file=road_file,
            lookup_file=lookup_file,
            depth_product_file=depth_product_file,
            vehicle_policy_file=vehicle_policy_file,
            gate_report_file=gate_report_file,
            server_max_snap_distance_m=server_max_snap_distance_m,
            endpoint_snap_tolerance_m=endpoint_snap_tolerance_m,
            enable_intersection_splitting=enable_intersection_splitting,
            routing_config_path=routing_config_path,
        )
    except (RoadGraphError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("health")
def health(
    road_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"
    ),
    lookup_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
    ),
    depth_product_file: Path | None = typer.Option(None),
    vehicle_policy_file: Path | None = typer.Option(None),
    gate_report_file: Path | None = typer.Option(None),
    server_max_snap_distance_m: float | None = typer.Option(None),
    endpoint_snap_tolerance_m: float | None = typer.Option(
        None,
        help="Optional endpoint snap tolerance in metres (WF-1 scheme). None => resolve from configs/routing.yaml routing_graph.endpoint_snap_tolerance_m; null/absent => legacy exact match.",  # noqa: E501
    ),
    enable_intersection_splitting: bool = typer.Option(
        False,
        help="If true, attempt interior intersection splitting (routing_graph.enable_intersection_splitting).",  # noqa: E501
    ),
    routing_config: Path | None = typer.Option(
        None,
        help="Routing YAML for routing_graph keys; defaults to configs/routing.yaml when present.",
    ),
) -> None:
    """Load the configured inputs and print realized readiness on CPU."""

    service = _common_service(
        road_file,
        lookup_file,
        depth_product_file,
        vehicle_policy_file,
        gate_report_file,
        server_max_snap_distance_m,
        endpoint_snap_tolerance_m=endpoint_snap_tolerance_m,
        enable_intersection_splitting=(
            enable_intersection_splitting if enable_intersection_splitting else None
        ),
        routing_config_path=routing_config,
    )
    typer.echo(json.dumps(service.health(), indent=2, sort_keys=True))


@app.command("route")
def route(
    request_file: Path = typer.Argument(
        ..., help="JSON request with origin/destination/vehicle/lead."
    ),
    road_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"
    ),
    lookup_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
    ),
    depth_product_file: Path | None = typer.Option(None),
    vehicle_policy_file: Path | None = typer.Option(None),
    gate_report_file: Path | None = typer.Option(None),
    server_max_snap_distance_m: float | None = typer.Option(None),
    endpoint_snap_tolerance_m: float | None = typer.Option(
        None,
        help="Optional endpoint snap tolerance in metres (WF-1 scheme). None => resolve from configs/routing.yaml.",  # noqa: E501
    ),
    enable_intersection_splitting: bool = typer.Option(
        False, help="If true, attempt interior intersection splitting."
    ),
    routing_config: Path | None = typer.Option(
        None,
        help="Routing YAML for routing_graph keys; defaults to configs/routing.yaml when present.",
    ),
) -> None:
    """Evaluate one route request and print the realized result or refusal."""

    service = _common_service(
        road_file,
        lookup_file,
        depth_product_file,
        vehicle_policy_file,
        gate_report_file,
        server_max_snap_distance_m,
        endpoint_snap_tolerance_m=endpoint_snap_tolerance_m,
        enable_intersection_splitting=(
            enable_intersection_splitting if enable_intersection_splitting else None
        ),
        routing_config_path=routing_config,
    )
    try:
        request = json.loads(request_file.read_text(encoding="utf-8"))
        result = service.route(request)
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"could not read request JSON: {exc}") from exc
    except RoutingAPIError as exc:
        typer.echo(json.dumps(exc.as_dict(), indent=2, sort_keys=True))
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command("serve")
def serve(
    road_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_centrelines.gpkg"
    ),
    lookup_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
    ),
    depth_product_file: Path | None = typer.Option(None),
    vehicle_policy_file: Path | None = typer.Option(None),
    gate_report_file: Path | None = typer.Option(None),
    server_max_snap_distance_m: float | None = typer.Option(None),
    endpoint_snap_tolerance_m: float | None = typer.Option(
        None,
        help="Optional endpoint snap tolerance in metres (WF-1 scheme). None => resolve from configs/routing.yaml routing_graph.endpoint_snap_tolerance_m; null/absent => legacy exact match.",  # noqa: E501
    ),
    enable_intersection_splitting: bool = typer.Option(
        False,
        help="If true, attempt interior intersection splitting (routing_graph.enable_intersection_splitting).",  # noqa: E501
    ),
    routing_config: Path | None = typer.Option(
        None,
        help="Routing YAML for routing_graph keys; defaults to configs/routing.yaml when present.",
    ),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8080),
    cors_origin: str | None = typer.Option(
        None,
        help=(
            "Allow-listed browser origin(s) for cross-origin calls: only requests whose "
            "Origin equals one of these comma-separated values get CORS headers. Default None "
            "emits no CORS header at all."
        ),
    ),
) -> None:
    """Serve /health, /policies, and POST /v1/route on CPU."""

    service = _common_service(
        road_file,
        lookup_file,
        depth_product_file,
        vehicle_policy_file,
        gate_report_file,
        server_max_snap_distance_m,
        endpoint_snap_tolerance_m=endpoint_snap_tolerance_m,
        enable_intersection_splitting=(
            enable_intersection_splitting if enable_intersection_splitting else None
        ),
        routing_config_path=routing_config,
    )
    server = _server_for(service, host, port, cors_origin=cors_origin)
    typer.echo(json.dumps(service.health(), sort_keys=True))
    typer.echo(f"routing_api_listening={host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    app()
