"""Build the scan universe: NSE main-board equities listed in the last N years,
joined to Angel One instrument tokens."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import requests

from . import config as C


def _download(url: str, dest, headers=None):
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)


def ensure_raw_files(refresh: bool = False):
    eq = C.RAW / "EQUITY_L.csv"
    sm = C.RAW / "OpenAPIScripMaster.json"
    if refresh or not eq.exists():
        _download(
            C.EQUITY_LIST_URL,
            eq,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    if refresh or not sm.exists():
        _download(C.SCRIP_MASTER_URL, sm)
    return eq, sm


def build_universe(refresh: bool = False) -> pd.DataFrame:
    eq_path, sm_path = ensure_raw_files(refresh)

    eq = pd.read_csv(eq_path)
    eq.columns = [c.strip() for c in eq.columns]
    eq = eq.rename(
        columns={
            "SYMBOL": "symbol",
            "NAME OF COMPANY": "name",
            "SERIES": "series",
            "DATE OF LISTING": "listing_date",
            "ISIN NUMBER": "isin",
        }
    )
    for col in ("symbol", "name", "series", "isin"):
        eq[col] = eq[col].astype(str).str.strip()
    eq["listing_date"] = pd.to_datetime(eq["listing_date"].str.strip(), format="%d-%b-%Y", errors="coerce")

    # Main board, normal-settlement equities only (EQ). BE/BZ are trade-for-trade
    # series and SME stocks are not in this file at all.
    eq = eq[eq["series"] == "EQ"].dropna(subset=["listing_date"])
    cutoff = pd.Timestamp(date.today() - timedelta(days=365 * C.LISTING_YEARS))
    eq = eq[eq["listing_date"] >= cutoff].copy()

    with open(sm_path, "r", encoding="utf-8") as f:
        scrips = json.load(f)
    rows = [
        {"symbol": s["name"], "token": str(s["token"]), "angel_symbol": s["symbol"]}
        for s in scrips
        if s.get("exch_seg") == "NSE" and str(s.get("symbol", "")).endswith("-EQ")
    ]
    tokens = pd.DataFrame(rows).drop_duplicates("symbol")

    # Angel One tokens are optional (only the tick-level feed needs them); the Upstox key is the ISIN.
    uni = eq.merge(tokens, on="symbol", how="left")
    missing = int(uni["token"].isna().sum())
    uni["token"] = uni["token"].fillna(uni["isin"])
    uni["angel_symbol"] = uni["angel_symbol"].fillna("")
    uni["upstox_key"] = "NSE_EQ|" + uni["isin"]
    uni = uni[["symbol", "name", "isin", "listing_date", "token", "angel_symbol", "upstox_key"]].sort_values("symbol")
    uni["listing_date"] = uni["listing_date"].dt.strftime("%Y-%m-%d")
    uni.to_csv(C.UNIVERSE_CSV, index=False)

    print(f"Universe: {len(uni)} NSE EQ stocks listed since {cutoff.date()} ({missing} without an Angel One token)")
    return uni


def load_universe() -> pd.DataFrame:
    if not C.UNIVERSE_CSV.exists():
        return build_universe()
    return pd.read_csv(C.UNIVERSE_CSV, dtype={"token": str})
