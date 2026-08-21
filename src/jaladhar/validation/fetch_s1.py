"""Fetch the Sentinel-1 GRD pair for the September 2022 Bengaluru flood.

OPEN-ITEMS.md item 6. Downloads VV measurement plus the annotation, calibration
and noise XMLs — calibration is not optional: thresholding raw DNs instead of
calibrated sigma0 would make the backscatter cut meaningless across scenes.

Pair rationale (see OPEN-ITEMS.md item 6): the 24 Aug 2022 acquisition is absent
from the catalogue, so the pre-event reference is 12 Aug — 24 days before the
flood image rather than the ideal 12-day repeat.

Streams to disk via boto3 download_file; never buffers a 670 MB band in RAM
(host RAM is the binding constraint on this machine, see CLAUDE.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import boto3
import typer
from dotenv import load_dotenv

app = typer.Typer(add_completion=False)

REPO = Path(__file__).resolve().parents[3]

SCENES = {
    "flood_20220905": (
        "Sentinel-1/SAR/IW_GRDH_1S-COG/2022/09/05/"
        "S1A_IW_GRDH_1SDV_20220905T004028_20220905T004053_044862_055BB0_D13D_COG.SAFE/"
    ),
    "pre_20220812": (
        "Sentinel-1/SAR/IW_GRDH_1S-COG/2022/08/12/"
        "S1A_IW_GRDH_1SDV_20220812T004026_20220812T004051_044512_054FD9_3309_COG.SAFE/"
    ),
}

# VV only: validation.yaml consumes the measurement plus the annotation,
# calibration, and noise XMLs for that same polarization.  Selecting by both
# directory and basename prevents VH files and unrelated SAFE metadata from
# entering the evidence bundle.
REQUIRED_OBJECT_KINDS = ("measurement", "annotation", "calibration", "noise")


def client():
    load_dotenv(REPO / ".env")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["CDSE_S3_ENDPOINT"],
        aws_access_key_id=os.environ["CDSE_ACCESS_KEY"],
        aws_secret_access_key=os.environ["CDSE_SECRET_KEY"],
        region_name="default",
    )


def required_object_kind(key: str, prefix: str) -> str | None:
    """Return the required VV object kind represented by an S3 key, if any."""
    if not key.startswith(prefix):
        return None
    rel = key[len(prefix) :].lower()
    basename = rel.rsplit("/", 1)[-1]
    if "-vv-" not in basename:
        return None
    if rel.startswith("measurement/") and basename.endswith((".tif", ".tiff")):
        return "measurement"
    if rel.startswith("annotation/calibration/") and basename.startswith("calibration-"):
        return "calibration" if basename.endswith(".xml") else None
    if rel.startswith("annotation/calibration/") and basename.startswith("noise-"):
        return "noise" if basename.endswith(".xml") else None
    if rel.startswith("annotation/") and "/" not in rel[len("annotation/") :]:
        return "annotation" if basename.endswith(".xml") else None
    return None


def list_required_objects(s3: Any, prefix: str) -> list[dict[str, Any]]:
    """List all pages and return exactly one object for every required kind."""
    objects: list[dict[str, Any]] = []
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": "eodata", "Prefix": prefix}
        if continuation_token is not None:
            kwargs["ContinuationToken"] = continuation_token
        response = s3.list_objects_v2(**kwargs)
        objects.extend(response.get("Contents", []))
        if not response.get("IsTruncated", False):
            break
        continuation_token = response.get("NextContinuationToken")
        if not continuation_token:
            raise RuntimeError(f"truncated S3 listing for {prefix} omitted continuation token")

    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in REQUIRED_OBJECT_KINDS}
    for obj in objects:
        kind = required_object_kind(obj["Key"], prefix)
        if kind is not None:
            by_kind[kind].append(obj)

    bad_counts = {kind: len(found) for kind, found in by_kind.items() if len(found) != 1}
    if bad_counts:
        raise RuntimeError(
            f"Sentinel-1 SAFE {prefix} must contain exactly one required VV object per kind; "
            f"bad counts={bad_counts}"
        )
    return [by_kind[kind][0] for kind in REQUIRED_OBJECT_KINDS]


def remove_stale_partials_after_verification(target: Path) -> list[Path]:
    """Remove interrupted siblings only after a complete target exists.

    A successful exact-size target is the independent observable that makes a
    ``.part*`` sibling stale. This avoids deleting a possible in-progress file
    when no verified target exists.
    """
    stale = sorted(target.parent.glob(f"{target.name}.part*"))
    for path in stale:
        path.unlink()
    return stale


@app.command()
def main(
    out: Path = typer.Option(REPO / "data/raw/sentinel1", help="Download directory"),
) -> None:
    """Download the Sept 2022 flood/pre-event Sentinel-1 VV pair."""
    s3 = client()
    for label, prefix in SCENES.items():
        dest = out / label
        dest.mkdir(parents=True, exist_ok=True)
        picked = list_required_objects(s3, prefix)
        typer.echo(f"\n=== {label}: {len(picked)} objects ===")
        for o in picked:
            rel = o["Key"][len(prefix) :]
            target = dest / rel
            if target.exists() and target.stat().st_size == o["Size"]:
                removed = remove_stale_partials_after_verification(target)
                for stale in removed:
                    typer.echo(f"  removed stale partial {stale.relative_to(dest)}")
                typer.echo(f"  skip (have) {rel}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            typer.echo(f"  get {o['Size'] / 1e6:8.1f} MB  {rel}")
            tmp = target.with_suffix(target.suffix + ".part")
            s3.download_file("eodata", o["Key"], str(tmp))
            actual_size = tmp.stat().st_size
            if actual_size != o["Size"]:
                raise RuntimeError(
                    f"Sentinel-1 object size mismatch for {o['Key']}: "
                    f"downloaded {actual_size}, expected {o['Size']}"
                )
            tmp.rename(target)  # atomic: a partial file must never look complete
            remove_stale_partials_after_verification(target)
    typer.echo("\ndone")


if __name__ == "__main__":
    app()
