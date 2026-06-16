"""
Fetch active-satellite TLEs from CelesTrak and enrich with SATCAT metadata,
in one pass. Single source (CelesTrak): no auth, no pagination, and
redistribution-friendly.

Output: tles.json (repo root)
  {
    "fetched_at": "...Z",
    "count": N,
    "tles": [{"norad","name","line1","line2","category",
              "country?","launchDate?","objectType?"}, ...]
  }
Each run OVERWRITES the previous file.
"""

import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
import urllib.request

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
SATCAT_URL = "https://celestrak.org/pub/satcat.csv"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tles.json")
UA = "satellite-tracker-ci/2.0 (+github-actions)"

COUNTRY_NAMES = {
    "US": "United States", "PRC": "China", "CIS": "Russia", "UK": "United Kingdom",
    "FR": "France", "JPN": "Japan", "IND": "India", "ESA": "ESA (Europe)",
    "GER": "Germany", "ITA": "Italy", "CA": "Canada", "SES": "SES (Luxembourg)",
    "ITSO": "Intelsat", "SKOR": "South Korea", "SPN": "Spain", "NETH": "Netherlands",
    "LUXE": "Luxembourg", "AUS": "Australia", "BRAZ": "Brazil", "ARGN": "Argentina",
    "ISRA": "Israel", "IRAN": "Iran", "TURK": "Turkey", "UAE": "UAE", "SAUD": "Saudi Arabia",
    "EUME": "EUMETSAT", "ESRO": "ESA (Europe)", "NICO": "New ICO", "ORB": "ORBCOMM",
    "GLOB": "Globalstar", "O3B": "O3b", "STCT": "Singapore/Taiwan", "EUTE": "Eutelsat",
    "INDO": "Indonesia", "THAI": "Thailand", "MEX": "Mexico", "NOR": "Norway",
    "SWED": "Sweden", "SWTZ": "Switzerland", "BEL": "Belgium", "DEN": "Denmark",
    "FIN": "Finland", "POL": "Poland", "POR": "Portugal", "RP": "Philippines",
    "VTNM": "Vietnam", "EGYP": "Egypt", "ALG": "Algeria", "NIG": "Nigeria",
    "PAKI": "Pakistan", "MALA": "Malaysia", "TWN": "Taiwan", "CZCH": "Czech Republic",
    "AB": "Arab Sat. League", "ASRA": "Austria", "TBD": "Unknown", "TBA": "Unknown",
    "UNK": "Unknown", "ISS": "Int'l (ISS)", "IT": "Italy", "PRES": "Multinational",
}

OPS_STATUS = {
    "+": "Operational", "-": "Non-operational", "P": "Partially operational",
    "B": "Standby", "S": "Spare", "X": "Extended mission", "D": "Decayed",
}

LAUNCH_SITES = {
    "AFETR": "Cape Canaveral, US", "AFWTR": "Vandenberg, US", "WLPIS": "Wallops Island, US",
    "ERAS": "Cape Canaveral, US", "KSCUT": "Uchinoura, Japan", "TANSC": "Tanegashima, Japan",
    "TYMSC": "Baikonur, Kazakhstan", "TTMTR": "Baikonur, Kazakhstan",
    "PLMSC": "Plesetsk, Russia", "GIK": "Plesetsk, Russia", "VOSTO": "Vostochny, Russia",
    "KYMTR": "Kapustin Yar, Russia", "SVOBO": "Svobodny, Russia", "OREN": "Yasny, Russia",
    "FRGUI": "Kourou, French Guiana", "HGSTR": "Hammaguir, Algeria",
    "JSC": "Jiuquan, China", "XSC": "Xichang, China", "TSC": "Taiyuan, China", "WSC": "Wenchang, China",
    "SRILR": "Sriharikota, India", "SRISA": "Sriharikota, India",
    "SEAL": "Sea Launch (Pacific)", "KWAJ": "Kwajalein", "WOMRA": "Woomera, Australia",
    "SNMLP": "San Marco (Kenya)", "NSC": "Naro, South Korea", "RLLB": "Mahia, New Zealand",
    "SEMLS": "Kodiak, Alaska, US", "KODAK": "Kodiak, Alaska, US", "DLS": "Dombarovsky, Russia",
}


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


# Belt-and-suspenders: GROUP=active is already payloads only, but keep the filter.
def is_real_satellite(name: str) -> bool:
    n = name.upper()
    if "DEB" in n or "DEBRIS" in n:
        return False
    if "R/B" in n or "ROCKET BODY" in n:
        return False
    if "UNKNOWN" in n or "TBA" in n:
        return False
    return True


def http_get(url: str, timeout: int = 180) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_tle_text(text: str):
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            out.append((name, l1, l2))
    return out


def load_satcat() -> dict:
    text = http_get(SATCAT_URL, timeout=180)
    meta = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            meta[int(row["NORAD_CAT_ID"])] = row
        except (KeyError, ValueError, TypeError):
            continue
    return meta


def main() -> None:
    print("Fetching CelesTrak active TLEs…")
    stanzas = parse_tle_text(http_get(GP_URL, timeout=120))
    print(f"  {len(stanzas):,} TLE stanzas")

    print("Fetching CelesTrak SATCAT…")
    try:
        satcat = load_satcat()
        print(f"  {len(satcat):,} SATCAT rows")
    except Exception as e:  # noqa: BLE001 — metadata is best-effort
        print(f"  SATCAT failed ({e}); continuing without metadata", file=sys.stderr)
        satcat = {}

    tles = []
    skipped = 0
    enriched = 0
    for name, l1, l2 in stanzas:
        if not is_real_satellite(name):
            skipped += 1
            continue
        try:
            norad = int(l1[2:7])
        except ValueError:
            continue
        rec = {
            "norad": norad,
            "name": name,
            "line1": l1,
            "line2": l2,
            "category": categorize(name),
        }
        m = satcat.get(norad)
        if m:
            owner = (m.get("OWNER") or "").strip()
            if owner:
                rec["country"] = COUNTRY_NAMES.get(owner, owner)
            launch = (m.get("LAUNCH_DATE") or "").strip()
            if launch:
                rec["launchDate"] = launch
            otype = (m.get("OBJECT_TYPE") or "").strip().upper()
            otype_names = {"R/B": "Rocket Body", "DEB": "Debris", "UNK": "Unknown"}
            if otype and otype not in ("PAY", "PAYLOAD"):
                rec["objectType"] = otype_names.get(otype, otype.title())
            status = OPS_STATUS.get((m.get("OPS_STATUS_CODE") or "").strip())
            if status:
                rec["status"] = status
            site = LAUNCH_SITES.get((m.get("LAUNCH_SITE") or "").strip())
            if site:
                rec["launchSite"] = site
            try:
                rec["perigeeKm"] = int(float(m["PERIGEE"]))
                rec["apogeeKm"] = int(float(m["APOGEE"]))
            except (KeyError, ValueError, TypeError):
                pass
            try:
                rec["periodMin"] = round(float(m["PERIOD"]), 1)
            except (KeyError, ValueError, TypeError):
                pass
            try:
                rec["inclinationDeg"] = round(float(m["INCLINATION"]), 1)
            except (KeyError, ValueError, TypeError):
                pass
            if owner or launch:
                enriched += 1
        tles.append(rec)

    output = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(tles),
        "tles": tles,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Kept {len(tles):,} satellites (filtered {skipped:,}); "
          f"enriched {enriched:,} with country/launch")
    print(f"Saved -> {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
