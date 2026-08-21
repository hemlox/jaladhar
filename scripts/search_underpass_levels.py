from __future__ import annotations

import csv
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / "data" / "raw" / "underpass_search" / "search_register.csv"
OUT_DIR = REPO / "data" / "raw" / "underpass_search" / "results"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
QUERY_TEMPLATES = [
    "{name} height restriction",
    "{name} vertical clearance metres",
    "{name} underpass road level",
    "{name} flooding submerged depth",
]
SLEEP_S = 2.0
TRIES = 4
NS = {"": "http://www.w3.org/2005/Atom"}


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def gnews_rss(query: str, timeout: int = 30) -> list[dict]:
    """Query the Google News RSS search endpoint. Returns parsed entries."""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    last = None
    for i in range(TRIES):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            root = ET.fromstring(body)
            entries = []
            for e in root.iter("item"):
                title_el = e.find("title")
                link_el = e.find("link")
                src_el = e.find("source")
                date_el = e.find("pubDate")
                if title_el is None or link_el is None:
                    continue
                entries.append(
                    {
                        "title": (title_el.text or "").strip(),
                        "url": (link_el.text or "").strip(),
                        "source": (src_el.text or "").strip() if src_el is not None else "",
                        "pub_date": (date_el.text or "").strip() if date_el is not None else "",
                    }
                )
            return entries
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(SLEEP_S * (i + 1))
    raise RuntimeError(f"gnews failed for {query!r}: {last}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    locations = list(csv.DictReader(open(REGISTER)))

    all_rows = []
    failures = []
    skipped = 0
    for idx, loc in enumerate(locations):
        name = loc["location_name"].strip()
        search = (loc.get("search_name") or name).strip()
        for qidx, tpl in enumerate(QUERY_TEMPLATES):
            q = tpl.format(name=search)
            qid = f"{idx:02d}_{qidx}"
            out_path = OUT_DIR / f"{qid}.json"
            if out_path.exists():
                try:
                    with open(out_path) as f:
                        saved = json.load(f)
                    saved["results"]  # validate
                except Exception:  # noqa: BLE001  truncated write; refetch
                    out_path.unlink()
                    saved = None
            else:
                saved = None
            if saved is not None:
                skipped += 1
                for res in saved["results"]:
                    all_rows.append(
                        {
                            "location_idx": idx,
                            "location_name": name,
                            "search_name": search,
                            "query": q,
                            "query_idx": qidx,
                            "result_title": res.get("title", ""),
                            "result_source": res.get("source", ""),
                            "result_url": res.get("url", ""),
                            "pub_date": res.get("pub_date", ""),
                        }
                    )
                continue
            try:
                results = gnews_rss(q)
            except Exception as e:  # noqa: BLE001
                failures.append({"location": name, "query": q, "error": str(e)})
                print(f"[{qid}] FAIL {name}: {e}", flush=True)
                continue
            time.sleep(SLEEP_S)
            with open(out_path, "w") as f:
                json.dump({"query": q, "location": name, "results": results}, f, indent=2)
            for res in results:
                all_rows.append(
                    {
                        "location_idx": idx,
                        "location_name": name,
                        "search_name": search,
                        "query": q,
                        "query_idx": qidx,
                        "result_title": res["title"],
                        "result_source": res.get("source", ""),
                        "result_url": res["url"],
                        "pub_date": res.get("pub_date", ""),
                    }
                )
            print(f"[{qid}] {name}: {len(results)} results", flush=True)

    out_csv = OUT_DIR / "search_results.csv"
    fieldnames = [
        "location_idx",
        "location_name",
        "search_name",
        "query",
        "query_idx",
        "result_title",
        "result_source",
        "result_url",
        "pub_date",
    ]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)

    n_fail = len(failures)
    n_ok = len(locations) * len(QUERY_TEMPLATES) - n_fail - skipped
    manifest = {
        "schema": "jaladhar.underpass-search.v2",
        "git_sha": git_sha(),
        "started_at": started,
        "status": "completed",
        "n_locations": len(locations),
        "queries_per_location": len(QUERY_TEMPLATES),
        "n_queries_fresh_ok": n_ok,
        "n_queries_skipped_resume": skipped,
        "n_queries_failed": n_fail,
        "n_result_rows": len(all_rows),
        "query_failures": failures,
        "engine": "google news rss (news.google.com/rss/search)",
        "note": "duckduckgo html endpoint rate-limited this IP during v1 attempt; switched engine",
        "output_csv": str(out_csv),
        "results_dir": str(OUT_DIR),
    }
    with open(OUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()