"""Core analysis: does smoothed network difficulty track relative BTC price?

The idea being tested:
    price(t) / price(t0)  ~=  smooth(difficulty, t) / smooth(difficulty, t0)

We evaluate that hypothesis across many smoothing windows and report
fit metrics (correlation, R^2, median relative error) per window, then
pick the best window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Windows tried for the difficulty moving average, in days.
WINDOWS_DAYS = [1, 7, 14, 30, 45, 60, 90, 120, 180, 270, 365, 540, 730, 1095, 1400]

# Default reference: 2010-07-18 is the first day with a nonzero price.
DEFAULT_T0 = "2010-07-18"


@dataclass
class WindowResult:
    window: int
    n: int
    r_squared: float          # R2 of regression: ln(P_ratio) ~ ln(D_ratio)
    intercept: float          # fitted a in ln(P_ratio) = a + b * ln(D_ratio)
    slope: float              # fitted exponent b
    median_abs_error: float   # median |P_pred/P - 1|, price-ratio space
    rmse_log: float           # RMSE of log residuals
    corr_log: float           # Pearson r on log-ratios
    mae: float                # mean absolute error of price ratio (0..inf)


def load_data(data_dir: str | None = None) -> pd.DataFrame:
    """Load difficulty + price into a daily-indexed DataFrame (already sorted)."""
    from . import download

    data = download.all_charts()
    frames = []
    for key in ("difficulty", "market_price"):
        chart = data[key]
        df = pd.DataFrame(chart["values"])
        df["date"] = pd.to_datetime(df["x"], unit="s", utc=True).dt.date
        df = df.rename(columns={"y": key})
        frames.append(df[["date", key]].set_index("date"))
    df = frames[0].join(frames[1], how="outer").sort_index()
    df.index = pd.to_datetime(df.index)  # Timestamp index
    # Drop illiquid pre-market history (flat zero price), but keep difficulty series.
    df["price"] = df["market_price"].replace(0.0, np.nan)
    return df


def _prep_ratios(df: pd.DataFrame, window: int, t0: str) -> pd.DataFrame:
    """Return normalised log-ratios (and smoothed diff) w.r.t. t0."""
    t0_ts = pd.Timestamp(t0)
    sub = df.loc[df.index >= t0_ts].copy()
    if sub.empty:
        raise ValueError(f"t0={t0} after end of data")
    sub["diff_sm"] = sub["difficulty"].rolling(window, min_periods=1).mean()
    ref = sub.iloc[0]  # first sample at/after t0
    sub["lr"] = np.log(sub["price"] / ref["price"])          # relative log-price
    sub["ld"] = np.log(sub["diff_sm"] / ref["diff_sm"])      # relative log-diff
    return sub.dropna(subset=["lr", "ld"])


def evaluate(
    df: pd.DataFrame,
    window: int,
    t0: str = DEFAULT_T0,
) -> WindowResult:
    sub = _prep_ratios(df, window, t0)
    lr = sub["lr"].to_numpy()
    ld = sub["ld"].to_numpy()
    n = len(sub)
    if n == 0:
        raise ValueError(f"empty slice for window={window} t0={t0}")

    # Linear fit on logs: ln(P_ratio) ~ a + b * ln(D_ratio)
    b, a = np.polyfit(ld, lr, 1)
    pred = a + b * ld
    resid = lr - pred

    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((lr - lr.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    # Price-ratio space errors using the fixed fit.
    pred_ratio = np.exp(pred)
    actual_ratio = np.exp(lr)
    rel_err = np.abs(pred_ratio - actual_ratio) / actual_ratio
    mae = float(np.mean(np.abs(pred_ratio - actual_ratio)))

    corr = float(np.corrcoef(ld, lr)[0, 1])
    return WindowResult(
        window=window,
        n=n,
        r_squared=r2,
        intercept=a,
        slope=b,
        median_abs_error=float(np.median(rel_err)),
        rmse_log=float(np.sqrt(ss_res / n)),
        corr_log=corr,
        mae=mae,
    )


def run_all(df: pd.DataFrame, windows=None, t0: str = DEFAULT_T0) -> list[WindowResult]:
    windows = windows or WINDOWS_DAYS
    out = []
    for w in windows:
        try:
            out.append(evaluate(df, w, t0))
        except ValueError as exc:
            print(f"  window {w:>5}d skipped: {exc}")
    out.sort(key=lambda r: r.corr_log, reverse=True)
    return out


def best(results: list[WindowResult]) -> WindowResult:
    return results[0]


def rolling_corr(df: pd.DataFrame, smooth_window: int, t0: str, span_days: int = 365) -> pd.DataFrame:
    """Rolling Pearson corr of the two log-ratios over a trailing span."""
    sub = _prep_ratios(df, smooth_window, t0)
    ld = sub["ld"]
    lr = sub["lr"]
    # Standardised rolling covariance: corr = cov/(sd_a*sd_b)
    cov = (ld * lr).rolling(span_days, min_periods=120).mean() - ld.rolling(
        span_days, min_periods=120).mean() * lr.rolling(span_days, min_periods=120).mean()
    sda = ld.rolling(span_days, min_periods=120).std()
    sdb = lr.rolling(span_days, min_periods=120).std()
    out = pd.DataFrame(index=sub.index)
    out["corr"] = cov / (sda * sdb)
    return out.dropna()


def deviation_series(df: pd.DataFrame, res: WindowResult, t0: str) -> pd.DataFrame:
    """predicted/actual price ratio over time from the fitted model."""
    sub = _prep_ratios(df, res.window, t0)
    out = pd.DataFrame(index=sub.index)
    out["dev"] = np.exp(res.intercept + res.slope * sub["ld"]) / np.exp(sub["lr"])
    return out


def to_frame(results: list[WindowResult]) -> pd.DataFrame:
    rows = [
        {
            "window_days": r.window,
            "n": r.n,
            "corr_log": r.corr_log,
            "r2_log": r.r_squared,
            "slope": r.slope,
            "intercept": r.intercept,
            "mae_price": r.mae,
            "median_rel_err": r.median_abs_error,
            "rmse_log": r.rmse_log,
        }
        for r in results
    ]
    return pd.DataFrame(rows).set_index("window_days")