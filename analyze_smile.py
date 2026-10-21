"""
analyze_smile.py
----------------
Gives Kalshi its own "volatility smile," using the downloaded history.

IDEA (plain version)
  Every Kalshi range price says "X% chance the S&P ends above this level."
  The standard option formula can be run backward: what volatility (expected
  amount of movement) would make that probability come out right?
  Doing this at every range edge gives one volatility per level: a smile.
  If far-away levels get higher volatility than levels near the current
  price, the market is charging extra for big moves. If the downside is
  higher than the upside, it's charging extra for crashes ("skew").

STEPS, for each day and each hour (10am-3pm ET):
  1. Probability of each range = Kalshi bid-ask midpoint. Only ranges with a
     real buyer (bid > 0) are used, so Kalshi's 1-cent minimum price can't
     create a fake smile. The probabilities are rescaled to add up to 100%.
  2. P(above level K) = sum of probabilities of all ranges above K.
  3. Kalshi's "center" F = the level with a 50% chance of ending above it.
  4. Implied volatility at K: from P(above K) = N( ln(F/K) / (vol * sqrt(T)) )
         vol = ln(F/K) / ( sqrt(T) * Ninv(P) )
     where T = time left until 4pm, in years of trading time
     (6.5 hours per day, 252 days per year), and N is the bell-curve formula.
     Levels very close to F are skipped (the formula is unstable there).
  5. To compare days with calm and wild markets, each volatility is divided by
     that hour's typical (median) volatility, and each level is measured in
     "typical moves" away from F.

OUTPUTS (results/)
  smile_points.csv     every level, every hour, every day
  smile_curve.csv      the average smile (overall and by year)
  skew_daily.csv       one crash-premium number per day + the S&P's move
  smile.png            chart

Run:  python analyze_smile.py      (needs pandas, numpy, scipy, matplotlib)
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HIST = Path(__file__).parent / "history"
OUT = Path(__file__).parent / "results"
BINS = np.arange(-3.25, 3.5, 0.5)          # "typical moves" from the center
DOWN, UP = (-2.0, -1.0), (1.0, 2.0)         # where the crash premium is measured


def load():
    m = pd.read_csv(HIST / "kalshi_markets.csv")
    # Older markets don't say what kind of range they are; work it out.
    st = m["strike_type"].copy()
    st[st.isna() & m["floor_strike"].isna()] = "less"
    st[st.isna() & m["cap_strike"].isna()] = "greater"
    st[st.isna()] = "between"
    m["strike_type"] = st
    h = pd.read_csv(HIST / "kalshi_hourly.csv")
    d = h.merge(m[["ticker", "strike_type", "floor_strike"]], on="ticker")
    d["prob"] = np.where(d["yes_bid"] > 0, (d["yes_bid"] + d["yes_ask"]) / 2, 0.0)
    # The S&P's actual close each day (older entries are written like "3,583.07").
    ev = pd.to_numeric(m["expiration_value"].astype(str).str.replace(",", ""), errors="coerce")
    closes = ev.groupby(m["date"]).first().dropna().sort_index()
    return d, closes


def smile_points(d):
    rows = []
    for (day, hour), g in d.groupby(["date", "hour_et"]):
        total = g["prob"].sum()
        if not 0.8 < total < 1.6 or (g["prob"] > 0).sum() < 3:
            continue                                  # too few real prices this hour
        p = g["prob"] / total
        low_edge = np.where(g["strike_type"] == "less", -np.inf, g["floor_strike"])
        levels = np.array(sorted(g.loc[g["strike_type"] != "less", "floor_strike"]))
        p_above = np.array([p[low_edge >= k - 1e-6].sum() for k in levels])
        if len(levels) < 2 or not p_above.max() > 0.5 > p_above.min():
            continue
        center = np.interp(0.5, p_above[::-1], levels[::-1])
        T = (16 - hour) / (6.5 * 252)
        for k, pa in zip(levels, p_above):
            if 0.02 < pa < 0.98 and abs(pa - 0.5) > 0.12:
                vol = np.log(center / k) / (np.sqrt(T) * norm.ppf(pa))
                if vol > 0:
                    rows.append((day, hour, k, center, pa, vol, T))
    r = pd.DataFrame(rows, columns=["date", "hour_et", "level", "center", "p_above", "implied_vol", "T"])
    r["typical_vol"] = r.groupby(["date", "hour_et"])["implied_vol"].transform("median")
    r["moves_from_center"] = np.log(r["level"] / r["center"]) / (r["typical_vol"] * np.sqrt(r["T"]))
    r["relative_vol"] = r["implied_vol"] / r["typical_vol"]
    return r


def curve(r):
    r = r[r["moves_from_center"].between(BINS[0], BINS[-1])]
    nearest_half = (r["moves_from_center"] * 2).round() / 2     # group into half-move steps
    out = (r.assign(moves=nearest_half).groupby("moves")
           .agg(points=("relative_vol", "size"), relative_vol=("relative_vol", "median"),
                days=("date", "nunique")).reset_index())
    return out[(out["points"] >= 50) & (out["moves"].abs() >= 0.5)]


def daily_skew(r, closes):
    x = r["moves_from_center"]
    side = np.select([x.between(*DOWN), x.between(*UP)], ["down", "up"], "")
    s = (r.assign(side=side).query("side != ''")
         .groupby(["date", "side"])["relative_vol"].median().unstack())
    s = s.dropna()
    s["crash_premium"] = s["down"] / s["up"] - 1          # 0.10 = downside priced 10% higher
    ret = closes.pct_change()
    s["sp500_prev_day_return"] = ret.shift(1).reindex(s.index)
    s["sp500_same_day_return"] = ret.reindex(s.index)
    return s.reset_index()


def main():
    OUT.mkdir(exist_ok=True)
    d, closes = load()
    r = smile_points(d)
    r.round(5).to_csv(OUT / "smile_points.csv", index=False)
    print(f"{len(r):,} implied-volatility points from {r['date'].nunique()} days "
          f"({r['date'].min()} to {r['date'].max()})\n")

    overall = curve(r).assign(period="all")
    by_year = pd.concat([curve(g).assign(period=y) for y, g in r.groupby(r["date"].str[:4])])
    curves = pd.concat([overall, by_year])
    curves.round(4).to_csv(OUT / "smile_curve.csv", index=False)
    print("Kalshi's average smile (volatility relative to typical, by distance from center):")
    print(curves.pivot(index="moves", columns="period", values="relative_vol").round(3).to_string())

    s = daily_skew(r, closes)
    s.round(5).to_csv(OUT / "skew_daily.csv", index=False)
    print(f"\nCrash premium (downside vol / upside vol - 1), median over {len(s)} days: "
          f"{s['crash_premium'].median():+.1%}")
    print(f"Days with downside priced higher than upside: {(s['crash_premium'] > 0).mean():.0%}")
    print("By year:", s.groupby(s["date"].str[:4])["crash_premium"].median().map("{:+.1%}".format).to_dict())
    b = pd.cut(s["sp500_prev_day_return"], [-1, -0.01, 0.01, 1],
               labels=["after a drop of 1%+", "after a normal day", "after a rise of 1%+"])
    print("\nCrash premium depending on the previous day:")
    print(s.groupby(b, observed=True)["crash_premium"].agg(["median", "count"])
          .assign(median=lambda t: t["median"].map("{:+.1%}".format)).to_string())
    chart(curves, s, OUT / "smile.png")
    print("\nWrote results/smile_points.csv, smile_curve.csv, skew_daily.csv, smile.png")


BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=INK2, length=0, labelsize=8)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_visible(False)


def chart(curves, s, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.8), dpi=150, facecolor=SURFACE,
                                 gridspec_kw={"width_ratios": [1, 1.25]})
    style(a1)
    c = curves[curves["period"] == "all"]
    for side in (c[c["moves"] < 0], c[c["moves"] > 0]):
        a1.plot(side["moves"], side["relative_vol"], color=BLUE, marker="o", markersize=7,
                linewidth=2, markeredgecolor=SURFACE, markeredgewidth=1.5)
    a1.axhline(1, color=INK2, linewidth=1, linestyle="--")
    a1.axvline(0, color=GRID, linewidth=1)
    a1.set_title("Kalshi's average volatility smile", loc="left", fontsize=10, color=INK)
    a1.set_xlabel("Distance from Kalshi's center (typical moves)\n← S&P lower            S&P higher →",
                  fontsize=9, color=INK2)
    a1.set_ylabel("Implied volatility ÷ typical volatility", fontsize=9, color=INK2)
    a1.text(0.36, 0.97, "Above 1 = extra price charged\nfor moves that far away", transform=a1.transAxes,
            va="top", fontsize=8, color=INK2)

    style(a2)
    m = s.assign(month=pd.to_datetime(s["date"]).dt.to_period("M").dt.to_timestamp())
    mo = m.groupby("month")["crash_premium"].median() * 100
    a2.bar(mo.index, mo.values, width=24, color=np.where(mo.values >= 0, ORANGE, BLUE))
    a2.axhline(0, color=INK2, linewidth=1)
    a2.set_title("Crash premium by month (downside vs. upside volatility)", loc="left",
                 fontsize=10, color=INK)
    a2.set_ylabel("Downside priced higher by (%)", fontsize=9, color=INK2)
    a2.text(0.01, 0.97, "Orange = downside priced higher (fear)\nBlue = upside priced higher",
            transform=a2.transAxes, va="top", fontsize=8, color=INK2)
    fig.suptitle(f"Kalshi S&P 500 range markets, {s['date'].min()} to {s['date'].max()} "
                 f"({len(s)} days)", x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    main()
