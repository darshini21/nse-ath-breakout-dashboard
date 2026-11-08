"""Real-time quotes: Angel One SmartAPI WebSocket v2 relay, or a clearly-labelled simulator."""
from __future__ import annotations

import math
import random
import threading
import time
from datetime import datetime

from . import config as C


class QuoteBook:
    """Latest quote per token plus a change-notification hook. Thread-safe."""

    def __init__(self, last_close: dict[str, float]):
        self._lock = threading.Lock()
        self.quotes: dict[str, dict] = {}
        self.last_close = last_close  # token -> last daily close from history (for sanity + %chg)
        self.dirty: set[str] = set()
        self.last_tick_at: float | None = None
        self.last_quote_ts: str | None = None  # newest exchange/candle timestamp seen (IST ISO)
        self.connected = False
        self.mode = "idle"  # "live" (Angel WS) | "upstox" (1-min candles) | "simulated" | "idle"
        self.error: str | None = None
        self._scale: float | None = None  # paise->rupees auto-detect

    def _detect_scale(self, token: str, raw_ltp: float):
        ref = self.last_close.get(token)
        if not ref or raw_ltp <= 0:
            return
        # Angel One streams prices as integers in paise; detect rather than assume.
        self._scale = 100.0 if abs(math.log10(raw_ltp / ref) - 2) < abs(math.log10(raw_ltp / ref)) else 1.0

    def update(self, token: str, ltp: float, open_=None, high=None, low=None, prev_close=None, volume=None, ts=None, raw=True):
        if raw:
            if self._scale is None:
                self._detect_scale(token, ltp)
            s = self._scale or 100.0
            ltp = ltp / s
            open_ = open_ / s if open_ is not None else None
            high = high / s if high is not None else None
            low = low / s if low is not None else None
            prev_close = prev_close / s if prev_close is not None else None
        prev = prev_close if prev_close else self.last_close.get(token)
        chg = ((ltp - prev) / prev * 100) if prev else None
        q = {
            "token": token,
            "ltp": round(ltp, 2),
            "open": round(open_, 2) if open_ is not None else None,
            "high": round(high, 2) if high is not None else None,
            "low": round(low, 2) if low is not None else None,
            "prev_close": round(prev, 2) if prev else None,
            "change_pct": round(chg, 2) if chg is not None else None,
            "volume": int(volume) if volume is not None else None,
            "ts": ts or datetime.now(C.IST).isoformat(timespec="seconds"),
        }
        with self._lock:
            self.quotes[token] = q
            self.dirty.add(token)
            self.last_tick_at = time.time()

    def note_quote_time(self, ts: str | None):
        if ts and (self.last_quote_ts is None or ts > self.last_quote_ts):
            self.last_quote_ts = ts

    def drain(self) -> list[dict]:
        with self._lock:
            out = [self.quotes[t] for t in self.dirty if t in self.quotes]
            self.dirty.clear()
        return out

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.quotes.values())


# --------------------------------------------------------------------------- Angel One feed
class AngelFeed:
    def __init__(self, client, tokens: list[str], book: QuoteBook):
        self.client = client
        self.tokens = [str(t) for t in tokens]
        self.book = book
        self.sws = None
        self._thread: threading.Thread | None = None

    def start(self):
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        self.sws = SmartWebSocketV2(
            self.client.jwt_token, C.API_KEY, C.CLIENT_CODE, self.client.feed_token,
            max_retry_attempt=10, retry_strategy=1, retry_delay=5, retry_multiplier=2, retry_duration=120,
        )
        book = self.book

        def on_open(wsapp):
            book.connected = True
            book.mode = "live"
            book.error = None
            for i in range(0, len(self.tokens), 100):
                self.sws.subscribe(f"sub{i}", 2, [{"exchangeType": 1, "tokens": self.tokens[i:i + 100]}])

        def on_data(wsapp, msg):
            try:
                if not isinstance(msg, dict) or "token" not in msg:
                    return
                tok = str(msg["token"]).strip('"').strip("\x00")
                ts = msg.get("exchange_timestamp")
                ts_iso = datetime.fromtimestamp(ts / 1000, C.IST).isoformat(timespec="seconds") if ts else None
                book.update(
                    tok, float(msg["last_traded_price"]),
                    open_=msg.get("open_price_of_the_day"), high=msg.get("high_price_of_the_day"),
                    low=msg.get("low_price_of_the_day"), prev_close=msg.get("closed_price"),
                    volume=msg.get("volume_trade_for_the_day"), ts=ts_iso, raw=True,
                )
            except Exception as e:  # never let a bad packet kill the feed
                book.error = f"tick parse: {e}"

        def on_error(wsapp, err):
            book.error = str(err)

        def on_close(wsapp):
            book.connected = False

        self.sws.on_open = on_open
        self.sws.on_data = on_data
        self.sws.on_error = on_error
        self.sws.on_close = on_close
        self._thread = threading.Thread(target=self.sws.connect, daemon=True, name="angel-ws")
        self._thread.start()

    def stop(self):
        try:
            if self.sws:
                self.sws.close_connection()
        except Exception:
            pass


# --------------------------------------------------------------------------- simulator
class SimulatedFeed:
    """Random-walk ticks around the last known close. Labelled SIMULATED in the UI."""

    def __init__(self, tokens: list[str], book: QuoteBook):
        # Only simulate symbols with a real last close to anchor to (so indices stay blank)
        self.tokens = [str(t) for t in tokens if book.last_close.get(str(t))]
        self.book = book
        self._stop = threading.Event()

    def start(self):
        self.book.mode = "simulated"
        self.book.connected = True
        state = {}
        for t in self.tokens:
            base = self.book.last_close[t]
            state[t] = {"px": base, "o": base, "h": base, "l": base, "v": 0}

        def run():
            while not self._stop.is_set() and self.tokens:
                for t in random.sample(self.tokens, min(25, len(self.tokens))):
                    s = state[t]
                    s["px"] = max(0.05, s["px"] * (1 + random.gauss(0, 0.0012)))
                    s["h"], s["l"] = max(s["h"], s["px"]), min(s["l"], s["px"])
                    s["v"] += random.randint(50, 5000)
                    self.book.update(t, s["px"], s["o"], s["h"], s["l"], self.book.last_close.get(t), s["v"], raw=False)
                time.sleep(0.5)

        threading.Thread(target=run, daemon=True, name="sim-feed").start()

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------- Upstox polling
class UpstoxPollFeed:
    """Polls Upstox's keyless 1-minute intraday candles and publishes the latest close as the price.

    `keymap`: quote id (Angel token or ISIN) -> Upstox instrument key.
    `closes`: quote id -> (last_date, last_close, prev_close) from the stored daily history, so the
    day's % change is measured against the correct previous session close.
    Priority ids (indices, breakouts) are polled first on every cycle.
    """

    def __init__(self, keymap: dict[str, str], closes: dict[str, tuple], book: QuoteBook, priority: list[str] | None = None):
        self.keymap = keymap
        self.closes = closes
        self.book = book
        order = list(dict.fromkeys((priority or []) + list(keymap)))
        self.order = [t for t in order if t in keymap]
        self._stop = threading.Event()
        self.last_cycle_seconds: float | None = None

    def start(self):
        from . import upstox  # local import keeps demo/Angel modes free of it
        self.book.mode = "upstox"
        self.book.connected = True
        from concurrent.futures import ThreadPoolExecutor

        def poll_one(tok):
            q = upstox.latest_quote(self.keymap[tok])
            if not q:
                return
            last_date, last_close, prev_close = self.closes.get(tok, (None, None, None))
            # If the candles are from the same day as the last stored daily bar, that bar IS today,
            # so compare with the close before it; otherwise the stored last close is yesterday's.
            ref = prev_close if (last_date and q["day"] == last_date and prev_close) else last_close
            self.book.update(tok, q["ltp"], q["open"], q["high"], q["low"], ref, q["volume"], ts=q["ts"], raw=False)
            self.book.note_quote_time(q["ts"])

        def run():
            while not self._stop.is_set():
                t0 = time.time()
                with ThreadPoolExecutor(max_workers=4) as ex:
                    list(ex.map(poll_one, self.order))
                self.last_cycle_seconds = round(time.time() - t0, 1)
                self.book.error = None
                phase = market_state()["phase"]
                wait = C.LIVE_POLL_SECONDS if phase in ("open", "pre_open") else C.IDLE_POLL_SECONDS
                self._stop.wait(max(5, wait - self.last_cycle_seconds))

        threading.Thread(target=run, daemon=True, name="upstox-poll").start()

    def stop(self):
        self._stop.set()


def market_state(now: datetime | None = None) -> dict:
    now = now or datetime.now(C.IST)
    t = now.hour * 60 + now.minute
    weekday = now.weekday() < 5
    if not weekday:
        phase = "closed"
    elif 9 * 60 <= t < 9 * 60 + 15:
        phase = "pre_open"
    elif 9 * 60 + 15 <= t < 15 * 60 + 30:
        phase = "open"
    else:
        phase = "closed"
    return {"phase": phase, "ist": now.isoformat(timespec="seconds")}
