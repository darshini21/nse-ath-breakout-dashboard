"""Synthetic-shape checks for the U / W detector. Run: python -m tests.test_screener"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.config import ScreenParams
from app.screener import analyse, find_bases, to_weekly


def make_daily(weekly_closes: list[float], start="2018-01-01") -> pd.DataFrame:
    """Expand weekly closes into 5 daily candles each (Mon-Fri), with mild intraweek noise."""
    rng = np.random.default_rng(0)
    days = pd.bdate_range(start, periods=len(weekly_closes) * 5)
    closes = np.repeat(weekly_closes, 5) * (1 + rng.normal(0, 0.002, len(days)))
    # make Friday's close exactly the weekly close so resampling is deterministic
    closes[4::5] = weekly_closes
    df = pd.DataFrame({"date": days, "close": closes})
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df[["open", "close"]].max(axis=1) * 1.005
    df["low"] = df[["open", "close"]].min(axis=1) * 0.995
    df["volume"] = 100000
    return df[["date", "open", "high", "low", "close", "volume"]]


def ramp(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def cup(rim=100, low=70, down=10, flat=6, up=10):
    return ramp(rim, low, down) + [low] * flat + ramp(low, rim, up)


def test_u_breakout():
    weekly = ramp(50, 100, 20) + cup() + [103, 106, 110, 112]   # breaks 100, keeps rising
    res = analyse("TESTU", {"name": "Test U", "token": "1", "listing_date": "2018-01-05"}, make_daily(weekly), ScreenParams(), date(2026, 11, 8))
    assert res and res["status"] == "breakout", res
    assert res["pattern"] == "U", res["pattern"]
    assert abs(res["base"]["rim_price"] - 100) < 1e-6
    assert res["base"]["depth_pct"] == 30.0
    print("U breakout: ok", res["base"]["weeks"], "weeks")


def test_w_breakout():
    w_shape = ramp(100, 70, 8) + ramp(70, 90, 6) + ramp(90, 71, 6) + ramp(71, 100, 8)   # two troughs ~70/71, middle peak 90
    weekly = ramp(50, 100, 20) + w_shape + [104, 108, 111]
    res = analyse("TESTW", {"name": "Test W", "token": "2", "listing_date": "2018-01-05"}, make_daily(weekly), ScreenParams(), date(2026, 11, 8))
    assert res and res["status"] == "breakout", res
    assert res["pattern"] == "W", res["pattern"]
    print("W breakout: ok", res["base"])


def test_u_then_w_requires_new_high():
    # U breaks 100 -> 110, then W forms under 110; stock now at 105 (above 100 but below 110): must NOT qualify
    weekly = ramp(50, 100, 20) + cup() + ramp(100, 110, 4) + ramp(110, 85, 8) + ramp(85, 100, 5) + ramp(100, 86, 5) + ramp(86, 105, 6)
    res = analyse("TESTUW", {"name": "Test UW", "token": "3", "listing_date": "2018-01-05"}, make_daily(weekly), ScreenParams(), date(2026, 11, 8))
    assert res is None or res["status"] != "breakout", res and res["status"]
    # ...then it crosses 110: now it qualifies and the latest base is the W
    weekly2 = weekly + [112, 115]
    res2 = analyse("TESTUW", {"name": "Test UW", "token": "3", "listing_date": "2018-01-05"}, make_daily(weekly2), ScreenParams(), date(2026, 11, 8))
    assert res2 and res2["status"] == "breakout" and res2["pattern"] == "W", res2
    assert res2["n_bases"] == 2 and res2["bases"][0]["pattern"] == "U"
    print("U then W: ok ->", [b["pattern"] for b in res2["bases"]])


def test_fallen_back_excluded():
    weekly = ramp(50, 100, 20) + cup() + [105, 110] + ramp(110, 80, 10)  # broke out then collapsed 27%
    res = analyse("TESTF", {"name": "Fallen", "token": "4", "listing_date": "2018-01-05"}, make_daily(weekly), ScreenParams(), date(2026, 11, 8))
    assert res is None or res["status"] != "breakout", res
    print("fallen back excluded: ok")


def test_shallow_dip_not_a_base():
    weekly = ramp(50, 100, 20) + ramp(100, 92, 4) + ramp(92, 101, 4) + [103]  # only 8% dip
    daily = make_daily(weekly)
    bases, _ = find_bases(to_weekly(daily), ScreenParams())
    assert bases == [], bases
    print("shallow dip ignored: ok")


def test_approaching():
    weekly = ramp(50, 100, 20) + ramp(100, 70, 10) + ramp(70, 97, 10) + [97]  # 3% below rim, not yet broken out
    res = analyse("TESTA", {"name": "Approach", "token": "5", "listing_date": "2018-01-05"}, make_daily(weekly), ScreenParams(), date(2026, 11, 8))
    assert res and res["status"] == "approaching", res
    print("approaching: ok", res["pct_below_rim"], "% below")


if __name__ == "__main__":
    for fn in [test_u_breakout, test_w_breakout, test_u_then_w_requires_new_high, test_fallen_back_excluded, test_shallow_dip_not_a_base, test_approaching]:
        fn()
    print("all screener checks passed")
