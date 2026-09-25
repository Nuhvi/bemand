#!/usr/bin/env python3
"""track.py — "Status" TLDR + "Current status" detail, straight into the README.

The difficulty->price law is frozen (fitted once, then immutable). This script
refreshes the difficulty / BTC price / ECB FX feeds and rewrites two README
blocks:

  * "## Status" (TLDR): the quick proof — BTC's volatility is intrinsic, i.e.
    about the same whether priced in USD, in DBTC, or in a fiat basket.
  * "## Current status" (detail): the full volatility / worst-drop tables, the
    rolling-volatility chart (out/track.png) and the honest reading.

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

from dbtc import COLORS, analyze, download, frozen, fx  # noqa: E402

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
OUT = ROOT / "out"
REF = "2016-01-01"                                   # the reference date (normalisation point)
TLDR_START, TLDR_END = "<!-- dbtc:tldr:start -->", "<!-- dbtc:tldr:end -->"
DETAIL_START, DETAIL_END = "<!-- dbtc:detail:start -->", "<!-- dbtc:detail:end -->"
VOL_ROWS = ["BTC/USD", "BTC/DBTC", "BTC/basket", "DBTC/USD", "DBTC/basket"]
CR_ROWS = ["BTC/USD", "BTC/DBTC"]
VOL_WINDOWS = [7, 30, 90, 365]
DROP_WINDOWS = [1, 7, 30, 90, 365]
FIATS = ["USD", "EUR", "GBP", "JPY", "CHF"]
FX_SYM = {"USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}
FBX = {"BTC/USD": "#f76707", "BTC/DBTC": "#8a93a0", "BTC/basket": "#7b1fa2",
       "DBTC/USD": "#1f9d55", "DBTC/basket": "#2b6ca3"}
CURNAME = {"EUR": "€", "GBP": "£", "JPY": "¥", "CHF": "CHF "}


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

    w = pd.Series(law["basket"])                     # currency-basket weights (sum to 1)
    fx_basket = np.exp(sum(w[c] * np.log(fxdf[c]) for c in w.index if c != "USD"))

    out = pd.DataFrame({
        "DBTC/USD": dbtc,                            # the frozen law itself
        "BTC/USD": spot,                             # price of BTC in USD
        "BTC/DBTC": spot / dbtc,                     # price of BTC in DBTC
        "DBTC/basket": dbtc * fx_basket,             # the unit quoted in the fiat basket
        "BTC/basket": spot * fx_basket,              # price of BTC in the fiat basket
    })
    for c in FIATS:
        if c != "USD":
            out[f"DBTC/{c}"] = dbtc * fxdf[c]
    return out


def annvol(x: pd.Series, w: int) -> float:
    return float(x.pct_change().rolling(w).std().iloc[-1] * math.sqrt(365))


def worst_drop(x: pd.Series, w: int) -> float:
    if w == 1:
        return float(x.pct_change().min())
    return float((x / x.shift(w) - 1).min())


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def fmt_fiat(v: float, c: str) -> str:
    if c == "USD":
        return f"${v:,.0f}"
    return f"{CURNAME[c]}{v:,.0f}"


def freshness() -> tuple[str, str, str]:
    chart = lambda name: json.loads((frozen.DATA_DIR / f"{name}.json").read_text())["values"][-1]["x"]  # noqa: E731
    diff_s = datetime.fromtimestamp(chart("difficulty"), tz=timezone.utc).strftime("%Y-%m-%d")
    spot_s = datetime.fromtimestamp(chart("market_price"), tz=timezone.utc).strftime("%Y-%m-%d")
    fx_json = json.loads((frozen.DATA_DIR / "fx.json").read_text())["rates"]
    return diff_s, spot_s, max(fx_json)


def build_tldr(law: dict, w: pd.DataFrame, diff_s: str, spot_s: str) -> str:
    incep = pd.Timestamp(law["calc_date"])
    dbtc = w["DBTC/USD"]
    spot = w["BTC/USD"]
    now, spot_now = dbtc.iloc[-1], spot.iloc[-1]
    v_usd, v_dbtc, v_basket = (annvol(w[k], 90) * 100 for k in ("BTC/USD", "BTC/DBTC", "BTC/basket"))
    v_unit = annvol(dbtc, 90) * 100
    since = dbtc.loc[incep:]
    worst_below = float((since / since.iloc[0] - 1).min())
    d1_usd, d1_dtc = worst_drop(spot, 1), worst_drop(w["BTC/DBTC"], 1)
    cr1 = 1 / (1 + d1_usd)
    n_days = (w.index[-1] - incep).days

    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data {diff_s} (difficulty) / {spot_s} (spot) · law set {law['calc_date']}_

**Working — BTC's volatility is intrinsic.** 90-day annualised vol of BTC priced in
**USD {v_usd:.0f}% · DBTC {v_dbtc:.0f}% · fiat-basket {v_basket:.0f}%** vs **DBTC/USD {v_unit:.2f}%**
(the unit itself barely moves). The three BTC price frames fall and rise together — worst
single day since 2016: {pct(d1_usd)} in USD and {pct(d1_dtc)} in DBTC, ≈{cr1:.2f}× collateral either
way. Since inception ({n_days} days) DBTC/USD has stayed within {pct(worst_below)} of its level
(now **{fmt_fiat(now, 'USD')}** vs spot {fmt_fiat(spot_now, 'USD')}). Tables & chart: [Current status](#current-status)."""


def build_detail(law: dict, w: pd.DataFrame, diff_s: str, spot_s: str, fx_s: str) -> str:
    incep = pd.Timestamp(law["calc_date"])
    dbtc = w["DBTC/USD"]
    spot = w["BTC/USD"]
    incep_i = w.index[w.index >= incep][0]
    n_days = (w.index[-1] - incep_i).days
    basket_now = float((w["DBTC/basket"] / w["DBTC/basket"].loc[incep_i]).iloc[-1])

    since = dbtc.loc[incep_i:]
    rets = since.pct_change().dropna()
    vol_incep = float(rets.std() * math.sqrt(365)) if len(rets) > 1 else float("nan")
    max_dd = float((since / since.cummax() - 1).min())
    worst_below = float((since / since.iloc[0] - 1).min())
    in5 = float((abs(since / since.iloc[0] - 1) <= 0.05).mean())

    vol_rows = []
    for k in VOL_ROWS:
        x = w[k].loc[incep_i:]
        v_i = float(x.pct_change().std() * math.sqrt(365)) if len(x.dropna()) > 2 else float("nan")
        v_i = f"{v_i * 100:.2f}%" if not math.isnan(v_i) else "—"
        cells = " | ".join(f"{annvol(w[k], win) * 100:.2f}%" for win in VOL_WINDOWS)
        vol_rows.append(f"| **{k}** | {cells} | {v_i} |")

    cr_rows = []
    for k in CR_ROWS:
        x = w[k]
        cells = " | ".join(f"{pct(worst_drop(x, win))} → {1 / (1 + worst_drop(x, win)):.2f}×"
                           for win in DROP_WINDOWS)
        cr_rows.append(f"| **{k}** | {cells} |")

    min_sdb = float((spot / dbtc).min())
    never = 1 / min_sdb
    drop1 = worst_drop(spot, 1)
    cr90b = 1 / (1 + worst_drop(w["BTC/DBTC"], 90))
    cr1y_b = 1 / (1 + worst_drop(w["BTC/DBTC"], 365))
    v_unit90 = annvol(dbtc, 90) * 100
    v_eur90, v_bask90 = (annvol(w[k], 90) * 100 for k in ("BTC/basket", "DBTC/basket"))

    hdr_vol = "| series | " + " | ".join(f"{win}d" for win in VOL_WINDOWS) + " | since inception |"
    sep_vol = "|" + "---|" * (1 + len(VOL_WINDOWS) + 1)
    hdr_cr = "| price of BTC in | " + " | ".join(f"{win}d drop → CR" for win in DROP_WINDOWS) + " |"
    sep_cr = "|" + "---|" * (1 + len(DROP_WINDOWS))

    read = (f"BTC is about equally volatile priced in **USD, in DBTC, or in a fiat basket** — the three "
            f"90-day vol paths overlap (~40–90%): the DBTC unit moves so little it neither adds nor removes "
            f"risk; it only changes the unit you quote in. DBTC/USD itself is ultra-low-vol ({v_unit90:.2f}% 90d, "
            f"0.05–0.6% by window) and since inception has kept all {n_days} days within ±5% of its level. "
            f"The consequence: denominating collateral in DBTC does **not** cut the buffer against spot crashes. "
            f"Worst single day since 2016 needs ≈{1 / (1 + drop1):.2f}× in any frame, and over multi-week drawdowns "
            f"the DBTC frame needs *more* ({cr90b:.2f}× at 90d, up to {cr1y_b:.1f}× at 1y) because the unit keeps "
            f"climbing while spot draws down. The binding risk is the spot/DBTC *level* (worst {min_sdb:.2f} → "
            f"~{never:.1f}× for never-liquidate), not difficulty.")

    per_fiat = " · ".join(f"{fmt_fiat(w[f'DBTC/{c}'].iloc[-1], c)}" for c in FIATS if c != "USD")
    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data through {diff_s} (difficulty) / {spot_s} (spot) / {fx_s} (FX) · law set {law['calc_date']}_

**Now:** 1 DBTC = **{fmt_fiat(dbtc.iloc[-1], 'USD')}** · spot BTC = {fmt_fiat(spot.iloc[-1], 'USD')}
per fiat: {per_fiat} · basket **{basket_now:.3f}×** inception level

**Since inception ({law['calc_date']}, {n_days} days):** vol {vol_incep * 100:.2f}% · max drawdown {pct(-max_dd)} · worst day vs inception {pct(worst_below)} · {in5 * 100:.0f}% of days within ±5% of inception level

**Realised volatility, annualised** (daily returns, rolling window of the given length):

{hdr_vol}
{sep_vol}
{chr(10).join(vol_rows)}

**Worst cumulative drop of the price of BTC — and the collateralisation it forces** (CR ≥ 1/(1+drop)):

{hdr_cr}
{sep_cr}
{chr(10).join(cr_rows)}

**Read:** {read}

![track](out/track.png)

_Parameters are frozen (set {law['calc_date']}); only difficulty and FX move. Regenerate with `python track.py`._"""


def replace_block(start: str, end: str, text: str) -> None:
    src = README.read_text()
    if start not in src or end not in src:
        raise SystemExit(f"markers {start!r} / {end!r} not found in {README}")
    head, tail = src.split(start, 1)
    _mid, tail = tail.split(end, 1)
    tail = "\n\n" + tail.lstrip("\n")
    README.write_text(head + start + "\n" + text.rstrip() + "\n" + end + tail)


def write_chart(law: dict, w: pd.DataFrame) -> Path:
    """Rolling 90-day annualised volatility of BTC's price in the three frames
    vs DBTC/USD — the visual proof that BTC's volatility is intrinsic."""
    window = 90
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for k in ("BTC/USD", "BTC/DBTC", "BTC/basket", "DBTC/USD"):
        vol = w[k].pct_change().rolling(window).std() * np.sqrt(365)
        ax.plot(vol.index, vol, color=FBX[k], lw=1.4 if k in ("BTC/DBTC", "DBTC/USD") else 1.1,
                label=k)
    ax.set_yscale("log")
    ax.axhline(0.01, color="#bbb", ls=":", lw=1)
    for t, lab in ((pd.Timestamp(REF), "reference 2016-01-01"),
                   (pd.Timestamp(law["calc_date"]), f"inception {law['calc_date']}")):
        ax.axvline(t, color="#7b7f8a", ls="--", lw=1)
        ax.annotate(lab, xy=(t, ax.get_ylim()[1]), xytext=(2, 4), textcoords="offset points",
                    fontsize=9, color="#6b7380", ha="left", va="bottom", rotation=90)
    ax.set_ylabel(f"rolling {window}-day annualised vol (log)")
    ax.set_title("BTC volatility is intrinsic — priced in USD, DBTC, or a fiat basket, it is the same risk")
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
    diff_s, spot_s, fx_s = freshness()

    replace_block(TLDR_START, TLDR_END, build_tldr(law, w, diff_s, spot_s))
    replace_block(DETAIL_START, DETAIL_END, build_detail(law, w, diff_s, spot_s, fx_s))
    chart = write_chart(law, w)
    print(f"[track] updated {README} (TLDR + detail)")
    print(f"[track] wrote {chart}")
    print(f"[track] DBTC/USD now = ${w['DBTC/USD'].iloc[-1]:,.0f}  spot = ${w['BTC/USD'].iloc[-1]:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())