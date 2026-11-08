# NSE all-time-high breakout dashboard

Scans every NSE main-board stock listed in the last 10 years for a **cup (U)** or
**double-bottom (W)** base that broke above the stock's previous all-time high, and shows
the survivors with near-real-time prices. **No API key or broker login is needed**: all
price data comes from Upstox's public candle API. An Angel One account is optional and
only upgrades the price feed from 1-minute candles to tick-by-tick streaming.

## 1. One-time setup

```bat
run.cmd setup
```

Creates a Python virtualenv at `%LOCALAPPDATA%\nse-breakout-venv` and installs the
dependencies. (The venv lives outside the project folder because very long Windows
paths break pip.)

## 2. Daily use

| Command | What it does |
|---|---|
| `run.cmd universe` | Builds the stock list: NSE `EQUITY_L.csv` (series EQ, listed within 10 years) with Upstox keys (`NSE_EQ\|<ISIN>`) and optional Angel One tokens. |
| `run.cmd history` | Downloads up to 10 years of daily candles per stock into `data/history/*.parquet` from Upstox. First run ≈ 15–20 min (paced at 2.5 requests/s); later runs only fetch new days. |
| `run.cmd screen` | Runs the pattern screen and writes `data/results.json`. Options: `--depth 15 --weeks 7 --age 0`. |
| `run.cmd serve` | Starts the dashboard at <http://127.0.0.1:8000>. |
| `run.cmd all` | universe → history → screen → serve. |
| `run.cmd test` | Synthetic-shape checks for the U/W detector. |

Run `history` after each market close (it only fetches the new day), then `screen`, and
keep `serve` running during the day.

## Sharing it: GitHub Pages (free hosting, auto-updated)

The dashboard also runs as a **static site** that anyone can open. Each visitor's browser
fetches live 1-minute candles from Upstox directly (the API allows browser requests), and
a GitHub Actions workflow re-runs the whole scan after every market close and republishes.

1. Push this folder to a GitHub repository (public repos get Pages and Actions for free).
2. In the repo: **Settings → Pages → Source: GitHub Actions**.
3. **Actions → "Daily scan and publish to GitHub Pages" → Run workflow** for the first
   build (about 12 minutes: it downloads the 10-year history, later runs are incremental
   thanks to the cache). The site appears at `https://<user>.github.io/<repo>/`.

The workflow ([.github/workflows/pages.yml](.github/workflows/pages.yml)) runs at 16:20 IST
on weekdays and on every push to `main`. `run.cmd export` builds the same site locally
into `site/` if you ever want to host it elsewhere; it is plain HTML/JS/JSON.

What the hosted version cannot do: Angel One tick streaming (needs your private key) and
the Re-scan button (the scan runs in the Action instead). Filters still work.

## Price data sources

| Source | Used for | Needs | Latency |
|---|---|---|---|
| **Upstox public candle API** (`api.upstox.com/v3/historical-candle/...`) | daily history; live prices as the latest 1-minute candle close | nothing | ≈ 1 minute; polled every 60 s during market hours (`LIVE_POLL_SECONDS`), every 10 min otherwise |
| Angel One SmartAPI WebSocket | tick-by-tick prices | `.env` with API key, client code, MPIN, TOTP secret (see `.env.example`) | sub-second |
| NSE `EQUITY_L.csv` | the stock universe and listing dates | nothing | — |

Verified behaviour of the Upstox endpoints (2026-11-08): no authentication; daily candles
accept a 5-year range per request (10 years is rejected, so history is fetched in two
chunks); intraday 1-minute candles return the current/last session, newest first, with
the day's running volume; responses are cacheable for 4 h, so each request carries a
30-second cache-buster. The server paces requests and backs off on HTTP 429.

Set `SIMULATE=1` to run with a random-walk demo feed instead (labelled on screen).

## How the screen works

All structure is measured on **weekly closes** (daily data is used for the current price
and for the chart).

1. **Rim** – the highest weekly close so far (the all-time-high close).
2. **Base** – from a rim until the first weekly close *above* it (the breakout week).
   A base counts only if it is at least `min_depth_pct` deep (default 15 %) and
   `min_weeks` long (default 7).
3. **U or W** – one trough = cup. Two troughs with a middle peak that recovers ≥ 1/3 of
   the depth, the second trough within 25 % of depth of the first = double bottom.
4. **Qualifies ("Breakouts" tab)** – the stock's *latest* base is a valid U/W, it broke
   out, the price is still above that rim, and it has not since dropped ≥ `min_depth_pct`
   from its post-breakout high (that would be a new, unfinished base). When a stock
   makes a U, runs to a new high, then forms a W under that new high, the W has to
   break the *new* high to count — the newer base always wins.
5. **Approaching tab** – stocks currently inside a deep-enough base and within
   `near_breakout_pct` (5 %) below the rim: the ones that may cross next.

Extra information per stock: breakout-week volume vs. the base average (a classic
confirmation), time spent near the bottom (separates round U's from sharp V's), the
post-breakout high and pullback from it, 52-week range, listing date, and the full
history of earlier bases.

## Accuracy notes

* Prices are exchange candles, not estimates. The day's % change is measured against the
  previous session's official close from the stored daily history.
* The header shows the timestamp of the newest candle seen, so you always know how fresh
  the numbers are; with the Angel One feed it shows **stale** if ticks stop for 60 s
  during market hours.
* Market phase (pre-open 09:00–09:15, open 09:15–15:30 IST) is computed locally. Special
  sessions and holidays are not known in advance: the poller keeps checking at the idle
  cadence and will pick up a Muhurat-type session, just more slowly.
* Pattern rules are heuristics with tunable parameters, not a guarantee of anything.
  This is not investment advice.
