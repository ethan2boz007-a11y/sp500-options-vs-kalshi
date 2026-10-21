"""
download_history.py
-------------------
One-time download of PAST Kalshi S&P 500 daily range markets: how each range
was priced during the day, and how it actually settled. Used to test how
accurate (well "calibrated") Kalshi's probabilities have been over many days,
without waiting months for new data.

Run it with the "download-history" button in the repo's Actions tab
(.github/workflows/history.yml), or locally:
    pip install pandas requests
    python download_history.py --max-days 400

Writes, into history/:
    explore.txt          what was found: series, date range, sample raw data
    kalshi_markets.csv   one row per range: date, edges, volume, result (yes/no),
                         the S&P's actual settlement value
    kalshi_hourly.csv    one row per range per hour (10am-3pm ET): yes bid/ask
                         at the end of that hour
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API = "https://external-api.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "history"
# Kalshi renamed its series over time; try the current name and older ones.
SERIES_CANDIDATES = ["KXINX", "INXD", "INX"]
HOURS_ET = [10, 11, 12, 13, 14, 15]   # candle end times to keep
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

notes = []   # goes into explore.txt


def note(*parts):
    line = " ".join(str(p) for p in parts)
    print(line)
    notes.append(line)


def get(path, params=None, tries=6):
    """GET with retries; waits longer when Kalshi says we're going too fast."""
    for attempt in range(tries):
        try:
            r = requests.get(f"{API}{path}", params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            time.sleep(0.12)   # stay well under the rate limit
            return r.json()
        except requests.RequestException as e:
            if attempt == tries - 1:
                note(f"  request failed: {path} {params} -> {e}")
                return None
            time.sleep(2 ** attempt)
    return None


def paged(path, key, params):
    """Follow Kalshi's cursor pagination and return all items."""
    items, cursor = [], None
    while True:
        p = dict(params, limit=200)
        if cursor:
            p["cursor"] = cursor
        data = get(path, p)
        if not data:
            break
        items += data.get(key, [])
        cursor = data.get("cursor")
        if not cursor:
            break
    return items


def to_prob(obj, *names):
    """Read a price (dollars or cents) as a 0-1 probability from the first field found."""
    for n in names:
        if isinstance(obj, dict) and obj.get(f"{n}_dollars") not in (None, ""):
            return float(obj[f"{n}_dollars"])
        if isinstance(obj, dict) and obj.get(n) not in (None, ""):
            v = float(obj[n])
            return v / 100 if v > 1 else v
    return None


def to_num(obj, *names):
    for n in names:
        for key in (f"{n}_fp", n):
            if isinstance(obj, dict) and obj.get(key) not in (None, ""):
                try:
                    return float(obj[key])
                except (TypeError, ValueError):
                    pass
    return None


def event_date(event_ticker):
    """KXINX-26OCT19H1600 -> 2026-10-19 (also works for older formats)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    return datetime(2000 + int(yy), MONTHS[mon], int(dd)).date()


def find_events():
    """All S&P daily range events across the candidate series names."""
    found = []
    for s in SERIES_CANDIDATES:
        info = get(f"/series/{s}")
        events = paged("/events", "events", {"series_ticker": s})
        title = (info or {}).get("series", {}).get("title")
        dates = [event_date(e["event_ticker"]) for e in events]
        dates = [d for d in dates if d]
        note(f"Series {s}: title={title!r}, events={len(events)}",
             f"from {min(dates)} to {max(dates)}" if dates else "")
        # Save the full list of event names, so formats can be checked later.
        pd.DataFrame({"event_ticker": [e["event_ticker"] for e in events]}).to_csv(
            OUT / f"events_{s}.csv", index=False)
        # Older events are named without a time (e.g. KXINX-23MAR10); newer ones
        # end in H1600 (4pm). Skip events at any OTHER time, then keep one per day,
        # preferring the 4pm one.
        per_day = {}
        for e in events:
            t, d = e["event_ticker"], event_date(e["event_ticker"])
            if d is None:
                continue
            hour = re.search(r"H(\d{4})$", t)
            if hour and hour.group(1) != "1600":
                continue
            if d not in per_day or "H1600" in t:
                per_day[d] = t
        found += [(s, t, d) for d, t in per_day.items()]
    return found


def markets_for(event_ticker):
    """Markets of one event, from the live list or the historical archive."""
    m = paged("/markets", "markets", {"event_ticker": event_ticker})
    if m:
        return m, "live"
    return paged("/historical/markets", "markets", {"event_ticker": event_ticker}), "historical"


def hourly_candles(series, ticker, day, source):
    start = int(datetime(day.year, day.month, day.day, 9, 0, tzinfo=ET).timestamp())
    end = int(datetime(day.year, day.month, day.day, 16, 5, tzinfo=ET).timestamp())
    params = {"start_ts": start, "end_ts": end, "period_interval": 60}
    path = (f"/historical/markets/{ticker}/candlesticks" if source == "historical"
            else f"/series/{series}/markets/{ticker}/candlesticks")
    data = get(path, params)
    if not data and source == "live":   # some markets move to the archive
        data = get(f"/historical/markets/{ticker}/candlesticks", params)
    return (data or {}).get("candlesticks", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=400, help="most recent N trading days")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    note(f"Run at {datetime.now(ET):%Y-%m-%d %H:%M} ET")
    note("Historical cutoff:", json.dumps(get("/historical/cutoff")))

    events = find_events()
    today = datetime.now(ET).date()
    events = sorted({(s, e, d) for s, e, d in events if d < today}, key=lambda x: x[2], reverse=True)
    events = events[: args.max_days]
    note(f"Downloading {len(events)} past days"
         + (f", {events[-1][2]} to {events[0][2]}" if events else ""))

    market_rows, candle_rows = [], []
    sample_market = sample_candle = None
    for i, (series, ev, day) in enumerate(events, 1):
        markets, source = markets_for(ev)
        if i % 20 == 0 or i == 1:
            print(f"  {i}/{len(events)} {ev}: {len(markets)} ranges ({source})")
        for m in markets:
            sample_market = sample_market or m
            vol = to_num(m, "volume")
            market_rows.append({
                "date": day, "series": series, "event_ticker": ev, "ticker": m.get("ticker"),
                "range_text": m.get("subtitle") or m.get("yes_sub_title") or m.get("title"),
                "strike_type": m.get("strike_type"),
                "floor_strike": m.get("floor_strike"), "cap_strike": m.get("cap_strike"),
                "volume": vol, "result": m.get("result"),
                "expiration_value": m.get("expiration_value"),
                "source": source,
            })
            if not vol:
                continue   # never traded: no prices to download
            for c in hourly_candles(series, m["ticker"], day, source):
                sample_candle = sample_candle or c
                t = datetime.fromtimestamp(c.get("end_period_ts", 0), timezone.utc).astimezone(ET)
                if t.date() != day or t.hour not in HOURS_ET or t.minute != 0:
                    continue
                candle_rows.append({
                    "date": day, "ticker": m["ticker"], "hour_et": t.hour,
                    "yes_bid": to_prob(c.get("yes_bid"), "close"),
                    "yes_ask": to_prob(c.get("yes_ask"), "close"),
                    "last_price": to_prob(c.get("price"), "close"),
                    "volume": to_num(c, "volume"),
                })

    pd.DataFrame(market_rows).to_csv(OUT / "kalshi_markets.csv", index=False)
    pd.DataFrame(candle_rows).to_csv(OUT / "kalshi_hourly.csv", index=False)
    note(f"Saved {len(market_rows)} ranges and {len(candle_rows)} hourly prices")
    note("Sample market (raw):", json.dumps(sample_market, default=str)[:3000])
    note("Sample candlestick (raw):", json.dumps(sample_candle, default=str)[:1500])
    (OUT / "explore.txt").write_text("\n".join(notes))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # still save what we learned, so it can be fixed
        note("ERROR:", repr(e))
        OUT.mkdir(exist_ok=True)
        (OUT / "explore.txt").write_text("\n".join(notes))
        raise
