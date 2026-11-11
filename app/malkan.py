"""Malkan's view, in action: scanners for the three systems from the class notes.

System 1  Trend following (weekly): buy when weekly RSI(14) closes above 60; stop = previous
          week's 20-week SMA; exit when price closes below the 20 SMA. Pyramid when the SMA
          rises to your entry. Long only.
System 2  Cup strategy: scan the monthly chart (5-year lookback) for a cup whose neckline is
          the resolution point (RP). Entry = RP + 4 %, SL = RP - 2 %. Level 1 targets +10/20/30 %
          (sell a third each, trail SL to the previous level); level 2 keeps the last third
          with a trailing SL; level 3 targets +20/40/60 %. Max target = cup depth above RP.
System 3  Positional: monthly RSI(14) near 40 (never below 40). Entry above the high of the
          first green weekly candle, SL = weekly swing low, target 1 = 1:3, target 2 = monthly
          swing high.

Every rule that is a judgement call is a parameter in MalkanParams.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import config as C
from .history import load_history
from .indicators import rsi, sma, to_weekly, to_monthly, period_complete
from .nifty500 import load_nifty500

MALKAN_JSON = C.DATA / "malkan.json"


@dataclass
class MalkanParams:
    rsi_period: int = 14
    ma_period: int = 20
    s1_rsi_buy: float = 60.0
    s1_fresh_weeks: int = 4          # "fresh buy" = RSI crossed 60 within this many completed weeks
    s2_lookback_years: int = 5
    s2_min_depth_pct: float = 15.0
    s2_min_months: int = 3
    s2_entry_pct: float = 4.0
    s2_sl_pct: float = 2.0
    s2_trigger_weeks: int = 8        # 4-8 weeks is the expected move window
    s2_approach_pct: float = 10.0    # list forming cups when price is within this % below RP
    s3_rsi_low: float = 40.0
    s3_rsi_high: float = 50.0
    s3_swing_low_weeks: int = 10
    s3_swing_high_months: int = 24
    s3_trigger_weeks: int = 4
    capital: float = 1_000_000.0
    risk_pct: float = 1.0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MalkanParams":
        p = cls()
        for k, v in (d or {}).items():
            if hasattr(p, k) and v is not None:
                setattr(p, k, type(getattr(p, k))(v))
        return p


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


# --------------------------------------------------------------------------- System 1
def system1(w: pd.DataFrame, today: date, p: MalkanParams) -> dict | None:
    if len(w) < p.ma_period + p.rsi_period + 2:
        return None
    w = w.copy()
    w["rsi"] = rsi(w["close"], p.rsi_period)
    w["sma"] = sma(w["close"], p.ma_period)
    complete = period_complete(w["date"].iloc[-1], today, "W")
    cur = w.iloc[-1]                          # current week (may be unfinished)
    done = w if complete else w.iloc[:-1]     # completed weeks only
    if len(done) < 3 or pd.isna(done["sma"].iloc[-1]) or pd.isna(done["rsi"].iloc[-1]):
        return None
    last, prev = done.iloc[-1], done.iloc[-2]

    # most recent completed-week RSI cross above the buy level
    above = (done["rsi"] > p.s1_rsi_buy).to_numpy()
    cross_idx = None
    for k in range(len(done) - 1, 0, -1):
        if above[k] and not above[k - 1]:
            cross_idx = k
            break
    weeks_since_cross = (len(done) - 1 - cross_idx) if cross_idx is not None else None
    # has it stayed above 60 (no dip below) since that cross?
    held = bool(cross_idx is not None and above[cross_idx:].all())

    in_trend = bool(last["rsi"] > p.s1_rsi_buy and last["close"] >= last["sma"])
    exit_now = bool(last["close"] < last["sma"] and prev["close"] >= prev["sma"] and prev["rsi"] > p.s1_rsi_buy)
    if in_trend and held and weeks_since_cross is not None and weeks_since_cross < p.s1_fresh_weeks:
        state = "buy"
    elif in_trend:
        state = "trend"
    elif exit_now:
        state = "exit"
    else:
        return None

    stop = float(prev["sma"]) if state != "exit" else float(last["sma"])   # SL = previous week's 20 SMA
    entry = float(cur["close"])
    return {
        "state": state,
        "rsi_w": _r(last["rsi"], 1),
        "rsi_w_prev": _r(prev["rsi"], 1),
        "rsi_w_now": _r(cur["rsi"], 1),                    # includes the unfinished week
        "sma20_w": _r(last["sma"]),
        "sma20_w_prev": _r(prev["sma"]),
        "week_complete": complete,
        "cross_date": done["date"].iloc[cross_idx].strftime("%Y-%m-%d") if cross_idx is not None else None,
        "weeks_since_cross": weeks_since_cross,
        "weeks_above_sma": int(((done["close"] >= done["sma"])[::-1].cumprod()).sum()),
        "plan": {"entry": _r(entry), "sl": _r(stop), "rps": _r(entry - stop),
                 "valid": bool(entry > stop), "note": "SL = previous week's 20-week SMA; exit on a weekly close below the 20 SMA"},
    }


# --------------------------------------------------------------------------- System 2
def _monthly_bases(m: pd.DataFrame, p: MalkanParams):
    """Completed cups on monthly closes: (rim_idx, breakout_idx, trough_idx, depth)."""
    closes = m["close"].to_numpy()
    bases, rim = [], 0
    for k in range(1, len(closes)):
        if closes[k] > closes[rim]:
            if k - rim >= 2:
                seg = closes[rim + 1 : k]
                t = int(np.argmin(seg))
                depth = (closes[rim] - seg[t]) / closes[rim] * 100
                if depth >= p.s2_min_depth_pct and (k - rim) >= p.s2_min_months:
                    bases.append((rim, k, rim + 1 + t, float(depth)))
            rim = k
    return bases, rim


def system2(daily: pd.DataFrame, m: pd.DataFrame, today: date, p: MalkanParams) -> dict | None:
    cutoff = pd.Timestamp(today) - pd.DateOffset(years=p.s2_lookback_years)
    m = m[m["date"] >= cutoff].reset_index(drop=True)
    if len(m) < p.s2_min_months + 2:
        return None
    closes = m["close"].to_numpy()
    bases, rim_idx = _monthly_bases(m, p)
    last_close = float(daily["close"].iloc[-1])
    last_date = daily["date"].iloc[-1]

    # ---- open cup: from the latest rim (highest monthly close so far) to now, not yet resolved
    R_open = float(closes[rim_idx])
    seg = closes[rim_idx + 1 :]
    cand = None
    if len(seg) >= 1:
        t = int(np.argmin(seg))
        depth = (R_open - seg[t]) / R_open * 100
        months = len(m) - 1 - rim_idx
        if depth >= p.s2_min_depth_pct and months >= p.s2_min_months:
            cand = {"rp": R_open, "rp_date": m["date"].iloc[rim_idx], "trough": float(seg[t]),
                    "trough_date": m["date"].iloc[rim_idx + 1 + t], "depth_pct": float(depth), "months": months, "completed": False}
    # ---- else the most recent completed cup (monthly close above the rim)
    if cand is None and bases:
        ri, bi, ti, depth = bases[-1]
        cand = {"rp": float(closes[ri]), "rp_date": m["date"].iloc[ri], "trough": float(closes[ti]),
                "trough_date": m["date"].iloc[ti], "depth_pct": depth, "months": bi - ri, "completed": True}
    if cand is None:
        return None

    R = cand["rp"]
    entry = R * (1 + p.s2_entry_pct / 100)
    sl = R * (1 - p.s2_sl_pct / 100)
    after = daily[daily["date"] > cand["trough_date"]]
    trig = after[after["close"] >= entry]
    trigger_date = trig["date"].iloc[0] if len(trig) else None
    days_since = (pd.Timestamp(today) - trigger_date).days if trigger_date is not None else None

    if trigger_date is not None:
        if last_close < sl:
            return None                                       # stopped out, nothing to do
        status = "triggered" if days_since <= p.s2_trigger_weeks * 7 else "extended"
    elif last_close >= R:
        status = "breakout"                                   # above RP, waiting for the +4 % filter
    elif last_close >= R * (1 - p.s2_approach_pct / 100):
        status = "approaching"
    else:
        return None

    depth_px = R - cand["trough"]
    gain = (last_close - entry) / entry * 100 if trigger_date is not None else None
    return {
        "status": status,
        "rp": _r(R), "rp_date": cand["rp_date"].strftime("%Y-%m-%d"),
        "trough": _r(cand["trough"]), "trough_date": cand["trough_date"].strftime("%Y-%m-%d"),
        "depth_pct": _r(cand["depth_pct"], 1), "months": int(cand["months"]), "cup_completed": cand["completed"],
        "trigger_date": trigger_date.strftime("%Y-%m-%d") if trigger_date is not None else None,
        "days_since_trigger": days_since,
        "pct_vs_rp": _r((last_close - R) / R * 100, 1),
        "gain_pct": _r(gain, 1),
        "plan": {
            "entry": _r(entry), "sl": _r(sl), "rps": _r(entry - sl), "valid": True,
            "level1": [_r(entry * 1.10), _r(entry * 1.20), _r(entry * 1.30)],
            "level3": [_r(entry * 1.20), _r(entry * 1.40), _r(entry * 1.60)],
            "max_target": _r(R + depth_px),
            "note": "Entry = RP + 4 %, SL = RP - 2 %. Sell a third at each level-1 target and trail the SL to the previous level.",
        },
    }


# --------------------------------------------------------------------------- System 3
def system3(w: pd.DataFrame, m: pd.DataFrame, today: date, p: MalkanParams) -> dict | None:
    if len(m) < p.rsi_period + 3 or len(w) < p.s3_swing_low_weeks + 3:
        return None
    m = m.copy()
    m["rsi"] = rsi(m["close"], p.rsi_period)
    m_complete = period_complete(m["date"].iloc[-1], today, "M")
    m_done = m if m_complete else m.iloc[:-1]
    rsi_m = float(m_done["rsi"].iloc[-1])
    rsi_m_now = float(m["rsi"].iloc[-1])
    if pd.isna(rsi_m) or not (p.s3_rsi_low <= rsi_m <= p.s3_rsi_high):
        return None

    w_complete = period_complete(w["date"].iloc[-1], today, "W")
    done = w if w_complete else w.iloc[:-1]
    cur = w.iloc[-1]
    # signal candle: the most recent completed green week that follows a red week (first green after a dip)
    green = (done["close"] > done["open"]).to_numpy()
    sig = None
    for k in range(len(done) - 1, 0, -1):
        if green[k] and not green[k - 1]:
            sig = k
            break
    if sig is None or (len(done) - 1 - sig) > 12:
        return None
    signal = done.iloc[sig]
    entry = float(signal["high"])
    sl = float(done["low"].iloc[max(0, sig - p.s3_swing_low_weeks + 1) : sig + 1].min())
    if entry <= sl:
        return None
    target1 = entry + 3 * (entry - sl)
    target2 = float(m["high"].tail(p.s3_swing_high_months).max())
    after = w.iloc[sig + 1 :]
    crossed = after[after["high"] >= entry]
    trigger_date = crossed["date"].iloc[0] if len(crossed) else None
    last_close = float(cur["close"])
    if last_close < sl:
        return None
    if trigger_date is not None:
        weeks = int((pd.Timestamp(today) - trigger_date).days // 7)
        status = "triggered" if weeks <= p.s3_trigger_weeks else "extended"
    else:
        status = "setup"
    rr2 = (target2 - entry) / (entry - sl) if target2 > entry else 0.0
    return {
        "status": status,
        "rsi_m": _r(rsi_m, 1), "rsi_m_now": _r(rsi_m_now, 1), "month_complete": m_complete,
        "signal_date": signal["date"].strftime("%Y-%m-%d"),
        "signal_high": _r(entry), "signal_low": _r(signal["low"]),
        "trigger_date": trigger_date.strftime("%Y-%m-%d") if trigger_date is not None else None,
        "pct_to_entry": _r((entry - last_close) / last_close * 100, 1),
        "plan": {"entry": _r(entry), "sl": _r(sl), "rps": _r(entry - sl), "valid": True,
                 "target1": _r(target1), "target2": _r(target2), "rr_target2": _r(rr2, 1),
                 "note": "Buy above the signal candle's high; SL = weekly swing low; target 1 = 1:3, target 2 = monthly swing high"},
    }


# --------------------------------------------------------------------------- run
def _sector_strength(returns: dict[str, list[tuple[float, float]]]) -> list[dict]:
    rows = []
    for ind, vals in returns.items():
        r13 = float(np.median([v[0] for v in vals]))
        r4 = float(np.median([v[1] for v in vals]))
        rows.append({"industry": ind, "stocks": len(vals), "ret_13w": round(r13, 1), "ret_4w": round(r4, 1)})
    rows.sort(key=lambda r: r["ret_13w"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def run_malkan(p: MalkanParams | None = None, write: bool = True) -> dict:
    p = p or MalkanParams()
    uni = load_nifty500()
    today = date.today()
    s1, s2, s3 = [], [], []
    sector_returns: dict[str, list] = {}
    stock_ret: dict[str, tuple] = {}
    scanned = 0
    cache = {}
    for row in uni.itertuples(index=False):
        daily = load_history(row.symbol)
        if daily is None or len(daily) < 120:
            continue
        scanned += 1
        w, m = to_weekly(daily), to_monthly(daily)
        last_close = float(daily["close"].iloc[-1])
        c = daily["close"]
        r13 = (last_close / float(c.iloc[-66]) - 1) * 100 if len(c) > 66 else 0.0
        r4 = (last_close / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else 0.0
        sector_returns.setdefault(row.industry, []).append((r13, r4))
        stock_ret[row.symbol] = (r13, r4)
        yr = daily[daily["date"] >= daily["date"].iloc[-1] - pd.Timedelta(days=365)]
        common = {
            "symbol": row.symbol, "name": row.name, "isin": row.isin, "token": row.isin, "industry": row.industry,
            "last_close": round(last_close, 2),
            "prev_close": round(float(daily["close"].iloc[-2]), 2),
            "last_date": daily["date"].iloc[-1].strftime("%Y-%m-%d"),
            "hi_52w": round(float(yr["high"].max()), 2), "lo_52w": round(float(yr["low"].min()), 2),
            "ath_close": round(float(daily["close"].max()), 2),
            "pct_from_ath": round((last_close / float(daily["close"].max()) - 1) * 100, 1),
            "ret_13w": round(r13, 1), "ret_4w": round(r4, 1),
            "avg_vol_20d": int(daily["volume"].tail(20).mean()),
        }
        cache[row.symbol] = common
        try:
            r1 = system1(w, today, p)
            if r1:
                s1.append({**common, **r1})
            r2 = system2(daily, m, today, p)
            if r2:
                s2.append({**common, **r2})
            r3 = system3(w, m, today, p)
            if r3:
                s3.append({**common, **r3})
        except Exception as e:  # one bad series must not kill the scan
            print(f"{row.symbol}: {e}")

    sectors = _sector_strength(sector_returns)
    rank = {s["industry"]: s["rank"] for s in sectors}
    for lst in (s1, s2, s3):
        for r in lst:
            r["sector_rank"] = rank.get(r["industry"])
    order1 = {"buy": 0, "trend": 1, "exit": 2}
    s1.sort(key=lambda r: (order1[r["state"]], r["weeks_since_cross"] if r["weeks_since_cross"] is not None else 999, -(r["rsi_w"] or 0)))
    order2 = {"triggered": 0, "breakout": 1, "approaching": 2, "extended": 3}
    s2.sort(key=lambda r: (order2[r["status"]], -(r["pct_vs_rp"] or 0)))
    order3 = {"triggered": 0, "setup": 1, "extended": 2}
    s3.sort(key=lambda r: (order3[r["status"]], r["pct_to_entry"] if r["pct_to_entry"] is not None else 0))
    out = {
        "generated_at": datetime.now(C.IST).isoformat(timespec="seconds"),
        "params": p.to_dict(),
        "universe_size": int(len(uni)), "scanned": scanned,
        "sectors": sectors,
        "system1": s1, "system2": s2, "system3": s3,
    }
    if write:
        MALKAN_JSON.write_text(json.dumps(out, indent=1))
    return out


def load_malkan() -> dict | None:
    if not MALKAN_JSON.exists():
        return None
    return json.loads(MALKAN_JSON.read_text())
