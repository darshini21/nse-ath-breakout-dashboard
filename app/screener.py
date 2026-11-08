"""Cup (U) / double-bottom (W) all-time-high breakout screener.

Definitions (all on WEEKLY closes, which filters out intraday noise):

* rim        - the stock's highest weekly close so far (its all-time-high close).
* base       - the stretch from a rim until the first weekly close ABOVE that rim
               (the breakout week). It only counts if it is deep enough
               (min_depth_pct) and long enough (min_weeks).
* U vs W     - one trough = U (cup). Two troughs separated by a middle peak that
               recovers a good part of the depth, with the second trough close to
               the first = W (double bottom).
* qualifies  - the LATEST base of the stock is a valid U/W, the stock broke out of
               it, and it is still trading above that rim and has NOT since fallen
               into a new base of its own (drawdown from post-breakout high <
               min_depth_pct). When the stock later makes a new rim, dips, and
               breaks out again, that newer base becomes "the latest base", which is
               exactly the "U then W must cross the new high" rule.

Everything that is a judgement call is a parameter in config.ScreenParams.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, date

import numpy as np
import pandas as pd

from . import config as C
from .config import ScreenParams
from .history import load_history
from .universe import load_universe


# ----------------------------------------------------------------------------- helpers
def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    w = (
        df.set_index("date")
        .resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
        .reset_index()
    )
    return w


@dataclass
class Base:
    rim_idx: int
    breakout_idx: int
    rim_date: str
    rim_price: float
    breakout_date: str
    breakout_close: float
    trough_date: str
    trough_price: float
    depth_pct: float
    weeks: int
    pattern: str                 # "U" or "W"
    second_trough_date: str | None
    second_trough_price: float | None
    mid_peak_date: str | None
    mid_peak_price: float | None
    breakout_vol_ratio: float | None   # breakout-week volume / avg weekly volume inside the base
    time_in_lower_half_pct: float      # % of base weeks spent in the lower half of the depth (U-ness; a V scores low)


def _classify(w: pd.DataFrame, i: int, j: int, p: ScreenParams) -> Base | None:
    closes = w["close"].to_numpy()
    rim = closes[i]
    seg = closes[i + 1 : j]
    if len(seg) == 0:
        return None
    low_rel = int(np.argmin(seg))
    low = float(seg[low_rel])
    depth = (rim - low) / rim
    weeks = j - i
    if depth * 100 < p.min_depth_pct or weeks < p.min_weeks:
        return None

    span = rim - low
    pattern = "U"
    t2_date = t2_price = mp_date = mp_price = None
    # Look for a second trough on either side of the deepest one: a middle peak that
    # recovers enough of the depth, followed (or preceded) by a trough close to the first.
    # Every candidate peak is tried; the most prominent valid one wins.
    best = None  # (prominence, peak_abs_idx, trough2_abs_idx, peak_val, trough2_val)
    seg_abs0 = i + 1
    right = seg[low_rel + 1 :]
    if len(right) >= 3:
        suffix_min = np.minimum.accumulate(right[::-1])[::-1]          # suffix_min[m] = min(right[m:])
        for m in range(len(right) - 1):
            pk_val, t2_val = float(right[m]), float(suffix_min[m + 1])
            if (pk_val - low) >= p.w_peak_recovery * span and (t2_val - low) <= p.w_trough_tolerance * span and t2_val < pk_val:
                prom = pk_val - t2_val
                if best is None or prom > best[0]:
                    t2_rel = m + 1 + int(np.argmin(right[m + 1 :]))
                    best = (prom, seg_abs0 + low_rel + 1 + m, seg_abs0 + low_rel + 1 + t2_rel, pk_val, t2_val)
    left = seg[:low_rel]
    if len(left) >= 3:
        prefix_min = np.minimum.accumulate(left)                        # prefix_min[m] = min(left[:m+1])
        for m in range(1, len(left)):
            pk_val, t2_val = float(left[m]), float(prefix_min[m - 1])
            if (pk_val - low) >= p.w_peak_recovery * span and (t2_val - low) <= p.w_trough_tolerance * span and t2_val < pk_val:
                prom = pk_val - t2_val
                if best is None or prom > best[0]:
                    t2_rel = int(np.argmin(left[:m]))
                    best = (prom, seg_abs0 + m, seg_abs0 + t2_rel, pk_val, t2_val)
    if best:
        pattern = "W"
        _, pk_abs, t2_abs, peak_val, t2_val = best
        t2_date = w["date"].iloc[t2_abs].strftime("%Y-%m-%d")
        t2_price = round(t2_val, 2)
        mp_date = w["date"].iloc[pk_abs].strftime("%Y-%m-%d")
        mp_price = round(peak_val, 2)

    vols = w["volume"].to_numpy(dtype=float)
    base_vol = vols[i + 1 : j]
    avg = float(base_vol.mean()) if len(base_vol) and base_vol.mean() > 0 else None
    vol_ratio = round(float(vols[j]) / avg, 2) if avg else None
    lower_half = float(np.mean(seg <= low + 0.5 * span)) * 100

    return Base(
        rim_idx=i,
        breakout_idx=j,
        rim_date=w["date"].iloc[i].strftime("%Y-%m-%d"),
        rim_price=round(float(rim), 2),
        breakout_date=w["date"].iloc[j].strftime("%Y-%m-%d"),
        breakout_close=round(float(closes[j]), 2),
        trough_date=w["date"].iloc[i + 1 + low_rel].strftime("%Y-%m-%d"),
        trough_price=round(low, 2),
        depth_pct=round(float(depth) * 100, 1),
        weeks=weeks,
        pattern=pattern,
        second_trough_date=t2_date,
        second_trough_price=t2_price,
        mid_peak_date=mp_date,
        mid_peak_price=mp_price,
        breakout_vol_ratio=vol_ratio,
        time_in_lower_half_pct=round(lower_half, 0),
    )


def find_bases(w: pd.DataFrame, p: ScreenParams) -> tuple[list[Base], int]:
    """Return all qualifying completed bases, plus the index of the current rim (ATH close)."""
    closes = w["close"].to_numpy()
    bases: list[Base] = []
    rim = 0
    for k in range(1, len(closes)):
        if closes[k] > closes[rim]:
            if k - rim >= 2:
                b = _classify(w, rim, k, p)
                if b:
                    bases.append(b)
            rim = k
    return bases, rim


# ----------------------------------------------------------------------------- per stock
def analyse(symbol: str, meta: dict, daily: pd.DataFrame, p: ScreenParams, today: date) -> dict | None:
    if daily is None or len(daily) < 30:
        return None
    w = to_weekly(daily)
    if len(w) < p.min_weeks + 2:
        return None
    bases, rim_idx = find_bases(w, p)

    last_close = float(daily["close"].iloc[-1])
    last_date = daily["date"].iloc[-1]
    ath_close = float(w["close"].iloc[rim_idx])
    ath_date = w["date"].iloc[rim_idx]
    since_ath = daily[daily["date"] > ath_date]
    dd_since_ath = ((ath_close - float(since_ath["close"].min())) / ath_close * 100) if len(since_ath) else 0.0
    pct_from_ath = (last_close - ath_close) / ath_close * 100

    yr = daily[daily["date"] >= last_date - pd.Timedelta(days=365)]
    hi52, lo52 = float(yr["high"].max()), float(yr["low"].min())
    listing = pd.Timestamp(meta["listing_date"])
    common = {
        "symbol": symbol,
        "name": meta["name"],
        "token": str(meta["token"]),
        "isin": str(meta.get("isin", "")),
        "listing_date": listing.strftime("%Y-%m-%d"),
        "age_years": round((pd.Timestamp(today) - listing).days / 365.25, 1),
        "last_close": round(last_close, 2),
        "prev_close": round(float(daily["close"].iloc[-2]), 2) if len(daily) > 1 else None,
        "last_date": last_date.strftime("%Y-%m-%d"),
        "ath_close": round(ath_close, 2),
        "ath_date": ath_date.strftime("%Y-%m-%d"),
        "pct_from_ath": round(pct_from_ath, 1),
        "hi_52w": round(hi52, 2),
        "lo_52w": round(lo52, 2),
        "avg_vol_20d": int(daily["volume"].tail(20).mean()),
        "n_bases": len(bases),
        "bases": [asdict(b) for b in bases],
    }

    # ---- Breakouts: latest base is valid and the stock is still above it, not in a new base
    if bases:
        b = bases[-1]
        age_days = (pd.Timestamp(today) - pd.Timestamp(b.breakout_date)).days
        above_rim = last_close > b.rim_price
        fresh_enough = p.max_breakout_age_days <= 0 or age_days <= p.max_breakout_age_days
        if above_rim and dd_since_ath < p.min_depth_pct and fresh_enough:
            return {
                **common,
                "status": "breakout",
                "pattern": b.pattern,
                "base": asdict(b),
                "days_since_breakout": age_days,
                "pct_above_rim": round((last_close - b.rim_price) / b.rim_price * 100, 1),
                "drawdown_since_ath_pct": round(dd_since_ath, 1),
            }

    # ---- Approaching: currently inside a base that is deep/long enough and close under the rim
    rim_price = ath_close
    weeks_in_base = len(w) - 1 - rim_idx
    seg = w["close"].to_numpy()[rim_idx + 1 :]
    if len(seg) >= 2 and weeks_in_base >= p.min_weeks:
        depth = float((rim_price - seg.min()) / rim_price * 100)
        gap = float((rim_price - last_close) / rim_price * 100)
        if depth >= p.min_depth_pct and 0 <= gap <= p.near_breakout_pct:
            return {
                **common,
                "status": "approaching",
                "pattern": None,
                "base": {
                    "rim_date": ath_date.strftime("%Y-%m-%d"),
                    "rim_price": round(rim_price, 2),
                    "trough_price": round(float(seg.min()), 2),
                    "trough_date": w["date"].iloc[rim_idx + 1 + int(np.argmin(seg))].strftime("%Y-%m-%d"),
                    "depth_pct": round(depth, 1),
                    "weeks": weeks_in_base,
                },
                "pct_below_rim": round(gap, 1),
            }
    return None


# ----------------------------------------------------------------------------- run
def run_screen(p: ScreenParams | None = None, write: bool = True) -> dict:
    p = p or ScreenParams()
    uni = load_universe()
    today = date.today()
    breakouts, approaching = [], []
    scanned = 0
    for row in uni.itertuples(index=False):
        daily = load_history(row.symbol)
        if daily is None or daily.empty:
            continue
        scanned += 1
        res = analyse(row.symbol, row._asdict(), daily, p, today)
        if not res:
            continue
        (breakouts if res["status"] == "breakout" else approaching).append(res)

    breakouts.sort(key=lambda r: r["days_since_breakout"])
    approaching.sort(key=lambda r: r["pct_below_rim"])
    out = {
        "generated_at": datetime.now(C.IST).isoformat(timespec="seconds"),
        "params": p.to_dict(),
        "universe_size": int(len(uni)),
        "scanned": scanned,
        "breakouts": breakouts,
        "approaching": approaching,
    }
    if write:
        C.RESULTS_JSON.write_text(json.dumps(out, indent=1))
    return out


def load_results() -> dict | None:
    if not C.RESULTS_JSON.exists():
        return None
    return json.loads(C.RESULTS_JSON.read_text())
