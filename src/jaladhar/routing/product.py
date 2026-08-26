"""Read and validate the frozen per-segment depth product contract."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer

from jaladhar.routing.graph import REPO
from jaladhar.validation.depth_product_contract import (
    MAX_FORECAST_LEAD_MINUTES,
    DepthProductContractError,
    load_requirements,
    validate_product_manifest,
)

app = typer.Typer(add_completion=False)


class DepthProductError(RuntimeError):
    """Raised when a flood product is absent or violates its frozen contract."""


@dataclass(frozen=True)
class SegmentFloodState:
    segment_id: int
    band_low_cm: int
    band_high_cm: int
    confidence: str
    flood_status: str


@dataclass(frozen=True)
class DepthProduct:
    source_path: Path
    source_manifest_path: Path
    run_id: str
    forecast_lead_minutes: int
    valid_time_utc: datetime
    issue_time_utc: datetime
    rows: dict[int, SegmentFloodState]
    manifest: dict[str, Any]
    flood_band_threshold_cm: int

    def state(self, segment_id: int) -> SegmentFloodState:
        try:
            return self.rows[segment_id]
        except KeyError as exc:
            raise DepthProductError(
                f"depth product has no realized state for graph segment_id={segment_id}"
            ) from exc

    def summary(self) -> dict[str, Any]:
        unknown = sum(row.flood_status == "unknown" for row in self.rows.values())
        flooded = sum(row.flood_status == "flooded" for row in self.rows.values())
        return {
            "status": "available",
            "product_label": self.manifest.get("product_label"),
            "forcing_kind": self.manifest.get("forcing_kind"),
            "temporal_aggregation": self.manifest.get("temporal_aggregation"),
            "event_window_start_utc": self.manifest.get("event_window_start_utc"),
            "event_window_end_utc": self.manifest.get("event_window_end_utc"),
            "source": str(self.source_path),
            "source_manifest": str(self.source_manifest_path),
            "run_id": self.run_id,
            "forecast_lead_minutes": self.forecast_lead_minutes,
            "valid_time_utc": self.valid_time_utc.isoformat(),
            "issue_time_utc": self.issue_time_utc.isoformat(),
            "segment_rows": len(self.rows),
            "flooded_rows": flooded,
            "unknown_rows": unknown,
            "flood_band_threshold_cm_from_contract": self.flood_band_threshold_cm,
        }


def _aware_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DepthProductError(f"{field} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise DepthProductError(f"{field} is not valid ISO8601: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DepthProductError(f"{field} must be aware UTC, got {value!r}")
    return parsed.astimezone(UTC)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise DepthProductError(f"{field} must be an integer, not boolean")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise DepthProductError(f"{field} must be an integer, got {value!r}") from exc
    if str(value).strip() != str(parsed):
        raise DepthProductError(f"{field} must be written as an integer, got {value!r}")
    return parsed


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DepthProductError(f"could not read depth-product JSON {path}: {exc}") from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = next(
                (
                    payload[key]
                    for key in ("rows", "segments", "data")
                    if isinstance(payload.get(key), list)
                ),
                None,
            )
        else:
            rows = None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise DepthProductError(
                "depth-product JSON twin has no documented row shape; expected a list or "
                "an object containing rows/segments/data"
            )
        return rows

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError as exc:
        raise DepthProductError(f"could not read depth-product CSV {path}: {exc}") from exc


def load_depth_product(
    product_path: Path,
    repo_root: Path = REPO,
    lookup_path: Path | None = None,
    contract_path: Path | None = None,
) -> DepthProduct:
    """Load a read-only CSV/JSON depth product and enforce consumer assertions."""

    product_path = product_path.resolve()
    repo_root = repo_root.resolve()
    lookup_path = (
        lookup_path or repo_root / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
    ).resolve()
    contract_path = (
        contract_path or repo_root / "configs" / "contracts" / "depth_product.json"
    ).resolve()
    if not product_path.is_file():
        raise DepthProductError(
            f"no realized depth product exists at {product_path}; routing refuses to "
            "invent flood state"
        )
    rows = _read_rows(product_path)
    required = {
        "run_id",
        "segment_id",
        "band_low_cm",
        "band_high_cm",
        "confidence",
        "flood_status",
        "valid_time_utc",
        "issue_time_utc",
        "forecast_lead_minutes",
        "source_manifest_path",
    }
    if not rows:
        raise DepthProductError(f"realized depth product is empty: {product_path}")
    missing = required - set(rows[0])
    if missing:
        raise DepthProductError(f"depth product lacks required columns: {sorted(missing)}")

    lookup_ids: set[int] | None = None
    if lookup_path.is_file():
        try:
            with lookup_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                lookup_ids = {_integer(row["segment_id"], "lookup.segment_id") for row in reader}
        except (OSError, KeyError) as exc:
            raise DepthProductError(f"could not read segment lookup {lookup_path}: {exc}") from exc
    if lookup_ids is None or not lookup_ids:
        raise DepthProductError(f"realized segment lookup is unavailable or empty: {lookup_path}")

    try:
        requirements = load_requirements(contract_path)
    except DepthProductContractError as exc:
        raise DepthProductError(str(exc)) from exc
    flood_band_threshold_cm = requirements.flood_threshold_cm
    parsed: dict[int, SegmentFloodState] = {}
    normalized_rows: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    source_manifest_values: set[str] = set()
    valid_times: set[datetime] = set()
    issue_times: set[datetime] = set()
    leads: set[int] = set()

    for row_number, row in enumerate(rows, start=2):
        missing = required - set(row)
        if missing:
            raise DepthProductError(f"depth product row {row_number} lacks {sorted(missing)}")
        segment_id = _integer(row["segment_id"], f"row {row_number}.segment_id")
        if segment_id in parsed:
            raise DepthProductError(f"duplicate segment_id={segment_id} at row {row_number}")
        band_low = _integer(row["band_low_cm"], f"row {row_number}.band_low_cm")
        band_high = _integer(row["band_high_cm"], f"row {row_number}.band_high_cm")
        if band_low < 0 or band_high < 0 or band_low > band_high:
            raise DepthProductError(
                f"invalid depth band at row {row_number}: {band_low}, {band_high}"
            )
        confidence = str(row["confidence"]).strip()
        status = str(row["flood_status"]).strip()
        legal_confidence = {"modeled_direct", "modeled_interpolated", "no_data"}
        legal_status = {"flooded", "not_flooded", "unknown"}
        if confidence not in legal_confidence or status not in legal_status:
            raise DepthProductError(f"invalid confidence/status at row {row_number}")
        if confidence == "modeled_interpolated":
            raise DepthProductError(
                f"modeled_interpolated at row {row_number} is reserved and invalid in v1"
            )
        if confidence == "no_data" and (status != "unknown" or (band_low, band_high) != (0, 0)):
            raise DepthProductError(f"no_data row {row_number} violates unknown/[0,0] rule")
        if confidence != "no_data" and status == "unknown":
            raise DepthProductError(f"modeled row {row_number} cannot have unknown status")
        if status == "flooded" and band_high < flood_band_threshold_cm:
            raise DepthProductError(
                f"flooded row {row_number} has band_high_cm={band_high}, below contract "
                f"threshold {flood_band_threshold_cm}"
            )
        run_id = str(row["run_id"]).strip()
        source_manifest_value = str(row["source_manifest_path"]).strip()
        if not run_id or not source_manifest_value:
            raise DepthProductError(f"row {row_number} lacks run_id or source_manifest_path")
        source_manifest_path = Path(source_manifest_value)
        if source_manifest_path.is_absolute():
            raise DepthProductError(f"row {row_number} source_manifest_path must be repo-relative")
        source_manifest_path = (repo_root / source_manifest_path).resolve()
        try:
            source_manifest_path.relative_to(repo_root)
        except ValueError as exc:
            raise DepthProductError(
                f"row {row_number} source_manifest_path escapes repo root"
            ) from exc
        if not source_manifest_path.is_file():
            raise DepthProductError(
                f"row {row_number} source manifest is missing: {source_manifest_path}"
            )
        valid_time = _aware_utc(row["valid_time_utc"], f"row {row_number}.valid_time_utc")
        issue_time = _aware_utc(row["issue_time_utc"], f"row {row_number}.issue_time_utc")
        lead = _integer(row["forecast_lead_minutes"], f"row {row_number}.forecast_lead_minutes")
        if not 0 <= lead <= MAX_FORECAST_LEAD_MINUTES:
            raise DepthProductError(
                f"row {row_number} forecast lead must be within the fixed 0-180 minute horizon"
            )
        if lead == 0 and issue_time < valid_time:
            raise DepthProductError(f"nowcast row {row_number} has issue_time before valid_time")
        if lead > 0 and issue_time > valid_time:
            raise DepthProductError(f"forecast row {row_number} has issue_time after valid_time")

        run_ids.add(run_id)
        source_manifest_values.add(str(source_manifest_path))
        valid_times.add(valid_time)
        issue_times.add(issue_time)
        leads.add(lead)
        parsed[segment_id] = SegmentFloodState(
            segment_id=segment_id,
            band_low_cm=band_low,
            band_high_cm=band_high,
            confidence=confidence,
            flood_status=status,
        )
        normalized_rows.append(
            {
                **row,
                "run_id": run_id,
                "segment_id": segment_id,
                "band_low_cm": band_low,
                "band_high_cm": band_high,
                "confidence": confidence,
                "flood_status": status,
                "forecast_lead_minutes": lead,
            }
        )

    if (
        len(run_ids) != 1
        or len(source_manifest_values) != 1
        or len(valid_times) != 1
        or len(issue_times) != 1
    ):
        raise DepthProductError("depth product metadata changes within one product file")
    if len(leads) != 1:
        raise DepthProductError("forecast_lead_minutes changes within one product file")
    if set(parsed) != lookup_ids:
        raise DepthProductError(
            "depth product segment IDs do not exactly match the realized lookup: "
            f"missing={len(lookup_ids - set(parsed))} extra={len(set(parsed) - lookup_ids)}"
        )

    manifest_path = Path(next(iter(source_manifest_values)))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DepthProductError(f"could not read source manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise DepthProductError(f"source manifest is not a JSON object: {manifest_path}")
    try:
        validate_product_manifest(
            manifest,
            manifest_path,
            rows=normalized_rows,
            lookup_ids=lookup_ids,
            repo_root=repo_root,
            contract_path=contract_path,
        )
    except DepthProductContractError as exc:
        raise DepthProductError(str(exc)) from exc
    return DepthProduct(
        source_path=product_path,
        source_manifest_path=manifest_path,
        run_id=next(iter(run_ids)),
        forecast_lead_minutes=next(iter(leads)),
        valid_time_utc=next(iter(valid_times)),
        issue_time_utc=next(iter(issue_times)),
        rows=parsed,
        manifest=manifest,
        flood_band_threshold_cm=flood_band_threshold_cm,
    )


@app.command("validate")
def validate_product(
    product_file: Path = typer.Argument(..., help="Realized segment_status.csv or its JSON twin."),
    lookup_file: Path = typer.Option(
        REPO / "data" / "interim" / "terrain" / "roads_segment_lookup.csv"
    ),
    contract_file: Path = typer.Option(REPO / "configs" / "contracts" / "depth_product.json"),
) -> None:
    """Validate a realized depth product without modifying it."""

    product = load_depth_product(product_file, REPO, lookup_file, contract_file)
    typer.echo(json.dumps(product.summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
