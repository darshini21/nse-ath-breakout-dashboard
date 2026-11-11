"""Nifty 500 universe (with NSE's industry classification) for the Malkan systems."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from . import config as C

NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
NIFTY500_CSV = C.DATA / "nifty500.csv"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def build_nifty500(refresh: bool = False) -> pd.DataFrame:
    raw = C.RAW / "ind_nifty500list.csv"
    if refresh or not raw.exists():
        r = requests.get(NIFTY500_URL, headers=_HEADERS, timeout=120)
        r.raise_for_status()
        raw.write_bytes(r.content)
    df = pd.read_csv(raw)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Company Name": "name", "Industry": "industry", "Symbol": "symbol",
                            "Series": "series", "ISIN Code": "isin"})
    for c in ("name", "industry", "symbol", "series", "isin"):
        df[c] = df[c].astype(str).str.strip()
    df = df[df["series"].isin(["EQ", "BE"])].copy()          # drop REIT/InvIT units (RR)
    df["upstox_key"] = "NSE_EQ|" + df["isin"]
    df["token"] = df["isin"]                                   # quote id (Angel tokens not needed here)
    # No listing date in this file: history download starts at the 10-year floor
    df["listing_date"] = (date.today() - timedelta(days=365 * C.LISTING_YEARS)).strftime("%Y-%m-%d")
    df = df[["symbol", "name", "industry", "isin", "series", "upstox_key", "token", "listing_date"]].sort_values("symbol")
    df.to_csv(NIFTY500_CSV, index=False)
    print(f"Nifty 500 universe: {len(df)} stocks, {df['industry'].nunique()} industries")
    return df


def load_nifty500() -> pd.DataFrame:
    if not NIFTY500_CSV.exists():
        return build_nifty500()
    return pd.read_csv(NIFTY500_CSV, dtype={"token": str, "isin": str})
