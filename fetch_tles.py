"""
Fetch all TLE data from Ivan Stanojević's API and save as tles.json.
Runs in GitHub Actions daily.

Output: tles.json (repo root)
  {
    "fetched_at": "2026-03-27T06:00:00Z",
    "count": 15920,
    "tles": [{"name": "...", "line1": "...", "line2": "...", "category": "..."}, ...]
  }

Each run OVERWRITES the previous file.
"""

import json
import os
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

IVAN_BASE   = "https://tle.ivanstanojevic.me/api/tle/"
PAGE_SIZE   = 100
MAX_WORKERS = 10

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "tles.json")


def categorize(name: str) -> str:
    n = name.upper()
    if "ISS" in n or "ZARYA" in n or "ZVEZDA" in n:
        return "ISS"
    if "STARLINK" in n:
        return "Starlink"
    if any(k in n for k in ("NOAA", "GOES", "METOP", "METEOR",
                             "FENG YUN", "FENGYUN", "DMSP", "NIMBUS", "SUOMI")):
        return "Weather"
    if any(k in n for k in ("GPS", "NAVSTAR", "GLONASS", "GALILEO", "BEIDOU", "COMPASS")):
        return "GPS"
    if any(k in n for k in ("USA ", "NROL", "KH-", "LACROSSE", "ONYX", "TRUMPET", "KEYHOLE")):
        return "Military"
    return "Other"


def fetch_page(session: requests.Session, page: int) -> list[dict]:
    url = f"{IVAN_BASE}?page-size={PAGE_SIZE}&page={page}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("member", [])


def main() -> None:
    with requests.Session() as session:
        session.headers["User-Agent"] = "satellite-tracker-ci/1.0 (+github-actions)"

        # Step 1: get total count
        meta = session.get(f"{IVAN_BASE}?page-size=1&page=1", timeout=30).json()
        total = meta.get("totalItems", 24000)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        print(f"Catalog: {total:,} satellites across {total_pages} pages")

        # Step 2: fetch all pages concurrently
        records: dict[int, dict] = {}
        errors = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_page, session, p): p
                for p in range(1, total_pages + 1)
            }
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    for item in future.result():
                        norad = item.get("satelliteId")
                        if norad and item.get("line1") and item.get("line2"):
                            records[norad] = item
                except Exception as e:
                    errors += 1
                    print(f"  Warning: page failed ({e})", file=sys.stderr)

                if i % 50 == 0 or i == total_pages:
                    print(f"  {i}/{total_pages} pages · {len(records):,} unique sats")

        print(f"Done: {len(records):,} unique satellites ({errors} page errors)")

        # Step 3: build output
        tles = [
            {
                "name":     item["name"],
                "line1":    item["line1"],
                "line2":    item["line2"],
                "category": categorize(item["name"]),
            }
            for item in records.values()
        ]

        output = {
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count":      len(tles),
            "tles":       tles,
        }

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(output, f, separators=(",", ":"))

        size_kb = os.path.getsize(OUTPUT_PATH) / 1024
        print(f"Saved {len(tles):,} satellites → {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
