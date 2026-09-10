"""Vehicle-specific flood impassability policies with mandatory citations.
integer-centimetre threshold and a citation whose source evidence is hash-bound
realized instance of this contract; this module still hardcodes no threshold.
Optional per-class annotations (``sensitivity_band_cm``, ``provenance_grade``,
threshold.  A top-level ``blocked_classes`` object passes through summaries"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from jaladhar.routing.graph import REPO

app = typer.Typer(add_completion=False)


class VehiclePolicyError(RuntimeError):
    """Raised for an invalid or missing vehicle policy file."""


@dataclass(frozen=True)
class VehiclePolicy:
    vehicle_class: str
    max_impassable_depth_cm: int
    citation_title: str
    citation_locator: str
    sensitivity_band_cm: list[int] | None = None
    provenance_grade: str | None = None
    interpretation: str | None = None

    def summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "vehicle_class": self.vehicle_class,
            "max_impassable_depth_cm": self.max_impassable_depth_cm,
            "citation": {
                "title": self.citation_title,
                "locator": self.citation_locator,
            },
        }
        if self.sensitivity_band_cm is not None:
            summary["sensitivity_band_cm"] = list(self.sensitivity_band_cm)
        if self.provenance_grade is not None:
            summary["provenance_grade"] = self.provenance_grade
        if self.interpretation is not None:
            summary["interpretation"] = self.interpretation
        return summary


class VehiclePolicyCatalog:

    def __init__(
        self,
        path: Path | None,
        policies: dict[str, VehiclePolicy],
        error: str | None,
        *,
        source_sha256: str | None = None,
        evidence_path: Path | None = None,
        evidence_sha256: str | None = None,
        blocked_classes: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.policies = policies
        self.error = error
        self.source_sha256 = source_sha256
        self.evidence_path = evidence_path
        self.evidence_sha256 = evidence_sha256
        self.blocked_classes = blocked_classes

    @property
    def available(self) -> bool:
        return self.error is None and bool(self.policies)

    def get(self, vehicle_class: str) -> VehiclePolicy:
        normalized = vehicle_class.strip().lower()
        if self.error is not None:
            raise VehiclePolicyError(self.error)
        if not normalized:
            raise VehiclePolicyError("vehicle_class is required")
        try:
            return self.policies[normalized]
        except KeyError as exc:
            raise VehiclePolicyError(
                f"no cited vehicle impassability policy is configured for {vehicle_class!r}"
            ) from exc

    def summary(self) -> dict[str, Any]:
        return {
            "status": "available" if self.available else "unavailable_external_unknown",
            "source": str(self.path) if self.path is not None else None,
            "source_sha256": self.source_sha256,
            "source_evidence": (
                {"path": str(self.evidence_path), "sha256": self.evidence_sha256}
                if self.evidence_path is not None
                else None
            ),
            "error": self.error,
            "policies": [self.policies[key].summary() for key in sorted(self.policies)],
            "blocked_classes": self.blocked_classes,
        }


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise VehiclePolicyError(f"{field} must be a positive integer centimetre value")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VehiclePolicyError(f"{field} must be a positive integer centimetre value") from exc
    if parsed <= 0 or str(value).strip() != str(parsed):
        raise VehiclePolicyError(f"{field} must be a positive integer centimetre value")
    return parsed


def _citation(entry: dict[str, Any], vehicle_class: str) -> tuple[str, str]:
    citation = entry.get("citation")
    if not isinstance(citation, dict):
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} lacks a citation object; external source is unknown"
        )
    title = str(citation.get("title", "")).strip()
    locator = str(citation.get("locator", "")).strip()
    if not title or not locator:
        raise VehiclePolicyError(f"vehicle class {vehicle_class!r} has an incomplete citation")
    return title, locator


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_relative_file(repo_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise VehiclePolicyError(f"{field} must be a non-empty repository-relative path")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise VehiclePolicyError(f"{field} must be repository-relative")
    resolved = (repo_root / raw).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise VehiclePolicyError(f"{field} escapes the repository") from exc
    if not resolved.is_file():
        raise VehiclePolicyError(f"{field} does not exist: {value}")
    return resolved


def _optional_sensitivity_band(value: Any, vehicle_class: str) -> list[int] | None:

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} sensitivity_band_cm must be a "
            "two-element [low_cm, high_cm] array"
        )
    try:
        low = int(value[0])
        high = int(value[1])
    except (TypeError, ValueError) as exc:
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} sensitivity_band_cm bounds must be integer cm"
        ) from exc
    if str(value[0]).strip() != str(low) or str(value[1]).strip() != str(high):
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} sensitivity_band_cm bounds must be integer cm"
        )
    if low <= 0 or high < low:
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} sensitivity_band_cm must satisfy 0 < low <= high"
        )
    return [low, high]


def _optional_text_field(value: Any, field: str, vehicle_class: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise VehiclePolicyError(
            f"vehicle class {vehicle_class!r} {field} must be a non-empty string when present"
        )
    return value.strip()


def load_vehicle_policies(path: Path | None, *, repo_root: Path = REPO) -> VehiclePolicyCatalog:

    if path is None:
        return VehiclePolicyCatalog(
            path=None,
            policies={},
            error=(
                "no vehicle policy file configured; the repository has no verified published "
                "vehicle-wading threshold, so routing refuses to choose one"
            ),
        )
    path = path.resolve()
    if not path.is_file():
        return VehiclePolicyCatalog(
            path=path,
            policies={},
            error=f"vehicle policy file is missing: {path}",
        )
    repo_root = repo_root.resolve()
    try:
        relative_policy = path.relative_to(repo_root)
    except ValueError:
        return VehiclePolicyCatalog(
            path=path,
            policies={},
            error="vehicle policy file must remain inside the repository",
        )
    allowed_roots = (("data", "curation"), ("runs",))
    if not any(relative_policy.parts[: len(root)] == root for root in allowed_roots):
        return VehiclePolicyCatalog(
            path=path,
            policies={},
            error="vehicle policy file must live under data/curation or runs",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return VehiclePolicyCatalog(
            path=path, policies={}, error=f"could not read vehicle policy: {exc}"
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("policies"), dict):
        return VehiclePolicyCatalog(
            path=path,
            policies={},
            error="vehicle policy JSON must contain an object named policies",
        )
    try:
        source_evidence = payload.get("source_evidence")
        if not isinstance(source_evidence, dict):
            raise VehiclePolicyError(
                "vehicle policy lacks source_evidence {path, sha256}; citation strings alone "
                "cannot verify a numeric threshold"
            )
        evidence_path = _repo_relative_file(
            repo_root, source_evidence.get("path"), "source_evidence.path"
        )
        if evidence_path.relative_to(repo_root).parts[:1] != ("data",):
            raise VehiclePolicyError("source_evidence.path must be under repository data/")
        evidence_hash = _sha256(evidence_path)
        if source_evidence.get("sha256") != evidence_hash:
            raise VehiclePolicyError("source_evidence.sha256 does not match realized bytes")
        # Shape-check only: blocked_classes documents deliberately unconfigured
        blocked_classes_raw = payload.get("blocked_classes")
        if blocked_classes_raw is not None and not isinstance(blocked_classes_raw, dict):
            raise VehiclePolicyError("blocked_classes must be an object when present")
        policies: dict[str, VehiclePolicy] = {}
        for raw_class, raw_entry in payload["policies"].items():
            vehicle_class = str(raw_class).strip().lower()
            if not vehicle_class or not isinstance(raw_entry, dict):
                raise VehiclePolicyError(
                    "each vehicle policy must be a non-empty object key and value"
                )
            if vehicle_class in policies:
                raise VehiclePolicyError(f"duplicate vehicle policy class {vehicle_class!r}")
            title, locator = _citation(raw_entry, vehicle_class)
            policies[vehicle_class] = VehiclePolicy(
                vehicle_class=vehicle_class,
                max_impassable_depth_cm=_positive_integer(
                    raw_entry.get("max_impassable_depth_cm"),
                    f"{vehicle_class}.max_impassable_depth_cm",
                ),
                citation_title=title,
                citation_locator=locator,
                sensitivity_band_cm=_optional_sensitivity_band(
                    raw_entry.get("sensitivity_band_cm"), vehicle_class
                ),
                provenance_grade=_optional_text_field(
                    raw_entry.get("provenance_grade"), "provenance_grade", vehicle_class
                ),
                interpretation=_optional_text_field(
                    raw_entry.get("interpretation"), "interpretation", vehicle_class
                ),
            )
        if not policies:
            raise VehiclePolicyError("vehicle policy JSON contains no classes")
    except VehiclePolicyError as exc:
        return VehiclePolicyCatalog(path=path, policies={}, error=str(exc))
    return VehiclePolicyCatalog(
        path=path,
        policies=policies,
        error=None,
        source_sha256=_sha256(path),
        evidence_path=evidence_path,
        evidence_sha256=evidence_hash,
        blocked_classes=blocked_classes_raw,
    )


@app.callback()
def _main() -> None:
    """Vehicle-policy loader CLI (M2 cited-wading policy track)."""


@app.command("validate")
def validate_policy(policy_file: Path = typer.Argument(...)) -> None:

    catalog = load_vehicle_policies(policy_file)
    if not catalog.available:
        raise typer.BadParameter(catalog.error or "vehicle policy unavailable")
    typer.echo(json.dumps(catalog.summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
