"""Fetch GPM IMERG half-hourly granules for a date window over Bengaluru.

OPEN-ITEMS.md item 8 / the gate's event-forcing fallback. IMERG is what forces
the September 2022 replay because KSNDMC historical gauge data is unavailable
(item 1b). Raw granules are kept whole for provenance; the Bengaluru window is
extracted alongside as the actual forcing input.

SCALE CAVEAT, to be repeated wherever this data is used: the full BBMP extent
(717 km^2) falls inside a 3x3 block of 0.1-degree IMERG cells. Nine values cover
198 wards. Per-ward rainfall attribution is not defensible from this source.

Requires EARTHDATA_TOKEN in .env AND one-time acceptance of the GES DISC EULA at
https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ
(a 403 "EULA Acceptance Failure" means the click was not done, not a bad token).
"""

from __future__ import annotations

import csv
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import typer
from dotenv import load_dotenv

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
COLLECTION = "C2723754847-GES_DISC"  # GPM_3IMERGHH v07 (Final run)

# BBMP extent, from the 2023 ward boundaries (OPEN-ITEMS.md question A).
BBOX = (77.4601, 12.8336, 77.7844, 13.1427)


def token() -> str:
    load_dotenv(REPO / ".env")
    t = os.environ.get("EARTHDATA_TOKEN")
    if not t:
        raise SystemExit("EARTHDATA_TOKEN missing from .env")
    return t


def granule_urls(start: date, end: date, tok: str) -> list[str]:
    r = requests.get(
        CMR,
        params={
            "collection_concept_id": COLLECTION,
            "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z",
            "page_size": 2000,
        },
        headers={"Authorization": f"Bearer {tok}"},
        timeout=120,
    )
    r.raise_for_status()
    urls = []
    for e in r.json()["feed"]["entry"]:
        for ln in e.get("links", []):
            h = ln.get("href", "")
            if h.endswith(".HDF5") and h.startswith("http"):
                urls.append(h)
                break
    # CMR can expose the same data link through duplicate metadata entries.
    # Deduplicate before parallel dispatch: two workers sharing one ``.part``
    # path can otherwise race at the atomic rename and report FileNotFoundError.
    return list(dict.fromkeys(urls))


def fetch_one(url: str, dest_dir: Path, tok: str) -> tuple[str, str]:
    name = url.rsplit("/", 1)[-1]
    target = dest_dir / name
    if target.exists() and target.stat().st_size > 1_000_000:
        return name, "have"
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(
            url, headers={"Authorization": f"Bearer {tok}"}, stream=True, timeout=300
        ) as r:
            if r.status_code != 200:
                return name, f"HTTP{r.status_code}"
            with open(tmp, "wb") as fh:  # stream: never buffer a granule in RAM
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        tmp.rename(target)  # atomic
        return name, "ok"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return name, type(e).__name__


def extract_window(files: list[Path], out_csv: Path) -> int:
    """Pull the 3x3 Bengaluru block from each granule into one tidy CSV."""
    import h5py
    import numpy as np

    rows = []
    for p in sorted(files):
        try:
            with h5py.File(p, "r") as f:
                g = f["Grid"]
                lon, lat = g["lon"][:], g["lat"][:]
                ilon = np.where((lon >= BBOX[0]) & (lon <= BBOX[2]))[0]
                ilat = np.where((lat >= BBOX[1]) & (lat <= BBOX[3]))[0]
                pr = g["precipitation"][0][np.ix_(ilon, ilat)]
                pr = np.where(pr < 0, np.nan, pr)  # IMERG fill value is negative
                # granule name carries ...3IMERG.YYYYMMDD-SHHMMSS-E...
                stamp = p.name.split(".3IMERG.")[1].split("-")[0:2]
                ts = datetime.strptime(stamp[0] + stamp[1][1:], "%Y%m%d%H%M%S")
                rows.append(
                    {
                        "time_utc": ts.isoformat(),
                        "mean_mm_hr": float(np.nanmean(pr)),
                        "max_mm_hr": float(np.nanmax(pr)),
                        "n_cells": int(pr.size),
                    }
                )
        except Exception as e:  # a corrupt granule must not kill the run
            typer.echo(f"  skip {p.name}: {type(e).__name__} {e}")
    rows.sort(key=lambda r: r["time_utc"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["time_utc", "mean_mm_hr", "max_mm_hr", "n_cells"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


@app.command()
def main(
    start: str = typer.Option("2022-08-28", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("2022-09-10", help="End date YYYY-MM-DD"),
    workers: int = typer.Option(4, help="Parallel downloads (keep modest — host RAM)"),
    out: Path = typer.Option(REPO / "data/raw/imerg", help="Output directory"),
) -> None:
    """Download IMERG granules and extract the Bengaluru window."""
    tok = token()
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    dest = out / f"{start}_{end}"
    dest.mkdir(parents=True, exist_ok=True)

    urls = granule_urls(s, e, tok)
    typer.echo(f"{len(urls)} granules for {start}..{end} ({(e - s + timedelta(days=1)).days} days)")

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (_name, status) in enumerate(ex.map(lambda u: fetch_one(u, dest, tok), urls), 1):
            counts[status] = counts.get(status, 0) + 1
            if i % 50 == 0:
                typer.echo(f"  {i}/{len(urls)} {counts}")
    typer.echo(f"download summary: {counts}")

    files = sorted(dest.glob("*.HDF5"))
    csv_path = out / f"bengaluru_window_{start}_{end}.csv"
    n = extract_window(files, csv_path)
    typer.echo(f"extracted {n} timesteps -> {csv_path}")


if __name__ == "__main__":
    app()
