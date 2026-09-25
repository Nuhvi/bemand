#!/usr/bin/env python3
"""track.py — the "Status" block + chart for the README.

The difficulty->price law is frozen (fitted once, then immutable); only the
difficulty / BTC price / ECB FX feeds move. This script refreheras them and
rewrites the small Status block in README.md plus out/track.png — one chart,
two panels:

  * price   — DBTC/USD vs spot BTC/USD (log, since the 2016 reference date);
  * vol     — realised annualised volatility of DBTC/USD vs the currency basket
              basket/USD, the standard 90-day window (log, smoothed).

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

from dbtc import analyze, download, frozen, fx, units  # noqa: E402

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
OUT = ROOT / "out"
REF = "2016-01-01"                                       # reference date (normalisation point)
STATUS_START, STATUS_END = "<!-- dbtc:status:start -->", "<!-- dbtc:status:end -->"
VOL_WINDOW = 90                                          # the volatility window (standard 3-month)
SMOOTH = 30                                              # moving-average smoothing of the vol line
# spot=orange · dbtc=green are the repo-wide rules; fiat yardstick in grey.
PRICE = {"DBTC/USD": ("#1f9d55", 1.6, "DBTC/USD"), "BTC/USD": ("#f76707", 1.0, "spot BTC/USD")}
VOL = {"DBTC/USD": ("#1f9d55", 1.4), "basket/USD": ("#8a93a0", 1.2)}


def refresh(force: bool) -> None:
    if force or download.stale_files():
        print("[track] refreshing difficulty / market-price feeds …")
        download.all_charts(force=force)
    _ = fx.load(force=force)


def load_series() -> pd.DataFrame:
    """Daily frame since the reference date with the priced series (USD terms).

    The currency basket (equal-weight USD/EUR/GBP/JPY/CHF, per the frozen law)
    is kept on its native business-day grid (weekends are NaN), since FX has no
    weekend fixings — measuring its volatility on a forward-filled calendar grid
    would dilute it with zero returns.
    """
    law = frozen.load()
    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])
    sd = analyze.sliding_smoothed_diff(df, law["smooth"])
    dbtc = law["P0"] * (sd / law["D0"]) ** law["b"]
    s = df.loc[df.index >= pd.Timestamp(REF)]
    dbtc = dbtc.reindex(s.index)
    spot = s["price"]

    wgt = pd.Series(law["basket"])                       # currency-basket weights (sum to 1)
    rates = json.loads((frozen.DATA_DIR / "fx.json").read_text())["rates"]
    basket = {d: math.exp(sum(wgt[c] * math.log(rec[c]) for c in wgt.index if c != "USD"))
              for d, rec in rates.items()}
    basket = pd.Series(basket).sort_index()
    basket.index = pd.to_datetime(basket.index)
    basket = basket.loc[pd.Timestamp(REF):]              # clip: start at the reference date

    out = pd.DataFrame({
        "DBTC/USD": dbtc,                                # the frozen law itself
        "BTC/USD": spot,                                 # spot BTC, for the price panel
        "basket/USD": basket,                            # the fiat basket vs USD (units/USD)
    })
    return out


def annvol(x: pd.Series, w: int = VOL_WINDOW) -> float:
    rets = x.dropna().pct_change()          # each series on its own observed grid
    return float(rets.rolling(w).std().iloc[-1] * math.sqrt(365))


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
    v_basket = annvol(w["basket/USD"]) * 100
    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data {diff_s} / spot {spot_s} · law set {law['calc_date']}_

1 DBTC = **${now:,.0f}** · spot BTC = ${spot:,.0f} · {units.fmt_bem(1)} = {units.fmt_usd_bem(now)}

![track](out/track.png)

_DBTC/USD realised volatility is ~**{v_unit:.2f}%** ({VOL_WINDOW}d) — an order of magnitude calmer than the
basket of fiat currencies it can be quoted in (basket/USD ~{v_basket:.1f}%). The unit behaves like a near-fixed
peg: it tracks spot's long-run level, but barely wiggles day to day._"""


def replace_block(start: str, end: str, text: str) -> None:
    src = README.read_text()
    if start not in src or end not in src:
        raise SystemExit(f"markers {start!r} / {end!r} not found in {README}")
    head, tail = src.split(start, 1)
    _mid, tail = tail.split(end, 1)
    tail = "\n\n" + tail.lstrip("\n")
    README.write_text(head + start + "\n" + text.rstrip() + "\n" + end + tail)


def write_chart(law: dict, w: pd.DataFrame) -> Path:
    """One figure, two panels: (top) the price of the unit vs spot BTC, (bottom)
    realised volatility of DBTC/USD vs EUR/USD swept over the 1w/1m/3m/1y windows."""
    ref_ts, inc_ts = pd.Timestamp(REF), pd.Timestamp(law["calc_date"])
    fig, (axp, axv) = plt.subplots(2, 1, figsize=(10.5, 8.2), sharex=True, height_ratios=[1.05, 1.4])

    # ---- price panel ----
    for k, (color, lw, lab) in PRICE.items():
        axp.plot(w[k].index, w[k], color=color, lw=lw, label=lab)
    axp.set_yscale("log")
    axp.set_title("price of the unit vs spot BTC (log)", fontsize=11)
    axp.legend(loc="upper left", fontsize=9)
    axp.grid(alpha=0.3, which="both")

    # ---- volatility panel (single, standard 90-day window) ----
    for k, (color, lw) in VOL.items():
        vol = w[k].dropna().pct_change().rolling(VOL_WINDOW).std() * np.sqrt(365)
        vol = vol.rolling(SMOOTH, min_periods=1).mean()
        axv.plot(vol.index, vol, color=color, lw=lw, label=k)
    axv.set_yscale("log")
    axv.set_ylim(bottom=1e-4)
    axv.axhline(0.01, color="#bbb", ls=":", lw=0.8)
    axv.set_title(f"realised annualised volatility, {VOL_WINDOW}-day window (log; line smoothed {SMOOTH}d)",
                  fontsize=11)
    axv.legend(loc="lower left", fontsize=9, borderaxespad=0.6)

    for ax in (axp, axv):
        for t in (ref_ts, inc_ts):
            ax.axvline(t, color="#7b7f8a", ls="--", lw=1)
        ax.grid(alpha=0.3, which="both")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle("DBTC/USD is calmer than the basket of currencies it is quoted in", fontsize=12, y=0.995)
    fig.text(0.5, 0.012,
             f"vertical dashed lines: reference date {REF} (law normalisation) · "
             f"inception {law['calc_date']} (law set)",
             ha="center", fontsize=9, color="#6b7380")
    fig.tight_layout(rect=(0, 0.045, 1, 0.985), h_pad=0.8)
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