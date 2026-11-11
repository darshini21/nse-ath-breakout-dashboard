"""Indicator maths used by the Malkan systems: Wilder RSI, simple moving average,
weekly / monthly resampling (bars dated by their last actual trading day)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (the standard one on charting platforms)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    return out


def sma(close: pd.Series, period: int = 20) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = df.assign(_last=df["date"]).set_index("date")
    w = (
        d.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "_last": "last"})
        .dropna(subset=["close"])
        .reset_index(drop=True)
        .rename(columns={"_last": "date"})
    )
    return w[["date", "open", "high", "low", "close", "volume"]]


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return _resample(df, "W-FRI")


def to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return _resample(df, "ME")


def period_complete(last_date: pd.Timestamp, today, rule: str) -> bool:
    """Has the week/month containing last_date already ended (as of today)?"""
    last_date = pd.Timestamp(last_date)
    today = pd.Timestamp(today)
    if rule == "W":
        end = last_date + pd.Timedelta(days=(4 - last_date.weekday()) % 7)   # that week's Friday
    else:
        end = last_date + pd.offsets.MonthEnd(0)
    return today > end


def swing_low(lows: pd.Series, lookback: int = 10) -> float:
    return float(lows.tail(lookback).min())


def swing_high(highs: pd.Series, lookback: int = 12) -> float:
    return float(highs.tail(lookback).max())
