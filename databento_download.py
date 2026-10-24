"""
databento_download.py
---------------------
Downloads PAST bid/ask quotes for same-day-expiring SPX options (SPXW) from
Databento, at the same times as the Kalshi history (10am, 11am, ... 3pm ET),
plus 15:30 and 15:45.

For each day it asks for the options around Kalshi's range edges: strikes
5 points either side of every 25-point level within 300 points of that day's
S&P close, calls and puts. That is enough to compute the probability of the
S&P ending above each Kalshi edge, the same way analyze.py does.

SAFETY: before each day it asks Databento the price (free) and stops if the
running total would pass --max-cost (default $20). The free credit is $125.

Needs the GitHub secret DATABENTO_API_KEY.
Run with the "databento-download" button in the Actions tab.

Usage:
    python databento_download.py --start 2026-10-21 --end 2026-10-23     (test)
    python databento_download.py --start 2022-12-01 --end 2026-10-23     (all)

Writes:
    databento/quotes_<year>.csv   date, mark_et, type, strike, bid, ask, quote_time_et
    databento/download_log.txt
It skips days already saved, so it can be rerun safely if it stops partway.
"""

import argparse
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
OUT = ROOT / "databento"
DATASET, SCHEMA = "OPRA.PILLAR", "cbbo-1m"
MARKS = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "15:30", "15:45"]
SPAN = 300            # points either side of the close to cover
lines = []


def say(*p):
    s = " ".join(str(x) for x in p)
    print(s, flush=True)
    lines.append(s)


def osi(day, kind, strike):
    """OPRA symbol, e.g. 'SPXW  231020C04300000' (SPXW, 2023-10-20, call, 4300)."""
    return f"{'SPXW':<6}{day:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def day_levels():
    """S&P close for each day: Kalshi history, then the live collector's log."""
    out = {}
    m = pd.read_csv(ROOT / "history" / "kalshi_markets.csv")
    c = pd.to_numeric(m["expiration_value"].astype(str).str.replace(",", ""), errors="coerce")
    out.update(c.groupby(m["date"]).first().dropna().to_dict())
    log = ROOT / "data" / "snapshot_log.csv"
    if log.exists():
        lg = pd.read_csv(log)
        lv = lg.groupby(lg["snapshot_time_et"].str[:10])["spx_spot"].median().dropna()
        for d, v in lv.items():
            out.setdefault(d, v)
    return out


def symbols_for(day, level):
    base = int(level // 25 * 25)
    syms = []
    for edge in range(base - SPAN, base + SPAN + 25, 25):
        for k in (edge - 5, edge + 5):
            syms += [osi(day, "call", k), osi(day, "put", k)]
    return syms


def done_days():
    done = set()
    for f in OUT.glob("quotes_*.csv"):
        done |= set(pd.read_csv(f, usecols=["date"])["date"].astype(str))
    return done


def quotes_at_marks(df, day):
    """Last bid/ask at or before each mark time (within 10 minutes)."""
    if df.empty:
        return []
    df = df.reset_index()
    tcol = next(c for c in ("ts_recv", "ts_event", "index") if c in df.columns)
    df["t"] = pd.to_datetime(df[tcol], utc=True).dt.tz_convert("America/New_York")
    rows = []
    for mark in MARKS:
        hh, mm = map(int, mark.split(":"))
        mt = pd.Timestamp(datetime(day.year, day.month, day.day, hh, mm), tz="America/New_York")
        w = df[(df["t"] <= mt) & (df["t"] > mt - pd.Timedelta(minutes=10))]
        for sym, g in w.groupby("symbol"):
            last = g.iloc[-1]
            rows.append({"date": day.isoformat(), "mark_et": mark,
                         "type": "call" if sym[12] == "C" else "put",
                         "strike": int(sym[13:]) / 1000,
                         "bid": float(last["bid_px_00"]), "ask": float(last["ask_px_00"]),
                         "quote_time_et": last["t"].strftime("%H:%M")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-10-21")
    ap.add_argument("--end", default="2026-10-23")
    ap.add_argument("--max-cost", type=float, default=20.0)
    a = ap.parse_args()

    import databento as db
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        say("ERROR: no DATABENTO_API_KEY secret found.")
        return
    client = db.Historical(key)
    OUT.mkdir(exist_ok=True)

    levels = day_levels()
    days = sorted(d for d in levels if a.start <= d <= a.end)
    skip = done_days()
    todo = [d for d in days if d not in skip]
    say(f"{len(days)} trading days in range, {len(todo)} still to download, cost cap ${a.max_cost:.2f}")

    spent, buffer = 0.0, {}
    for i, d in enumerate(todo, 1):
        day = datetime.strptime(d, "%Y-%m-%d").date()
        syms = symbols_for(day, levels[d])
        params = dict(dataset=DATASET, schema=SCHEMA, symbols=syms, stype_in="raw_symbol",
                      start=f"{d}T13:00", end=f"{d}T21:00")
        try:
            cost = client.metadata.get_cost(**params)
        except Exception as e:
            say(f"{d}: price check failed: {repr(e)[:300]}")
            continue
        if spent + cost > a.max_cost:
            say(f"STOP: next day would bring the total to ${spent + cost:.2f}, over the "
                f"${a.max_cost:.2f} cap. Rerun with a higher --max-cost to continue.")
            break
        try:
            data = client.timeseries.get_range(**params)
            rows = quotes_at_marks(data.to_df(), day)
        except Exception as e:
            say(f"{d}: download failed: {repr(e)[:300]}")
            continue
        spent += cost
        buffer.setdefault(d[:4], []).extend(rows)
        if i % 20 == 0 or i == len(todo) or i <= 3:
            say(f"  {i}/{len(todo)} {d}: {len(rows)} quotes, total spent ${spent:.4f}")
        if i % 50 == 0:
            flush(buffer)
    flush(buffer)
    say(f"Done. Total spent: ${spent:.4f}")


def flush(buffer):
    for year, rows in buffer.items():
        if not rows:
            continue
        f = OUT / f"quotes_{year}.csv"
        new = pd.DataFrame(rows)
        new.to_csv(f, mode="a", index=False, header=not f.exists())
        rows.clear()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("ERROR:", traceback.format_exc()[-1500:])
    finally:
        OUT.mkdir(exist_ok=True)
        with open(OUT / "download_log.txt", "a") as f:
            f.write(f"\n--- run at {datetime.now():%Y-%m-%d %H:%M} ---\n" + "\n".join(lines) + "\n")
