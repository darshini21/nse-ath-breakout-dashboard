"""Command line entry point.

  python -m app.cli universe            build/refresh the stock universe
  python -m app.cli history [SYMBOLS]   download / update 10y daily candles (needs .env)
  python -m app.cli screen [--depth 15 --weeks 7 --age 0]   run the breakout screen
  python -m app.cli serve               start the dashboard
  python -m app.cli export              build the static site (./site) for GitHub Pages
  python -m app.cli malkan              scan the Nifty 500 for Malkan system 1/2/3 signals
  python -m app.cli all                 universe -> history -> screen -> serve
"""
from __future__ import annotations

import argparse
import sys

from . import config as C


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe").add_argument("--refresh", action="store_true")
    h = sub.add_parser("history")
    h.add_argument("symbols", nargs="*")
    h.add_argument("--full", action="store_true", help="re-download everything")
    s = sub.add_parser("screen")
    s.add_argument("--depth", type=float, default=None, help="min base depth %% (default 15)")
    s.add_argument("--weeks", type=int, default=None, help="min base length in weeks (default 7)")
    s.add_argument("--age", type=int, default=None, help="max days since breakout, 0 = any (default 0)")
    sub.add_parser("serve").add_argument("--port", type=int, default=C.PORT)
    sub.add_parser("export", help="build the static site in ./site for GitHub Pages")
    sub.add_parser("malkan", help="scan the Nifty 500 for Malkan system 1/2/3 signals")
    sub.add_parser("all")
    a = ap.parse_args(argv)

    if a.cmd == "universe":
        from .universe import build_universe
        from .nifty500 import build_nifty500
        build_universe(refresh=a.refresh)
        build_nifty500(refresh=a.refresh)
    elif a.cmd == "history":
        from .history import update_all
        from .nifty500 import load_nifty500
        update_all(a.symbols or None, full=a.full)
        if not a.symbols:
            update_all(full=a.full, universe=load_nifty500())
    elif a.cmd == "malkan":
        from .malkan import run_malkan
        out = run_malkan()
        print(f"Scanned {out['scanned']} Nifty 500 stocks: system1 {len(out['system1'])}, system2 {len(out['system2'])}, system3 {len(out['system3'])}")
    elif a.cmd == "screen":
        from .config import ScreenParams
        from .screener import run_screen
        p = ScreenParams.from_dict({"min_depth_pct": a.depth, "min_weeks": a.weeks, "max_breakout_age_days": a.age})
        res = run_screen(p)
        print(f"Scanned {res['scanned']} stocks: {len(res['breakouts'])} breakouts, {len(res['approaching'])} approaching.")
        for r in res["breakouts"][:40]:
            b = r["base"]
            print(f"  {r['symbol']:<14}{r['pattern']}  rim {b['rim_price']:>9} ({b['rim_date']})  depth {b['depth_pct']:>5}%  "
                  f"{b['weeks']:>3}w  broke {b['breakout_date']}  now {r['last_close']:>9}  +{r['pct_above_rim']}%")
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("app.server:app", host="127.0.0.1", port=a.port, log_level="info")
    elif a.cmd == "export":
        from .export import build
        build()
    elif a.cmd == "all":
        from .universe import build_universe
        from .history import update_all
        from .screener import run_screen
        from .nifty500 import build_nifty500, load_nifty500
        from .malkan import run_malkan
        build_universe(); build_nifty500()
        update_all(); update_all(universe=load_nifty500())
        run_screen(); run_malkan()
        import uvicorn
        uvicorn.run("app.server:app", host="127.0.0.1", port=C.PORT, log_level="info")


if __name__ == "__main__":
    main()
