"""The frozen ``nowcast_input`` contract describes an IMD WFS warning product,
access) BLOCKED.  Consequently this module has no default network source.
source response, parses only the frozen contract fields, and raises when
interpolation are not specified by the frozen contract and are refused;
using the declared contract mapping, with the heavy value retained as a
lower bound in provenance rather than presented as a measured intensity;
No live source is claimed by this module while N-3 remains blocked."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import typer
import yaml
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry

from jaladhar.forcing.interface import (
    ForcingMode,
    NowcastDistrictAbsentError,
    NowcastFetchError,
    NowcastStaleError,
    NowcastUnavailableError,
    RainfallAdapter,
    RainfallEvent,
    RainfallInterval,
    assert_nowcast_metadata_contract,
)
from jaladhar.terrain.grid import Grid, build_grid

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

CANONICAL_SHAPE = (3421, 3515)  # height x width, frozen nowcast_input contract
CANONICAL_RESOLUTION_M = 10.0
CANONICAL_EPSG = 32643
MAX_HORIZON = timedelta(hours=3)
IST = ZoneInfo("Asia/Kolkata")

N3_AXIS = "N-3 DWR/rainfall nowcast access"
N3_BLOCKED_MESSAGE = (
    "N-3 is BLOCKED: no verified IMD/DWR nowcast source is configured or available. "
    "The adapter refuses to produce rainfall until that external axis is resolved."
)

DISTRICT_PRODUCT = "IMD_WFS_NowcastWarningDistrict"
STATION_PRODUCT = "IMD_WFS_NowcastWarningStation"
DISTRICT_NAME = "BANGLORE URBAN"

# configs/contracts/nowcast_input.json, not measured rainfall intensities.
INTENSITY_MAPPING: dict[str, dict[str, float | bool | None] | str] = {
    "light": {
        "nominal_mm_hr": 2.5,
        "lower": 0.0,
        "upper": 5.0,
        "is_lower_bound": False,
    },
    "moderate": {
        "nominal_mm_hr": 10.0,
        "lower": 5.0,
        "upper": 15.0,
        "is_lower_bound": False,
    },
    "heavy": {
        "nominal_mm_hr": 15.0,
        "lower": 15.0,
        "upper": None,
        "is_lower_bound": True,
    },
    "basis": "DECLARED ASSUMPTION - categorical bands not measured intensities",
}
CATEGORY_TO_BAND = {"cat2": "light", "cat7": "moderate", "cat12": "heavy"}


SourceFetcher = Callable[[], Mapping[str, Any]]
SOURCE_CACHE_DECISIONS = {"captured", "cache_hit", "fetched"}


def git_sha() -> str:

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=REPO,
        ).strip()
    except Exception:
        return "unknown"


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return raw if isinstance(raw, dict) else {}


def _configured_interval_minutes(config: Mapping[str, Any]) -> int | None:

    nowcast_input = config.get("nowcast_input")
    if not isinstance(nowcast_input, Mapping):
        return None

    value = nowcast_input.get("interval_minutes")
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return None
    return interval if interval in {5, 10, 15, 30} else None


def _require_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC; naive timestamps are forbidden")
    return value.astimezone(UTC)


def _parse_aware_utc_iso(value: Any, name: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.strip():
        raise NowcastFetchError(f"{name} must be a non-empty aware-UTC ISO timestamp")
    raw = value.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise NowcastFetchError(f"{name} is not valid ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise NowcastFetchError(f"{name} must be expressed as aware UTC")
    return parsed.astimezone(UTC), raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(value: Any, name: str, *, allowed_roots: tuple[str, ...]) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise NowcastFetchError(f"{name} must be a non-empty repository-relative path")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise NowcastFetchError(f"{name} must remain repository-relative")
    path = (REPO / raw).resolve()
    try:
        relative = path.relative_to(REPO.resolve())
    except ValueError as exc:
        raise NowcastFetchError(f"{name} escapes the repository") from exc
    if relative.parts[:1] not in {(root,) for root in allowed_roots}:
        raise NowcastFetchError(
            f"{name} must be rooted under one of {sorted(allowed_roots)}, got {value!r}"
        )
    return path


def _parse_update_time(value: Any) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.strip():
        raise NowcastUnavailableError(
            "IMD update_time is missing; refusing a provenance-less nowcast response."
        )
    raw = value.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise NowcastUnavailableError(f"Invalid IMD update_time {raw!r}.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NowcastUnavailableError("IMD update_time must be aware UTC, not a naive timestamp.")
    if parsed.utcoffset() != timedelta(0):
        raise NowcastUnavailableError(
            "IMD update_time must be expressed in UTC; refusing a non-UTC offset."
        )
    return parsed.astimezone(UTC), raw


def _parse_ist_hhmm(value: Any, field: str) -> tuple[int, int]:
    if isinstance(value, bool):
        raise NowcastUnavailableError(f"IMD {field} is not a valid HHMM time.")
    text = str(value).strip()
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3])[0-5][0-9]", text):
        raise NowcastUnavailableError(
            f"IMD {field} must be a naive IST HHMM string; got {value!r}."
        )
    return int(text[:2]), int(text[2:])


def _local_time_for_date(day: date, value: Any, field: str) -> datetime:
    hour, minute = _parse_ist_hhmm(value, field)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def _parse_validity_times(
    toi: Any,
    vupto: Any,
    update_time: datetime,
) -> tuple[datetime, datetime]:

    local_update = update_time.astimezone(IST)
    issue_local = _local_time_for_date(local_update.date(), toi, "toi")
    # backfilled rainfall interval; the source's validity fields remain the
    if issue_local > local_update:
        issue_local -= timedelta(days=1)

    valid_local = _local_time_for_date(issue_local.date(), vupto, "vupto")
    if valid_local <= issue_local:
        valid_local += timedelta(days=1)

    issue_time = issue_local.astimezone(UTC)
    valid_until = valid_local.astimezone(UTC)
    if valid_until <= issue_time:
        raise NowcastUnavailableError("IMD validity window is empty or negative.")
    return issue_time, valid_until


def _ceil_wall_clock(value: datetime, interval_minutes: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    seconds = (value - epoch).total_seconds()
    step_seconds = interval_minutes * 60
    aligned_seconds = math.ceil(seconds / step_seconds - 1e-12) * step_seconds
    return epoch + timedelta(seconds=aligned_seconds)


def _feature_properties(
    feature: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise NowcastUnavailableError("IMD WFS feature has no properties object.")
    # provenance, never rainfall data; all warning fields remain strict.
    if "fetch_url" not in properties and "fetch_url" in payload:
        merged = dict(properties)
        merged["fetch_url"] = payload["fetch_url"]
        return merged
    return properties


def _validate_grid(grid: Grid) -> None:
    if (grid.height, grid.width) != CANONICAL_SHAPE:
        raise NowcastUnavailableError(
            "Canonical nowcast grid mismatch: expected height x width "
            f"{CANONICAL_SHAPE}, got {(grid.height, grid.width)}."
        )
    if abs(float(grid.resolution) - CANONICAL_RESOLUTION_M) > 1e-9:
        raise NowcastUnavailableError(
            f"Canonical nowcast resolution mismatch: expected {CANONICAL_RESOLUTION_M} m, "
            f"got {grid.resolution} m."
        )
    if CRS.from_user_input(grid.crs).to_epsg() != CANONICAL_EPSG:
        raise NowcastUnavailableError(
            f"Canonical nowcast CRS mismatch: expected EPSG:{CANONICAL_EPSG}, got {grid.crs}."
        )


class ImdNowcastAdapter(RainfallAdapter):
    """``fetcher`` is an integration seam for a verified source client.  It must
    ``raw_response`` is equivalent for a caller that already fetched a source"""

    def __init__(
        self,
        config_path: Path = REPO / "configs/forcing.yaml",
        *,
        fetcher: SourceFetcher | None = None,
        raw_response: Mapping[str, Any] | None = None,
        source_envelope: Mapping[str, Any] | None = None,
        grid: Grid | None = None,
        clock: Callable[[], datetime] | None = None,
        fetch_url: str | None = None,
    ) -> None:
        supplied_sources = sum(
            value is not None for value in (fetcher, raw_response, source_envelope)
        )
        if supplied_sources > 1:
            raise ValueError("Provide exactly one of fetcher, source_envelope, or raw_response.")
        self.config_path = Path(config_path)
        self.config = _load_config(self.config_path)
        self.grid = grid
        self._fetcher = fetcher
        self._raw_response = raw_response
        self._source_envelope = source_envelope
        self._source_evidence: dict[str, Any] | None = None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fetch_url = fetch_url or self._configured_fetch_url()
        self.interval_minutes = _configured_interval_minutes(self.config)
        self._config_gaps = self._find_config_gaps()

        # -1 is the uncovered/dry sentinel. The only valid source-native cell
        self.native_cell_ids = np.full(CANONICAL_SHAPE, -1, dtype=np.int32)
        self.native_cell_ids.flags.writeable = False

        if fetcher is None and source_envelope is None and raw_response is None:
            status = "blocked"
            reason = N3_BLOCKED_MESSAGE
        elif fetcher is not None:
            status = "injected_fetcher"
            reason = "Source fetcher supplied by caller; live availability is not asserted."
        elif source_envelope is not None:
            status = "captured_source_response"
            reason = (
                "Byte-bound captured source envelope supplied; live availability is not asserted."
            )
        else:
            status = "unbound_source_response"
            reason = "Bare in-memory response lacks byte provenance and will be refused."
        self.availability = {
            "status": status,
            "axis": N3_AXIS,
            "reason": reason,
            "config_gaps": list(self._config_gaps),
        }

    def _configured_fetch_url(self) -> str | None:
        nowcast_input = self.config.get("nowcast_input")
        if not isinstance(nowcast_input, Mapping):
            return None
        endpoint = nowcast_input.get("endpoint")
        return endpoint if isinstance(endpoint, str) and endpoint.strip() else None

    def _find_config_gaps(self) -> list[str]:
        gaps: list[str] = []
        nowcast_input = self.config.get("nowcast_input")
        if not isinstance(nowcast_input, Mapping):
            gaps.append("configs/forcing.yaml has no nowcast_input section")
            nowcast_input = {}
        if self.interval_minutes is None:
            gaps.append("nowcast_input.interval_minutes is absent or outside enum [5,10,15,30]")
        if self._raw_response is not None:
            gaps.append(
                "raw_response is an unbound in-memory mapping; a byte-bound source_envelope "
                "with path/hash/fetch evidence is required"
            )
        if (
            self._fetch_url is None
            and self._fetcher is None
            and self._source_envelope is None
            and self._raw_response is None
        ):
            gaps.append(N3_BLOCKED_MESSAGE)
        for key in ("poll_log_path", "cache_dir", "courtesy_interval_seconds"):
            if key not in nowcast_input:
                gaps.append(f"nowcast_input.{key} is absent")
        courtesy = nowcast_input.get("courtesy_interval_seconds")
        if courtesy is not None and (
            isinstance(courtesy, bool)
            or not isinstance(courtesy, (int, float))
            or not math.isfinite(float(courtesy))
            or float(courtesy) <= 0.0
        ):
            gaps.append("nowcast_input.courtesy_interval_seconds must be finite and positive")
        for key, roots in (("poll_log_path", ("runs",)), ("cache_dir", ("data", "runs"))):
            if key in nowcast_input:
                try:
                    _repo_path(nowcast_input[key], f"nowcast_input.{key}", allowed_roots=roots)
                except NowcastFetchError as exc:
                    gaps.append(str(exc))
        try:
            self._load_canonical_grid()
        except NowcastUnavailableError as exc:
            gaps.append(f"domain_config/grid: {exc}")
        return gaps

    def _require_startup_config(self) -> None:
        """Resolve every adapter input needed later before acquiring source bytes."""

        if self._config_gaps:
            raise NowcastFetchError(
                "nowcast startup configuration failed before source access:\n- "
                + "\n- ".join(self._config_gaps)
            )

    @property
    def mode(self) -> ForcingMode:
        return ForcingMode.NOWCAST

    def _get_envelope(self) -> Mapping[str, Any]:
        if self._source_envelope is not None:
            if not isinstance(self._source_envelope, Mapping):
                raise NowcastFetchError("Captured nowcast source envelope is not a mapping.")
            return self._source_envelope
        if self._fetcher is None:
            raise NowcastFetchError(N3_BLOCKED_MESSAGE)
        try:
            envelope = self._fetcher()
        except NowcastUnavailableError:
            raise
        except Exception as exc:
            raise NowcastFetchError(
                f"Nowcast source fetch failed; refusing all fallback data: {exc}"
            ) from exc
        if not isinstance(envelope, Mapping):
            raise NowcastFetchError("Nowcast source returned no provenance envelope mapping.")
        return envelope

    def _get_payload(self) -> Mapping[str, Any]:
        envelope = self._get_envelope()
        response_path = _repo_path(
            envelope.get("response_path"),
            "source_envelope.response_path",
            allowed_roots=("data", "runs"),
        )
        if not response_path.is_file():
            raise NowcastFetchError(
                f"source_envelope.response_path does not exist: {response_path}"
            )
        expected_hash = envelope.get("response_sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise NowcastFetchError("source_envelope.response_sha256 must be lowercase SHA-256")
        realized_hash = _sha256_file(response_path)
        if expected_hash != realized_hash:
            raise NowcastFetchError(
                "source_envelope.response_sha256 does not match realized response bytes"
            )
        fetch_time, fetch_time_raw = _parse_aware_utc_iso(
            envelope.get("fetch_time"), "source_envelope.fetch_time"
        )
        http_status = envelope.get("http_status")
        if type(http_status) is not int or not 100 <= http_status <= 599:
            raise NowcastFetchError("source_envelope.http_status must be an integer HTTP status")
        cache_decision = envelope.get("cache_decision")
        if cache_decision not in SOURCE_CACHE_DECISIONS:
            raise NowcastFetchError(
                f"source_envelope.cache_decision must be one of {sorted(SOURCE_CACHE_DECISIONS)}"
            )
        try:
            payload = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NowcastFetchError(
                f"captured nowcast response is not readable JSON: {response_path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise NowcastFetchError("captured nowcast response is not a GeoJSON mapping")
        self._source_evidence = {
            "path": response_path.relative_to(REPO.resolve()).as_posix(),
            "sha256": realized_hash,
            "fetch_time": fetch_time,
            "fetch_time_raw": fetch_time_raw,
            "http_status": http_status,
            "cache_decision": cache_decision,
        }
        return payload

    def _select_district_feature(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        features = payload.get("features")
        if not isinstance(features, list):
            raise NowcastUnavailableError(
                "Nowcast source response is not a GeoJSON FeatureCollection."
            )
        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for feature in features:
            if not isinstance(feature, Mapping):
                continue
            properties = _feature_properties(feature, payload)
            if properties.get("district_name") == DISTRICT_NAME:
                matches.append((feature, properties))
        if not matches:
            raise NowcastDistrictAbsentError(
                "BANGLORE URBAN is absent from the nowcast source response; refusing data."
            )
        if len(matches) != 1:
            raise NowcastDistrictAbsentError(
                "Nowcast source contains multiple BANGLORE URBAN features; refusing ambiguity."
            )
        return matches[0]

    def _parse_feature(
        self,
        feature: Mapping[str, Any],
        properties: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if "product_id" not in properties:
            raise NowcastUnavailableError(
                "IMD product_id is missing; refusing to manufacture source identity."
            )
        product_id = properties["product_id"]
        if product_id != DISTRICT_PRODUCT:
            if product_id == STATION_PRODUCT:
                raise NowcastUnavailableError(
                    "IMD station nowcast is not implemented: the frozen contract does not "
                    "define station-to-canonical-grid geometry or interpolation."
                )
            raise NowcastUnavailableError(f"Unsupported IMD nowcast product_id {product_id!r}.")

        category_code = str(properties.get("imd_category_code", "")).strip().lower()
        if not re.fullmatch(r"cat(?:[1-9]|1[0-9])", category_code):
            raise NowcastUnavailableError(
                "IMD imd_category_code is missing or outside the verified cat1-cat19 schema."
            )
        band = CATEGORY_TO_BAND.get(category_code)
        if band is None:
            raise NowcastUnavailableError(
                f"IMD category {category_code} has no frozen intensity mapping; refusing to guess."
            )

        geometry = feature.get("geometry")
        if not isinstance(geometry, Mapping):
            raise NowcastDistrictAbsentError(
                "District nowcast feature has no GeoJSON geometry; refusing a spatially "
                "unbounded fill."
            )
        try:
            source_geometry = shape(geometry)
        except Exception as exc:
            raise NowcastUnavailableError("District nowcast geometry could not be parsed.") from exc
        if source_geometry.is_empty or not source_geometry.is_valid:
            raise NowcastUnavailableError("District nowcast geometry is empty or invalid.")

        update_value = properties.get("update_time", payload.get("update_time"))
        update_time, update_time_raw = _parse_update_time(update_value)
        issue_time, valid_until = _parse_validity_times(
            properties.get("toi"),
            properties.get("vupto"),
            update_time,
        )

        fetch_url_value = properties.get("fetch_url", self._fetch_url)
        if not isinstance(fetch_url_value, str) or not fetch_url_value.strip():
            raise NowcastUnavailableError(
                "Nowcast provenance has no fetch_url; refusing an untraceable source response."
            )

        return {
            "product_id": product_id,
            "district_name": DISTRICT_NAME,
            "imd_category_code": category_code,
            "band": band,
            "nominal_mm_hr": float(INTENSITY_MAPPING[band]["nominal_mm_hr"]),  # type: ignore[index]
            "intensity_mapping": {
                key: (dict(value) if isinstance(value, dict) else value)
                for key, value in INTENSITY_MAPPING.items()
            },
            "issue_time": issue_time,
            "valid_until": valid_until,
            "update_time": update_time_raw,
            "update_time_utc": update_time,
            "fetch_url": fetch_url_value.strip(),
            "geometry": source_geometry,
        }

    def _load_canonical_grid(self) -> Grid:
        if self.grid is None:
            domain_config_value = self.config.get("domain_config")
            if not isinstance(domain_config_value, str) or not domain_config_value:
                raise NowcastUnavailableError(
                    "Canonical domain_config is unavailable; refusing to invent a rainfall grid."
                )
            domain_config_path = Path(domain_config_value)
            if not domain_config_path.is_absolute():
                domain_config_path = REPO / domain_config_path
            try:
                with domain_config_path.open(encoding="utf-8") as handle:
                    domain_config = yaml.safe_load(handle)
                self.grid, _ = build_grid(domain_config, REPO)
            except NowcastUnavailableError:
                raise
            except Exception as exc:
                raise NowcastUnavailableError(
                    f"Canonical domain grid could not be realized: {exc}"
                ) from exc
        _validate_grid(self.grid)
        return self.grid

    def _district_mask(self, source_geometry: Any, grid: Grid) -> np.ndarray:
        try:
            transformer = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
            projected = transform_geometry(transformer.transform, source_geometry)
            mask = rasterize(
                [(projected, 1)],
                out_shape=(grid.height, grid.width),
                transform=grid.transform,
                fill=0,
                dtype="uint8",
            ).astype(bool)
        except Exception as exc:
            raise NowcastUnavailableError(
                "District GeoJSON could not be reprojected/rasterized onto EPSG:32643."
            ) from exc
        if not np.any(mask):
            raise NowcastDistrictAbsentError(
                "BANGLORE URBAN geometry has no cells on the canonical grid; refusing output."
            )
        return mask

    def _source_metadata(self, source: Mapping[str, Any]) -> dict[str, Any]:
        if self._source_evidence is None:
            raise NowcastFetchError("source response evidence was not resolved")
        nowcast_input = self.config["nowcast_input"]
        poll_log = _repo_path(
            nowcast_input["poll_log_path"],
            "nowcast_input.poll_log_path",
            allowed_roots=("runs",),
        )
        cache_dir = _repo_path(
            nowcast_input["cache_dir"],
            "nowcast_input.cache_dir",
            allowed_roots=("data", "runs"),
        )
        fetch_time = self._source_evidence["fetch_time"]
        freshness_seconds = (fetch_time - source["update_time_utc"]).total_seconds()
        if freshness_seconds < 0.0:
            raise NowcastFetchError("source fetch_time precedes source update_time")
        return {
            "path": self._source_evidence["path"],
            "sha256": self._source_evidence["sha256"],
            "fetch_time": self._source_evidence["fetch_time_raw"],
            "http_status": self._source_evidence["http_status"],
            "cache_decision": self._source_evidence["cache_decision"],
            "cache_dir": cache_dir.relative_to(REPO.resolve()).as_posix(),
            "poll_log_path": poll_log.relative_to(REPO.resolve()).as_posix(),
            "courtesy_interval_seconds": float(nowcast_input["courtesy_interval_seconds"]),
            "freshness_seconds": freshness_seconds,
        }

    def _append_poll_log(self, source: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
        poll_log = REPO / str(evidence["poll_log_path"])
        poll_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "fetch_time": evidence["fetch_time"],
            "http_status": evidence["http_status"],
            "issue_time": source["issue_time"].isoformat(),
            "valid_until": source["valid_until"].isoformat(),
            "freshness_seconds": evidence["freshness_seconds"],
            "response_path": evidence["path"],
            "response_sha256": evidence["sha256"],
            "cache_decision": evidence["cache_decision"],
        }
        with poll_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")

    def get_forcing(self, start_time: datetime, end_time: datetime) -> RainfallEvent:
        """Return source-backed incremental rainfall for at most three hours."""

        start = _require_utc(start_time, "start_time")
        end = _require_utc(end_time, "end_time")
        if end <= start:
            raise ValueError("end_time must be after start_time")
        if end - start > MAX_HORIZON:
            raise ValueError("IMD nowcast requests are limited to the frozen 0-3 hour horizon")

        self._require_startup_config()
        payload = self._get_payload()
        feature, properties = self._select_district_feature(payload)
        source = self._parse_feature(feature, properties, payload)

        interval_minutes = self.interval_minutes
        if interval_minutes is None:
            raise NowcastUnavailableError(
                "Nowcast cadence is unresolved: configs/forcing.yaml has no valid "
                "nowcast_input.interval_minutes."
            )
        now = _require_utc(self._clock(), "nowcast clock")
        validity_window = source["valid_until"] - source["issue_time"]
        if now > source["valid_until"] + validity_window:
            raise NowcastStaleError(
                "Nowcast source is stale beyond one validity window; refusing persistence."
            )

        grid = self._load_canonical_grid()
        mask = self._district_mask(source["geometry"], grid)
        self.native_cell_ids = np.full((grid.height, grid.width), -1, dtype=np.int32)
        self.native_cell_ids[mask] = 0
        self.native_cell_ids.flags.writeable = False
        source_evidence = self._source_metadata(source)
        self._append_poll_log(source, source_evidence)

        effective_start = max(start, source["issue_time"])
        effective_end = min(end, source["valid_until"])
        first_start = _ceil_wall_clock(effective_start, interval_minutes)
        if first_start >= effective_end:
            raise NowcastStaleError(
                "Requested window has no source-valid wall-clock interval; refusing backfill."
            )

        intervals: list[RainfallInterval] = []
        lead_times_minutes: list[float] = []
        current = first_start
        while current < effective_end:
            interval_end = min(
                current + timedelta(minutes=interval_minutes),
                effective_end,
            )
            duration_minutes = (interval_end - current).total_seconds() / 60.0
            if duration_minutes <= 0.0:
                break
            depth_mm = source["nominal_mm_hr"] * duration_minutes / 60.0
            grid_mm = np.zeros((grid.height, grid.width), dtype=np.float32)
            grid_mm[mask] = np.float32(depth_mm)
            lead_time_minutes = (current - source["issue_time"]).total_seconds() / 60.0
            interval_metadata = {
                "product_id": source["product_id"],
                "issue_time": source["issue_time"],
                "valid_until": source["valid_until"],
                "update_time": source["update_time"],
                "fetch_url": source["fetch_url"],
                "district_name": source["district_name"],
                "imd_category_code": source["imd_category_code"],
                "lead_time_minutes": lead_time_minutes,
                "lead_time_hours": lead_time_minutes / 60.0,
                "interval_end": interval_end,
                "truncated_at_valid_until": interval_end == source["valid_until"]
                and interval_end < end,
                "source_response": dict(source_evidence),
            }
            intervals.append(
                RainfallInterval(
                    timestamp=current,
                    interval_minutes=duration_minutes,
                    rainfall_grid_mm=grid_mm,
                    native_cell_ids=self.native_cell_ids,
                    distinct_native_cells=1,
                    metadata=interval_metadata,
                )
            )
            lead_times_minutes.append(lead_time_minutes)
            current = interval_end

        if not intervals:
            raise NowcastStaleError("Source response produced no usable nowcast intervals.")

        event = RainfallEvent(
            mode=self.mode,
            intervals=intervals,
            cell_resolution_m=grid.resolution,
            source_name=source["product_id"],
            metadata={
                "product_id": source["product_id"],
                "issue_time": source["issue_time"],
                "valid_until": source["valid_until"],
                "update_time": source["update_time"],
                "fetch_url": source["fetch_url"],
                "district_name": source["district_name"],
                "imd_category_code": source["imd_category_code"],
                "intensity_mapping": source["intensity_mapping"],
                "intensity_band": source["band"],
                "truncated_at_valid_until": effective_end < end,
                "lead_times_minutes": lead_times_minutes,
                "lead_times_hours": [lead / 60.0 for lead in lead_times_minutes],
                "grid_shape": [grid.height, grid.width],
                "grid_crs": f"EPSG:{CANONICAL_EPSG}",
                "cell_resolution_m": grid.resolution,
                "source_axis": N3_AXIS,
                "source_status": "captured_or_injected_source_response",
                "source_response": dict(source_evidence),
            },
        )
        event.verify_mass_conservation()
        assert_nowcast_metadata_contract(event)
        return event


def _parse_cli_time(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return _require_utc(datetime.fromisoformat(candidate), "CLI timestamp")


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Forcing YAML path"),
    start: str = typer.Option("2026-08-24T00:00:00Z", help="Window start, aware UTC ISO"),
    end: str = typer.Option("2026-08-24T03:00:00Z", help="Window end, aware UTC ISO"),
) -> None:
    """Probe the nowcast source; blocked N-3 exits without producing rainfall."""

    adapter = ImdNowcastAdapter(config)
    try:
        event = adapter.get_forcing(_parse_cli_time(start), _parse_cli_time(end))
    except NowcastUnavailableError as exc:
        typer.echo(f"NOWCAST UNAVAILABLE [{exc.axis}]: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        # Request-side deterministic refusals (naive timestamps, over-horizon windows)
        # exit with the same sanctioned code as every other pre-source refusal.
        typer.echo(f"NOWCAST UNAVAILABLE [N-3 DWR/rainfall nowcast access]: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("Nowcast adapter execution summary:")
    typer.echo(f"  status: {adapter.availability['status']}")
    typer.echo(f"  product: {event.metadata['product_id']}")
    typer.echo(f"  intervals: {event.num_intervals}")
    typer.echo(f"  lead_times_minutes: {event.metadata['lead_times_minutes']}")
    typer.echo("  mass_conservation: PASS")
    typer.echo(f"  git_sha: {git_sha()}")


if __name__ == "__main__":
    app()
