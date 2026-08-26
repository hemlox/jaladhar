"""Load the realized OSM road centreline network for flood-safe routing.

The terrain stage has already fetched and persisted the OSM roads as a
GeoPackage.  This module consumes that file; it never calls Overpass and it
never creates a road from a coordinate supplied by an API caller.  The
GeoPackage contains one row per dense ``segment_id`` and a MultiLineString.
Each component of that geometry becomes an undirected graph edge between its
realized endpoints.  OSM one-way and turn-restriction fields are not present
in this artefact, so the graph deliberately exposes that limitation in its
summary instead of guessing directionality.
"""

from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import typer
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from scipy.spatial import cKDTree

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]
EXPECTED_CRS = CRS.from_epsg(32643)
EndpointRecord = tuple[
    tuple[float, float],
    tuple[float, float],
    int,
    float,
    int,
]


class RoadGraphError(RuntimeError):
    """Raised when the persisted road artefact cannot support routing safely."""


@dataclass(frozen=True)
class SegmentMetadata:
    """Realized tags copied from the OSM-derived lookup table."""

    segment_id: int
    osm_id: int
    highway: str | None
    name: str | None


@dataclass(frozen=True)
class SnappedPoint:
    """A caller point mapped to the nearest realized graph node."""

    node_id: int
    x_m: float
    y_m: float
    distance_m: float
    input_crs: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if bool(np.isnan(value)):
            return None
    except TypeError:
        pass
    value = str(value).strip()
    return value or None


def _load_lookup(path: Path) -> dict[int, SegmentMetadata]:
    if not path.is_file():
        raise RoadGraphError(f"OSM segment lookup is missing: {path}")

    required = {"segment_id", "osm_id", "highway", "name"}
    records: dict[int, SegmentMetadata] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise RoadGraphError(f"OSM segment lookup lacks columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                segment_id = int(row["segment_id"])
                osm_id = int(row["osm_id"])
            except (TypeError, ValueError) as exc:
                raise RoadGraphError(f"invalid segment identity at {path}:{line_number}") from exc
            if segment_id <= 0:
                raise RoadGraphError(f"segment_id must be positive at {path}:{line_number}")
            if segment_id in records:
                raise RoadGraphError(f"duplicate segment_id={segment_id} in {path}")
            records[segment_id] = SegmentMetadata(
                segment_id=segment_id,
                osm_id=osm_id,
                highway=_optional_text(row.get("highway")),
                name=_optional_text(row.get("name")),
            )

    expected = set(range(1, len(records) + 1))
    if set(records) != expected:
        raise RoadGraphError(
            "OSM segment lookup is not the frozen dense 1..N key space: "
            f"observed range {min(records, default=None)}..{max(records, default=None)} "
            f"for {len(records)} rows"
        )
    return records


def _coordinate(value: Any) -> tuple[float, float]:
    try:
        x, y = value
        x = float(x)
        y = float(y)
    except (TypeError, ValueError) as exc:
        raise RoadGraphError(f"invalid road endpoint {value!r}") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise RoadGraphError(f"non-finite road endpoint {value!r}")
    return x, y


class RoadNetwork:
    """An immutable-in-practice graph backed by the persisted OSM centreline file."""

    def __init__(
        self,
        graph: nx.MultiGraph,
        metadata: dict[int, SegmentMetadata],
        node_coordinates: dict[int, tuple[float, float]],
        component_by_node: dict[int, int],
        source_path: Path,
        lookup_path: Path,
        crs: CRS,
        edge_part_count: int,
    ) -> None:
        self.graph = graph
        self.metadata = metadata
        self.node_coordinates = node_coordinates
        self.component_by_node = component_by_node
        self.source_path = source_path
        self.lookup_path = lookup_path
        self.crs = crs
        self.edge_part_count = edge_part_count
        ordered_nodes = sorted(node_coordinates)
        self._tree_node_ids = np.asarray(ordered_nodes, dtype=np.int64)
        self._tree_coordinates = np.asarray(
            [node_coordinates[node_id] for node_id in ordered_nodes], dtype=np.float64
        )
        self._tree = cKDTree(self._tree_coordinates)

    @classmethod
    def from_files(
        cls,
        road_path: Path,
        lookup_path: Path,
    ) -> RoadNetwork:
        road_path = road_path.resolve()
        lookup_path = lookup_path.resolve()
        if not road_path.is_file():
            raise RoadGraphError(f"realized OSM road GeoPackage is missing: {road_path}")
        metadata = _load_lookup(lookup_path)

        try:
            roads = gpd.read_file(road_path)
        except Exception as exc:
            raise RoadGraphError(
                f"could not read realized OSM road GeoPackage {road_path}: {exc}"
            ) from exc
        if roads.empty:
            raise RoadGraphError(f"realized OSM road GeoPackage is empty: {road_path}")
        if roads.crs is None:
            raise RoadGraphError(f"realized OSM road GeoPackage has no CRS: {road_path}")
        actual_crs = CRS.from_user_input(roads.crs)
        if actual_crs != EXPECTED_CRS:
            raise RoadGraphError(
                f"road CRS {actual_crs.to_string()} does not match frozen EPSG:32643"
            )
        required_columns = {"segment_id", "osm_id", "geometry"}
        missing = required_columns - set(roads.columns)
        if missing:
            raise RoadGraphError(f"realized OSM road file lacks columns: {sorted(missing)}")

        graph_segment_ids: set[int] = set()
        endpoint_records: list[EndpointRecord] = []
        node_coordinates_set: set[tuple[float, float]] = set()

        for row in roads.itertuples(index=False):
            try:
                segment_id = int(row.segment_id)
                osm_id = int(row.osm_id)
            except (TypeError, ValueError) as exc:
                raise RoadGraphError(
                    "realized OSM road file contains a non-integer segment identity"
                ) from exc
            if segment_id not in metadata:
                raise RoadGraphError(
                    f"road GeoPackage segment_id={segment_id} is absent from the lookup CSV"
                )
            if metadata[segment_id].osm_id != osm_id:
                raise RoadGraphError(
                    f"OSM identity mismatch for segment_id={segment_id}: "
                    f"GeoPackage osm_id={osm_id}, lookup osm_id={metadata[segment_id].osm_id}"
                )
            graph_segment_ids.add(segment_id)
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                raise RoadGraphError(f"segment_id={segment_id} has empty geometry")
            if geometry.geom_type == "LineString":
                parts = (geometry,)
            elif geometry.geom_type == "MultiLineString":
                parts = geometry.geoms
            else:
                raise RoadGraphError(
                    f"segment_id={segment_id} has unsupported geometry {geometry.geom_type}"
                )
            for part_index, part in enumerate(parts):
                coordinates = list(part.coords)
                if len(coordinates) < 2:
                    raise RoadGraphError(f"segment_id={segment_id} has a one-point line part")
                start = _coordinate(coordinates[0])
                end = _coordinate(coordinates[-1])
                length_m = float(part.length)
                if not math.isfinite(length_m) or length_m <= 0.0:
                    raise RoadGraphError(
                        f"segment_id={segment_id} has non-positive line length {length_m}"
                    )
                node_coordinates_set.update((start, end))
                endpoint_records.append((start, end, segment_id, length_m, part_index))

        if graph_segment_ids != set(metadata):
            missing = sorted(set(metadata) - graph_segment_ids)
            extra = sorted(graph_segment_ids - set(metadata))
            raise RoadGraphError(
                "OSM road GeoPackage and lookup segment sets differ: "
                f"missing_in_geopackage={missing[:5]} extra_in_lookup={extra[:5]}"
            )

        ordered_coordinates = sorted(node_coordinates_set)
        node_id_by_coordinate = {
            coordinate: node_id for node_id, coordinate in enumerate(ordered_coordinates, start=1)
        }
        node_coordinates = {
            node_id: coordinate for coordinate, node_id in node_id_by_coordinate.items()
        }
        graph = nx.MultiGraph()
        graph.add_nodes_from(node_coordinates)
        for start, end, segment_id, length_m, part_index in endpoint_records:
            graph.add_edge(
                node_id_by_coordinate[start],
                node_id_by_coordinate[end],
                segment_id=segment_id,
                length_m=length_m,
                part_index=part_index,
            )
        component_by_node = {
            node_id: component_id
            for component_id, component in enumerate(nx.connected_components(graph), start=1)
            for node_id in component
        }
        return cls(
            graph=graph,
            metadata=metadata,
            node_coordinates=node_coordinates,
            component_by_node=component_by_node,
            source_path=road_path,
            lookup_path=lookup_path,
            crs=actual_crs,
            edge_part_count=len(endpoint_records),
        )

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def component_count(self) -> int:
        return len(set(self.component_by_node.values()))

    def summary(self) -> dict[str, Any]:
        """Return only measurements of the loaded artefacts and their provenance."""

        return {
            "source": str(self.source_path),
            "source_sha256": _sha256(self.source_path),
            "lookup": str(self.lookup_path),
            "lookup_sha256": _sha256(self.lookup_path),
            "crs": self.crs.to_string(),
            "segment_count": len(self.metadata),
            "edge_part_count": self.edge_part_count,
            "node_count": self.node_count,
            "component_count": self.component_count,
            "directionality": "undirected; source lacks one-way and turn-restriction fields",
        }

    def _to_graph_crs(self, point: Any) -> tuple[float, float, str]:
        """Parse a point without guessing a geographic coordinate system."""

        if isinstance(point, dict) and "coordinates" in point:
            input_crs = point.get("crs", self.crs.to_string())
            coordinates = point["coordinates"]
        elif isinstance(point, dict) and {"x", "y"} <= set(point):
            input_crs = point.get("crs", self.crs.to_string())
            coordinates = (point["x"], point["y"])
        elif isinstance(point, dict) and {"lon", "lat"} <= set(point):
            input_crs = point.get("crs", "EPSG:4326")
            coordinates = (point["lon"], point["lat"])
        elif isinstance(point, (list, tuple)) and len(point) == 2:
            input_crs = self.crs.to_string()
            coordinates = point
        else:
            raise RoadGraphError(
                "point must be [x,y], {x,y,crs}, {lon,lat,crs}, or {coordinates,crs}"
            )
        try:
            x, y = _coordinate(coordinates)
            source_crs = CRS.from_user_input(input_crs)
        except (CRSError, RoadGraphError, TypeError, ValueError) as exc:
            raise RoadGraphError(f"invalid point CRS or coordinates: {point!r}") from exc
        if source_crs != self.crs:
            transformer = Transformer.from_crs(source_crs, self.crs, always_xy=True)
            x, y = transformer.transform(x, y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise RoadGraphError("point transformed to non-finite graph coordinates")
        return float(x), float(y), source_crs.to_string()

    def snap_point(self, point: Any, *, max_distance_m: float) -> SnappedPoint:
        if not math.isfinite(max_distance_m) or max_distance_m <= 0.0:
            raise RoadGraphError("maximum snap distance must be finite and positive")
        x, y, input_crs = self._to_graph_crs(point)
        distance, index = self._tree.query((x, y))
        if not math.isfinite(float(distance)) or float(distance) > max_distance_m:
            raise RoadGraphError(
                "point exceeds the configured maximum snap distance: "
                f"distance_m={float(distance):.3f}, max_distance_m={max_distance_m:.3f}"
            )
        node_id = int(self._tree_node_ids[int(index)])
        snapped_x, snapped_y = self.node_coordinates[node_id]
        return SnappedPoint(
            node_id=node_id,
            x_m=float(snapped_x),
            y_m=float(snapped_y),
            distance_m=float(distance),
            input_crs=input_crs,
        )

    def component_id(self, node_id: int) -> int:
        return self.component_by_node[node_id]

    def shortest_path(
        self,
        origin_node: int,
        destination_node: int,
        blocked_segment_ids: set[int],
    ) -> tuple[list[int], list[tuple[int, int, int, dict[str, Any]]]] | None:
        """Dijkstra over the real MultiGraph, omitting blocked segment edges."""

        if origin_node == destination_node:
            return [origin_node], []
        distances: dict[int, float] = {origin_node: 0.0}
        predecessors: dict[int, tuple[int, int]] = {}
        queue: list[tuple[float, int]] = [(0.0, origin_node)]

        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            if node == destination_node:
                break
            for neighbor, keyed_edges in self.graph.adj[node].items():
                for edge_key, attributes in keyed_edges.items():
                    segment_id = int(attributes["segment_id"])
                    if segment_id in blocked_segment_ids:
                        continue
                    candidate = distance + float(attributes["length_m"])
                    if candidate < distances.get(neighbor, math.inf):
                        distances[neighbor] = candidate
                        predecessors[neighbor] = (node, int(edge_key))
                        heapq.heappush(queue, (candidate, neighbor))

        if destination_node not in distances:
            return None
        nodes = [destination_node]
        edges: list[tuple[int, int, int, dict[str, Any]]] = []
        current = destination_node
        while current != origin_node:
            previous, edge_key = predecessors[current]
            attributes = self.graph.get_edge_data(previous, current)[edge_key]
            edges.append((previous, current, edge_key, attributes))
            nodes.append(previous)
            current = previous
        nodes.reverse()
        edges.reverse()
        return nodes, edges


@app.command("inspect")
def inspect_graph(
    road_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_centrelines.gpkg",
        help="Realized OSM-derived road centreline GeoPackage.",
    ),
    lookup_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_segment_lookup.csv",
        help="Realized dense segment lookup CSV.",
    ),
) -> None:
    """Load and measure the realized OSM road graph on CPU."""

    network = RoadNetwork.from_files(road_file, lookup_file)
    typer.echo(json.dumps(network.summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
