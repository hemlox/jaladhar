"""Unpack the seeded inputs and report what still has to be fetched.

`data/` is gitignored (8.8 GB). Only `data/seed/` ships: the inputs that are
irreplaceable or costly to re-source. This unpacks them into the paths the
pipeline expects, verifies checksums, then prints the fetch checklist.

    python scripts/bootstrap_data.py            # unpack + report
    python scripts/bootstrap_data.py --check    # report only, touch nothing
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import tarfile
from pathlib import Path

import typer

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "data" / "seed"

# seed file -> destination the pipeline reads from
PLAIN = {
    "sept2022_points.csv": "data/raw/groundtruth/sept2022_points.csv",
}
GZIPPED = {
    "fetched_articles.json.gz": "data/fetched_articles.json",
    "unrepresentative_underpass_register.csv.gz": (
        "data/interim/terrain/unrepresentative_underpass_register.csv"
    ),
}
TARBALLS = {"bbmp_drains_2022.tar.gz": "data/raw"}
DIRS = {"bbmp": "data/raw/bbmp"}

FETCH = [
    (
        "BBMP 2023 boundary + FABDEM / GLO-30 DEM",
        "python -m jaladhar.terrain.fetch",
        "no credentials; boundary is byte-verified and GLO-30 is the fallback",
    ),
    (
        "OSM standing-water, quarry, landfill polygons, roads, waterways, buildings",
        "python -m jaladhar.terrain.water / .roads / .drains / .buildings",
        "no credentials",
    ),
    ("GPM IMERG granules", "python -m jaladhar.forcing.fetch_imerg", "EARTHDATA_TOKEN + one-time GES DISC EULA click"),
    (
        "Sentinel-1 GRD scenes",
        "python -m jaladhar.validation.fetch_s1",
        "CDSE_S3_ENDPOINT + CDSE_ACCESS_KEY + CDSE_SECRET_KEY",
    ),
    ("Derived terrain rasters", "python -m jaladhar.terrain.build", "CPU-hours; depends on the DEM above"),
]

app = typer.Typer(add_completion=False)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _verify() -> list[str]:
    sums = SEED / "SHA256SUMS"
    if not sums.exists():
        return ["SHA256SUMS missing from data/seed/"]
    bad = []
    for line in sums.read_text().splitlines():
        if not line.strip():
            continue
        want, name = line.split(maxsplit=1)
        target = SEED / name.strip()
        if not target.exists():
            bad.append(f"missing: {name.strip()}")
        elif _sha256(target) != want:
            bad.append(f"checksum mismatch: {name.strip()}")
    return bad


@app.command()
def main(check: bool = typer.Option(False, "--check", help="report only, write nothing")) -> None:
    if not SEED.exists():
        raise typer.Exit(f"no seed bundle at {SEED}")

    problems = _verify()
    if problems:
        for p in problems:
            typer.echo(f"  FAIL  {p}")
        raise typer.Exit(1)
    typer.echo(f"seed bundle verified against SHA256SUMS ({SEED})\n")

    for src, dst in PLAIN.items():
        out = REPO / dst
        typer.echo(f"  {'would write' if check else 'writing'}  {dst}")
        if not check:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SEED / src, out)

    for src, dst in GZIPPED.items():
        out = REPO / dst
        typer.echo(f"  {'would write' if check else 'writing'}  {dst}")
        if not check:
            out.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(SEED / src, "rb") as fin, out.open("wb") as fout:
                shutil.copyfileobj(fin, fout)

    for src, dst in TARBALLS.items():
        out = REPO / dst
        typer.echo(f"  {'would extract' if check else 'extracting'}  {src} -> {dst}/")
        if not check:
            out.mkdir(parents=True, exist_ok=True)
            with tarfile.open(SEED / src) as tf:
                tf.extractall(out, filter="data")

    for src, dst in DIRS.items():
        out = REPO / dst
        typer.echo(f"  {'would copy' if check else 'copying'}  {src}/ -> {dst}/")
        if not check:
            out.mkdir(parents=True, exist_ok=True)
            for f in (SEED / src).iterdir():
                shutil.copy2(f, out / f.name)

    typer.echo("\nSeeded. Still to fetch (none of it ships — see docs/BOOTSTRAP.md):\n")
    for name, cmd, note in FETCH:
        typer.echo(f"  {name}\n      {cmd}\n      {note}\n")
    typer.echo("The 24-point ground-truth set is hand-built from news research and CANNOT")
    typer.echo("be regenerated. It is in the seed bundle. Do not overwrite it.")


if __name__ == "__main__":
    app()
