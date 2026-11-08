"""Angel One SmartAPI session handling and rate-limited historical candle fetch."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, date

import pandas as pd
import pyotp

from . import config as C


class RateLimiter:
    """Simple token-interval limiter shared across threads."""

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


class AngelClient:
    """Logs in with TOTP, caches the day's tokens, fetches daily candles.

    Angel One's historical API is documented at 3 requests/second; we stay under it.
    """

    def __init__(self):
        if not C.HAS_CREDS:
            raise RuntimeError("Angel One credentials missing: fill in .env (see .env.example)")
        from SmartApi import SmartConnect  # imported lazily so demo mode needs no SDK

        self.api = SmartConnect(api_key=C.API_KEY)
        self.jwt_token: str | None = None
        self.refresh_token: str | None = None
        self.feed_token: str | None = None
        self._limiter = RateLimiter(2.5)
        self._login()

    # ------------------------------------------------------------------ auth
    def _login(self):
        today = datetime.now(C.IST).strftime("%Y-%m-%d")
        if C.SESSION_JSON.exists():
            try:
                cached = json.loads(C.SESSION_JSON.read_text())
                if cached.get("date") == today and cached.get("client") == C.CLIENT_CODE:
                    self.jwt_token = cached["jwt"]
                    self.refresh_token = cached["refresh"]
                    self.feed_token = cached["feed"]
                    self.api.setAccessToken(self.jwt_token)
                    self.api.setRefreshToken(self.refresh_token)
                    self.api.setFeedToken(self.feed_token)
                    prof = self.api.getProfile(self.refresh_token)
                    if prof and prof.get("status"):
                        return
            except Exception:
                pass  # fall through to a fresh login

        totp = pyotp.TOTP(C.TOTP_SECRET).now()
        resp = self.api.generateSession(C.CLIENT_CODE, C.MPIN, totp)
        if not resp or not resp.get("status"):
            raise RuntimeError(f"Angel One login failed: {resp.get('message') if resp else 'no response'}")
        d = resp["data"]
        self.jwt_token = d["jwtToken"]
        self.refresh_token = d["refreshToken"]
        self.feed_token = d.get("feedToken") or self.api.getfeedToken()
        C.SESSION_JSON.write_text(
            json.dumps(
                {"date": today, "client": C.CLIENT_CODE, "jwt": self.jwt_token,
                 "refresh": self.refresh_token, "feed": self.feed_token}
            )
        )

    # ------------------------------------------------------------- candles
    def daily_candles(self, token: str, start: date, end: date, exchange: str = "NSE") -> pd.DataFrame:
        """Daily OHLCV between start and end (inclusive). Handles the 2000-day cap per request."""
        frames = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + pd.Timedelta(days=1990).to_pytimedelta())
            frames.append(self._candles_chunk(token, chunk_start, chunk_end, exchange))
            chunk_start = chunk_end + pd.Timedelta(days=1).to_pytimedelta()
        if not frames:
            return _empty()
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return df
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        return df

    def _candles_chunk(self, token: str, start: date, end: date, exchange: str) -> pd.DataFrame:
        params = {
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": "ONE_DAY",
            "fromdate": f"{start:%Y-%m-%d} 09:00",
            "todate": f"{end:%Y-%m-%d} 15:30",
        }
        for attempt in range(6):
            self._limiter.wait()
            try:
                resp = self.api.getCandleData(params)
            except Exception as e:  # network hiccup, SDK raise
                resp = {"status": False, "message": str(e), "errorcode": "EXC"}
            if resp and resp.get("status"):
                rows = resp.get("data") or []
                return _to_frame(rows)
            msg = str((resp or {}).get("message", ""))
            code = str((resp or {}).get("errorcode", ""))
            if "rate" in msg.lower() or code in ("AB1004", "AB2001") or "exceed" in msg.lower():
                time.sleep(2.0 * (attempt + 1))
                continue
            if code == "AG8001" or "token" in msg.lower() and "invalid" in msg.lower():
                self._login()
                continue
            # Genuine "no data" responses come back as status False too; treat as empty
            if attempt >= 1:
                return _empty()
            time.sleep(1.0)
        return _empty()

    # ------------------------------------------------------------- quotes
    def ltp_snapshot(self, tokens: list[str], exchange: str = "NSE") -> dict:
        """Full quote snapshot for up to 50 tokens per call via getMarketData."""
        out = {}
        for i in range(0, len(tokens), 50):
            self._limiter.wait()
            try:
                resp = self.api.getMarketData("FULL", {exchange: tokens[i:i + 50]})
            except Exception:
                continue
            for q in ((resp or {}).get("data") or {}).get("fetched", []):
                out[str(q["symbolToken"])] = q
        return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _to_frame(rows) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["volume"] = df["volume"].astype("int64")
    return df
