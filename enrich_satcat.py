"""
Enrich tles.json with Space-Track SATCAT metadata (country, launch date,
object type). Reads tles.json, queries Space-Track once, merges by NORAD id,
and writes tles.json back.

Decoupled from the TLE fetch on purpose: it operates on whatever tles.json is
present, so it still adds metadata when the upstream TLE source is down.

Non-fatal by design: if credentials or the API are unavailable it logs and
leaves tles.json unchanged (the CI job continues).

Credentials come from the env var SPACE_TRACK_CREDS, in any of these formats:
  - JSON: {"identity": "...", "password": "..."}  (user/username/email + pass also accepted)
  - "email:password"
  - two lines:  email<newline>password
"""

import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TLES_PATH = os.path.join(HERE, "tles.json")

LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
SATCAT_URL = (
    "https://www.space-track.org/basicspacedata/query/class/satcat/"
    "CURRENT/Y/orderby/NORAD_CAT_ID/format/json"
)

# Space-Track COUNTRY codes → readable names (common ones; unknown codes pass through)
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
    "AB": "Arab Sat. League", "ASRA": "Austria", "CHBZ": "China/Brazil", "ESRI": "Eritrea",
}


def parse_creds(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            u = d.get("identity") or d.get("user") or d.get("username") or d.get("email")
            p = d.get("password") or d.get("pass")
            if u and p:
                return u, p
        except json.JSONDecodeError:
            pass
    if "\n" in raw:
        parts = [x.strip() for x in raw.splitlines() if x.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1]
    if ":" in raw:
        u, p = raw.split(":", 1)
        return u.strip(), p.strip()
    if " " in raw:
        u, p = raw.split(None, 1)
        return u.strip(), p.strip()
    return None


def norad_of(rec: dict):
    n = rec.get("norad")
    if n:
        return int(n)
    try:
        return int(rec["line1"][2:7])   # cols 3-7 of TLE line 1
    except (KeyError, ValueError, TypeError):
        return None


def main() -> None:
    if not os.path.exists(TLES_PATH):
        print("enrich: no tles.json found; nothing to do")
        return

    # Preferred: two explicit secrets (no parsing ambiguity). Falls back to the
    # combined SPACE_TRACK_CREDS for backwards compatibility.
    identity = os.environ.get("SPACE_TRACK_IDENTITY", "").strip()
    password = os.environ.get("SPACE_TRACK_PASSWORD", "")
    if identity and password:
        creds = (identity, password)
        print("enrich: using SPACE_TRACK_IDENTITY / SPACE_TRACK_PASSWORD")
    else:
        raw = os.environ.get("SPACE_TRACK_CREDS", "")
        creds = parse_creds(raw)
        if not creds:
            print("enrich: no usable Space-Track credentials; leaving tles.json unchanged")
            return
        # Safe diagnostics only — never print credential characters (logs are public)
        nonblank = len([x for x in raw.splitlines() if x.strip()])
        print(f"enrich: parsed SPACE_TRACK_CREDS (nonblank_lines={nonblank}, has_at={'@' in raw})")

    with open(TLES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    tles = data.get("tles", [])
    if not tles:
        print("enrich: tles.json has no records; nothing to do")
        return

    def fetch_satcat(identity, password):
        try:
            with requests.Session() as s:
                s.headers["User-Agent"] = "satellite-tracker-enrich/1.0"
                s.post(LOGIN_URL, data={"identity": identity, "password": password}, timeout=60)
                sat = s.get(SATCAT_URL, timeout=180)
                if sat.status_code != 200:
                    return None
                rows = sat.json()
                return rows if isinstance(rows, list) else None
        except (requests.RequestException, json.JSONDecodeError):
            return None

    a, b = creds
    rows = fetch_satcat(a, b)
    if rows is None:
        # Tolerate reversed line/field order (e.g. password before username)
        print("enrich: first credential order failed; trying reversed order")
        rows = fetch_satcat(b, a)
    if rows is None:
        print("enrich: Space-Track login/query failed (check the secret); leaving tles.json unchanged")
        return
    print(f"enrich: fetched {len(rows):,} SATCAT rows")

    by_norad = {}
    for row in rows:
        try:
            by_norad[int(row["NORAD_CAT_ID"])] = row
        except (KeyError, ValueError, TypeError):
            continue

    enriched = 0
    for rec in tles:
        m = by_norad.get(norad_of(rec))
        if not m:
            continue
        country = (m.get("COUNTRY") or "").strip()
        if country:
            rec["country"] = COUNTRY_NAMES.get(country, country)
        launch = (m.get("LAUNCH") or "").strip()
        if launch:
            rec["launchDate"] = launch
        otype = (m.get("OBJECT_TYPE") or "").strip()
        if otype and otype != "PAYLOAD":
            rec["objectType"] = otype.title()
        if country or launch:
            enriched += 1

    data["tles"] = tles
    with open(TLES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"enrich: added country/launch to {enriched:,}/{len(tles):,} records")


if __name__ == "__main__":
    main()
