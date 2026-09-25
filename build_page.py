#!/usr/bin/env python3
"""build_page.py — regenerate site/index.html, the DBTC frozen-law tracker page.

Pipeline:
  1. refresh difficulty / market-price / FX caches if their *data* is stale
     (content-aware; a fresh checkout won't mask old data any more)
  2. read data/frozen_law.json (fail loudly if it doesn't exist yet)
  3. bundle (data + law + build stamp) into site/index.html — a fully
     self-contained page that renders immediately, then refreshes in the
     browser from sibling ./data/*.json and ECB FX
  4. write sibling data copies under site/data/ so hosted/CI versions can
     serve (and git-diff) the same raw feeds

Usage:
    python build_page.py            # refresh if stale, then build
    python build_page.py --force    # force-re-download all feeds, then build
    python build_page.py --no-refresh   # build from existing caches only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbtc import download, fx, frozen  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
TEMPLATE = SITE / "index.template.html"
OUTPUT = SITE / "index.html"
MARKER = "__DBTC_BUNDLE__"


def chart_pairs(key: str) -> list[list[float]]:
    """Return [[unixTs, y], ...] straight from the cached chart file."""
    chart = json.loads((DATA / f"{key}.json").read_text())
    return [[float(v["x"]), float(v["y"])] for v in chart["values"]]


def fx_pairs() -> dict[str, list[list[float]]]:
    """Return {CUR: [[unixTs, y], ...]} straight from the cached FX file."""
    data = json.loads((DATA / "fx.json").read_text())
    out: dict[str, list[list[float]]] = {}
    for datestr, vals in data["rates"].items():
        ts = int(datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        for cur, y in vals.items():
            out.setdefault(cur, []).append([ts, float(y)])
    return out


def refresh_feeds(force: bool) -> None:
    """(Re)download anything stale so the bundle embeds the freshest data."""
    if force or download.stale_files():
        print("[build] refreshing difficulty / market-price feeds …")
        download.all_charts(force=force)
    _ = fx.load(force=force)
    for name in fx.stale():
        print(f"[build] refreshing {name} …")


def build_bundle() -> dict:
    diff = chart_pairs("difficulty")
    price = chart_pairs("market_price")
    fxr = fx_pairs()
    if not (diff and price and fxr):
        raise RuntimeError("no data available — run without --no-refresh, or check data/")

    law = frozen.load()
    law = {
        **{k: law[k] for k in ("calc_date", "anchor", "smooth", "b", "a", "P0", "D0")},
        "currencies": law["currencies"],
        "basket": law["basket"],
        "cr": 3.0,
    }
    meta = {
        **law,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_asof": {
            "difficulty": diff[-1][0],
            "market_price": price[-1][0],
            "fx": fxr["GBP"][-1][0] if "GBP" in fxr else None,
        },
    }
    return {"meta": meta, "difficulty": diff, "market_price": price, "fx": fxr}


def write_sibling_data(bundle: dict) -> None:
    """Write site/data/*.json in the exact shapes the page's JS consumes."""
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    for key in ("difficulty", "market_price"):
        with open(SITE / "data" / f"{key}.json", "w") as fh:
            json.dump(bundle[key], fh, separators=(",", ":"))
    with open(SITE / "data" / "fx.json", "w") as fh:
        json.dump(bundle["fx"], fh, separators=(",", ":"))
    with open(SITE / "data" / "frozen_law.json", "w") as fh:
        json.dump(bundle["meta"], fh, indent=2)
        fh.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="force re-download all feeds")
    parser.add_argument("--no-refresh", action="store_true", help="build from existing caches only")
    args = parser.parse_args()

    freeze = frozen.FROZEN_PATH
    if not freeze.exists():
        print("[build] data/frozen_law.json missing — compute it first with "
              "`python -m dbtc.frozen` against the original cached data.", file=sys.stderr)
        return 1

    if not args.no_refresh:
        refresh_feeds(args.force)
    diff = chart_pairs("difficulty")
    print(f"[build] difficulty cache as of {datetime.fromtimestamp(diff[-1][0], tz=timezone.utc).date()}")
    print(f"[build] market-price cache as of {datetime.fromtimestamp(chart_pairs('market_price')[-1][0], tz=timezone.utc).date()}")

    bundle = build_bundle()
    meta = bundle["meta"]
    print(f"[build] frozen law: calc_date={meta['calc_date']}  b={meta['b']:.4f}  P0={meta['P0']:,.2f}  D0={meta['D0']:.3g}")

    src = TEMPLATE.read_text()
    if MARKER not in src:
        raise SystemExit(f"[build] marker {MARKER!r} not found in {TEMPLATE}")
    packed = json.dumps(bundle, separators=(",", ":")).replace("</", "<\\u002f")
    OUTPUT.write_text(src.replace(MARKER, packed))
    write_sibling_data(bundle)
    print(f"[build] wrote {OUTPUT} ({OUTPUT.stat().st_size/1024:.0f} KiB) + site/data/*.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())