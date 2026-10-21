"""
analyze_history.py
------------------
How accurate have Kalshi's S&P 500 range prices been?  ("calibration")

Idea: take every time Kalshi priced a range at about X%, and check how often
the S&P actually closed in that range. If prices are accurate, ranges priced
at 30% should win about 30% of the time.

Uses the files from download_history.py:
    history/kalshi_markets.csv   (which range won each day)
    history/kalshi_hourly.csv    (Kalshi bid/ask at 10am, 11am, ... 3pm)

Run:
    pip install pandas numpy matplotlib
    python analyze_history.py

Writes:
    results/calibration.csv          price bucket -> how often it actually won
    results/calibration_by_hour.csv  the same, separately for each hour
    results/calibration.png          the chart
"""

from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path(__file__).parent / "history"
OUT = Path(__file__).parent / "results"
BUCKETS = [0, .05, .10, .20, .30, .40, .50, .60, .70, .80, 1.0001]


def load():
    m = pd.read_csv(HIST / "kalshi_markets.csv")
    h = pd.read_csv(HIST / "kalshi_hourly.csv")
    d = h.merge(m[["ticker", "result", "floor_strike", "cap_strike"]], on="ticker")
    d["won"] = (d["result"] == "yes").astype(int)
    d["price"] = (d["yes_bid"] + d["yes_ask"]) / 2          # Kalshi's probability
    # Kalshi's prices add up to more than 100% (a built-in markup). Dividing
    # by the total gives a "markup removed" version that adds up to exactly 100%.
    d["price_no_markup"] = d["price"] / d.groupby(["date", "hour_et"])["price"].transform("sum")
    return d


def calibration(d, col):
    x = d.assign(bucket=pd.cut(d[col], BUCKETS, right=False))
    g = x.groupby("bucket", observed=True).agg(
        count=("won", "size"), avg_price=(col, "mean"), actually_won=("won", "mean"),
        days=("date", "nunique"))
    # rough 95% range for the win rate (treats observations as independent,
    # so it's a bit too narrow: ranges on the same day are related)
    g["plus_minus"] = 1.96 * np.sqrt(g["actually_won"] * (1 - g["actually_won"]) / g["count"])
    g["won_per_price"] = g["actually_won"] / g["avg_price"]
    return g.reset_index()


def main():
    OUT.mkdir(exist_ok=True)
    d = load()
    print(f"{d['date'].nunique()} days, {d['date'].min()} to {d['date'].max()}, "
          f"{len(d):,} hourly range prices\n")

    raw = calibration(d, "price").assign(version="Kalshi price")
    adj = calibration(d, "price_no_markup").assign(version="markup removed")
    table = pd.concat([raw, adj])
    table.round(4).to_csv(OUT / "calibration.csv", index=False)
    show = ["bucket", "count", "avg_price", "actually_won", "plus_minus", "won_per_price"]
    print("Kalshi price vs. how often it actually happened (all hours):")
    print(raw[show].round(3).to_string(index=False))
    print("\nSame, with Kalshi's markup removed:")
    print(adj[show].round(3).to_string(index=False))

    by_hour = pd.concat([calibration(g, "price").assign(hour_et=hr) for hr, g in d.groupby("hour_et")])
    by_hour.round(4).to_csv(OUT / "calibration_by_hour.csv", index=False)

    brier = ((d["price"] - d["won"]) ** 2).mean()
    print(f"\nBrier score (lower = more accurate; 0 = perfect): {brier:.4f}")
    chart(raw, adj, d, OUT / "calibration.png")
    print("Wrote results/calibration.csv, results/calibration_by_hour.csv, results/calibration.png")


BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def chart(raw, adj, d, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 5), dpi=150, facecolor=SURFACE)
    for ax, lim, title in ((axes[0], 100, "All prices"), (axes[1], 25, "Zoom: long shots (0–25%)")):
        ax.set_facecolor(SURFACE)
        ax.plot([0, lim], [0, lim], color=INK2, linewidth=1, linestyle="--", label="Perfectly accurate")
        for tbl, color, label, mk in ((raw, ORANGE, "Kalshi price", "o"),
                                      (adj, BLUE, "Kalshi price, markup removed", "s")):
            t = tbl[tbl["avg_price"] * 100 <= lim * 1.02]
            ax.errorbar(t["avg_price"] * 100, t["actually_won"] * 100, yerr=t["plus_minus"] * 100,
                        color=color, marker=mk, markersize=7, linewidth=2, capsize=0,
                        markeredgecolor=SURFACE, markeredgewidth=1.5, label=label)
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.set_title(title, loc="left", fontsize=10, color=INK)
        ax.set_xlabel("Kalshi's probability (%)", fontsize=9, color=INK2)
        ax.set_ylabel("How often the S&P actually closed in that range (%)", fontsize=9, color=INK2)
        ax.tick_params(colors=INK2, length=0, labelsize=8)
        ax.grid(color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ax.spines.values():
            s.set_visible(False)

    low = raw.iloc[0]
    axes[1].annotate(f"Priced {low.avg_price:.1%}, happened {low.actually_won:.1%}",
                     xy=(low.avg_price * 100, low.actually_won * 100), xytext=(4, 1.5),
                     fontsize=8, color=INK2, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK, loc="upper left")
    fig.suptitle(f"How accurate are Kalshi's S&P 500 range prices?  "
                 f"{d['date'].nunique()} trading days, {d['date'].min()} to {d['date'].max()}",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.01, "Points below the dashed line = Kalshi priced it too high. "
             "Vertical bars = rough 95% range from chance.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    main()
