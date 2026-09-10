"""Scope: adapter construction and source-availability/provenance boundary only.
N-3 is BLOCKED and no captured IMD WFS response exists in ``data/``; therefore
green.  The source-backed conversion path remains executable when a verified"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.crs import CRS
from shapely.geometry import box, mapping
from shapely.ops import transform as transform_geometry

from jaladhar.forcing import nowcast
from jaladhar.forcing.interface import (
    ForcingMode,
    NowcastDistrictAbsentError,
    NowcastFetchError,
    NowcastUnavailableError,
    RainfallAdapter,
    RainfallEvent,
    RainfallInterval,
    assert_rate_conversion_parameterised,
    interval_depth_mm_to_rate_m_s,
)
from jaladhar.forcing.nowcast import ImdNowcastAdapter
from jaladhar.terrain.grid import Grid

START = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
END = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


def _canonical_grid() -> Grid:
    """Use the realized canonical lattice; this fixture never invents a model grid."""

    path = Path("data/processed/road_segment_id.tif")
    if not path.is_file():
        pytest.skip(
            "BLOCKED fixture scope: data/processed/road_segment_id.tif is required to "
            "exercise the full canonical nowcast seam"
        )
    with rasterio.open(path) as source:
        bounds = source.bounds
        return Grid(
            transform=source.transform,
            width=source.width,
            height=source.height,
            crs=CRS.from_user_input(source.crs),
            bounds=(bounds.left, bounds.bottom, bounds.right, bounds.top),
            resolution=float(source.transform.a),
        )


def _captured_source_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    product_id: str | None = "IMD_WFS_NowcastWarningDistrict",
    district_present: bool = True,
) -> tuple[Path, dict[str, object], Grid]:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(nowcast, "REPO", repo)
    grid = _canonical_grid()
    left = grid.transform.c
    top = grid.transform.f
    projected_cell = box(left, top - grid.resolution, left + grid.resolution, top)
    to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    source_geometry = transform_geometry(to_wgs84.transform, projected_cell)
    properties: dict[str, object] = {
        "district_name": "BANGLORE URBAN",
        "imd_category_code": "cat7",
        "toi": "0530",
        "vupto": "0545",
        "update_time": "2026-08-24T00:00:00Z",
        "fetch_url": "https://example.invalid/captured-not-fetched",
    }
    if product_id is not None:
        properties["product_id"] = product_id
    features: list[dict[str, object]] = []
    if district_present:
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(source_geometry),
            }
        )
    payload = {
        "type": "FeatureCollection",
        "features": features,
    }
    response_path = repo / "data/raw/imd/captured-response.json"
    response_path.parent.mkdir(parents=True)
    response_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    response_hash = hashlib.sha256(response_path.read_bytes()).hexdigest()
    config = repo / "configs/forcing.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "\n".join(
            (
                "nowcast_input:",
                "  interval_minutes: 15",
                "  poll_log_path: runs/forcing_nowcast/poll_log.jsonl",
                "  cache_dir: data/raw/imd/cache",
                "  courtesy_interval_seconds: 60",
                "",
            )
        ),
        encoding="utf-8",
    )
    envelope: dict[str, object] = {
        "response_path": "data/raw/imd/captured-response.json",
        "response_sha256": response_hash,
        "fetch_time": "2026-08-24T00:01:00+00:00",
        "http_status": 200,
        "cache_decision": "captured",
    }
    return config, envelope, grid


def test_nowcast_adapter_implements_interface_and_reports_n3_blocked() -> None:
    """If the source is unavailable, the independent observable is an explicit refusal."""

    adapter = ImdNowcastAdapter()

    assert isinstance(adapter, RainfallAdapter)
    assert adapter.mode is ForcingMode.NOWCAST
    assert adapter.availability["status"] == "blocked"
    assert adapter.availability["axis"] == "N-3 DWR/rainfall nowcast access"

    with pytest.raises(NowcastFetchError, match="N-3 is BLOCKED"):
        adapter.get_forcing(START, END)


def test_unavailable_source_red_under_mutation(tmp_path) -> None:
    """Mutation record: source callback is changed to raise; fallback must stay impossible.
    The deliberately unavailable callback is the red-under-mutation input.  A
    forcing mode here would make this assertion fail by returning a"""

    def unavailable_source() -> dict[str, object]:
        raise OSError("simulated source outage")

    config = tmp_path / "forcing.yaml"
    config.write_text(
        "\n".join(
            (
                "domain_config: configs/domain_bengaluru.yaml",
                "nowcast_input:",
                "  interval_minutes: 15",
                "  poll_log_path: runs/forcing_nowcast/poll_log.jsonl",
                "  cache_dir: data/raw/imd/cache",
                "  courtesy_interval_seconds: 60",
                "",
            )
        ),
        encoding="utf-8",
    )
    adapter = ImdNowcastAdapter(config, fetcher=unavailable_source)

    with pytest.raises(NowcastFetchError, match="refusing all fallback data"):
        adapter.get_forcing(START, END)


def test_missing_district_is_refused_without_a_zero_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the source has no target district, the independent observable is no event.
    Scope: one byte-bound captured envelope whose FeatureCollection holds zero
    features; the refusal must precede any interval construction."""

    config, envelope, grid = _captured_source_fixture(tmp_path, monkeypatch, district_present=False)
    adapter = ImdNowcastAdapter(config, source_envelope=envelope, grid=grid)

    with pytest.raises(NowcastDistrictAbsentError, match="BANGLORE URBAN"):
        adapter.get_forcing(START, END)


def test_bare_in_memory_response_is_refused_as_unprovenanced(tmp_path) -> None:
    """V5 invariant: an arbitrary in-memory mapping is not provenance-bearing source data.
    If this were broken, a caller could fabricate rainfall by passing any dict
    Scope: adapter construction and the pre-fetch startup gate only; no"""

    config = tmp_path / "forcing.yaml"
    config.write_text(
        "\n".join(
            (
                "domain_config: configs/domain_bengaluru.yaml",
                "nowcast_input:",
                "  interval_minutes: 15",
                "  poll_log_path: runs/forcing_nowcast/poll_log.jsonl",
                "  cache_dir: data/raw/imd/cache",
                "  courtesy_interval_seconds: 60",
                "",
            )
        ),
        encoding="utf-8",
    )

    def source_that_must_not_run() -> dict[str, object]:
        raise AssertionError("source must not run for an unprovenanced response")

    adapter = ImdNowcastAdapter(config, raw_response={"features": []}, fetcher=None)
    assert adapter.availability["status"] == "unbound_source_response"
    with pytest.raises(NowcastFetchError, match="unbound in-memory mapping"):
        adapter.get_forcing(START, END)
    assert source_that_must_not_run  # never invoked above; refusal is deterministic


def test_cli_request_side_violation_exits_2_before_source() -> None:
    """V5 invariant: naive-timestamp requests exit with the sanctioned code 2.
    Observable if broken: the CLI tracebacks with exit code 1 instead of mapping the
    deterministic pre-source refusal onto exit 2 like every other refusal path."""

    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        nowcast.app,
        ["--start", "2026-08-24T00:00:00", "--end", "2026-08-24T03:00:00Z"],
    )
    assert result.exit_code == 2
    assert "timezone-aware" in result.output


def test_naive_window_is_rejected_before_source_access() -> None:
    """If timezone handling were broken, a naive request would reach source parsing."""

    called = False

    def source_that_must_not_run() -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("source must not be called for a contract-invalid window")

    adapter = ImdNowcastAdapter(fetcher=source_that_must_not_run)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        adapter.get_forcing(START.replace(tzinfo=None), END)
    assert called is False


def test_horizon_above_three_hours_is_rejected_before_source_access() -> None:
    """If the 0-3 hour guard were absent, the source would receive an invalid request."""

    called = False

    def source_that_must_not_run() -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("source must not be called for an over-horizon window")

    adapter = ImdNowcastAdapter(fetcher=source_that_must_not_run)

    with pytest.raises(ValueError, match="0-3 hour horizon"):
        adapter.get_forcing(START, datetime(2026, 8, 24, 3, 1, tzinfo=UTC))
    assert called is False


def test_contract_exception_base_is_value_error() -> None:
    """The frozen exception contract remains catchable as a ValueError."""

    assert issubclass(NowcastFetchError, NowcastUnavailableError)
    assert issubclass(NowcastFetchError, ValueError)


def test_rate_conversion_uses_realized_interval_duration() -> None:
    """Mutation: replacing interval_minutes with 30 makes the 15-minute case red."""

    assert interval_depth_mm_to_rate_m_s(1.0, 15.0) == pytest.approx(1.0 / 900_000.0)
    assert interval_depth_mm_to_rate_m_s(1.0, 30.0) == pytest.approx(1.0 / 1_800_000.0)


def test_missing_cadence_refuses_before_source_fetch(tmp_path) -> None:
    """V5 red: startup config failure must be observed before any external source access."""

    config = tmp_path / "forcing.yaml"
    config.write_text("domain_config: configs/domain_bengaluru.yaml\n", encoding="utf-8")
    called = False

    def source_that_must_not_run() -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("source must not run before startup config resolves")

    adapter = ImdNowcastAdapter(config, fetcher=source_that_must_not_run)
    with pytest.raises(NowcastUnavailableError, match="interval_minutes"):
        adapter.get_forcing(START, END)
    assert called is False


def test_named_rate_assertion_observes_consumer_output() -> None:
    """V5 red: the historic factor-two consumer mutant must fail at 15-minute cadence."""

    native_ids = np.zeros((1, 1), dtype=np.int32)
    interval = RainfallInterval(
        timestamp=START,
        interval_minutes=15.0,
        rainfall_grid_mm=np.ones((1, 1), dtype=np.float32),
        native_cell_ids=native_ids,
        distinct_native_cells=1,
    )
    event = RainfallEvent(
        mode=ForcingMode.NOWCAST,
        intervals=[interval],
        cell_resolution_m=10.0,
        source_name="consumer-observable-fixture",
    )
    hardcoded_factor_two_rate = np.array([2.0 / 3600.0 / 1000.0], dtype=np.float64)

    with pytest.raises(AssertionError, match="consumer rate conversion"):
        assert_rate_conversion_parameterised(event, [hardcoded_factor_two_rate])


def test_source_backed_district_cells_do_not_alias_dry_outside_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V5 invariant: one valid district ID may coexist with uncovered sentinel cells.
    and reintegrate zero instead of the realized district rainfall depth. Scope: one"""

    config, envelope, grid = _captured_source_fixture(tmp_path, monkeypatch)
    adapter = ImdNowcastAdapter(
        config,
        source_envelope=envelope,
        grid=grid,
        clock=lambda: datetime(2026, 8, 24, 0, 10, tzinfo=UTC),
    )
    event = adapter.get_forcing(
        datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 24, 0, 15, tzinfo=UTC),
    )
    interval = event.intervals[0]
    inside = interval.native_cell_ids == 0
    outside = interval.native_cell_ids == -1

    assert interval.distinct_native_cells == 1
    assert inside.any() and outside.any()
    assert np.all(interval.rainfall_grid_mm[inside] > 0.0)
    assert np.all(interval.rainfall_grid_mm[outside] == 0.0)
    rate = np.array(
        [
            interval.rainfall_grid_mm[inside][0]
            * (60.0 / interval.interval_minutes)
            / 3600.0
            / 1000.0
        ]
    )
    assert assert_rate_conversion_parameterised(event, [rate])
    poll_log = tmp_path / "repo/runs/forcing_nowcast/poll_log.jsonl"
    poll_entry = json.loads(poll_log.read_text(encoding="utf-8").strip())
    assert poll_entry["response_sha256"] == envelope["response_sha256"]
    assert poll_entry["http_status"] == 200
    assert event.metadata["source_response"]["path"] == envelope["response_path"]


def test_startup_collects_domain_poll_cache_and_rate_gaps_before_fetch(
    tmp_path: Path,
) -> None:
    """V5 invariant: every deterministic downstream gap is visible before fetch."""

    config = tmp_path / "forcing.yaml"
    config.write_text("nowcast_input:\n  interval_minutes: 15\n", encoding="utf-8")
    called = False

    def source_that_must_not_run() -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    adapter = ImdNowcastAdapter(config, fetcher=source_that_must_not_run)
    with pytest.raises(NowcastFetchError) as caught:
        adapter.get_forcing(START, END)
    message = str(caught.value)
    for field in (
        "domain_config",
        "poll_log_path",
        "cache_dir",
        "courtesy_interval_seconds",
    ):
        assert field in message
    assert called is False


def test_source_envelope_rejects_missing_product_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V5 invariant: provenance cannot default an absent product_id."""

    config, envelope, grid = _captured_source_fixture(tmp_path, monkeypatch, product_id=None)
    adapter = ImdNowcastAdapter(config, source_envelope=envelope, grid=grid)
    with pytest.raises(NowcastUnavailableError, match="product_id is missing"):
        adapter.get_forcing(
            datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 0, 15, tzinfo=UTC),
        )


def test_source_envelope_hash_mismatch_fails_before_payload_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V5 invariant: asserted source identity must match realized response bytes."""

    config, envelope, grid = _captured_source_fixture(tmp_path, monkeypatch)
    envelope["response_sha256"] = "0" * 64
    adapter = ImdNowcastAdapter(config, source_envelope=envelope, grid=grid)
    with pytest.raises(NowcastFetchError, match="response_sha256"):
        adapter.get_forcing(
            datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 0, 15, tzinfo=UTC),
        )
