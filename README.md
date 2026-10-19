# S&P 500: Options vs. Prediction Markets

Collects matched snapshots of:

- **Kalshi** "S&P 500 price range today at 4pm" prices (each range's price ≈ the crowd's probability)
- **S&P 500 options expiring today** (SPX, with SPY as a backup), from Yahoo Finance

Both settle on the same 4pm close, so later we can turn the option prices into probabilities for the same ranges and compare.

## Option A: run it automatically in the cloud (recommended, free)

1. Create a free GitHub account and a new **private** repository.
2. Upload `collect_snapshot.py`, `requirements.txt`, and `README.md`. The `.github` folder is hidden on most computers and often gets skipped when uploading, so add it by hand: click **Add file → Create new file**, type `.github/workflows/collect.yml` as the name, and paste in the contents of `collect.yml`.
3. Go to the repo's **Actions** tab and enable workflows if asked.
4. Click **collect-snapshots → Run workflow** once to test. During market hours you should see a new `data/` folder appear within a minute or two.

After that, it runs by itself every 30 minutes on weekdays during market hours. Check the `data/snapshot_log.csv` file every few days to make sure it's still working.

## Option B: run it on your own computer

```
pip install -r requirements.txt
python collect_snapshot.py --force
```

`--force` lets you test it anytime (outside market hours, today's options may be missing). Without `--force`, it only collects between 9:40am and 3:55pm Eastern on weekdays. The computer has to be on, so Option A is more reliable.

## What gets saved

| File | What's in it |
|---|---|
| `data/YYYY-MM-DD/HHMM_kalshi.csv` | One row per S&P range: lower/upper edge, yes bid, yes ask, mid, volume |
| `data/YYYY-MM-DD/HHMM_SPX_options.csv` | Every SPX call and put expiring that day: strike, bid, ask, mid, volume, open interest, implied vol, last trade time |
| `data/YYYY-MM-DD/HHMM_SPY_options.csv` | Same for SPY (about 1/10 of the S&P's price) |
| `data/snapshot_log.csv` | One line per run: time, S&P level, how many rows each source returned, any errors |

## Things to know

- **Use bid/ask midpoints, not last prices.** Last trades can be stale; the midpoint is a better estimate of the current price.
- **Yahoo data can be delayed (often ~15 minutes).** Check the `lastTradeDate` column and the `spx_spot_time` in the log, and account for this when matching times.
- **Kalshi probabilities should add up to about 1.0** across all ranges (the log records the sum). If it's far off, something is wrong with that snapshot.
- **Holidays:** on market holidays there's no Kalshi event or expiring option, and the log will show errors for that day. That's expected.
- **If Kalshi errors every day**, the ticker format may have changed. Look up today's S&P range market on kalshi.com, compare the ticker in the URL (e.g. `kxinx-26oct19h1600`) with `kalshi_event_ticker()` in the script.
