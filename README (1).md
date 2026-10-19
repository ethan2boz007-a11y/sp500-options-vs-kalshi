# S&P 500: Options vs. Prediction Markets

Collects matched snapshots of:

- **Kalshi** "S&P 500 price range at 4pm" prices (each range's price ≈ the crowd's probability)
- **S&P 500 options** (SPX, with SPY as a backup), from Yahoo Finance

Each snapshot covers two horizons: the market settling **today** at 4pm (`0dte`) and the one settling on the **next trading day** (`next`). Kalshi and the options settle on the same 4pm close, so later we turn the option prices into probabilities for the same ranges and compare.

## Option A: run it automatically in the cloud (recommended, free)

1. Create a free GitHub account and a new **private** repository.
2. Upload `collect_snapshot.py`, `requirements.txt`, and `README.md`. The `.github` folder is hidden on most computers and often gets skipped when uploading, so add it by hand: click **Add file → Create new file**, type `.github/workflows/collect.yml` as the name, and paste in the contents of `collect.yml`.
3. Go to the repo's **Actions** tab and enable workflows if asked.
4. Click **collect-snapshots → Run workflow** once to test. During market hours you should see a new `data/` folder appear within a minute or two.

After that, it runs by itself on weekdays: every 15 minutes from 9:35am, and every 5 minutes from 3:00 to 3:58pm Eastern. Check `data/snapshot_log.csv` once or twice a week to make sure new lines keep appearing.

**Keep it running for the whole project, ideally 3+ months.** Snapshots from the same day share one 4pm outcome, so the number of *days* matters most for judging which source is better calibrated.

The schedule uses roughly 1,100 of the 2,000 free GitHub Actions minutes per month that private repositories get. (Public repositories have no limit.)

## Option B: run it on your own computer

```
pip install -r requirements.txt
python collect_snapshot.py --force
```

`--force` lets you test it anytime (outside market hours, some expiries may be missing). Without `--force`, it only collects between 9:35am and 3:58pm Eastern on weekdays. The computer has to be on, so Option A is more reliable.

## What gets saved

Every file name starts with the snapshot time (`HHMM`) and ends with the horizon (`0dte` or `next`).

| File | What's in it |
|---|---|
| `data/YYYY-MM-DD/HHMM_kalshi_0dte.csv`, `..._kalshi_next.csv` | One row per S&P range: settle date, lower/upper edge, yes bid, yes ask, mid, volume, open interest |
| `data/YYYY-MM-DD/HHMM_SPX_options_0dte.csv`, `..._next.csv` | Every SPX call and put for that expiry: strike, bid, ask, mid, volume, open interest, implied vol, last trade time |
| `data/YYYY-MM-DD/HHMM_SPY_options_0dte.csv`, `..._next.csv` | Same for SPY (about 1/10 of the S&P's price) |
| `data/snapshot_log.csv` | One line per run: time, S&P level, settle dates and row counts for each source, any errors |

**Not collected (download later for free):** the actual 4pm S&P closes (Yahoo Finance history) and Kalshi's final results (Kalshi's historical API). Only option prices have to be captured live.

## Things to know

- **Use bid/ask midpoints, not last prices.** Last trades can be stale; the midpoint is a better estimate of the current price.
- **Yahoo data can be delayed (often ~15 minutes).** Check the `lastTradeDate` column and the `spx_spot_time` in the log, and account for this when matching times.
- **Kalshi probabilities sum to more than 1.0** (often 1.2–1.4). Far-away ranges barely trade, so their midpoints (e.g. 0¢ bid, 3¢ ask → 1.5%) overstate tiny chances. The analysis will rescale and focus on ranges near the current price.
- **Holidays:** on market holidays there's no event or expiring option, and the log will show errors for that day. That's expected.
- **If Kalshi errors every day**, the ticker format may have changed. Look up today's S&P range market on kalshi.com, compare the ticker in the URL (e.g. `kxinx-26oct19h1600`) with `kalshi_event_ticker()` in the script.
- **The first test run** (Oct 19, 2026, 2:31pm) used an earlier version with slightly different file names (`1431_kalshi.csv`, etc.). It's fine to keep.
