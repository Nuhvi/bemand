#!/usr/bin/env python3
"""track.py — the "Status" block + volatility chart for the README.

The difficulty->price law is frozen (fitted once, then immutable); only the
difficulty / BTC price / ECB FX feeds move. This script refreshes those feeds
and rewrites the small Status block in README.md plus out/track.png — a single
rolling-volatility chart that makes the whole point in one image: the DBTC unit
adds almost nothing on top of whatever numeraire it is quoted in (DBTC/USD
~0.2% vs basket/USD's own ~4%). DBTC/basket volatility is derivable from those
two lines and is not plotted.

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
WINDOWS = [7, 30, 90, 365]          # 1w / 1m / 1q / 1y — the standard finance windows
SMOOTH = 30                         # days of moving-average smoothing on the plotted vol path
# (color, width, linestyle). spot=orange · dbtc=green are repo rules.
# DBTC/basket volatility is derivable from the two lines shown, so it is not plotted.
LINE = {"DBTC/USD": ("#1f9d55", 1.4, "-"),
        "basket/USD": ("#8a93a0", 1.2, "-")}


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
        "BTC/USD": spot,                                 # spot, for the "now" line
        "basket/USD": fx_basket,                         # the fiat basket vs USD (units/USD)
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
    v_unit = annvol(w["DBTC/USD"]) * 100
    v_fx = annvol(w["basket/USD"]) * 100
    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data {diff_s} / spot {spot_s} · law set {law['calc_date']}_

1 DBTC = **${now:,.0f}** · spot BTC = ${spot:,.0f} — realised volatility over the 1w/1m/3m/1y windows:

![track](out/track.png)

_DBTC in USD is ~**{v_unit:.2f}%** (90d); in a basket of currencies it inherits the basket's own ~{v_fx:.1f}% vs USD —
DBTC's volatility is not very different from the basket of currencies vs USD. The unit adds almost nothing on top of
whatever numeraire it is quoted in._"""


def replace_block(start: str, end: str, text: str) -> None:
    src = README.read_text()
    if start not in src or end not in src:
        raise SystemExit(f"markers {start!r} / {end!r} not found in {README}")
    head, tail = src.split(start, 1)
    _mid, tail = tail.split(end, 1)
    tail = "\n\n" + tail.lstrip("\n")
    README.write_text(head + start + "\n" + text.rstrip() + "\n" + end + tail)


def write_chart(law: dict, w: pd.DataFrame) -> Path:
    """Rolling annualised volatility of DBTC in USD vs in the fiat basket, against
    the basket's own volatility vs USD — one stacked panel per window (1w/1m/1q/1y),
    so there is room to read the post-inception period. Vol lines are smoothed and
    the y-axis cropped above 1e-4: the DBTC unit riding near the bottom vs the
    overlapping basket lines is the whole point."""
    fig, axes = plt.subplots(len(WINDOWS), 1, figsize=(10.5, 11.5), sharex=True, sharey=True)
    for ax, win in zip(axes, WINDOWS):
        for k in LINE:
            vol = w[k].pct_change().rolling(win).std() * np.sqrt(365)
            vol = vol.rolling(SMOOTH, min_periods=1).mean()
            color, lw, ls = LINE[k]
            ax.plot(vol.index, vol, color=color, lw=lw, ls=ls, label=k)
        ax.set_yscale("log")
        ax.set_ylim(bottom=1e-4)
        ax.axhline(0.01, color="#bbb", ls=":", lw=0.8)
        for t in (pd.Timestamp(REF), pd.Timestamp(law["calc_date"])):
            ax.axvline(t, color="#7b7f8a", ls="--", lw=1)
        ax.set_title(f"{win}-day window  (line smoothed over {SMOOTH}d)", fontsize=11)
        ax.legend(loc="lower left", ncol=3, fontsize=8, borderaxespad=0.6, handlelength=2.4)
        ax.grid(alpha=0.3, which="both")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axes[0].set_ylabel("annualised vol (log)")
    fig.suptitle("DBTC volatility is not very different from the basket of currencies vs USD",
                 fontsize=12, y=0.995)
    fig.text(0.5, 0.004, "realised annualised volatility, rolling window of the given length; chart lines "
             f"smoothed with a {SMOOTH}-day moving average", ha="center", fontsize=9, color="#6b7380")
    fig.tight_layout(rect=(0, 0, 1, 0.985), h_pad=1.0)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "track.png"
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