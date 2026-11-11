"""FastAPI backend: serves the dashboard, screener results, history, and a live quote stream.

Price feed selection (see live.py):
  1. Angel One WebSocket (tick-level)      - when .env has SmartAPI credentials
  2. Upstox 1-minute candle polling        - default, no key needed (~1 minute behind)
  3. Random-walk simulator                 - only when SIMULATE=1
"""
from __future__ import annotations

import asyncio
import threading
import time

import pandas as pd
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from .config import ScreenParams
from .history import load_history
from .live import QuoteBook, AngelFeed, SimulatedFeed, UpstoxPollFeed, market_state
from .screener import load_results, run_screen, to_weekly
from .malkan import load_malkan, run_malkan, MalkanParams
from .indicators import rsi, sma, to_weekly as _tw, to_monthly as _tm
from . import journal as J

app = FastAPI(title="NSE ATH Breakout Dashboard")

STATE = {
    "results": None,
    "malkan": None,
    "book": None,
    "feed": None,
    "client": None,
    "feed_tokens": [],
    "rescan_running": False,
    "started_at": time.time(),
}


def _rows(results: dict) -> list[dict]:
    """Every stock that needs a live quote: breakout screen, Malkan signals and open journal trades."""
    rows = results.get("breakouts", []) + results.get("approaching", [])
    mk = STATE.get("malkan") or {}
    rows += mk.get("system1", []) + mk.get("system2", []) + mk.get("system3", [])
    for t in J.list_trades():
        if t.get("status", "open") == "open" and t.get("isin"):
            rows.append({"token": t["isin"], "isin": t["isin"], "last_close": t.get("entry"), "prev_close": None, "last_date": None})
    seen, out = set(), []
    for r in rows:
        if r["token"] not in seen:
            seen.add(r["token"]); out.append(r)
    return out


def _start_upstox(results: dict, book: QuoteBook):
    keymap = dict(C.UPSTOX_INDEX_KEYS)
    closes = {}
    for r in _rows(results):
        if r.get("isin"):
            keymap[r["token"]] = f"NSE_EQ|{r['isin']}"
            closes[r["token"]] = (r.get("last_date"), r.get("last_close"), r.get("prev_close"))
    mk = STATE.get("malkan") or {}
    priority = (list(C.UPSTOX_INDEX_KEYS)
                + [t["isin"] for t in J.list_trades() if t.get("status", "open") == "open" and t.get("isin")]
                + [r["token"] for r in mk.get("system1", []) if r.get("state") in ("buy", "exit")]
                + [r["token"] for r in mk.get("system2", []) if r.get("status") in ("triggered", "breakout")]
                + [r["token"] for r in mk.get("system3", []) if r.get("status") == "triggered"]
                + [r["token"] for r in results.get("breakouts", [])])
    feed = UpstoxPollFeed(keymap, closes, book, priority)
    feed.start()
    STATE["feed"] = feed
    STATE["feed_tokens"] = list(keymap)


def _start_feed(results: dict):
    """(Re)start the price feed for the current result set."""
    if STATE["feed"]:
        STATE["feed"].stop()
    rows = _rows(results)
    book = QuoteBook({r["token"]: r["last_close"] for r in rows})
    STATE["book"] = book

    if C.SIMULATE:
        feed = SimulatedFeed([r["token"] for r in rows], book)
        feed.start()
        STATE["feed"] = feed
        STATE["feed_tokens"] = feed.tokens
        return

    if C.HAS_CREDS:
        try:
            if STATE["client"] is None:
                from .angel import AngelClient
                STATE["client"] = AngelClient()
            tokens = [r["token"] for r in rows if r["token"].isdigit()] + list(C.INDEX_TOKENS)
            snap = STATE["client"].ltp_snapshot([t for t in tokens if t not in C.INDEX_TOKENS])
            for tok, q in snap.items():
                book.update(tok, float(q["ltp"]), q.get("open"), q.get("high"), q.get("low"), q.get("close"),
                            q.get("tradeVolume"), raw=False)
                if q.get("close"):
                    book.last_close[tok] = float(q["close"])
            feed = AngelFeed(STATE["client"], tokens, book)
            feed.start()
            STATE["feed"] = feed
            STATE["feed_tokens"] = tokens
            return
        except Exception as e:
            book.error = f"Angel One connection failed ({e}); using Upstox candles instead."

    _start_upstox(results, book)


@app.on_event("startup")
def _startup():
    results = load_results()
    if results is None:
        results = {"generated_at": None, "params": ScreenParams().to_dict(), "universe_size": 0, "scanned": 0,
                   "breakouts": [], "approaching": []}
    STATE["results"] = results
    STATE["malkan"] = load_malkan() or {"generated_at": None, "params": MalkanParams().to_dict(), "universe_size": 0,
                                        "scanned": 0, "sectors": [], "system1": [], "system2": [], "system3": []}
    _start_feed(results)


def _pack_bars(bars, with_ind: bool, p: MalkanParams):
    b = bars.copy()
    if with_ind:
        b["rsi"] = rsi(b["close"], p.rsi_period)
        b["sma"] = sma(b["close"], p.ma_period)
    out = []
    for row in b.itertuples(index=False):
        rec = [row.date.strftime("%Y-%m-%d"), float(row.open), float(row.high), float(row.low), float(row.close), int(row.volume)]
        if with_ind:
            rec += [None if pd.isna(row.rsi) else round(float(row.rsi), 1), None if pd.isna(row.sma) else round(float(row.sma), 2)]
        out.append(rec)
    return out


# ------------------------------------------------------------------------------- Malkan systems
@app.get("/api/malkan")
def malkan_results():
    return STATE["malkan"]


@app.get("/api/malkan/history/{symbol}")
def malkan_history(symbol: str):
    """Weekly + monthly bars with RSI(14)/SMA(20) and one year of daily bars, for the detail chart."""
    df = load_history(symbol.upper())
    if df is None or df.empty:
        raise HTTPException(404, f"No history for {symbol}")
    p = MalkanParams.from_dict(STATE["malkan"].get("params", {}))
    cutoff = df["date"].iloc[-1] - pd.Timedelta(days=366)
    return {"symbol": symbol.upper(), "W": _pack_bars(_tw(df), True, p), "M": _pack_bars(_tm(df), True, p),
            "D": _pack_bars(df[df["date"] >= cutoff], False, p)}


@app.post("/api/malkan/rescan")
def malkan_rescan():
    if STATE["rescan_running"]:
        return JSONResponse({"ok": False, "message": "A scan is already running"}, status_code=409)

    def work():
        STATE["rescan_running"] = True
        try:
            STATE["malkan"] = run_malkan(MalkanParams.from_dict(STATE["malkan"].get("params", {})), write=True)
            _start_feed(STATE["results"])
        finally:
            STATE["rescan_running"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------------------------- Journal
@app.get("/api/journal")
def journal_list():
    return J.list_trades()


@app.post("/api/journal")
def journal_upsert(trade: dict):
    rec = J.upsert(trade)
    _start_feed(STATE["results"])   # make sure the new symbol is polled
    return rec


@app.delete("/api/journal/{tid}")
def journal_delete(tid: str):
    return {"ok": J.delete(tid)}


# ------------------------------------------------------------------------------- REST
@app.get("/api/status")
def status():
    book: QuoteBook = STATE["book"]
    feed = STATE["feed"]
    stale = (time.time() - book.last_tick_at) if book and book.last_tick_at else None
    return {
        "market": market_state(),
        "feed": {
            "mode": book.mode if book else "idle",
            "connected": bool(book and book.connected),
            "seconds_since_tick": round(stale, 1) if stale is not None else None,
            "last_quote_ts": book.last_quote_ts if book else None,
            "cycle_seconds": getattr(feed, "last_cycle_seconds", None),
            "poll_seconds": C.LIVE_POLL_SECONDS,
            "error": book.error if book else None,
            "subscribed": len(STATE["feed_tokens"]),
        },
        "has_credentials": C.HAS_CREDS,
        "results_generated_at": STATE["results"].get("generated_at"),
        "rescan_running": STATE["rescan_running"],
        "indices": C.INDEX_TOKENS,
    }


@app.get("/api/results")
def results():
    return STATE["results"]


@app.get("/api/history/{symbol}")
def history(symbol: str, tf: str = "W", limit: int = 0):
    df = load_history(symbol.upper())
    if df is None or df.empty:
        raise HTTPException(404, f"No history for {symbol}")
    if tf.upper() == "W":
        df = to_weekly(df)
    if limit > 0:
        df = df.tail(limit)
    rows = [
        {"time": d.strftime("%Y-%m-%d"), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v)}
        for d, o, h, l, c, v in zip(df["date"], df["open"], df["high"], df["low"], df["close"], df["volume"])
    ]
    return {"symbol": symbol.upper(), "tf": tf.upper(), "candles": rows}


@app.get("/api/intraday/{symbol}")
def intraday(symbol: str, minutes: int = 5):
    """Today's intraday candles for one stock, straight from Upstox (for the detail chart)."""
    from . import upstox
    res = next((r for r in _rows(STATE["results"]) if r["symbol"] == symbol.upper()), None)
    if not res or not res.get("isin"):
        raise HTTPException(404, f"{symbol} is not in the current results")
    df = upstox.intraday_candles(f"NSE_EQ|{res['isin']}", minutes if minutes in (1, 5) else 5)
    rows = [{"time": int(d.timestamp()), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v)}
            for d, o, h, l, c, v in zip(df["date"], df["open"], df["high"], df["low"], df["close"], df["volume"])]
    return {"symbol": symbol.upper(), "minutes": minutes, "candles": rows}


@app.post("/api/rescan")
def rescan(min_depth_pct: Optional[float] = None, min_weeks: Optional[int] = None,
           max_breakout_age_days: Optional[int] = None, near_breakout_pct: Optional[float] = None):
    if STATE["rescan_running"]:
        return JSONResponse({"ok": False, "message": "A scan is already running"}, status_code=409)
    params = ScreenParams.from_dict({
        "min_depth_pct": min_depth_pct, "min_weeks": min_weeks,
        "max_breakout_age_days": max_breakout_age_days, "near_breakout_pct": near_breakout_pct,
    })

    def work():
        STATE["rescan_running"] = True
        try:
            res = run_screen(params, write=True)
            STATE["results"] = res
            _start_feed(res)
        finally:
            STATE["rescan_running"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "params": params.to_dict()}


# ------------------------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    book: QuoteBook = STATE["book"]
    try:
        await websocket.send_json({"type": "snapshot", "quotes": book.snapshot() if book else [],
                                   "mode": book.mode if book else "idle"})
        while True:
            await asyncio.sleep(0.5)
            book = STATE["book"]
            if book is None:
                continue
            updates = book.drain()
            if updates:
                await websocket.send_json({"type": "ticks", "quotes": updates, "mode": book.mode})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# The web app (index.html, app.js, styles.css, config.js) is served from the root so the
# same files work unchanged on GitHub Pages. API routes above take precedence.
app.mount("/", StaticFiles(directory=str(C.WEB), html=True), name="web")
