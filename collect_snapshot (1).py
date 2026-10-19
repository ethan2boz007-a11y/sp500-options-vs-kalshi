"""
collect_snapshot.py
-------------------
Takes ONE snapshot of two markets at the same moment and saves them to CSV:

  1. Kalshi's "S&P 500 price range at 4pm" markets (series KXINX)
  2. S&P 500 options (SPX, plus SPY as a backup)

Each snapshot covers two horizons:
  "0dte" = the market that settles TODAY at 4pm
  "next" = the market that settles on the NEXT trading day at 4pm

Run it many times per trading day (automatically, with the GitHub Actions
workflow in .github/workflows/collect.yml). Over a few months this builds the
dataset for comparing prediction-market probabilities with option-implied
probabilities.

Output (one folder per day, file names start with the time HHMM):
  data/2026-10-19/1030_kalshi_0dte.csv        Kalshi ranges settling today
  data/2026-10-19/1030_kalshi_next.csv        Kalshi ranges settling next trading day
  data/2026-10-19/1030_SPX_options_0dte.csv   SPX calls + puts expiring today
  data/2026-10-19/1030_SPX_options_next.csv   SPX calls + puts, next expiry
  data/2026-10-19/1030_SPY_options_0dte.csv   same for SPY
  data/2026-10-19/1030_SPY_options_next.csv
  data/snapshot_log.csv                        one line per run: what worked

Not collected here (can be downloaded for free later):
  the actual 4pm S&P closes, and Kalshi's final settlement results.

Usage:
  python collect_snapshot.py            # normal run (skips outside market hours)
  python collect_snapshot.py --force    # run anytime, e.g. to test it
"""

import argparse
import sys
import time
from datetime import datetime, timedelta
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

# Only collect between these times (Eastern). Everything settles at 4pm.
START_HHMM = 935
END_HHMM = 1558

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
    """Event ticker for a date, e.g. KXINX-26OCT19H1600 for Oct 19, 2026 at 4pm."""
    return f"{KALSHI_SERIES}-{day:%y}{MONTHS[day.month - 1]}{day:%d}H1600"


def fetch_kalshi(day):
    """All range markets for the event settling at 4pm on `day`."""
    event = kalshi_event_ticker(day)
    data = get_json(f"{KALSHI_API}/markets", {"event_ticker": event, "limit": 200})
    markets = data.get("markets", [])
    if not markets:
        raise RuntimeError(f"no Kalshi markets found for {event}")

    rows = []
    for m in markets:
        bid, ask = price(m, "yes_bid"), price(m, "yes_ask")
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None
        rows.append({
            "ticker": m.get("ticker"),
            "event_ticker": m.get("event_ticker"),
            "settle_date": day.isoformat(),
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
    return pd.DataFrame(rows).sort_values("floor_strike", na_position="first")


def fetch_kalshi_next(today):
    """
    The Kalshi event for the next trading day. Tries the next few weekdays,
    so it skips over weekends and market holidays automatically.
    """
    day = today
    for _ in range(6):
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        try:
            return fetch_kalshi(day)
        except RuntimeError:
            continue
    raise RuntimeError("no Kalshi event found for the next few trading days")


# ----------------------------------------------------------------------------
# Options (Yahoo Finance via yfinance)
# ----------------------------------------------------------------------------
def fetch_options(tk, expiry):
    """All calls and puts on ticker `tk` that expire on `expiry` (YYYY-MM-DD)."""
    chain = tk.option_chain(expiry)
    df = pd.concat([chain.calls.assign(type="call"),
                    chain.puts.assign(type="put")], ignore_index=True)
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["underlying"] = tk.ticker
    df["expiry"] = expiry
    return df


def option_expiries(tk, today):
    """Return (today's expiry or None, next expiry after today or None)."""
    exps = sorted(tk.options)
    t = today.isoformat()
    later = [e for e in exps if e > t]
    return (t if t in exps else None), (later[0] if later else None)


def fetch_spot(symbol):
    """Latest price of the index/ETF and the time of that price."""
    hist = yf.Ticker(symbol).history(period="1d", interval="1m")
    if hist.empty:
        return None, None
    return float(hist["Close"].iloc[-1]), hist.index[-1].tz_convert(ET).isoformat()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def save(df, path, stamp_iso):
    df.insert(0, "snapshot_time_et", stamp_iso)
    df.to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="run even outside market hours / on weekends")
    args = parser.parse_args()

    now = datetime.now(ET)
    today = now.date()
    hhmm = now.hour * 100 + now.minute
    if not args.force and (now.weekday() >= 5 or not START_HHMM <= hhmm <= END_HHMM):
        print(f"{now:%a %H:%M} ET is outside collection hours; nothing to do.")
        return 0

    stamp = now.strftime("%H%M")
    iso = now.isoformat(timespec="seconds")
    day_dir = DATA_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    log = {"snapshot_time_et": iso}
    print(f"Snapshot at {now:%Y-%m-%d %H:%M:%S} ET")

    # Index level first, so we know where the S&P was at this moment.
    try:
        spot, spot_time = fetch_spot(SPOT_SYMBOL)
        log.update(spx_spot=spot, spx_spot_time=spot_time)
        print(f"  S&P 500 = {spot} (as of {spot_time})")
    except Exception as e:
        log["spx_spot_error"] = str(e)
        print(f"  S&P spot failed: {e}")

    # Kalshi: today and next trading day
    for horizon, getter in (("0dte", lambda: fetch_kalshi(today)),
                            ("next", lambda: fetch_kalshi_next(today))):
        try:
            kal = getter()
            save(kal, day_dir / f"{stamp}_kalshi_{horizon}.csv", iso)
            log[f"kalshi_{horizon}_date"] = kal["settle_date"].iloc[0]
            log[f"kalshi_{horizon}_markets"] = len(kal)
            log[f"kalshi_{horizon}_prob_sum"] = round(kal["yes_mid"].sum(), 3)
            print(f"  Kalshi {horizon}: {len(kal)} ranges for {kal['settle_date'].iloc[0]}")
        except Exception as e:
            log[f"kalshi_{horizon}_error"] = str(e)
            print(f"  Kalshi {horizon} failed: {e}")

    # Options: today's expiry and the next expiry
    for sym in OPTION_SYMBOLS:
        name = sym.replace("^", "")
        try:
            tk = yf.Ticker(sym)
            exp_today, exp_next = option_expiries(tk, today)
        except Exception as e:
            log[f"{name}_error"] = str(e)
            print(f"  {name} options list failed: {e}")
            continue
        for horizon, expiry in (("0dte", exp_today), ("next", exp_next)):
            if expiry is None:
                log[f"{name}_{horizon}_error"] = "no matching expiry"
                print(f"  {name} {horizon}: no matching expiry")
                continue
            try:
                opt = fetch_options(tk, expiry)
                save(opt, day_dir / f"{stamp}_{name}_options_{horizon}.csv", iso)
                log[f"{name}_{horizon}_expiry"] = expiry
                log[f"{name}_{horizon}_contracts"] = len(opt)
                print(f"  {name} {horizon}: {len(opt)} contracts expiring {expiry}")
            except Exception as e:
                log[f"{name}_{horizon}_error"] = str(e)
                print(f"  {name} {horizon} failed: {e}")

    # One summary line per run, so it's easy to spot gaps later.
    # (The set of columns can change from run to run, so we rewrite the
    # whole log with all columns rather than appending blindly.)
    log_path = DATA_DIR / "snapshot_log.csv"
    old = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
    pd.concat([old, pd.DataFrame([log])], ignore_index=True).to_csv(log_path, index=False)

    got_any = any(k.endswith(("_markets", "_contracts")) for k in log)
    return 0 if got_any else 1


if __name__ == "__main__":
    sys.exit(main())
