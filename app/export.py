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

    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file()) / 1e6
    print(f"Static site built in {SITE} ({len(rows)} stocks, {size:.1f} MB)")


if __name__ == "__main__":
    build()
