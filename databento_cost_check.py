"""
databento_cost_check.py
-----------------------
Asks Databento what it WOULD cost to download the SPX option prices the paper
needs. Downloads nothing and spends nothing (price quotes are free).

For a sample of days (one per month), it lists the same-day-expiring SPXW
options around Kalshi's range edges (strikes 5 points either side of every
25-point level within 200 points of that day's close; calls and puts), and
asks Databento the price for two kinds of minute data:
    ohlcv-1m  : minute trade bars (like Massive)
    cbbo-1m   : minute bid/ask quotes (better)
Then it scales up to every trading day in the period.

Needs the GitHub secret DATABENTO_API_KEY. Run with the "databento-cost-check"
button in the Actions tab. Writes databento/cost_check.txt.
"""

import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "databento"
HIST = Path(__file__).parent / "history" / "kalshi_markets.csv"
DATASET = "OPRA.PILLAR"
START, END = "2023-01-01", "2024-10-31"   # the period the free Massive plan can't reach
lines = []


def say(*p):
    s = " ".join(str(x) for x in p)
    print(s, flush=True)
    lines.append(s)


def osi(day, kind, strike):
    """Databento/OPRA raw symbol, e.g. 'SPXW  231020C04300000'."""
    return f"{'SPXW':<6}{day:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def symbols_for(day, close):
    out = []
    base = int(close // 25 * 25)
    for edge in range(base - 200, base + 225, 25):
        for k in (edge - 5, edge + 5):
            out += [osi(day, "call", k), osi(day, "put", k)]
    return out


def main():
    import databento as db
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        say("ERROR: no DATABENTO_API_KEY secret found.")
        return
    client = db.Historical(key)

    try:
        say("Dataset range:", client.metadata.get_dataset_range(dataset=DATASET))
    except Exception as e:
        say("Could not read dataset range:", repr(e))

    # S&P close each day, from the Kalshi history (to know which strikes matter)
    m = pd.read_csv(HIST)
    close = pd.to_numeric(m["expiration_value"].astype(str).str.replace(",", ""), errors="coerce")
    closes = close.groupby(m["date"]).first().dropna()
    days = [d for d in closes.index if START <= d <= END]
    say(f"Trading days in {START} to {END}: {len(days)}")

    sample = pd.Series(days).groupby(pd.Series(days).str[:7]).first().tolist()
    results = {"ohlcv-1m": [], "cbbo-1m": []}
    for d in sample:
        day = datetime.strptime(d, "%Y-%m-%d").date()
        syms = symbols_for(day, closes[d])
        for schema in results:
            try:
                cost = client.metadata.get_cost(
                    dataset=DATASET, symbols=syms, stype_in="raw_symbol", schema=schema,
                    start=f"{d}T13:00", end=f"{(day + timedelta(days=1)).isoformat()}T00:00")
                results[schema].append(cost)
                say(f"{d} {schema}: {len(syms)} options -> ${cost:.4f}")
            except Exception as e:
                say(f"{d} {schema}: ERROR {repr(e)[:400]}")

    say("")
    for schema, costs in results.items():
        if costs:
            avg = sum(costs) / len(costs)
            say(f"{schema}: average ${avg:.4f} per day -> about ${avg * len(days):.2f} "
                f"for all {len(days)} days")
    say("(The free credit for new accounts is $125.)")

    # One real-size check: the exact price for a full month in one request.
    month = [d for d in days if d.startswith(sample[len(sample) // 2][:7])]
    syms = [s for d in month for s in symbols_for(datetime.strptime(d, "%Y-%m-%d").date(), closes[d])]
    for schema in results:
        try:
            cost = client.metadata.get_cost(dataset=DATASET, symbols=syms, stype_in="raw_symbol",
                                            schema=schema, start=f"{month[0]}T13:00",
                                            end=f"{month[-1]}T23:59")
            say(f"Full month {month[0][:7]} {schema}: {len(syms)} symbols -> ${cost:.2f}")
        except Exception as e:
            say(f"Full month check {schema}: ERROR {repr(e)[:400]}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("ERROR:", traceback.format_exc()[-1500:])
    finally:
        OUT.mkdir(exist_ok=True)
        (OUT / "cost_check.txt").write_text("\n".join(lines))
