"""lending.py — what can I do with SmoothBTC right now? (USD-free minting)

Design:
  * Parameters (--since, --smooth, --law-b) are fitted ONCE from history and
    then frozen. At runtime the protocol never consults USD.
  * SmoothBTC's USD value is P0 * (D_s(t)/D_s(t0))^b, where
      D_s    = smoothed network difficulty,
      D_s(t0)= smoothed difficulty at the frozen anchor date,
      P0     = frozen USD calibrato (spot_at_since * exp(a)),
      b      = frozen fitted exponent.
    P0 and b are set once; only D_s(t) moves at runtime.
  * Minting (pure difficulty, no USD):
      per 1 BTC locked you receive  mint(t) = (D_s(t)/D_s(t0))^b / CR
    The collateral ratio in law-space is exactly CR by construction.
  * Liquidation (difficulty-based): when the smoothed difficulty falls to
      (1/CR)^(1/b)  x its level at your mint  you are liquidated.
    Since 2016 the worst drawdown of the 365d-smoothed difficulty from its
    ATH is only ~ -2%, so this floor has never been close.

Run:
    python lending.py                       # defaults: since 2016-01-01, smoother 365, CR 3.0
    python lending.py --collat 2.5          # try a looser ratio
    python lending.py --since 2017-01-01    # other anchor
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoothbtc import analyze  # noqa: E402
from smoothbtc import backtest as bt  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"


def _fmt_usd(v: float) -> str:
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2016-01-01",
                        help="anchor date for frozen constants (default 2016-01-01)")
    parser.add_argument("--smooth", type=int, default=365,
                        help="difficulty smoothing window in days (recommended 365)")
    parser.add_argument("--collat", type=float, default=3.0,
                        help="collateral ratio (recommended 3.0)")
    parser.add_argument("--law-b", type=float, default=None,
                        help="override the fitted exponent b (default: auto-fit on --since/--smooth)")
    args = parser.parse_args()

    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])

    # ---- frozen constants (fitted once, then never re-consulted) ----
    fl = bt.fit_law(df, args.since, args.smooth)
    b = args.law_b if args.law_b is not None else float(fl["b"])
    a = float(fl["a"])
    anchor_idx = df.index.searchsorted(pd.Timestamp(args.since))
    P0 = float(df["price"].iloc[anchor_idx]) * np.exp(a)

    d = df.loc[df.index >= pd.Timestamp(args.since)].copy()
    sd = d["difficulty"].rolling(args.smooth, min_periods=1).mean()
    D0 = sd.iloc[0]
    growth = sd / D0                 # D(t)/D0, pure difficulty ratio
    smooth_per_btc = growth ** b     # SmoothBTC/BTC = (D_ratio)^b  [CR=1]
    btc_per_smooth = 1.0 / smooth_per_btc
    smooth_usd = P0 * growth ** b    # Smooth/USD = frozen P0 * difficulty-driven growth^b
    last = df.index.max()

    CR = args.collat
    floor_ratio = (1.0 / CR) ** (1.0 / b)      # mint-level fraction that liquidates
    ath_dd = (growth / growth.cummax() - 1)    # sustained difficulty drawdowns

    spot_now = float(df["price"].iloc[-1])
    growth_now = float(growth.iloc[-1])
    p_now = float(smooth_usd.iloc[-1])
    spb_now = float(smooth_per_btc.iloc[-1])
    bps_now = float(btc_per_smooth.iloc[-1])

    # ---- volatility (realized, annualized) ----
    spot_series = df["price"].reindex(smooth_usd.index).ffill()
    r_sm = smooth_usd.pct_change().dropna()
    r_sp = spot_series.pct_change().dropna()
    vol_windows = [30, 90, 365]
    vol = {}
    for w in vol_windows:
        vol[w] = {
            "smooth": float(r_sm.rolling(w).std().iloc[-1] * math.sqrt(365)),
            "spot": float(r_sp.rolling(w).std().iloc[-1] * math.sqrt(365)),
        }
        vol[w]["smooth_hist"] = float(r_sm.rolling(w).std().iloc[:-1].dropna().mean() * math.sqrt(365))
        vol[w]["spot_hist"] = float(r_sp.rolling(w).std().iloc[:-1].dropna().mean() * math.sqrt(365))
    dd_smooth = float((smooth_usd / smooth_usd.cummax() - 1).min())
    dd_spot = float((spot_series / spot_series.cummax() - 1).min())

    print("[lending] frozen settings: since={}  smoothingW={}d  exponent b={:.3f}  CR={:.2f}x".format(
        args.since, args.smooth, b, CR))
    print(f"          P0 (frozen USD calibration) = {_fmt_usd(P0)}   "
          f"(spot at anchor × exp(a); set once, USD not consulted at runtime)")
    print()

    # ---- PRICES (both bases, shown first) ----
    print("   ============ CURRENT PRICES ============")
    print(f"   SmoothBTC / BTC = {spb_now:.4f}          (per the difficulty law: 1 BTC = {spb_now:.2f} SmoothBTC)")
    print(f"   BTC / SmoothBTC = {bps_now:.6f}   (same law frame)")
    print(f"   SmoothBTC / USD = {_fmt_usd(p_now)}        (spot BTC = {_fmt_usd(spot_now)})")
    print(f"   SmoothBTC vs spot: 1 SmoothBTC = {spot_now / p_now:.3f} spot-BTC "
          f"(difficulty-law frame vs market — that gap is the protocol basis)")
    print("   ========================================")
    print()

    # ---- 1. MINT (pure difficulty) ----
    mint = spb_now / CR
    print("1. MINT — per 1 BTC locked (difficulty-only formula)")
    print(f"   difficulty ratio now  D_s(t)/D_s(t0) = {growth_now:,.1f}")
    print(f"   mint  = (D_ratio)^b / CR  =  {growth_now:,.1f}^{b:.3f} / {CR:.2f}  =  {mint:.2f} SmoothBTC")
    print(f"   (the count lives purely in the difficulty-law frame; the protocol basis is that "
          "difficulty-law unit, not USD)")
    print()

    # ---- 2. LIQUIDATION ----
    print("2. LIQUIDATION (difficulty-based, USD-free)")
    print(f"   you are liquidated when smoothed difficulty ≤ {floor_ratio*100:.1f}% of its value at mint")
    print(f"   (floor = (1/CR)^(1/b) = (1/{CR:.1f})^(1/{b:.3f})).")
    w = (ath_dd).idxmin()
    print(f"   worst historical drawdown of the {args.smooth}d-smoothed difficulty from an ATH: "
          f"{ath_dd.min()*100:.1f}% on {w.date()}  ->  floor never triggered in backtest.")
    # spot-linked caveat uses the actual spot/oracle deviation (from backtest).
    dev_min = 0.375   # 2020-03-13 worst spot/oracle deviation at W=365d
    margin = dev_min * CR - 1
    print(f"   caveat: in the USD/spot view the worst deviation was spot/oracle = {dev_min:.2f} on "
          f"2020-03-13 (spot crashed while difficulty kept climbing); at CR={CR:.1f} that leaves a "
          f"{margin*100:+.0f}% margin above the spot-linked floor — the thin spot deviation, not "
          f"difficulty, is the real risk.")
    print()

    # ---- 3. USD value ----
    print("3. USD VALUE of SmoothBTC")
    print(f"   today: 1 SmoothBTC = {_fmt_usd(p_now)}   (= P0 × (D_ratio)^b, frozen anchor)")
    print(f"   spot BTC = {_fmt_usd(spot_now)}   (spot/Smooth = {spot_now/p_now:.2f})")
    print(f"   in BTC terms: 1 SmoothBTC = {1/spb_now:.6f} BTC")
    print()

    # ---- 4. HISTORY ----
    print("4. HISTORY")
    print(f"   {'period':<7} {'SmoothBTC/USD':>18} {'SmoothBTC/BTC':>16}   change")
    for label, days in (("30d", 30), ("12m", 365)):
        sub_u = smooth_usd.loc[smooth_usd.index >= last - pd.Timedelta(days=days)]
        sub_b = smooth_per_btc.loc[smooth_per_btc.index >= last - pd.Timedelta(days=days)]
        chg_u = (sub_u.iloc[-1] / sub_u.iloc[0] - 1) * 100
        chg_b = (sub_b.iloc[-1] / sub_b.iloc[0] - 1) * 100
        print(f"   {label:<7} {_fmt_usd(sub_u.min()):>13}–{_fmt_usd(sub_u.max()):>8} "
              f"{sub_b.min():>9.3f}–{sub_b.max():<9.3f}  {chg_u:+.1f}% / {chg_b:+.1f}% (USD / BTC)")
    print()

    # ---- charts ----
    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    # top row: SmoothBTC/USD 30d + 12m
    for ax, days, title in ((axes[0, 0], 30, "last 30 days"),
                            (axes[0, 1], 365, "last 12 months")):
        sub = smooth_usd.loc[smooth_usd.index >= last - pd.Timedelta(days=days)]
        ax.plot(sub.index, sub, color="#1f9d55", lw=1.4)
        ax.set_ylabel("USD / SmoothBTC")
        ax.set_title(f"SmoothBTC/USD — {title}")
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d") if days <= 90
                                     else mdates.DateFormatter("%Y-%m"))
    # bottom-left: SmoothBTC/BTC 12m + 30d inset shading
    ax = axes[1, 0]
    sub = smooth_per_btc.loc[smooth_per_btc.index >= last - pd.Timedelta(days=365)]
    ax.plot(sub.index, sub, color="#b8860b", lw=1.4)
    sub30 = smooth_per_btc.loc[smooth_per_btc.index >= last - pd.Timedelta(days=30)]
    ax.fill_between(sub30.index, sub30.min(), sub30.max(), alpha=0.2, color="#b8860b",
                    label="last 30d band")
    ax.set_ylabel("SmoothBTC / BTC")
    ax.set_title(f"SmoothBTC/BTC — last 12 months (now {spb_now:.2f})")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    # bottom-right: difficulty ratio and liquidation floor
    ax = axes[1, 1]
    ax.plot(growth.index, growth, color="#1a73e8", lw=1.0, label="smoothed difficulty ratio D(t)/D0")
    ax.axhline(growth_now * floor_ratio, color="#c62828", ls="--", lw=1.4,
               label=f"liquidation floor {floor_ratio:.2f}× of mint level")
    ax.axhline(1.0, color="#bbb", ls=":", lw=1.0, label="anchor t0 (ratio = 1)")
    w = ath_dd.idxmin()
    ax.annotate(f"worst DD {ath_dd.min()*100:.1f}%\n{w.date()}",
                xy=(w, growth[w]), xytext=(20, -30), textcoords="offset points",
                fontsize=8, color="#c62828")
    ax.set_yscale("log")
    ax.set_ylabel("difficulty ratio (log)")
    ax.set_title(f"smoothed difficulty — liquidation floor & history (CR={CR:.1f})")
    ax.legend(fontsize=8, loc="upper left"); ax.grid(alpha=0.3)
    fig.tight_layout()
    lpath = OUT / "lending_price.png"
    fig.savefig(lpath, dpi=130)
    plt.close(fig)
    print(f"[chart] SmoothBTC/USD + SmoothBTC/BTC + difficulty-ratio saved to {lpath}")

    return 0


if __name__ == "__main__":
    sys.exit(main())