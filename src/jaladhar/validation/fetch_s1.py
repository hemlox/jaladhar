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

# VV only: PROMPT.md §8 specifies VV backscatter thresholding. VH is present in
# the same products if urban-density stratification later needs it.
WANTED = ("-vv-", "quick-look.png")


def client():
    load_dotenv(REPO / ".env")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["CDSE_S3_ENDPOINT"],
        aws_access_key_id=os.environ["CDSE_ACCESS_KEY"],
        aws_secret_access_key=os.environ["CDSE_SECRET_KEY"],
        region_name="default",
    )


@app.command()
def main(
    out: Path = typer.Option(REPO / "data/raw/sentinel1", help="Download directory"),
) -> None:
    """Download the Sept 2022 flood/pre-event Sentinel-1 VV pair."""
    s3 = client()
    for label, prefix in SCENES.items():
        dest = out / label
        dest.mkdir(parents=True, exist_ok=True)
        objs = s3.list_objects_v2(Bucket="eodata", Prefix=prefix).get("Contents", [])
        picked = [o for o in objs if any(w in o["Key"] for w in WANTED)]
        typer.echo(f"\n=== {label}: {len(picked)} objects ===")
        for o in picked:
            rel = o["Key"][len(prefix) :]
            target = dest / rel
            if target.exists() and target.stat().st_size == o["Size"]:
                typer.echo(f"  skip (have) {rel}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            typer.echo(f"  get {o['Size'] / 1e6:8.1f} MB  {rel}")
            tmp = target.with_suffix(target.suffix + ".part")
            s3.download_file("eodata", o["Key"], str(tmp))
            tmp.rename(target)  # atomic: a partial file must never look complete
    typer.echo("\ndone")


if __name__ == "__main__":
    app()
