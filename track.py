#!/usr/bin/env python3
"""track.py — the "Status" block + volatility chart for the README.

The difficulty->price law is frozen (fitted once, then immutable); only the
difficulty / BTC price / ECB FX feeds move. This script refreheras those feeds
and rewrites the small Status block in README.md plus out/track.png — a single
rolling-volatility chart that makes the whole point in one image: BTC's
volatility is intrinsic (same priced in USD, DBTC, or a fiat basket), and the
DBTC unit itself is the stable thing (in USD ~0.2%, in the fiat basket it
inherits FX noise).

Usage:
    python track.py                # refresh stale feeds, then update README
    python track.py --force        # force re-download of all feeds
    python track.py --no-refresh   # use existing caches only
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbtc import analyze, download, frozen, fx  # noqa: E402

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
OUT = ROOT / "out"
REF = "2016-01-01"                                       # reference date (normalisation point)
STATUS_START, STATUS_END = "<!-- dbtc:status:start -->", "<!-- dbtc:status:end -->"
VOL_WINDOW = 90
# spot=orange · dbtc=green are the repo-wide rules; the rest are fixed too.
LINE = {"BTC/USD": ("#f76707", 1.1), "BTC/DBTC": ("#8a93a0", 1.4), "BTC/basket": ("#7b1fa2", 1.1),
        "DBTC/USD": ("#1f9d55", 1.6), "DBTC/basket": ("#2b6ca3", 1.6)}


def refresh(force: bool) -> None:
    if force or download.stale_files():
        print("[track] refreshing difficulty / market-price feeds …")
        download.all_charts(force=force)
    _ = fx.load(force=force)


def load_series() -> pd.DataFrame:
    """Daily frame since the reference date with all priced series (USD terms)."""
    law = frozen.load()
    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])
    sd = analyze.sliding_smoothed_diff(df, law["smooth"])
    dbtc = law["P0"] * (sd / law["D0"]) ** law["b"]
    s = df.loc[df.index >= pd.Timestamp(REF)]
    dbtc = dbtc.reindex(s.index)
    spot = s["price"]
    fxdf = fx.load().reindex(s.index).ffill()

    w = pd.Series(law["basket"])                         # currency-basket weights (sum to 1)
    fx_basket = np.exp(sum(w[c] * np.log(fxdf[c]) for c in w.index if c != "USD"))

    out = pd.DataFrame({
        "DBTC/USD": dbtc,                                # the frozen law itself
        "BTC/USD": spot,                                 # price of BTC in USD
        "BTC/DBTC": spot / dbtc,                         # price of BTC in DBTC
        "DBTC/basket": dbtc * fx_basket,                 # the unit quoted in the fiat basket
        "BTC/basket": spot * fx_basket,                  # price of BTC in the fiat basket
    })
    return out


def annvol(x: pd.Series, w: int = VOL_WINDOW) -> float:
    return float(x.pct_change().rolling(w).std().iloc[-1] * math.sqrt(365))


def freshness() -> tuple[str, str, str]:
    chart = lambda name: json.loads((frozen.DATA_DIR / f"{name}.json").read_text())["values"][-1]["x"]  # noqa: E731
    diff_s = datetime.fromtimestamp(chart("difficulty"), tz=timezone.utc).strftime("%Y-%m-%d")
    spot_s = datetime.fromtimestamp(chart("market_price"), tz=timezone.utc).strftime("%Y-%m-%d")
    fx_json = json.loads((frozen.DATA_DIR / "fx.json").read_text())["rates"]
    return diff_s, spot_s, max(fx_json)


def build_status(law: dict, w: pd.DataFrame, diff_s: str, spot_s: str) -> str:
    now = w["DBTC/USD"].iloc[-1]
    spot = w["BTC/USD"].iloc[-1]
    v_busd, v_bdtc, v_bb = (annvol(w[k]) * 100 for k in ("BTC/USD", "BTC/DBTC", "BTC/basket"))
    v_unit, v_bask = annvol(w["DBTC/USD"]) * 100, annvol(w["DBTC/basket"]) * 100
    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data {diff_s} / spot {spot_s} · law set {law['calc_date']}_

1 DBTC = **${now:,.0f}** · spot BTC = ${spot:,.0f} — rolling {VOL_WINDOW}-day annualised volatility:

![track](out/track.png)

_BTC is ~{v_busd:.0f}% volatile whether priced in **USD, DBTC, or a fiat basket** ({v_bdtc:.0f}% / {v_bb:.0f}%); the DBTC unit itself is **{v_unit:.2f}%** in USD
and {v_bask:.1f}% in the basket (that spread is pure fiat FX). The law is holding when the three BTC frames overlap high and the two unit lines sit far below._"""


def replace_block(start: str, end: str, text: str) -> None:
    src = README.read_text()
    if start not in src or end not in src:
        raise SystemExit(f"markers {start!r} / {end!r} not found in {README}")
    head, tail = src.split(start, 1)
    _mid, tail = tail.split(end, 1)
    tail = "\n\n" + tail.lstrip("\n")
    README.write_text(head + start + "\n" + text.rstrip() + "\n" + end + tail)


def write_chart(law: dict, w: pd.DataFrame) -> Path:
    """Rolling annualised volatility of the price of BTC in USD / DBTC / basket,
    plus the DBTC unit in USD and in the basket — the whole argument in one image."""
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for k in LINE:
        vol = w[k].pct_change().rolling(VOL_WINDOW).std() * np.sqrt(365)
        color, lw = LINE[k]
        ax.plot(vol.index, vol, color=color, lw=lw, label=k)
    ax.set_yscale("log")
    ax.axhline(0.01, color="#bbb", ls=":", lw=1)
    for t, lab in ((pd.Timestamp(REF), "reference 2016-01-01"),
                   (pd.Timestamp(law["calc_date"]), f"inception {law['calc_date']}")):
        ax.axvline(t, color="#7b7f8a", ls="--", lw=1)
        ax.annotate(lab, xy=(t, ax.get_ylim()[1]), xytext=(2, 4), textcoords="offset points",
                    fontsize=9, color="#6b7380", ha="left", va="bottom", rotation=90)
    ax.set_ylabel(f"rolling {VOL_WINDOW}-day annualised vol (log)")
    ax.set_title("BTC volatility is intrinsic — priced in USD, DBTC, or a fiat basket, it is the same risk; the DBTC unit isn't")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3, which="both")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "track.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="force re-download all feeds")
    parser.add_argument("--no-refresh", action="store_true", help="use existing caches only")
    args = parser.parse_args()

    if not frozen.FROZEN_PATH.exists():
        print("[track] data/frozen_law.json missing — run `python -m dbtc.frozen` first.", file=sys.stderr)
        return 1

    if not args.no_refresh:
        refresh(args.force)

    law = frozen.load()
    w = load_series()
    diff_s, spot_s, _fx_s = freshness()

    replace_block(STATUS_START, STATUS_END, build_status(law, w, diff_s, spot_s))
    chart = write_chart(law, w)
    print(f"[track] updated {README} (Status block) + {chart}")
    print(f"[track] DBTC/USD now = ${w['DBTC/USD'].iloc[-1]:,.0f}  spot = ${w['BTC/USD'].iloc[-1]:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())