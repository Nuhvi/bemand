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

# Windows tried for the difficulty moving average, in difficulty periods.
# A difficulty period is 2016 blocks (~2 weeks), so 26 periods ≈ 1 year: the
# flagship smoothing used in `lending.py` and the reference contract.
WINDOWS_PERIODS = [1, 2, 4, 6, 8, 13, 20, 26, 39, 52, 78, 104]

# Default smoothing window: 26 difficulty periods (≈ one year).
DEFAULT_WINDOW = 52

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


def period_index(difficulty: pd.Series) -> pd.Series:
    """Map each day to the index of its 2016-block difficulty period.

    Difficulty is constant within a period, so a period boundary is wherever the
    daily difficulty value changes. Returns an integer Series indexed like
    ``difficulty``, 0,1,2,… for the periods in chronological order.
    """
    chg = (difficulty != difficulty.shift(1)).fillna(True)
    return chg.cumsum() - 1


def smoothed_diff(df: pd.DataFrame, window: int) -> pd.Series:
    """Mean difficulty over the trailing `window` difficulty periods.

    Each of the last ``window`` periods counts once, equal-weight (matching the
    on-chain ring buffer used by the reference contract). ``window`` is in
    periods (2016 blocks), not days. Before `window` periods of history exist
    the mean is over whatever is available (min_periods=1).
    """
    dif = df["difficulty"]
    p = period_index(dif)
    per = dif.groupby(p).first()                     # one difficulty per period
    per_sm = per.rolling(window, min_periods=1).mean()
    return p.map(per_sm).reindex(df.index)


def sliding_smoothed_diff(df: pd.DataFrame, window: int) -> pd.Series:
    """Block-window mean of difficulty: sliding mean over trailing `window*2016`
    blocks, one block at a time.

    Each block contributes the difficulty of the period it belongs to. Because
    difficulty is constant within a period the window advances continuously by
    block, so it drifts smoothly across a retarget instead of jumping.

    The daily series has no per-block breakdown, so each calendar day is
    treated as 144 blocks (2016/14) of that day's difficulty; the trailing
    block-window mean over `window*2016` blocks is computed from a
    repeated-day cumulative sum. This mirrors the on-chain ring (which knows
    real block heights) under the 144-blocks/day convention.
    """
    dif = df["difficulty"]
    blocks_per_day = 144
    nb = int(window * 2016)           # window length in blocks
    reps = dif.to_numpy().repeat(blocks_per_day)   # one entry per block
    cs = np.concatenate([[0.0], np.cumsum(reps)])  # cs[i] = sum blocks[0..i-1]
    out = np.full(len(dif), np.nan)
    for i in range(len(dif)):
        end = (i + 1) * blocks_per_day - 1          # last block of day i
        lo = end - nb + 1
        total = cs[end + 1] - (cs[lo] if lo > 0 else 0.0)
        denom = (end + 1) if lo <= 0 else nb
        out[i] = total / denom
    return pd.Series(out, index=df.index)


def _prep_ratios(df: pd.DataFrame, window: int, t0: str) -> pd.DataFrame:
    """Return normalised log-ratios (and smoothed diff) w.r.t. t0."""
    t0_ts = pd.Timestamp(t0)
    sub = df.loc[df.index >= t0_ts].copy()
    if sub.empty:
        raise ValueError(f"t0={t0} after end of data")
    sub["diff_sm"] = sliding_smoothed_diff(df, window).loc[sub.index]
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
    windows = windows if windows is not None else WINDOWS_PERIODS
    out = []
    for w in windows:
        try:
            out.append(evaluate(df, w, t0))
        except ValueError as exc:
            print(f"  window {w:>5d}p skipped: {exc}")
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
            "window": r.window,
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
    return pd.DataFrame(rows).set_index("window")