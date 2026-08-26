"""Single-attempt drain-network snapshot reader for the dashboard.

WF-1 may be actively rewriting ``runs/drain_graph_build/`` while this server
runs.  The concurrency contract for this module is therefore:

* exactly ONE read attempt per server process -- no polling, no retry loop;
* whatever bytes are successfully read become the immutable in-memory
  snapshot the UI renders against, and their provenance fields
  (``graph_fingerprint``, manifest ``status`` at read time) are recorded and
  served so the screen always names the exact graph it drew;
* a failed or mid-write artefact degrades to "drains unavailable" -- it never
  blocks startup and never invents geometry;

and, binding under rule 2: **a surcharge pulse is rendered only where one was
measured.**  The current build's manifest reads
``status=stopped_owner_adjudication`` with
``capacity_status=blocked_missing_design_intensity`` and zero capacity edges
written, so the layer renders GEOMETRY ONLY until a coupled run supplies real
surcharge events.  The payload shape already carries the slot where measured
surcharge volumes drop in later without a rewrite.

NOTE for the integrator (WF-6 requirement A5) -- segment-geometry injection
point: ``causal_for_segment`` maps a STREET segment onto nearby flagged drain
edges by proximity, which needs the street's own geometry.  This module does
not read the roads layer (no new dependency here), so the caller injects a
callable at construction::

    DrainSnapshotReader(geometry_provider=my_centroid_lookup)

where ``my_centroid_lookup(segment_id) -> [x, y]`` returns the street
segment's representative point in EPSG:32643 metres (the same CRS as the
drain graph).  In app.py the natural provider is the roads bundle's centroid
table; routes belong to the integrator.  Without an injected provider every
causal payload degrades honestly to the ``none_measured`` empty state -- it
never guesses a location.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
DRAIN_DIR = REPO_ROOT / "runs" / "drain_graph_build"
GPKG_PATH = DRAIN_DIR / "drain_graph.gpkg"
MANIFEST_PATH = DRAIN_DIR / "manifest.json"

CAUSAL_PROXIMITY_M = 25.0
CAUSAL_CRS = "EPSG:32643"
NONE_MEASURED_STATEMENT = "No measured surcharge for this street — coupled run pending."
MEASURED_STATUS = "measured"
PREDICTED_STATUS = "predicted"
NONE_MEASURED_STATUS = "none_measured"


def _distance_within(distance_m: float, tolerance_m: float = CAUSAL_PROXIMITY_M) -> bool:
    """Single comparison site for the proximity predicate (V5 mutation target):
    flipping the operator here must redden the WF-6 causal tests."""
    return distance_m <= tolerance_m


def _point_to_edges_min_distances(
    point_xy: Sequence[float],
    edge_coords: np.ndarray,
    edge_offsets: np.ndarray,
    edge_ids: np.ndarray,
) -> dict[int, float]:
    """Minimum planar distance (metres) from ``point_xy`` to each drain edge,
    computed over every polyline SEGMENT (not just vertices), vectorised.

    Coordinates are EPSG:32643 metres held as float32; at Bengaluru easting/
    northing magnitudes (~1e6) float32 quantisation is <=~0.13 m, far below the
    25 m tolerance, so f64 accumulation over f32 inputs stays well inside it.
    """
    pts = np.asarray(edge_coords, dtype="<f8").reshape(-1, 2)
    offsets = np.asarray(edge_offsets, dtype="<i8")
    if pts.shape[0] < 2 or offsets.size < 2:
        return {}
    counts = np.diff(offsets)
    owner_point = np.repeat(np.arange(counts.size, dtype="<i8"), counts)
    pos_in_edge = np.arange(pts.shape[0], dtype="<i8") - np.repeat(offsets[:-1], counts)
    starts = pos_in_edge < (counts[owner_point] - 1)
    start_rows = np.flatnonzero(starts)
    a = pts[start_rows]
    b = pts[start_rows + 1]  # next vertex, same edge by construction
    seg_owner = owner_point[start_rows]

    p = np.asarray(point_xy, dtype="<f8")
    ab = b - a
    ap = p[None, :] - a
    ab2 = np.einsum("ij,ij->i", ab, ab)
    t = np.where(ab2 > 0.0, np.einsum("ij,ij->i", ap, ab) / np.where(ab2 > 0.0, ab2, 1.0), 0.0)
    proj = a + np.clip(t, 0.0, 1.0)[:, None] * ab
    d_seg = np.sqrt(np.einsum("ij,ij->i", proj - p[None, :], proj - p[None, :]))

    best_per_edge = np.full(counts.size, np.inf)
    np.minimum.at(best_per_edge, seg_owner, d_seg)
    return {int(edge_ids[i]): float(best_per_edge[i]) for i in range(counts.size)}


@dataclass(frozen=True)
class DrainSnapshot:
    """One atomic read of the drain-graph artefacts."""

    available: bool
    reason: str | None
    snapshot_utc: str
    manifest_status_at_read: str | None
    graph_fingerprint_at_read: str | None
    capacity_status: str | None
    n_edges: int
    n_nodes: int
    observed_edges: int
    synthesised_edges: int
    edge_offsets: np.ndarray = field(repr=False, default=None)
    edge_coords: np.ndarray = field(repr=False, default=None)
    edge_source_codes: np.ndarray = field(repr=False, default=None)
    node_ids: np.ndarray = field(repr=False, default=None)
    node_xy: np.ndarray = field(repr=False, default=None)
    surcharge: dict[str, Any] = field(default_factory=dict)
    # --- WF-6 causal-link fields (additive; existing consumers unaffected) ---
    # edge id per kept geometry row, aligned with edge_offsets/edge_coords.
    edge_ids: np.ndarray = field(repr=False, default=None)
    # The PREDICTED set: edges whose realised capacity_basis carries the
    # demand_exceeds_capacity record. (edge_id, from_node, to_node, basis_raw)
    flagged_edges: tuple[tuple[int, int, int, str], ...] = ()
    manifest_sha256_at_read: str | None = None
    node_xy_map: dict[int, list[float]] = field(default_factory=dict)

    def meta_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "snapshot_utc": self.snapshot_utc,
            "manifest_status_at_read": self.manifest_status_at_read,
            "graph_fingerprint_at_read": self.graph_fingerprint_at_read,
            "capacity_status": self.capacity_status,
            "n_edges": self.n_edges,
            "n_nodes": self.n_nodes,
            "edge_source_codes": (
                [int(v) for v in self.edge_source_codes]
                if self.edge_source_codes is not None
                else None
            ),
            "edge_source_counts": {
                "observed": self.observed_edges,
                "synthesised": self.synthesised_edges,
            },
            "surcharge": self.surcharge,
        }


class DrainSnapshotReader:
    """Reads the drain graph once, on first request, under a lock.

    ``geometry_provider`` (integrator injection point, see module NOTE):
    ``callable(segment_id) -> [x, y]`` in EPSG:32643, used only by
    ``causal_for_segment``.
    """

    def __init__(self, geometry_provider: Callable[[str], Sequence[float]] | None = None) -> None:
        self.lock = threading.Lock()
        self._snapshot: DrainSnapshot | None = None
        self.geometry_provider = geometry_provider

    def get(self) -> DrainSnapshot:
        with self.lock:
            if self._snapshot is None:
                self._snapshot = self._read_once()
            return self._snapshot

    # ------------------------------------------------------------------ read

    def _read_once(self) -> DrainSnapshot:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        manifest_status: str | None = None
        fingerprint: str | None = None
        capacity_status: str | None = None
        coupling_enabled: bool | None = None
        surcharge_path_value: Any = None
        try:
            raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            manifest_status = raw.get("status")
            fingerprint = raw.get("graph_fingerprint")
            capacity_metrics = raw.get("capacity_metrics") or {}
            capacity_status = capacity_metrics.get("capacity_status") or raw.get(
                "capacity_metrics", {}
            ).get("capacity_status")
            coupling_enabled = raw.get("coupling_enabled")
            surcharge_path_value = raw.get("surcharge_events_path")
        except (OSError, json.JSONDecodeError) as exc:
            # Mid-write or unreadable manifest: record and continue to the gpkg.
            manifest_error = f"{type(exc).__name__}: {exc}"
        else:
            manifest_error = None
        # Provenance is hashed from the bytes actually read, not from a name
        # (V1): if the manifest changed under us the payload names what it saw.
        manifest_sha256: str | None = None
        try:
            manifest_sha256 = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
        except OSError:
            manifest_sha256 = None

        surcharge = self._surcharge_state(coupling_enabled, surcharge_path_value)

        if not GPKG_PATH.is_file():
            return DrainSnapshot(
                available=False,
                reason=f"drain GeoPackage not found: {_repo_relative(GPKG_PATH)}",
                snapshot_utc=now,
                manifest_status_at_read=manifest_status,
                graph_fingerprint_at_read=fingerprint,
                capacity_status=capacity_status,
                n_edges=0,
                n_nodes=0,
                observed_edges=0,
                synthesised_edges=0,
                manifest_sha256_at_read=manifest_sha256,
                surcharge=surcharge,
            )
        try:
            import fiona

            edge_chunks: list[np.ndarray] = []
            offsets = np.zeros(1, dtype="<u4")
            source_codes: list[int] = []
            edge_id_rows: list[int] = []
            flagged: list[tuple[int, int, int, str]] = []
            with fiona.open(GPKG_PATH, layer="drain_edges") as source:
                for feature in source:
                    geometry = feature.get("geometry")
                    if geometry is None:
                        continue
                    xy = _linestring_xy(geometry)
                    if len(xy) < 4:
                        continue
                    properties = feature.get("properties") or {}
                    edge_chunks.append(np.asarray(xy, dtype="<f4"))
                    offsets = np.append(
                        offsets,
                        np.asarray([sum(len(c) // 2 for c in edge_chunks)], dtype="<u4"),
                    )
                    edge_source = properties.get("edge_source")
                    source_codes.append(1 if edge_source == "synthesised" else 0)
                    try:
                        eid = int(properties.get("edge_id"))
                    except (TypeError, ValueError):
                        eid = len(edge_id_rows)
                    edge_id_rows.append(eid)
                    basis_raw = properties.get("capacity_basis")
                    # The predicted set is read from realised bytes: the build
                    # writes the demand_exceeds_capacity record INTO
                    # capacity_basis only where rational demand exceeded the
                    # bounded section's conveyance (drainage/capacity.py D23).
                    if isinstance(basis_raw, str) and "demand_exceeds_capacity" in basis_raw:
                        try:
                            from_node = int(properties.get("from_node"))
                            to_node = int(properties.get("to_node"))
                        except (TypeError, ValueError):
                            continue
                        flagged.append((eid, from_node, to_node, basis_raw))
            node_ids: list[int] = []
            node_xy: list[list[float]] = []
            with fiona.open(GPKG_PATH, layer="drain_nodes") as source:
                for feature in source:
                    geometry = feature.get("geometry")
                    properties = feature.get("properties") or {}
                    if geometry is None or geometry.get("type") != "Point":
                        continue
                    coords = geometry.get("coordinates")
                    if not coords or len(coords) < 2:
                        continue
                    node_ids.append(int(properties.get("node_id", len(node_ids) + 1)))
                    node_xy.append([float(coords[0]), float(coords[1])])
        except Exception as exc:  # noqa: BLE001 - mid-write gpkg surfaces honestly
            return DrainSnapshot(
                available=False,
                reason=f"drain GeoPackage unreadable at snapshot time: {type(exc).__name__}: {exc}",
                snapshot_utc=now,
                manifest_status_at_read=manifest_status,
                graph_fingerprint_at_read=fingerprint,
                capacity_status=capacity_status,
                n_edges=0,
                n_nodes=0,
                observed_edges=0,
                synthesised_edges=0,
                manifest_sha256_at_read=manifest_sha256,
                surcharge={**surcharge, "manifest_error": manifest_error},
            )

        coords = np.concatenate(edge_chunks) if edge_chunks else np.zeros(0, dtype="<f4")
        source_codes = np.asarray(source_codes, dtype="u1")
        # count_nonzero, NOT sum(): sum over a uint8 array wraps at 256 and
        # misclassified 256 synthesised connectors as observed (286 -> 30).
        synth = int(np.count_nonzero(source_codes))
        node_xy_map = {int(nid): list(xy) for nid, xy in zip(node_ids, node_xy, strict=True)}
        return DrainSnapshot(
            available=True,
            reason=(
                "geometry-only render: manifest status "
                f"{manifest_status!r} at snapshot time; no measured surcharge exists"
                if surcharge.get("available") is False
                else None
            ),
            snapshot_utc=now,
            manifest_status_at_read=manifest_status,
            graph_fingerprint_at_read=fingerprint,
            capacity_status=capacity_status,
            n_edges=len(source_codes),
            n_nodes=len(node_ids),
            observed_edges=len(source_codes) - synth,
            synthesised_edges=synth,
            edge_offsets=offsets,
            edge_coords=coords,
            edge_source_codes=source_codes,
            edge_ids=np.asarray(edge_id_rows, dtype="<i8"),
            flagged_edges=tuple(flagged),
            manifest_sha256_at_read=manifest_sha256,
            node_ids=np.asarray(node_ids, dtype="<u4"),
            node_xy=np.asarray(node_xy, dtype="<f4").reshape(-1, 2),
            node_xy_map=node_xy_map,
            surcharge={**surcharge, "manifest_error": manifest_error},
        )

    @staticmethod
    def _surcharge_state(
        coupling_enabled: bool | None, surcharge_path_value: Any
    ) -> dict[str, Any]:
        """Surcharge is rendered ONLY where a realised run measured it."""

        if coupling_enabled is not True:
            return {
                "available": False,
                "reason": (
                    "source manifest declares coupling_enabled="
                    f"{coupling_enabled!r}; no coupled surcharge was measured"
                ),
            }
        if not isinstance(surcharge_path_value, str) or not surcharge_path_value.strip():
            return {
                "available": False,
                "reason": "coupled manifest names no surcharge_events_path",
            }
        path = REPO_ROOT / surcharge_path_value
        if not path.is_file():
            return {
                "available": False,
                "reason": f"declared surcharge file does not exist: {surcharge_path_value}",
            }
        try:
            import csv

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            return {"available": False, "reason": f"surcharge file unreadable: {exc}"}
        return {"available": True, "path": surcharge_path_value, "n_events": len(rows)}

    # ----------------------------------------------------------------- causal

    def causal_for_segment(self, segment_id: str) -> dict[str, Any]:
        """WF-6 requirement A5: causal-link payload for one street segment.

        Status precedence, bound by rule 2 (never assert unmeasured surcharge):

        * ``measured``   -- only when the snapshot carries per-street MEASURED
          surcharge events under ``surcharge['segment_events']``.  No coupled
          run exists today, nothing writes that slot, so this branch is wired
          but INERT; its statement cites only what the events file records.
        * ``predicted``  -- the segment's injected geometry sits within
          ``CAUSAL_PROXIMITY_M`` of an edge in the PREDICTED set (edges whose
          realised capacity_basis records demand_exceeds_capacity from the
          cited design storm and cited capacities).  Predicted is never
          worded as measured.
        * ``none_measured`` -- default empty state; statement fixed so the UI
          cannot imply a measurement that did not happen.
        """
        snapshot = self.get()
        predicted_set = {
            "size": len(snapshot.flagged_edges),
            "source": _repo_relative(MANIFEST_PATH),
            "sha256": snapshot.manifest_sha256_at_read,
            "basis": "design-storm rational demand vs bounded-section conveyance "
            "(drain_graph_build capacity stage); NOT a measured event",
        }

        surcharge_state = snapshot.surcharge or {}
        segment_events = surcharge_state.get("segment_events")
        if surcharge_state.get("available") is True and isinstance(segment_events, dict):
            mapped = segment_events.get(str(segment_id))
            if isinstance(mapped, dict) and mapped.get("n_events"):
                return {
                    "segment_id": str(segment_id),
                    "status": MEASURED_STATUS,
                    "statement": (
                        "Measured surcharge recorded for this street by the coupled run "
                        f"({mapped['n_events']} events, {surcharge_state.get('path')})."
                    ),
                    "nodes": [],
                    "nearest_flagged_edge_id": None,
                    "proximity_m": CAUSAL_PROXIMITY_M,
                    "crs": CAUSAL_CRS,
                    "predicted_set": predicted_set,
                }

        provider = self.geometry_provider
        if provider is None:
            return {
                "segment_id": str(segment_id),
                "status": NONE_MEASURED_STATUS,
                "statement": NONE_MEASURED_STATEMENT,
                "nodes": [],
                "nearest_flagged_edge_id": None,
                "proximity_m": CAUSAL_PROXIMITY_M,
                "crs": CAUSAL_CRS,
                "detail": "no geometry_provider injected; proximity mapping unavailable",
                "predicted_set": predicted_set,
            }
        try:
            point = [float(v) for v in (provider(segment_id) or [])]
        except Exception as exc:  # noqa: BLE001 - provider failure degrades honestly
            return self._none_measured(
                segment_id, predicted_set, detail=f"geometry_provider failed: {exc}"
            )
        if len(point) != 2 or not all(math.isfinite(v) for v in point):
            return self._none_measured(
                segment_id,
                predicted_set,
                detail="geometry_provider returned no finite EPSG:32643 point",
            )

        distances = _point_to_edges_min_distances(
            point, snapshot.edge_coords, snapshot.edge_offsets, snapshot.edge_ids
        )
        flagged_ids = {eid for eid, _, _, _ in snapshot.flagged_edges}
        matched = sorted(
            ((dist, eid) for eid, dist in distances.items() if eid in flagged_ids),
        )
        nearby = [(eid, dist) for dist, eid in matched if _distance_within(dist)]
        if not nearby:
            return self._none_measured(
                segment_id, predicted_set, detail="no flagged edge within tolerance"
            )

        nearest_edge_id = nearby[0][0]
        nodes_out: dict[int, dict[str, Any]] = {}
        for eid, from_node, to_node, basis_raw in snapshot.flagged_edges:
            if eid not in {e for e, _ in nearby}:
                continue
            for node_id in (from_node, to_node):
                xy = snapshot.node_xy_map.get(node_id)
                if xy is None:
                    continue
                entry = nodes_out.setdefault(
                    node_id,
                    {
                        "node_id": int(node_id),
                        "edge_ids": [],
                        "demand_exceeds_capacity": True,
                        "world_xy": [float(xy[0]), float(xy[1])],
                    },
                )
                if eid not in entry["edge_ids"]:
                    entry["edge_ids"].append(int(eid))
                # lowest edge id's basis is the node's canonical one (stable)
                if "capacity_basis" not in entry or eid <= min(entry["edge_ids"]):
                    entry["capacity_basis"] = _parse_capacity_basis(basis_raw)
        return {
            "segment_id": str(segment_id),
            "status": PREDICTED_STATUS,
            "statement": (
                "Predicted overcapacity nearby: drain edge "
                f"{nearest_edge_id} exceeds design capacity (cited design storm) — "
                "not a measured surcharge."
            ),
            "nodes": sorted(nodes_out.values(), key=lambda n: n["node_id"]),
            "nearest_flagged_edge_id": int(nearest_edge_id),
            "matched_flagged_edge_ids": [int(eid) for eid, _ in nearby],
            "distances_m": {str(eid): round(dist, 2) for eid, dist in nearby},
            "proximity_m": CAUSAL_PROXIMITY_M,
            "crs": CAUSAL_CRS,
            "predicted_set": predicted_set,
        }

    def _none_measured(
        self, segment_id: str, predicted_set: dict[str, Any], *, detail: str
    ) -> dict[str, Any]:
        return {
            "segment_id": str(segment_id),
            "status": NONE_MEASURED_STATUS,
            "statement": NONE_MEASURED_STATEMENT,
            "nodes": [],
            "nearest_flagged_edge_id": None,
            "proximity_m": CAUSAL_PROXIMITY_M,
            "crs": CAUSAL_CRS,
            "detail": detail,
            "predicted_set": predicted_set,
        }


def _linestring_xy(geometry: dict[str, Any]) -> list[float]:
    coordinates = geometry.get("coordinates") or []
    flat: list[float] = []
    for point in coordinates:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            flat.extend((float(point[0]), float(point[1])))
    return flat


def _parse_capacity_basis(basis_raw: str) -> Any:
    """capacity_basis is written by the build as JSON where it is structured;
    anything unparseable passes through verbatim rather than being reshaped."""
    try:
        return json.loads(basis_raw)
    except json.JSONDecodeError:
        return basis_raw


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


READER = DrainSnapshotReader()
