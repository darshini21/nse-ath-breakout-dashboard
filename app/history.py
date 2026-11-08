"""Download and incrementally update 10 years of daily candles for the universe.

Source: Upstox's public candle API (no key). A few worker threads share one rate limiter.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd

from . import config as C
from . import upstox
from .universe import load_universe


def hist_path(symbol: str):
    return C.HIST / f"{symbol}.parquet"


def load_history(symbol: str) -> pd.DataFrame | None:
    p = hist_path(symbol)
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _last_trading_day(today: date) -> date:
    d = today - timedelta(days=1)  # Upstox's daily series ends at the previous session
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _update_one(row, today: date, target: date, floor: date, full: bool) -> str:
    sym = row.symbol
    listing = pd.Timestamp(row.listing_date).date()
    start = max(listing - timedelta(days=5), floor)
    existing = None if full else load_history(sym)
    if existing is not None and not existing.empty:
        last = existing["date"].max().date()
        if last >= target:
            return "current"
        start = last + timedelta(days=1)
    new = upstox.daily_candles(row.upstox_key, start, today)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, new], ignore_index=True) if not new.empty else existing
    else:
        df = new
    if df is None or df.empty:
        return "nodata"
    df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    df.to_parquet(hist_path(sym), index=False)
    return "updated"


def update_all(symbols: list[str] | None = None, full: bool = False, workers: int = 3):
    uni = load_universe()
    if "upstox_key" not in uni.columns:
        from .universe import build_universe
        uni = build_universe()
    if symbols:
        uni = uni[uni["symbol"].isin([s.upper() for s in symbols])]
    today = date.today()
    target = _last_trading_day(today)
    floor = today - timedelta(days=365 * C.LISTING_YEARS + 30)

    rows = list(uni.itertuples(index=False))
    n = len(rows)
    counts = {"updated": 0, "current": 0, "nodata": 0, "failed": 0}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_update_one, r, today, target, floor, full): r.symbol for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                counts[f.result()] += 1
            except Exception as e:
                counts["failed"] += 1
                print(f"\n{futs[f]}: {e}", file=sys.stderr)
            if i % 10 == 0 or i == n:
                el = time.time() - t0
                eta = (n - i) / (i / el) if el and i else 0
                print(f"\r{i}/{n}  updated {counts['updated']}  up-to-date {counts['current']}  no-data {counts['nodata']}  "
                      f"failed {counts['failed']}  req {upstox.stats['requests']} (429s {upstox.stats['throttled']})  ETA {eta/60:.1f} min",
                      end="", flush=True)
    print(f"\nHistory update complete: {counts['updated']} updated, {counts['current']} already current, "
          f"{counts['nodata']} with no data, {counts['failed']} failed.")
