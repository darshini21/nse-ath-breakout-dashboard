"""Trade journal (the notes' table: entry date, entry, SL, RPS, RPT, qty, exit price, exit date, P&L).
Stored locally in data/journal.json. The static site keeps the same records in the browser instead."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime

from . import config as C

JOURNAL_JSON = C.DATA / "journal.json"
_lock = threading.Lock()
FIELDS = {"symbol", "name", "isin", "system", "entry_date", "entry", "sl", "qty", "rpt", "notes",
          "exit_date", "exit_price", "targets", "trail_level", "status"}


def _load() -> list[dict]:
    if not JOURNAL_JSON.exists():
        return []
    try:
        return json.loads(JOURNAL_JSON.read_text())
    except Exception:
        return []


def _save(rows: list[dict]):
    JOURNAL_JSON.write_text(json.dumps(rows, indent=1))


def list_trades() -> list[dict]:
    with _lock:
        return _load()


def upsert(trade: dict) -> dict:
    clean = {k: v for k, v in trade.items() if k in FIELDS}
    with _lock:
        rows = _load()
        tid = trade.get("id")
        now = datetime.now(C.IST).isoformat(timespec="seconds")
        if tid:
            for r in rows:
                if r["id"] == tid:
                    r.update(clean)
                    r["updated_at"] = now
                    _save(rows)
                    return r
        rec = {"id": uuid.uuid4().hex[:10], "created_at": now, "updated_at": now, "status": "open", **clean}
        rec["rps"] = round(float(rec.get("entry", 0)) - float(rec.get("sl", 0)), 2)
        rows.append(rec)
        _save(rows)
        return rec


def delete(tid: str) -> bool:
    with _lock:
        rows = _load()
        new = [r for r in rows if r["id"] != tid]
        _save(new)
        return len(new) != len(rows)
