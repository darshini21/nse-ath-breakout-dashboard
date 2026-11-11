"""Build a fully static copy of the dashboard in ./site for GitHub Pages.

The static site contains the screener results, weekly/daily history for the result
stocks (for the charts), and the same web app switched to "static" mode, where each
visitor's browser polls Upstox's public candle API directly for live prices.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime

import pandas as pd

from . import config as C
from .history import load_history
from .screener import load_results, to_weekly

SITE = C.ROOT / "site"
DAILY_YEARS = 3  # daily candles shipped per stock (weekly history is complete)


def _candles(df: pd.DataFrame) -> list[list]:
    return [[d.strftime("%Y-%m-%d"), float(o), float(h), float(l), float(c), int(v)]
            for d, o, h, l, c, v in zip(df["date"], df["open"], df["high"], df["low"], df["close"], df["volume"])]


def build():
    results = load_results()
    if results is None:
        raise SystemExit("No data/results.json - run `screen` first.")
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "data" / "history").mkdir(parents=True)

    # The web app itself
    for f in ("index.html", "app.js", "styles.css"):
        shutil.copy(C.WEB / f, SITE / f)
    (SITE / "config.js").write_text(
        "window.APP_CONFIG = " + json.dumps({
            "static": True,
            "built_at": datetime.now(C.IST).isoformat(timespec="seconds"),
            "poll_seconds": C.LIVE_POLL_SECONDS,
            "idle_poll_seconds": C.IDLE_POLL_SECONDS,
            "indices": C.INDEX_TOKENS,
            "index_keys": C.UPSTOX_INDEX_KEYS,
        }) + ";\n"
    )
    (SITE / ".nojekyll").write_text("")

    # Results + per-stock history
    (SITE / "data" / "results.json").write_text(json.dumps(results, separators=(",", ":")))
    rows = results.get("breakouts", []) + results.get("approaching", [])
    cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(years=DAILY_YEARS)
    for r in rows:
        df = load_history(r["symbol"])
        if df is None or df.empty:
            continue
        payload = {"symbol": r["symbol"], "W": _candles(to_weekly(df)), "D": _candles(df[df["date"] >= cutoff])}
        (SITE / "data" / "history" / f"{r['symbol']}.json").write_text(json.dumps(payload, separators=(",", ":")))

    # Malkan systems page + its data
    from .malkan import load_malkan, MalkanParams
    from .indicators import rsi, sma, to_monthly, to_weekly as tw
    mk = load_malkan()
    if mk:
        for f in ("malkan.html", "malkan.js"):
            shutil.copy(C.WEB / f, SITE / f)
        (SITE / "data" / "malkan.json").write_text(json.dumps(mk, separators=(",", ":")))
        (SITE / "data" / "mhist").mkdir(exist_ok=True)
        p = MalkanParams.from_dict(mk.get("params", {}))
        syms = {r["symbol"] for k in ("system1", "system2", "system3") for r in mk.get(k, [])}

        def pack(bars, ind):
            b = bars.copy()
            if ind:
                b["rsi"] = rsi(b["close"], p.rsi_period)
                b["sma"] = sma(b["close"], p.ma_period)
            out = []
            for row in b.itertuples(index=False):
                rec = [row.date.strftime("%Y-%m-%d"), float(row.open), float(row.high), float(row.low), float(row.close), int(row.volume)]
                if ind:
                    rec += [None if pd.isna(row.rsi) else round(float(row.rsi), 1), None if pd.isna(row.sma) else round(float(row.sma), 2)]
                out.append(rec)
            return out

        for sym in sorted(syms):
            df = load_history(sym)
            if df is None or df.empty:
                continue
            cut = df["date"].iloc[-1] - pd.Timedelta(days=366)
            payload = {"symbol": sym, "W": pack(tw(df), True), "M": pack(to_monthly(df), True), "D": pack(df[df["date"] >= cut], False)}
            (SITE / "data" / "mhist" / f"{sym}.json").write_text(json.dumps(payload, separators=(",", ":")))
        print(f"Malkan page: {len(syms)} stocks with charts")

    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file()) / 1e6
    print(f"Static site built in {SITE} ({len(rows)} stocks, {size:.1f} MB)")


if __name__ == "__main__":
    build()
