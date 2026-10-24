"""
massive_test.py
---------------
TEST: can past SPX option prices from Massive (formerly Polygon) reproduce
the probabilities from our own live snapshots?

For a few days the collector already covered, this downloads minute-by-minute
TRADE prices for the SPX options needed around Kalshi's range edges
(strikes 5 points either side of each 25-point edge, near the S&P's level),
plus at-the-money calls and puts. The analysis then compares them with the
bid/ask-based probabilities from data/.

Needs a Massive API key in the environment variable MASSIVE_API_KEY
(stored as a GitHub secret; see README). Works on the free "Options Basic"
plan: it waits ~13 seconds between requests to stay under 5 per minute.

Run with the "massive-test" button in the Actions tab, or locally:
    MASSIVE_API_KEY=... python massive_test.py 2026-10-21 2026-10-22 2026-10-23

Writes:
    massive/aggs_<date>.csv   one row per option per minute
    massive/test_log.txt      what happened (errors, counts)
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASES = ["https://api.massive.com", "https://api.polygon.io"]   # same API, old and new name
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "massive"
DATA = Path(__file__).parent / "data"
WAIT = 13            # seconds between requests (free plan: 5 per minute)
EDGE_RANGE = 100     # cover Kalshi edges within this many points of the S&P level
log_lines = []


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    log_lines.append(line)


def request(path, params, key):
    """GET from Massive, trying the new and old web addresses; retries if rate-limited."""
    for base in BASES:
        for attempt in range(4):
            try:
                r = requests.get(base + path, params={**params, "apiKey": key},
                                 headers={"Authorization": f"Bearer {key}"}, timeout=30)
            except requests.RequestException as e:
                log(f"  network error on {base}: {e}")
                break
            time.sleep(WAIT)
            if r.status_code == 429:
                log("  rate-limited, waiting a minute...")
                time.sleep(60)
                continue
            if r.status_code == 200:
                return r.json()
            log(f"  {base}{path} -> HTTP {r.status_code}: {r.text[:300]}")
            break
    return None


def ticker(day, kind, strike):
    """O:SPXW261021C07700000 = SPXW, expires 2026-10-21, Call, strike 7700."""
    return f"O:SPXW{day:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def spot_for(day):
    """Typical S&P level that day, from our own collector's log."""
    logf = pd.read_csv(DATA / "snapshot_log.csv")
    s = logf[logf["snapshot_time_et"].str.startswith(day.isoformat())]["spx_spot"]
    return float(s.median()) if len(s) else None


def main(days):
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        log("ERROR: no MASSIVE_API_KEY found. Add it as a repository secret (see README).")
        return
    OUT.mkdir(exist_ok=True)

    # 1) Check that SPX index options are visible on this plan.
    d0 = days[0]
    ref = request("/v3/reference/options/contracts",
                  {"underlying_ticker": "SPX", "expiration_date": d0.isoformat(),
                   "expired": "true", "limit": 5}, key)
    if ref:
        found = [r.get("ticker") for r in ref.get("results", [])]
        log(f"Contracts check for {d0}: {len(found)} found, e.g. {found[:3]}")
    else:
        log("Contracts check failed (continuing; will build ticker names directly)")

    # 2) Minute bars for the options we need.
    for day in days:
        spot = spot_for(day)
        if spot is None:
            log(f"{day}: no collector data that day, skipping")
            continue
        edges = [e for e in range(int(spot // 25 * 25) - EDGE_RANGE, int(spot) + EDGE_RANGE + 25, 25)
                 if abs(e - spot) <= EDGE_RANGE]
        want = set()
        for e in edges:
            for k in (e - 5, e + 5):
                want.add(("call" if k >= spot else "put", k))
        atm = round(spot / 5) * 5
        for k in (atm - 5, atm, atm + 5):
            want.update({("call", k), ("put", k)})
        log(f"{day}: S&P about {spot:.0f}, downloading {len(want)} options "
            f"(about {len(want) * WAIT // 60 + 1} minutes)")

        rows = []
        for kind, k in sorted(want, key=lambda x: (x[1], x[0])):
            t = ticker(day, kind, k)
            data = request(f"/v2/aggs/ticker/{t}/range/1/minute/{day}/{day}",
                           {"adjusted": "true", "sort": "asc", "limit": 50000}, key)
            bars = (data or {}).get("results", []) or []
            for b in bars:
                ts = datetime.fromtimestamp(b["t"] / 1000, ET)
                rows.append({"date": day, "ticker": t, "type": kind, "strike": k,
                             "minute_et": ts.strftime("%H:%M"), "open": b.get("o"),
                             "high": b.get("h"), "low": b.get("l"), "close": b.get("c"),
                             "vwap": b.get("vw"), "volume": b.get("v"), "trades": b.get("n")})
            log(f"  {t}: {len(bars)} minutes")
        pd.DataFrame(rows).to_csv(OUT / f"aggs_{day}.csv", index=False)
        log(f"{day}: saved {len(rows)} rows")


if __name__ == "__main__":
    args = sys.argv[1:] or ["2026-10-21", "2026-10-22", "2026-10-23"]
    days = [datetime.strptime(a, "%Y-%m-%d").date() for a in args]
    try:
        main(days)
    finally:
        OUT.mkdir(exist_ok=True)
        (OUT / "test_log.txt").write_text("\n".join(log_lines))
