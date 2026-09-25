#!/usr/bin/env python3
"""track.py — "is the model holding?" — regenerate the README's live summary.

Frozen difficulty->price law, live difficulty + ECB FX data in, a compact
risk summary out. Regenerates the section between the markers in README.md
and writes out/track.png. Nothing on this page refits the law.

Usage:
    python track.py                # refresh stale feeds, then update README
    python track.py --force        # force re-download of all feeds
    python track.py --no-refresh   # use existing caches only
"""

from __future__ import annotations

import argparse
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
START, END = "<!-- dbtc:track:start -->", "<!-- dbtc:track:end -->"
ANCHOR = "2016-01-01"
VOL_ROWS = ["DBTC/USD", "DBTC/basket", "DBTC/EUR", "BTC/USD", "BTC/DBTC"]
CR_ROWS = ["BTC/USD", "BTC/DBTC"]
VOL_WINDOWS = [7, 30, 90, 365]
DROP_WINDOWS = [1, 7, 30, 90, 365]
FIATS = ["USD", "EUR", "GBP", "JPY", "CHF"]
FX_SYM = {"USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}


def refresh(force: bool) -> None:
    if force or download.stale_files():
        print("[track] refreshing difficulty / market-price feeds …")
        download.all_charts(force=force)
    _ = fx.load(force=force)


def load_series() -> tuple[pd.DataFrame, dict]:
    law = frozen.load()
    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])
    sd = analyze.sliding_smoothed_diff(df, law["smooth"])
    dbtc = law["P0"] * (sd / law["D0"]) ** law["b"]
    s = df.loc[df.index >= pd.Timestamp(ANCHOR)]
    dbtc = dbtc.reindex(s.index)
    spot = s["price"]
    fxdf = fx.load().reindex(s.index).ffill()

    dbtc_cur = {"USD": dbtc}
    for c in FIATS:
        if c != "USD":
            dbtc_cur[c] = dbtc * fxdf[c]

    w = pd.Series(law["basket"])
    basket = np.exp(sum(w[c] * np.log(dbtc_cur[c]) for c in w.index))

    out = {"DBTC/USD": dbtc, "DBTC/basket": pd.Series(basket, index=s.index), "BTC/USD": spot}
    for c in FIATS:
        if c != "USD":
            out[f"DBTC/{c}"] = dbtc_cur[c]
    out["BTC/DBTC"] = spot / dbtc            # price of BTC quoted in DBTC
    out["DBTC/BTC"] = dbtc / spot            # DBTC per BTC
    return pd.DataFrame(out)


def annvol(x: pd.Series, w: int) -> float:
    return float(x.pct_change().rolling(w).std().iloc[-1] * math.sqrt(365))


def worst_drop(x: pd.Series, w: int) -> float:
    if w == 1:
        return float(x.pct_change().min())
    return float((x / x.shift(w) - 1).min())


def fmt_fiat(v: float, c: str) -> str:
    if math.isnan(v):
        return "—"
    return f"{FX_SYM[c]}{v:,.0f}"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def build_section(law: dict, w: pd.DataFrame,
                  diff_last: str, spot_last: str, fx_last: str) -> str:
    incep = pd.Timestamp(law["calc_date"])
    dbtc_usd = w["DBTC/USD"]
    spot = w["BTC/USD"]
    incep_i = w.index[w.index >= incep][0]
    n_days = (w.index[-1] - incep_i).days
    now = dbtc_usd.iloc[-1]
    spot_now = spot.iloc[-1]
    dbtc_per_btc = (dbtc_usd / spot).iloc[-1]

    since = dbtc_usd.loc[incep_i:]
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
        cells = " | ".join(
            f"{pct(worst_drop(x, win))} → {1 / (1 + worst_drop(x, win)):.2f}×"
            for win in DROP_WINDOWS)
        cr_rows.append(f"| **{k}** | {cells} |")

    min_sdb = float((spot / dbtc_usd).min())
    never = 1 / min_sdb
    basket_now = float((w["DBTC/basket"] / w["DBTC/basket"].loc[incep_i]).iloc[-1])

    drop1 = worst_drop(spot, 1)
    cr1 = 1 / (1 + drop1)
    cr90b = 1 / (1 + worst_drop(w["BTC/DBTC"], 90))
    cr1y_b = 1 / (1 + worst_drop(w["BTC/DBTC"], 365))
    vol_usd90, vol_eur90, vol_bask90 = (annvol(w[k], 90) * 100 for k in
                                        ("DBTC/USD", "DBTC/EUR", "DBTC/basket"))

    read = (f"DBTC is ultra-low-vol **in USD** ({vol_usd90:.2f}% 90d) but in any fiat numeraire it inherits "
            f"that fiat's FX volatility (EUR ≈ {vol_eur90:.0f}%, basket ≈ {vol_bask90:.0f}%). BTC is equally volatile priced "
            f"in USD or in DBTC (the two frames move 1:1 on any day because DBTC barely moves). On the worst single day in "
            f"10 years both frames fell {drop1 * 100:.0f}% — each needs ~{cr1:.2f}× collateral. Over longer windows the DBTC "
            f"frame needs *more* ({cr90b:.2f}× at 90d, up to {cr1y_b:.1f}× at 1y) because the smooth unit keeps climbing while "
            f"spot draws down. Denominating collateral in DBTC does **not** cut the buffer against spot crashes — the binding "
            f"risk is the spot/DBTC *level* (worst {min_sdb:.2f} → ~{never:.1f}× for never-liquidate), not difficulty.")

    hdr_vol = "| series | " + " | ".join(f"{win}d" for win in VOL_WINDOWS) + " | freeze→now |"
    sep_vol = "|" + "---|" * (1 + len(VOL_WINDOWS) + 1)
    hdr_cr = "| price of BTC in | " + " | ".join(f"{win}d drop → CR" for win in DROP_WINDOWS) + " |"
    sep_cr = "|" + "---|" * (1 + len(DROP_WINDOWS))

    return f"""\
_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · data through {diff_last} (difficulty) / {spot_last} (spot) / {fx_last} (FX) · law frozen {law['calc_date']}_

**Now:** 1 DBTC = **{fmt_fiat(now, "USD")}** · spot BTC = {fmt_fiat(spot_now, "USD")}
per fiat: {fmt_fiat(w['DBTC/EUR'].iloc[-1], 'EUR')} · {fmt_fiat(w['DBTC/GBP'].iloc[-1], 'GBP')} · {fmt_fiat(w['DBTC/JPY'].iloc[-1], 'JPY')} · {fmt_fiat(w['DBTC/CHF'].iloc[-1], 'CHF')} · basket **{basket_now:.3f}×** freeze level

**Since freeze ({law['calc_date']}, {n_days} days):** vol {vol_incep * 100:.2f}% · max drawdown {pct(-max_dd)} · worst day vs freeze {pct(worst_below)} · {in5 * 100:.0f}% of days within ±5% of freeze level

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

_Parameters are frozen; only difficulty and FX move. Regenerate this block with `python track.py`._
"""


def update_readme(text: str) -> Path:
    src = README.read_text()
    if START not in src or END not in src:
        raise SystemExit(f"markers {START!r} / {END!r} not found in {README}")
    head, tail = src.split(START, 1)
    _mid, tail = tail.split(END, 1)
    tail = "\n\n" + tail.lstrip("\n")
    README.write_text(head + START + "\n" + text.rstrip() + "\n" + END + tail)
    return README


def write_chart(law: dict, w: pd.DataFrame) -> Path:
    dbtc = w["DBTC/USD"]
    spot = w["BTC/USD"]
    anchor = pd.Timestamp(ANCHOR)
    freeze = pd.Timestamp(law["calc_date"])
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(dbtc.index, dbtc, color=COLORS["dbtc"], lw=1.4, label="DBTC/USD (frozen law)")
    ax.plot(spot.index, spot, color=COLORS["spot"], lw=1.0, label="spot BTC/USD")
    ax.set_yscale("log")
    ax.axvspan(anchor, freeze, color=COLORS["dbtc"], alpha=0.06)
    for t, lab in ((anchor, "anchor 2016-01-01"), (freeze, f"law frozen {law['calc_date']}")):
        ax.axvline(t, color="#7b7f8a", ls="--", lw=1)
        ax.annotate(lab, xy=(t, ax.get_ylim()[1]), xytext=(2, 4), textcoords="offset points",
                    fontsize=9, color="#6b7380", ha="left", va="bottom", rotation=90)
    ax.set_ylabel("USD (log)")
    ax.set_title("DBTC/USD (frozen law) vs spot — since the 2016 anchor")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "track.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def feed_dates() -> tuple[str, str, str]:
    """Freshness line: last dates of the cached difficulty, spot and FX feeds."""
    import json as _json

    chart = lambda name: _json.loads((frozen.DATA_DIR / f"{name}.json").read_text())["values"][-1]["x"]  # noqa: E731
    diff_s = datetime.fromtimestamp(chart("difficulty"), tz=timezone.utc).strftime("%Y-%m-%d")
    spot_s = datetime.fromtimestamp(chart("market_price"), tz=timezone.utc).strftime("%Y-%m-%d")
    fx_json = _json.loads((frozen.DATA_DIR / "fx.json").read_text())["rates"]
    return diff_s, spot_s, max(fx_json)


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
    diff_s, spot_s, fx_last = feed_dates()

    text = build_section(law, w, diff_s, spot_s, fx_last)
    update_readme(text)
    chart = write_chart(law, w)
    print(f"[track] updated {README}")
    print(f"[track] wrote {chart}")
    print(f"[track] DBTC/USD now = ${w['DBTC/USD'].iloc[-1]:,.0f}  spot = ${w['BTC/USD'].iloc[-1]:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())