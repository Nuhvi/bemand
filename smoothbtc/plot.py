"""Chart generation: write a set of PNGs into out/ on every run."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import analyze
from . import COLORS  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"


def _save(fig, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def chart_raw(df: pd.DataFrame) -> Path:
    fig, (a1, a2) = plt.subplots(2, 1, sharex=True, figsize=(11, 8))
    a2.plot(df.index, df["difficulty"], color=COLORS["sbtc"], lw=0.9)
    a2.set_yscale("log")
    a2.set_ylabel("network difficulty (log)")
    a2.set_title("raw data (since 2009)")
    a2.grid(alpha=0.3)

    a1.plot(df.index, df["price"], color=COLORS["spot"], lw=0.9)
    a1.set_yscale("log")
    a1.set_ylabel("BTC price USD (log)")
    a1.legend(["price (spot)"], loc="upper left")
    a1.grid(alpha=0.3)

    a2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    return _save(fig, "01_raw.png")


def chart_smoothed_vs_price(df: pd.DataFrame, best_w: int, t0: str) -> Path:
    """Normalised smoothed-difficulty ratios vs normalised price."""
    sub = analyze._prep_ratios(df, best_w, t0)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(sub.index, sub["lr"], color=COLORS["spot"], lw=1.1, label="ln(price/price_t0)")
    ax.plot(sub.index, sub["ld"], color=COLORS["sbtc"], lw=1.1, label=f"ln(diff_sm{best_w}/diff_sm{best_w}_t0)")
    ax.legend(loc="upper left")
    ax.set_title(f"normalised log-ratios vs t0={t0}  (smoothing window={best_w}d)")
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    return _save(fig, "02_ratios_log.png")


def chart_regression(df: pd.DataFrame, best: analyze.WindowResult, t0: str) -> Path:
    sub = analyze._prep_ratios(df, best.window, t0)
    ld = sub["ld"].to_numpy()
    lr = sub["lr"].to_numpy()

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(ld, lr, s=6, alpha=0.35, color="#1a73e8", label="daily obs")
    xr = np.linspace(ld.min(), ld.max(), 100)
    ax.plot(xr, best.intercept + best.slope * xr, color="#1f9d55", lw=2,
            label=f"fit b={best.slope:.3f}, a={best.intercept:.3f}, R2={best.r_squared:.3f}")
    ax.axline((0, 0), slope=1, color="#777", ls="--", lw=1,
              label="perfect 1:1 (a=0,b=1)")
    ax.set_xlabel("ln(diff_ratio_t0)")
    ax.set_ylabel("ln(price_ratio_t0)")
    ax.set_title(f"regression ln(price_ratio) ~ ln(diff_ratio)  [window={best.window}d]")
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, "03_regression.png")


def chart_window_sweep(results: list[analyze.WindowResult]) -> Path:
    fr = analyze.to_frame(results)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    a1.plot(fr.index, fr["corr_log"], "o-", color="#1c77e8")
    a1.set_ylabel("corr(ld,lr)")
    a1.axhline(0, color="#000", lw=0.8)
    a1.grid(alpha=0.3)

    a2.plot(fr.index, fr["median_rel_err"] * 100, "o-", color="#c62828")
    a2.set_ylabel("median |err| %")
    a2.grid(alpha=0.3)
    a2.set_xscale("log")
    a2.set_xlabel("smoothing window (days)")
    a2.set_title("fit quality vs difficulty-smoothing window")
    return _save(fig, "04_window_metrics.png")


def chart_price_space(df: pd.DataFrame, best: analyze.WindowResult, t0: str) -> Path:
    """Overlay model-predicted price path on actual BTC price (log scale)."""
    sub = analyze._prep_ratios(df, best.window, t0)
    ref_p = sub.iloc[0]["price"]
    pred_p = ref_p * np.exp(best.intercept + best.slope * sub["ld"])
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(sub.index, sub["price"], lw=1.0, color=COLORS["spot"], label="actual BTC price")
    ax.plot(sub.index, pred_p, lw=1.0, color=COLORS["sbtc"], ls="--",
            label=f"difficulty proxy: price_t0*exp(a+b*ld), W={best.window}d (a={best.intercept:.2f}, b={best.slope:.2f})")
    ax.set_yscale("log")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("price-proxy from smoothed difficulty (log scale)")
    ax.grid(alpha=0.3)
    return _save(fig, "05_price_proxy.png")


def chart_deviation(df: pd.DataFrame, best: analyze.WindowResult, t0: str) -> Path:
    """Predicted/actual price ratio over time: shows where the model over/under tracks."""
    dev = analyze.deviation_series(df, best, t0)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(dev.index, dev["dev"], lw=0.9, color=COLORS["sbtc"])
    ax.axhline(1.0, color="#000", lw=1)
    ax.fill_between(dev.index, dev["dev"], 1.0, alpha=0.15, color=COLORS["sbtc"])
    q = dev["dev"].quantile([0.05, 0.5, 0.95])
    ax.set_yscale("log")
    for pct, v in q.items():
        ax.axhline(v, color="#888", ls=":", lw=0.8)
    ax.set_title("predicted/actual price ratio (1.0 = perfect). "
                 f"q05/q50/q95 = {q[0.05]:.2f}/{q[0.5]:.2f}/{q[0.95]:.2f}")
    ax.set_xlabel("collateral guardrail: below-green line => difficulty proxy richer than market")
    ax.grid(alpha=0.3)
    return _save(fig, "06_deviation.png")


def chart_rolling_corr(df: pd.DataFrame, best_w: int, t0: str, span_days: int = 365) -> Path:
    roc = analyze.rolling_corr(df, best_w, t0, span_days)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(roc.index, roc["corr"], lw=1.0, color="#1a73e8")
    ax.set_ylim(0, 1.001)
    ax.set_ylabel(f"rolling corr (span={span_days}d)")
    ax.set_title(f"rolling correlation of ln(price_ratio) vs ln(diff_ratio), W={best_w}d")
    ax.grid(alpha=0.3)
    return _save(fig, "07_rolling_corr.png")