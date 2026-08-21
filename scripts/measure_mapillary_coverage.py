#!/usr/bin/env python3
"""Measure Mapillary street-level imagery coverage at Bengaluru underpass locations.

Research probe; no project-code changes. Every number written to the outputs is
either returned by a live HTTP response ([FINDING]) or a gate with the exact
request path and observed response ([BLOCKED]). No estimates, no invented tokens
(CLAUDE.md rules 3 & 6).

Usage:
    python scripts/measure_mapillary_coverage.py
"""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER_CSV = ROOT / "data/raw/underpass_search" / "search_register.csv"
OUT_DIR = ROOT / "data/raw/underpass_search" / "mapillary"
COVERAGE_CSV = OUT_DIR / "coverage.csv"
MANIFEST_JSON = OUT_DIR / "manifest.json"
ENV_FILE = ROOT / ".env"

RADIUS_M = 100.0
TIMEOUT_S = 25.0
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0
POLITE_SLEEP_S = 1.0
BODY_PREFIX_CHARS = 300
USER_AGENT = "JALADHAR-coverage-probe/1.0 (research; python stdlib only)"
REGISTRATION_URL = "https://www.mapillary.com/dashboard/developers"

# (name, url) -- probed once before the per-location loop.
PROBE_URLS = [
    ("graph_v4_root", "https://graph.mapillary.com/"),
    ("graph_v4_images_nobbox", "https://graph.mapillary.com/v4/images?fields=id&limit=1"),
    ("legacy_v3_root", "https://a.mapillary.com/"),
    ("legacy_v3_images", "https://a.mapillary.com/v3/images?limit=1"),
    ("web_mapim_root", "https://www.mapillary.com/map-im/"),
    ("vtp_v2_tiles", "https://tiles.mapillary.com/maps/vtp/v2/1/0/0"),
]


def redact(url: str) -> str:
    """Never write a token/client_id into outputs."""
    for key in ("access_token", "client_id"):
        url = re.sub(rf"({key}=)[^&]+", r"\1[REDACTED]", url)
    return url


def git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def file_git_sha(rel_path: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", f"HEAD:{rel_path}"], capture_output=True, text=True, cwd=ROOT
    )
    return out.stdout.strip() if out.returncode == 0 else "uncommitted"


def load_env_tokens() -> dict:
    """Read .env, return {KEY: value} only for keys whose name contains MAPILLARY.

    Values are never printed or written to outputs.
    """
    tokens: dict[str, str] = {}
    if not ENV_FILE.exists():
        return tokens
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if "MAPILLARY" in key.upper() and value:
            tokens[key] = value
    return tokens


def http_probe(url: str) -> dict:
    """One HTTP request, retried on timeout/429. Returns an observation dict.

    Keys: requested_url, final_url, status, reason, content_type, body_prefix,
    body_chars, body_full (only meaningful for small JSON), attempts.
    """
    attempts = 0
    while True:
        attempts += 1
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
                return {
                    "requested_url": redact(url),
                    "final_url": resp.geturl(),
                    "status": resp.status,
                    "reason": getattr(resp, "reason", ""),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "body_prefix": raw[:BODY_PREFIX_CHARS].decode("utf-8", errors="replace"),
                    "body_chars": len(raw),
                    "body_full": raw.decode("utf-8", errors="replace"),
                    "attempts": attempts,
                }
        except urllib.error.HTTPError as e:
            raw = e.read()
            obs = {
                "requested_url": redact(url),
                "final_url": e.geturl() or url,
                "status": e.code,
                "reason": str(e.reason),
                "content_type": e.headers.get("Content-Type", ""),
                "body_prefix": raw[:BODY_PREFIX_CHARS].decode("utf-8", errors="replace"),
                "body_chars": len(raw),
                "body_full": raw.decode("utf-8", errors="replace"),
                "attempts": attempts,
            }
            if e.code == 429 and attempts <= MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempts)
                continue
            return obs
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempts <= MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempts)
                continue
            return {
                "requested_url": redact(url),
                "final_url": url,
                "status": None,
                "reason": f"{type(e).__name__}: {e}",
                "content_type": "",
                "body_prefix": "",
                "body_chars": 0,
                "body_full": "",
                "attempts": attempts,
            }


def bbox_for(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.05))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def probe_location(row: dict, token: str | None) -> dict:
    """Query the v4 graph API for images in a radius around one location."""
    lat = float(row["lat"])
    lon = float(row["lon"])
    west, south, east, north = bbox_for(lat, lon, RADIUS_M)
    params = {
        "fields": "id,sequence",
        "limit": "200",
        "bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",  # v4 order: west,south,east,north
    }
    if token:
        params["access_token"] = token
    url = "https://graph.mapillary.com/v4/images?" + urllib.parse.urlencode(params)
    obs = http_probe(url)

    rec = {
        "osm_id": (row.get("osm_id") or "").strip(),
        "location_name": (row.get("location_name") or "").strip(),
        "lat": lat,
        "lon": lon,
        "radius_m": RADIUS_M,
        "status": "BLOCKED",
        "images_in_bbox": None,
        "distinct_sequences": None,
        "http_status": obs["status"],
        "note": "",
    }

    if obs["status"] == 200:
        try:
            payload = json.loads(obs["body_full"])
        except json.JSONDecodeError:
            rec["status"] = "BLOCKED_PARSE"
            rec["note"] = f"HTTP 200 but body is not JSON; body_prefix={obs['body_prefix']!r}"
            return rec
        images = payload.get("data") or []
        rec["status"] = "FINDING"
        rec["images_in_bbox"] = len(images)
        rec["distinct_sequences"] = len(
            {img.get("sequence") for img in images if img.get("sequence")}
        )
        meta = payload.get("meta") or {}
        rec["note"] = f"first page only (limit=200); next_cursor={meta.get('next_cursor')!r}"
        return rec

    if token is None:
        rec["status"] = "BLOCKED_NO_TOKEN"
        rec["note"] = (
            f"graph.mapillary.com v4 requires access_token; HTTP {obs['status']} "
            f"{obs['reason']}; body={obs['body_prefix']!r}; no token in .env; "
            f"registration: {REGISTRATION_URL}"
        )
    else:
        rec["status"] = f"BLOCKED_HTTP_{obs['status']}"
        rec["note"] = f"HTTP {obs['status']} {obs['reason']}; body={obs['body_prefix']!r}"
    return rec


def write_manifest(run: dict, endpoints: list[dict], locations: list[dict]) -> None:
    manifest = {
        "run": run,
        "token": {"mapillary_token_in_env": bool(run.get("_token_present"))},
        "endpoints_probed": [{k: v for k, v in e.items() if k != "body_full"} for e in endpoints],
        "per_location": locations,
    }
    run.pop("_token_present", None)
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    start_time = datetime.now(UTC).isoformat()
    run = {
        "script": "scripts/measure_mapillary_coverage.py",
        "git_sha": git_sha(),
        "script_git_sha": file_git_sha("scripts/measure_mapillary_coverage.py"),
        "register_git_sha": file_git_sha("data/raw/underpass_search/search_register.csv"),
        "start_time": start_time,
        "end_time": None,
        "wall_clock_s": None,
        "status": "running",
        "coverage_status": None,
        "python": sys.version.split()[0],
        "radius_m": RADIUS_M,
        "user_agent": USER_AGENT,
        "registration_path": REGISTRATION_URL,
        "note": "Manifest written at run start and updated in place on completion (rule 6).",
    }
    _token_present = False  # local only; never written

    tokens = load_env_tokens()
    token = tokens.get("MAPILLARY_TOKEN") or tokens.get("MAPILLARY_ACCESS_TOKEN")
    if token:
        _token_present = True
    run["_token_present"] = _token_present
    run["token_keys_present"] = sorted(tokens)  # key names only, never values
    write_manifest(run, [], [])

    print(f"[probe] git_sha={run['git_sha']}  token_in_env={_token_present}")

    endpoints: list[dict] = []
    for name, url in PROBE_URLS:
        obs = http_probe(url)
        obs["name"] = name
        endpoints.append(obs)
        print(
            f"[probe] {name:<22} HTTP {obs['status']} "
            f"final={obs['final_url'][:60]!r} body={obs['body_prefix'][:80]!r}"
        )
        time.sleep(POLITE_SLEEP_S)

    v4_gate = next((e for e in endpoints if e["name"] == "graph_v4_images_nobbox"), None)
    v4_requires_token = bool(v4_gate and v4_gate["status"] in (401, 403))

    with REGISTER_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"[probe] register rows={len(rows)}")

    locations: list[dict] = []
    for idx, row in enumerate(rows):
        if v4_requires_token and token is None:
            rec = {
                "osm_id": (row.get("osm_id") or "").strip(),
                "location_name": (row.get("location_name") or "").strip(),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "radius_m": RADIUS_M,
                "status": "BLOCKED_NO_TOKEN",
                "images_in_bbox": None,
                "distinct_sequences": None,
                "http_status": v4_gate["status"],
                "probed_live": False,
                "note": (
                    f"v4 API requires access_token (live probe HTTP "
                    f"{v4_gate['status']}, body={v4_gate['body_prefix']!r}); "
                    f"no token in .env; registration: {REGISTRATION_URL}"
                ),
            }
        else:
            rec = probe_location(row, token)
            rec["probed_live"] = True
            time.sleep(POLITE_SLEEP_S)
        locations.append(rec)
        print(
            f"[loc] {idx:02d} {rec['status']:<20} {rec['location_name'][:40]:<40} "
            f"http={rec['http_status']}"
        )

    end_time = datetime.now(UTC).isoformat()
    wall = time.monotonic() - start
    run["end_time"] = end_time
    run["wall_clock_s"] = round(wall, 2)
    run["status"] = "complete"
    statuses = {rec["status"] for rec in locations}
    run["coverage_status"] = (
        "FINDING"
        if "FINDING" in statuses
        else "BLOCKED_NO_TOKEN" if "BLOCKED_NO_TOKEN" in statuses else "BLOCKED"
    )

    with COVERAGE_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "osm_id",
                "location_name",
                "lat",
                "lon",
                "radius_m",
                "status",
                "images_in_bbox",
                "distinct_sequences",
                "http_status",
                "probed_live",
                "note",
            ],
        )
        writer.writeheader()
        for rec in locations:
            writer.writerow(rec)

    write_manifest(run, endpoints, locations)
    print(
        f"[done] {run['coverage_status']}  wall={run['wall_clock_s']}s  "
        f"out={COVERAGE_CSV.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
