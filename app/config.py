"""Paths, credentials and screener defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
HIST = DATA / "history"
UNIVERSE_CSV = DATA / "universe.csv"
RESULTS_JSON = DATA / "results.json"
SESSION_JSON = DATA / "session.json"
WEB = ROOT / "web"

for p in (DATA, RAW, HIST):
    p.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

API_KEY = os.getenv("ANGEL_API_KEY", "").strip()
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "").strip()
MPIN = os.getenv("ANGEL_MPIN", "").strip()
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "").strip().replace(" ", "")
PORT = int(os.getenv("PORT", "8000"))

HAS_CREDS = all([API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET])

IST = ZoneInfo("Asia/Kolkata")

# Public data sources (no login needed)
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# Index tokens on Angel One (exchangeType 1 = NSE) and their Upstox instrument keys
INDEX_TOKENS = {"99926000": "NIFTY 50", "99926009": "NIFTY BANK", "99926004": "NIFTY 500"}
UPSTOX_INDEX_KEYS = {"99926000": "NSE_INDEX|Nifty 50", "99926009": "NSE_INDEX|Nifty Bank", "99926004": "NSE_INDEX|Nifty 500"}

# Upstox public candle API (keyless) - request pacing and live polling cadence
UPSTOX_REQ_PER_SEC = float(os.getenv("UPSTOX_REQ_PER_SEC", "4"))
LIVE_POLL_SECONDS = int(os.getenv("LIVE_POLL_SECONDS", "60"))      # during market hours
IDLE_POLL_SECONDS = int(os.getenv("IDLE_POLL_SECONDS", "600"))     # outside market hours
SIMULATE = os.getenv("SIMULATE", "0") == "1"                       # force the random-walk demo feed

LISTING_YEARS = 10  # only stocks listed within this many years are scanned


@dataclass
class ScreenParams:
    """Tunable rules for the cup (U) / double-bottom (W) all-time-high breakout screen."""

    min_depth_pct: float = 15.0   # base must fall at least this % below the prior all-time-high close
    min_weeks: int = 7            # base must last at least this many weeks (rim to breakout)
    max_breakout_age_days: int = 0  # 0 = any time; else breakout must be within N days
    # A second trough counts as a "W" when the middle peak recovers at least this fraction
    # of the base depth and the second trough is within this fraction of depth of the first.
    w_peak_recovery: float = 0.33
    w_trough_tolerance: float = 0.25
    near_breakout_pct: float = 5.0  # "approaching" list: within this % below the rim

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenParams":
        base = cls()
        for k, v in d.items():
            if hasattr(base, k) and v is not None:
                setattr(base, k, type(getattr(base, k))(v))
        return base
