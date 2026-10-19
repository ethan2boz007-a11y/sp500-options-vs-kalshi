"""
collect_snapshot.py
-------------------
Takes ONE snapshot of two markets at the same moment and saves them to CSV:

  1. Kalshi's "S&P 500 price range today at 4pm" market (series KXINX)
  2. S&P 500 options that expire TODAY (SPX, plus SPY as a backup)

Run it several times per trading day (by hand, or automatically with the
GitHub Actions workflow in .github/workflows/collect.yml). Over a few weeks
this builds the dataset for comparing prediction-market probabilities with
option-implied probabilities.

Output (one folder per day):
  data/2026-10-19/1030_kalshi.csv        Kalshi range prices
  data/2026-10-19/1030_SPX_options.csv   SPX calls + puts expiring today
  data/2026-10-19/1030_SPY_options.csv   SPY calls + puts expiring today
  data/snapshot_log.csv                  one line per run: what worked

Usage:
  python collect_snapshot.py            # normal run (skips outside market hours)
  python collect_snapshot.py --force    # run anytime, e.g. to test it
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
ET = ZoneInfo("America/New_York")
KALSHI_API = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXINX"            # S&P 500 daily range series
OPTION_SYMBOLS = ["^SPX", "SPY"]   # Yahoo symbols; SPX is the exact match, SPY is backup
SPOT_SYMBOL = "^GSPC"              # S&P 500 index level
DATA_DIR = Path(__file__).parent / "data"

# Only collect between these times (Eastern). Options and Kalshi both settle at 4pm.
START_HHMM = 940
END_HHMM = 1555

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_json(url, params=None, tries=3):
    """GET a URL and return JSON, retrying a couple of times on errors."""
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"  retrying after error: {e}")
            time.sleep(3)


def price(market, name):
    """
    Read a Kalshi price as a probability between 0 and 1.
    Kalshi reports prices in dollars (e.g. "0.45" = 45% chance). Older API
    versions used cents (45), so we handle both.
    """
    val = market.get(f"{name}_dollars")
    if val not in (None, ""):
        return float(val)
    val = market.get(name)
    if val not in (None, ""):
        return float(val) / 100.0
    return None


def number(market, name):
    """Read a count like volume or open interest (newer API adds an _fp suffix)."""
    for key in (f"{name}_fp", name):
        val = market.get(key)
        if val not in (None, ""):
            return float(val)
    return None


# ----------------------------------------------------------------------------
# Kalshi
# ----------------------------------------------------------------------------
def kalshi_event_ticker(day):
    """Today's event ticker, e.g. KXINX-26OCT19H1600 for Oct 19, 2026 at 4pm."""
    return f"{KALSHI_SERIES}-{day:%y}{MONTHS[day.month - 1]}{day:%d}H1600"


def fetch_kalshi(day):
    event = kalshi_event_ticker(day)
    data = get_json(f"{KALSHI_API}/markets", {"event_ticker": event, "limit": 200})
    markets = data.get("markets", [])
    if not markets:
        raise RuntimeError(f"no Kalshi markets found for {event} (holiday or ticker format changed?)")

    rows = []
    for m in markets:
        bid, ask = price(m, "yes_bid"), price(m, "yes_ask")
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None
        rows.append({
            "ticker": m.get("ticker"),
            "event_ticker": m.get("event_ticker"),
            "range_text": m.get("subtitle") or m.get("yes_sub_title") or m.get("title"),
            "strike_type": m.get("strike_type"),   # "between", "greater", "less", ...
            "floor_strike": m.get("floor_strike"),  # lower edge of the range
            "cap_strike": m.get("cap_strike"),      # upper edge of the range
            "yes_bid": bid,
            "yes_ask": ask,
            "yes_mid": mid,                          # best single estimate of probability
            "last_price": price(m, "last_price"),
            "volume": number(m, "volume"),
            "open_interest": number(m, "open_interest"),
            "status": m.get("status"),
            "close_time": m.get("close_time"),
        })
    df = pd.DataFrame(rows).sort_values("floor_strike", na_position="first")
    return df


# ----------------------------------------------------------------------------
# Options (Yahoo Finance via yfinance)
# ----------------------------------------------------------------------------
def fetch_options(symbol, day):
    """All calls and puts on `symbol` that expire on `day`."""
    tk = yf.Ticker(symbol)
    expiry = day.strftime("%Y-%m-%d")
    if expiry not in tk.options:
        raise RuntimeError(f"{symbol}: no option expiring {expiry} "
                           f"(available: {list(tk.options)[:3]}...)")
    chain = tk.option_chain(expiry)
    calls = chain.calls.assign(type="call")
    puts = chain.puts.assign(type="put")
    df = pd.concat([calls, puts], ignore_index=True)
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["underlying"] = symbol
    df["expiry"] = expiry
    return df


def fetch_spot(symbol):
    """Latest price of the index/ETF and the time of that price."""
    hist = yf.Ticker(symbol).history(period="1d", interval="1m")
    if hist.empty:
        return None, None
    return float(hist["Close"].iloc[-1]), hist.index[-1].tz_convert(ET).isoformat()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="run even outside market hours / on weekends")
    args = parser.parse_args()

    now = datetime.now(ET)
    hhmm = now.hour * 100 + now.minute
    if not args.force and (now.weekday() >= 5 or not START_HHMM <= hhmm <= END_HHMM):
        print(f"{now:%a %H:%M} ET is outside collection hours; nothing to do.")
        return 0

    stamp = now.strftime("%H%M")
    day_dir = DATA_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    log = {"snapshot_time_et": now.isoformat(timespec="seconds")}
    print(f"Snapshot at {now:%Y-%m-%d %H:%M:%S} ET")

    # Index level first, so we know where the S&P was at this moment.
    try:
        spot, spot_time = fetch_spot(SPOT_SYMBOL)
        log.update(spx_spot=spot, spx_spot_time=spot_time)
        print(f"  S&P 500 = {spot} (as of {spot_time})")
    except Exception as e:
        log["spx_spot_error"] = str(e)
        print(f"  S&P spot failed: {e}")

    # Kalshi
    try:
        kal = fetch_kalshi(now.date())
        kal.insert(0, "snapshot_time_et", log["snapshot_time_et"])
        kal.to_csv(day_dir / f"{stamp}_kalshi.csv", index=False)
        log["kalshi_markets"] = len(kal)
        log["kalshi_prob_sum"] = round(kal["yes_mid"].sum(), 3)  # should be near 1.0
        print(f"  Kalshi: {len(kal)} ranges saved (mid prices sum to {log['kalshi_prob_sum']})")
    except Exception as e:
        log["kalshi_error"] = str(e)
        print(f"  Kalshi failed: {e}")

    # Options
    for sym in OPTION_SYMBOLS:
        name = sym.replace("^", "")
        try:
            opt = fetch_options(sym, now.date())
            opt.insert(0, "snapshot_time_et", log["snapshot_time_et"])
            opt.to_csv(day_dir / f"{stamp}_{name}_options.csv", index=False)
            log[f"{name}_contracts"] = len(opt)
            print(f"  {name}: {len(opt)} option contracts saved")
        except Exception as e:
            log[f"{name}_error"] = str(e)
            print(f"  {name} options failed: {e}")

    # One summary line per run, so it's easy to see gaps later.
    log_path = DATA_DIR / "snapshot_log.csv"
    pd.DataFrame([log]).to_csv(log_path, mode="a", index=False, header=not log_path.exists())

    # Exit with an error only if we got nothing useful at all.
    got_any = "kalshi_markets" in log or any(k.endswith("_contracts") for k in log)
    return 0 if got_any else 1


if __name__ == "__main__":
    sys.exit(main())
