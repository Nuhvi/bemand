"""Backtest CLI: run the "what if we launched N years ago" simulator.

Usage:
    python backtest.py                     # 10y horizon from today-10y
    python backtest.py --years 5           # 5y horizon
    python backtest.py --launch 2016-08-22 # 10y launch on a fixed date

Writes summary tables to stdout and PNG charts to out/backtest/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbtc import analyze  # noqa: E402
from dbtc import backtest as bt  # noqa: E402
from dbtc import COLORS, shade  # noqa: E402

OUT = Path(__file__).resolve().parent / "out" / "backtest"


def _save(fig, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _log_grid(ax) -> None:
    """Log y-grid with visible minor-tick labels so every horizontal line is readable."""
    from matplotlib.ticker import LogLocator, FormatStrFormatter
    ax.grid(which="both", alpha=0.3)
    ax.minorticks_on()
    loc = LogLocator(base=10, subs=(1.0, 2.0, 5.0))
    ax.yaxis.set_minor_locator(loc)
    ax.yaxis.set_minor_formatter(FormatStrFormatter("%g"))
    plt.setp(ax.get_yticklabels(minor=True), visible=True, fontsize=7)


def _derived_series(merch: pd.DataFrame, sal: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Month-end USD value of a $1 DBTC float (merchant) & salary income."""
    return merch["float_usd"], sal["income_usd"]


def chart_values(models: dict[str, bt.ValueModel], launch: str) -> Path:
    fig, (ax, av) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1]})
    for mkey, m in models.items():
        ax.plot(m.series.index, m.series, lw=1.2, label=m.name, color=COLORS[mkey])
    ax.set_yscale("log")
    ax.axvline(pd.Timestamp(launch), color="#000", ls="--", lw=1, label="launch")
    ax.set_ylabel("USD per 1 DBTC (log)")
    ax.set_title("candidate USD prices for 1 DBTC")
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(alpha=0.3)

    # Volatility comparison across the candidate USD prices.
    for mkey in ("dbtc", "wma", "wma200", "spot"):
        r = models[mkey].series.pct_change().rolling(90, min_periods=45).std() * np.sqrt(365.25)
        av.plot(r.index, r, lw=1.1, color=COLORS[mkey],
                label=f"{models[mkey].name} — 90d ann. vol")
    av.set_yscale("log")
    av.set_ylabel("volatility (90d window, ann., log)")
    av.set_title("rolling volatility: all models")
    av.legend(fontsize=8, loc="upper left")
    av.grid(alpha=0.3, which="both")
    return _save(fig, "01_values.png")


def chart_merchant(models: dict[str, bt.ValueModel], df: pd.DataFrame, launch: str,
                   n_months: int) -> Path:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    m = models["dbtc"]
    merch = bt.merchant_cashflow(m, df, launch, n_months)
    a1.plot(merch.index, merch["float_usd"] * 100, color=COLORS["dbtc"], lw=1.3,
            label="DBTC ($1 float, USD value)")
    a1.axhline(0, color="#bbb", lw=1.2)
    a1.set_ylabel("float value (USD cents)")
    a1.set_title("merchant accepting DBTC ($1/mo revenue, $1/mo USD restock)")
    a1.legend(fontsize=8, loc="upper left"); a1.grid(alpha=0.3)

    ride = {"dbtc": merch}
    for k in ("spot", "wma"):
        mk = bt.merchant_cashflow(models[k], df, launch, n_months)
        ride[k] = mk
    for k, mk in ride.items():
        a2.plot(mk.index, mk["wealth"], color=COLORS[k], lw=1.2, label=f"{k}: wealth={mk['wealth'].iloc[-1]:.0f}")
    a2.plot(merch.index, merch["usdt_wealth"], color="#bbb", lw=1.2, label="USDT baseline")
    a2.legend(fontsize=8, loc="upper left")
    a2.set_ylabel("USD wealth (cum. spend + float)")
    a2.set_title("merchant wealth by pricing model")
    a2.grid(alpha=0.3)
    return _save(fig, "02_merchant.png")


def chart_salary(models: dict[str, bt.ValueModel], df: pd.DataFrame, launch: str,
                 n_months: int) -> Path:
    m_or = models["dbtc"]
    sal_yearly = bt.salary_cashflow(m_or, df, launch, n_months, renew=True)
    sal_fixed = bt.salary_cashflow(m_or, df, launch, n_months, renew=False)
    m_sp = models["spot"]
    sal_spot = bt.salary_cashflow(m_sp, df, launch, n_months, renew=False)

    c_dbtc, c_dbtc_l = COLORS["dbtc"], shade(COLORS["dbtc"])
    c_spot = COLORS["spot"]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    a1.plot(sal_yearly.index, sal_yearly["income_usd"], color=c_dbtc, marker="o", ms=3,
            label="DBTC, yearly re-sign")
    a1.plot(sal_fixed.index, sal_fixed["income_usd"], color=c_dbtc_l, marker="o", ms=3,
            label="DBTC, fixed 10y")
    a1.axhline(1.0, color="#bbb", lw=1.2, label="USDT baseline")
    a1.set_yscale("log")
    a1.set_ylabel("USD received / mo (log)")
    a1.set_title("salary: fixed DBTC/month, yearly-re-sign vs fixed contract")
    a1.legend(fontsize=8, loc="upper left")
    _log_grid(a1)

    a2.plot(sal_spot.index, sal_spot["income_usd"], color=c_spot, marker="o", ms=3,
            label="BTC-spot (fixed)")
    a2.plot(sal_fixed.index, sal_fixed["income_usd"], color=c_dbtc_l, marker="o", ms=3,
            label="DBTC (fixed)")
    a2.plot(sal_yearly.index, sal_yearly["income_usd"], color=c_dbtc, marker="o", ms=3,
            label="DBTC (yearly re-sign)")
    a2.axhline(1.0, color="#bbb", lw=1.2, label="USDT")
    a2.set_xlabel("month end")
    a2.set_yscale("log")
    a2.set_ylabel("USD received / mo (log)")
    a2.set_title("candidate price models")
    a2.legend(fontsize=8, loc="upper left")
    _log_grid(a2)
    return _save(fig, "03_salary.png")


def chart_table(summaries: dict[str, dict]) -> Path:
    def short(k: str) -> str:
        # merchant:dbtc  ->  merch/DBTC ...
        return "/".join(k.split(":"))
    rows = [
        (short(k), f"{v['ratio_vs_usdt']:.2f}x", f"{v['max_rel_dd']*100:.0f}%",
         f"{v['monthly_min']:.2f}", f"{v['monthly_max']:.0f}", f"{v['monthly_std']:.1f}")
        for k, v in sorted(summaries.items())
    ]
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.axis("off")
    tbl = ax.table(cellText=rows,
                   colLabels=["scenario", "vs USDT", "max DD", "min $/mo", "max $/mo", "std $/mo"],
                   loc="center", cellLoc="center", colWidths=[0.2, 0.12, 0.12, 0.15, 0.15, 0.15])
    tbl.auto_set_font_size(False); tbl.set_fontsize(9)
    tbl.scale(1.4, 1.5)
    ax.set_title("backtest summary — all USD normalised to $1/mo USDT baseline")
    return _save(fig, "04_table.png")


def chart_float_sensitivity(df: pd.DataFrame, launch: str, n_months: int) -> Path:
    """Merchant result vs working-capital float size (DBTC and spot pricing)."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    floats = [0.25, 0.5, 1.0, 2.0, 3.0, 6.0]
    models = bt.build_value_models(df, launch)
    for mkey in ("dbtc", "spot"):
        ratios, dds = [], []
        for fm in floats:
            m = models[mkey]
            mk = bt.merchant_cashflow(m, df, launch, n_months, float_months=fm)
            s = bt.summary(mk, f"merchant:{mkey}")
            ratios.append(s["ratio_vs_usdt"])
            dds.append(s["max_rel_dd"] * 100)
        a1.plot(floats, ratios, "o-", color=COLORS[mkey], label=mkey)
        a2.plot(floats, dds, "o-", color=COLORS[mkey], label=mkey)
    a1.axhline(1, color="#bbb", ls="--")
    a1.set_xscale("log")
    a1.set_xlabel("working-capital float (months of spend)")
    a1.set_ylabel("merchant wealth vs USDT (x)")
    a1.legend(); a1.grid(alpha=0.3)
    a2.axhline(0, color="#bbb", ls="--")
    a2.set_xscale("log")
    a2.set_xlabel("working-capital float (months of spend)")
    a2.set_ylabel("max drawdown of buffer (%)")
    a2.legend(); a2.grid(alpha=0.3)
    a1.set_title("merchant: bigger float magnifies both win and risk")
    return _save(fig, "05_float_sensitivity.png")


def chart_cumulative(models: dict[str, bt.ValueModel], df: pd.DataFrame, launch: str,
                     n_months: int) -> Path:
    """Cumulative USD received vs USDT baseline, salary scenarios."""
    fig, ax = plt.subplots(figsize=(11, 5))
    styles = [("dbtc", "fixed", COLORS["dbtc"], "-"), ("dbtc", "yearly", shade(COLORS["dbtc"]), "--"),
              ("spot", "fixed", COLORS["spot"], "-"), ("spot", "yearly", shade(COLORS["spot"]), "--")]
    for mkey, tag, col, ls in styles:
        m = models[mkey]
        sal = bt.salary_cashflow(m, df, launch, n_months, renew=(tag == "yearly"))
        ax.plot(sal.index, sal["cumulative"] / sal["usdt_cum"], color=col, ls=ls, lw=1.4,
                label=f"{mkey}/{tag}")
    ax.axhline(1, color="#bbb", ls="--", label="USDT (1.0x)")
    ax.set_yscale("log")
    ax.set_ylabel("cumulative received vs USDT (multiple, log)")
    ax.set_title("salary: cumulative USD vs USDT baseline (1.0x = parity)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    return _save(fig, "06_cumulative.png")


def chart_w_sweep_merchant(df, since, bs=(0.53, 0.73), window="ME"):
    """Per iteration window W: merchant worst-month / worst-12m / max drawdown."""
    wpds = [4, 8, 13, 20, 26, 39, 52, 78, 104]
    fig, ((a1, a2), (a3, a4)) = plt.subplots(2, 2, figsize=(12, 8))
    for b in bs:
        worst_m, worst12, last_price = [], [], []
        for w in wpds:
            mm = bt.merchant_loss_metrics(df, smooth=w, since=since, b=b)
            worst_m.append(mm["worst_month"] * 100)
            worst12.append(mm["worst_12m_ann"] * 100)
            last_price.append(mm["end_price"])
        a1.plot(wpds, worst_m, "o-", label=f"b={b}")   # lower (less negative) = better
        a2.plot(wpds, worst12, "o-", label=f"b={b}")
        a3.plot(wpds, last_price, "o-", label=f"b={b}")
    a1.axhline(0, color="#bbb", ls="--")
    a1.set_yscale("symlog", linthresh=1)
    a1.set_title("worst month return"); a1.set_xscale("log"); a1.grid(alpha=0.3)
    a2.set_title("worst 12-month return"); a2.set_xscale("log"); a2.grid(alpha=0.3)
    a3.set_title("terminal DBTC price (USD)"); a3.set_xscale("log"); a3.grid(alpha=0.3)
    a4.plot(wpds, [bt.collateral_metrics(df, smooth=w, since=since, b=bs[0])["collat_never"] for w in wpds],
            "o-", label=f"b={bs[0]} never")
    a4.plot(wpds, [bt.collateral_metrics(df, smooth=w, since=since, b=bs[1])["collat_never"] for w in wpds],
            "o-", label=f"b={bs[1]} never")
    a4.set_title("collateral multiple to never liquidate (spot/DBTC)"); a4.set_xscale("log"); a4.grid(alpha=0.3)
    for ax in (a1, a2, a3, a4):
        ax.legend(fontsize=8)
    a4.set_xlabel("smoothing window W (difficulty periods)")
    fig.suptitle(f"merchant-loss & collateral vs smoothing window (since={pd.Timestamp(since).date()})")
    return _save(fig, "07_w_sweep.png")


def chart_merchant_window(df, since, b=0.73):
    """Heatmap of worst monthly return across W (drives merchant safety)."""
    wpds = [4, 8, 13, 20, 26, 39, 52, 78]
    vals = [bt.merchant_loss_metrics(df, smooth=w, since=since, b=b)["worst_month"] * 100 for w in wpds]
    fig, ax = plt.subplots(figsize=(9, 4))
    colors = ["#c62828" if v < -5 else ("#f9a825" if v < -1 else "#43a047") for v in vals]
    ax.bar([str(w) for w in wpds], vals, color=colors)
    ax.axhline(0, color="#000", lw=1)
    ax.set_ylabel("worst month %"); ax.set_xlabel("smoothing W (difficulty periods)")
    ax.set_title(f"merchant worst-month return by smoothing (b={b}, since {pd.Timestamp(since).date()})")
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, "08_merchant_window.png")


def chart_collateral_ts(df, since, smooth=26, b=0.73):
    """Spot/DBTC collateral ratio over time, marking the binding low."""
    P = bt.dbtc_series(df, since, smooth, b)
    spot = df["price"].reindex(P.index).ffill()
    rn = (spot / P) / (spot.iloc[0] / P.iloc[0])
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(rn.index, rn, lw=1.1, color=COLORS["spot"])
    ax.axhline(1.0, color="#000", lw=1, label="vault baseline (1.0)")
    bd = rn.idxmin()
    ax.axvline(bd, color="#c62828", ls="--", lw=1.2,
               label=f"binding low {bd.date()} ({rn.min():.2f})")
    ax.set_ylabel("collateral ratio spot/DBTC (normalised)")
    ax.set_title(f"collateral ratio over time — need ~{1/rn.min():.1f}x to cover the floor (b={b}, W={smooth}p)")
    ax.legend(fontsize=8); ax.grid(alpha=0.3); ax.set_yscale("log")
    return _save(fig, "09_collateral_ts.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--launch", default=None,
                        help="launch date (default: today-Y years)")
    parser.add_argument("--since", default="2016-01-01",
                        help="data window used to fit & evaluate (default 2016-01-01, drops pre-2013 + 2013-15 drift)")
    parser.add_argument("--smooth", type=int, default=26,
                        help="difficulty smoothing window in periods of 2016 blocks (default 26 ≈ 1 year)")
    parser.add_argument("--law-b", type=float, default=None,
                        help="override the fitted exponent b (default: auto-fit on --since)")
    parser.add_argument("--t0", default=None, help="deprecated; use --since")
    args = parser.parse_args()
    if args.t0:
        args.since = args.t0

    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])

    # Regime fit table (data-driven exponent choice).
    print("[regime fit]  fitted difficulty->price law per start date:")
    for s in ("2010-07-18", "2013-01-01", "2014-01-01", "2016-01-01"):
        fl = bt.fit_law(df, s)
        print(f"  since={s:10s}  b={fl['b']:.3f}  R2={fl['r2']:.3f}  mederr={fl['mederr']*100:.0f}%  n={fl['n']}")

    since = args.since
    if args.law_b is None:
        b = bt.fit_law(df, since)["b"]
    else:
        b = args.law_b
    print(f"\n[config] since={since}  smoothW={args.smooth}p  law exponent b={b:.3f}")

    # Merchant answer.
    mm = bt.merchant_loss_metrics(df, args.smooth, since, b)
    cm = bt.collateral_metrics(df, args.smooth, since, b)
    print(f"\n[merchant, USD costs, W={args.smooth}p]"
          f"  worst month={mm['worst_month']*100:.1f}%  worst 12m={mm['worst_12m_ann']*100:.1f}%  "
          f"months<launch={mm['frac_below_launch']*100:.0f}%  maxDD={mm['max_price_drawdown']*100:.1f}%")
    print(f"[collateral spot/DBTC, W={args.smooth}p]"
          f"  min={cm['min']:.3f} (bind {cm['bind_date']})  p1={cm['p1']:.3f}  "
          f"need ≥{cm['collat_never']:.1f}x to never liquidate, ≥{cm['collat_p1']:.1f}x at p1")

    # Existing scenario sims use the same law.
    launch = pd.Timestamp(args.launch) if args.launch else (
        pd.Timestamp(df.index.max() - pd.Timedelta(days=1)) - pd.DateOffset(years=args.years))
    launch = pd.Timestamp(launch).normalize().replace(day=1)
    n_months = int((df.index.max() - launch).days // 30)
    print(f"\n[backtest] launch={launch.date()}  months={n_months}")

    models = bt.build_value_models(df, launch, diff_w=args.smooth, a=0.0, b=b)
    for k, m in models.items():
        print(f"  model {k:>6}: {m.series.iloc[0]:>10,.2f} -> {m.series.iloc[-1]:>12,.2f} "
              f"USD/S-BTC ({m.series.iloc[-1]/m.series.iloc[0]:>10.1f}x)")

    res = bt.run_all(df, launch, n_months, b=b, smooth=args.smooth)
    for k in sorted(res):
        v = res[k]
        print(f"\n  {v['scenario']:<28} vs USDT={v['ratio_vs_usdt']:>6.2f}x "
              f"maxDD={v['max_rel_dd']*100:>6.1f}%  $/mo [min/max]={v['monthly_min']:>6.2f}/{v['monthly_max']:>7.2f} "
              f"std={v['monthly_std']:>5.2f}")

    paths = [chart_values(models, launch),
             chart_merchant(models, df, launch, n_months),
             chart_salary(models, df, launch, n_months),
             chart_table(res),
             chart_float_sensitivity(df, launch, n_months),
             chart_cumulative(models, df, launch, n_months),
             chart_w_sweep_merchant(df, since, bs=(0.53, 0.73)),
             chart_merchant_window(df, since, b),
             chart_collateral_ts(df, since, args.smooth, b)]
    print(f"\n[charts] wrote {len(paths)} to {OUT}/")
    for p in paths:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())