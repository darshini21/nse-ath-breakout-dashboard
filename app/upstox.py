"""Upstox public candle API - no API key or login needed.

Verified 2026-11-08:
  * GET /v3/historical-candle/intraday/{key}/minutes/1      today's 1-minute candles (newest first)
  * GET /v3/historical-candle/{key}/days/1/{to}/{from}       daily candles; a 10-year range is rejected
                                                             ("Invalid date range"), 5 years is accepted
  * instrument key for an NSE stock is "NSE_EQ|<ISIN>"; indices are "NSE_INDEX|Nifty 50" etc.
  * intraday candle volume is the day's running total, not per-candle
Responses carry Cache-Control: max-age=14400, so every URL gets a 30-second cache-buster.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from datetime import date, timedelta

import pandas as pd
import requests

from . import config as C

BASE = "https://api.upstox.com/v3/historical-candle"
MAX_DAILY_SPAN_DAYS = 1800          # under the accepted 5-year window
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "nse-breakout-dashboard/1.0"})


class _Limiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval

    def slow_down(self, seconds: float):
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)


limiter = _Limiter(C.UPSTOX_REQ_PER_SEC)
stats = {"requests": 0, "throttled": 0, "errors": 0}


def key_for_isin(isin: str) -> str:
    return f"NSE_EQ|{isin}"


def _get(url: str, timeout: float = 30) -> dict | None:
    for attempt in range(6):
        limiter.wait()
        stats["requests"] += 1
        try:
            r = _session.get(url, params={"t": int(time.time() // 30)}, timeout=timeout)
        except requests.RequestException:
            stats["errors"] += 1
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429 or r.status_code >= 500:
            stats["throttled"] += 1
            ra = r.headers.get("Retry-After")
            pause = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0 * (attempt + 1)
            limiter.slow_down(pause)
            time.sleep(pause)
            continue
        if r.status_code in (400, 404):
            return None  # unknown instrument / invalid range
        stats["errors"] += 1
        time.sleep(1.0)
    return None


def _frame(candles: list, daily: bool) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame([c[:6] for c in candles], columns=["date", "open", "high", "low", "close", "volume"])
    ts = pd.to_datetime(df["date"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = ts.dt.tz_localize(None).dt.normalize() if daily else ts
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["volume"] = df["volume"].astype("int64")
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def daily_candles(key: str, start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV between start and end inclusive, in <= 5-year chunks."""
    frames = []
    chunk_end = end
    while chunk_end >= start:
        chunk_start = max(start, chunk_end - timedelta(days=MAX_DAILY_SPAN_DAYS))
        url = f"{BASE}/{urllib.parse.quote(key, safe='')}/days/1/{chunk_end:%Y-%m-%d}/{chunk_start:%Y-%m-%d}"
        j = _get(url)
        candles = ((j or {}).get("data") or {}).get("candles") or []
        frames.append(_frame(candles, daily=True))
        chunk_end = chunk_start - timedelta(days=1)
    if not frames:
        return _frame([], True)
    return pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def intraday_candles(key: str, minutes: int = 1) -> pd.DataFrame:
    url = f"{BASE}/intraday/{urllib.parse.quote(key, safe='')}/minutes/{minutes}"
    j = _get(url, timeout=20)
    return _frame(((j or {}).get("data") or {}).get("candles") or [], daily=False)


def latest_quote(key: str) -> dict | None:
    """Collapse today's 1-minute candles into a quote: last close, day O/H/L, running volume, candle time.

    When there is no session today yet (overnight, weekend, holiday) fall back to the most
    recent daily candle, stamped with that day's date so the UI shows how old it is.
    """
    df = intraday_candles(key, 1)
    if df.empty:
        today = date.today()
        d = daily_candles(key, today - timedelta(days=10), today)
        if d.empty:
            return None
        last = d.iloc[-1]
        day = pd.Timestamp(last["date"]).strftime("%Y-%m-%d")
        return {"ltp": float(last["close"]), "open": float(last["open"]), "high": float(last["high"]),
                "low": float(last["low"]), "volume": int(last["volume"]), "ts": f"{day}T15:30:00+05:30", "day": day}
    last = df.iloc[-1]
    return {
        "ltp": float(last["close"]),
        "open": float(df["open"].iloc[0]),
        "high": float(df["high"].max()),
        "low": float(df["low"].min()),
        "volume": int(df["volume"].max()),
        "ts": pd.Timestamp(last["date"]).isoformat(timespec="seconds"),
        "day": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
    }
