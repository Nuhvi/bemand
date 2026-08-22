"""Backtesting: what would have happened if DBTC launched N years ago?

Computes four candidate values for "1 DBTC" in USD:
  * DBTC -- difficulty-derived, no USD inputs, as designed
  * wma       -- the accounting anchor DBTC aims to track (SMA of spot, ~50w)
  * wma200    -- 200-week SMA reference line (chart only, not a scenario)
  * spot      -- raw BTC/USD for reference

Then simulates two cashflow scenarios over the chosen horizon and compares
each against "you held USDT instead" (flat 1 USD):

  * merchant  -- business priced in USD, receives DBTC, restocks
                 inventory in USD monthly (working-capital float risk)
  * salary    -- one-year contract, fixed DBTC/month, converted to
                 USD as spent (income-volatility risk)

All results are normalised to USD per USD of starting monthly revenue /
salary, so "1.0" means exactly as well off as the USDT baseline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import analyze

# Fitted power-law from full-sample analysis: ln(P_ratio) ~ a + b*ln(D_ratio).
FIT_A = 0.49
FIT_B = 0.49


@dataclass
class ValueModel:
    """A candidate USD price for 1 DBTC over time."""

    name: str
    series: pd.Series   # index = dates, Values = USD per 1 S-BTC

    def __post_init__(self):
        self.series = self.series.sort_index()


def build_value_models(df: pd.DataFrame, launch: str, diff_w: int = 26,
                       wma_w: int = 350, a: float = FIT_A, b: float = FIT_B) -> dict[str, ValueModel]:
    """Return {name: ValueModel} for DBTC / wma(≈50w) / wma200 / spot from launch onwards.

    ``diff_w`` (default 26) is the DBTC smoothing window in difficulty
    periods (2016 blocks each). ``wma_w`` (default 350 days ≈ 50 weeks) is the
    accounting-anchor SMA the DBTC value is calibrated to. a 200-week SMA
    (4 * wma_w) is added as a long-run reference line.
    """
    ts = pd.Timestamp(launch)
    d = df.loc[df.index >= ts].copy()
    spot = d["price"]

    # DBTC: difficulty only, calibrated to the wma anchor at launch.
    sd = analyze.smoothed_diff(df, diff_w).reindex(d.index)
    ref_sd = sd.iloc[0]
    anchor = spot.rolling(wma_w, min_periods=1).mean().iloc[0]
    dbtc = anchor * np.exp(a) * (sd / ref_sd) ** b

    wma = spot.rolling(wma_w, min_periods=1).mean()
    wma200 = spot.rolling(wma_w * 4, min_periods=1).mean()

    return {
        "dbtc": ValueModel("DBTC (difficulty)", dbtc),
        "wma": ValueModel("wma (350d ≈ 50w, anchor)", wma),
        "wma200": ValueModel("wma200 (1400d ≈ 200w)", wma200),
        "spot": ValueModel("spot", spot),
    }


def _monthly_dates(start: pd.Timestamp, n_months: int, max_date) -> pd.DatetimeIndex:
    idx = pd.date_range(start, periods=n_months + 1, freq="MS")
    return idx[idx <= pd.Timestamp(max_date)]


def _daily_resample(series: pd.Series, month_bases) -> pd.Series:
    return series.reindex(series.index.union(month_bases)).ffill().reindex(month_bases)


def merchant_cashflow(model: ValueModel, df: pd.DataFrame, launch: str,
                      n_months: int, float_months: int = 1) -> pd.DataFrame:
    """Merchant accepting DBTC, restocking in USD monthly.

    The business is priced in USD: it receives $1/month of revenue as
    DBTC and spends $1/month restocking in USD (pass-through). Its only
    exposure is the working-capital buffer of ``float_months`` of spend kept
    in DBTC (units fixed from launch, no rebalancing). If the DBTC
    exchange rate falls, the buffer shrinks in USD; if it rises, it swells.

    Returns a DataFrame indexed by month with columns:
        buffer_usd    -- USD value of the working-capital buffer
        wealth        -- cumulative cash spent ($1/month) + buffer_usd
        usdt_wealth   -- cumulative $1/month baseline (buffer is USD, flat)
    """
    start = pd.Timestamp(launch)
    dates = _monthly_dates(start, n_months, df.index.max())
    P = _daily_resample(model.series, dates)
    n = len(P)

    buffer_units = float_months / P.iloc[0]           # fixed, worth float_months $ at launch
    buffer_usd = P * buffer_units

    cash = pd.Series(np.arange(n), index=P.index) * 1.0   # cumulative restocking spend
    wealth = cash + buffer_usd
    usdt_buf = pd.Series(float_months, index=P.index)      # USDT buffer stays flat
    usdt_wealth = cash + usdt_buf
    return pd.DataFrame({
        "buffer_usd": buffer_usd,
        "float_usd": buffer_usd,      # alias for summary()
        "wealth": wealth,
        "usdt_wealth": usdt_wealth,
    })


def salary_cashflow(model: ValueModel, df: pd.DataFrame, launch: str,
                    n_months: int, renew: bool = True) -> pd.DataFrame:
    """Worker on a yearly contract paid in DBTC every month.

    The contract pays a fixed DBTC amount per month; each year when the
    contract re-signs (if ``renew``) it is re-priced to $1/month at the then
    current exchange rate. The USD they actually get is K * P.

    Returns a DataFrame indexed by month with columns:
        income_usd  -- USD value received that month
        cumulative  -- cumulative USD received
        usdt_inc    -- $1/month baseline
        usdt_cum    -- cumulative USDT baseline
    """
    start = pd.Timestamp(launch)
    dates = _monthly_dates(start, n_months, df.index.max())
    P = _daily_resample(model.series, dates)

    Ks = pd.Series(0.0, index=P.index)
    K = 1.0 / P.iloc[0]
    for i in range(len(P)):
        if renew and i % 12 == 0:
            K = 1.0 / P.iloc[i]          # re-sign this month at current rate
        Ks.iloc[i] = K

    income = Ks * P
    usdt_inc = pd.Series(1.0, index=P.index)
    return pd.DataFrame({
        "income_usd": income,
        "cumulative": income.cumsum(),
        "usdt_inc": usdt_inc,
        "usdt_cum": usdt_inc.cumsum(),
    })


def fit_law(df: pd.DataFrame, since: str, diff_w: int = 26) -> dict:
    """Fit the difficulty->price power law on data from ``since`` onwards.

    ``diff_w`` is the smoothing window in difficulty periods. Returns dict with
    exponent 'b', intercept 'a' (log), R^2, median relative error and n. Uses
    the existing cross-sectional OLS in analyze.evaluate with t0 = ``since``.
    """
    r = analyze.evaluate(df, diff_w, t0=since)
    return {
        "b": float(r.slope),
        "a": float(r.intercept),
        "r2": float(r.r_squared),
        "mederr": float(r.median_abs_error),
        "n": int(r.n),
        "window": diff_w,
    }


def dbtc_series(df: pd.DataFrame, since: str, smooth: int = 26,
                b: float | None = None) -> pd.Series:
    """DBTC price anchored to spot at ``since`` via difficulty^b.

    ``smooth`` is the smoothing window in difficulty periods. Returns a daily
    USD series for 1 DBTC from ``since`` onwards. If ``b`` is None, auto-fit on
    the [since..end] sample.
    """
    d = df.loc[df.index >= pd.Timestamp(since)].copy()
    spot = d["price"]
    if b is None:
        b = fit_law(df, since, smooth)["b"]
    sd = analyze.smoothed_diff(df, smooth).reindex(d.index)
    return spot.iloc[0] * (sd / sd.iloc[0]) ** b


def merchant_loss_metrics(df: pd.DataFrame, smooth: int = 26, since: str = "2014-01-01",
                          b: float | None = None) -> dict:
    """Merchant that prices goods in DBTC and pays USD costs monthly.

    Reports how exposed a fixed-Smooth-priced merchant is: worst month, worst
    12-month, fraction of months below launch parity, and max price drawdown.
    """
    P = dbtc_series(df, since, smooth, b)
    m = P.resample("MS").last()
    dd = (m / m.cummax() - 1).min()
    below0 = float((m < m.iloc[0]).mean())
    worst_mo = float((m / m.shift(1) - 1).min())
    worst_12 = float((m / m.shift(12) - 1).min())
    return {
        "smooth": int(smooth),
        "since": str(pd.Timestamp(since).date()),
        "worst_month": worst_mo,
        "worst_12m_ann": worst_12,   # 12m return, not annualised
        "frac_below_launch": below0,
        "max_price_drawdown": dd,
        "end_price": float(P.iloc[-1]),
    }


def collateral_metrics(df: pd.DataFrame, smooth: int = 26, since: str = "2014-01-01",
                       b: float | None = None) -> dict:
    """Collateral ratio RBTC/DBTC and backing required never to liquidate.

    ratio = value(1 RBTC in USD) / value(1 DBTC in USD), normalised so a
    vault opened at ``since`` starts at 1.0. min/p1 of that ratio give the
    collateral multiples needed so the ratio never deviates below the vault's
    accounting baseline (1.0). Lower ratios = less backing needed.
    """
    P = dbtc_series(df, since, smooth, b)
    spot = df["price"].reindex(P.index).ffill()
    ratio = spot / P
    r0 = ratio.iloc[0]
    rn = ratio / r0
    return {
        "smooth": int(smooth),
        "since": str(pd.Timestamp(since).date()),
        "min": float(rn.min()),
        "p1": float(rn.quantile(0.01)),
        "collat_never": 1.0 / float(rn.min()),
        "collat_p1": 1.0 / float(rn.quantile(0.01)),
        "end_ratio": float(ratio.iloc[-1]),
        "bind_date": pd.Timestamp(rn.idxmin()).date(),
    }


def ratio_index(df: pd.DataFrame, since: str) -> pd.Index:
    """Daily index from ``since`` (union of df index), sorted."""
    return df.loc[df.index >= pd.Timestamp(since)].index


def run_all(df: pd.DataFrame, launch: str, n_months: int = 120,
            float_months: int = 1, b: float | None = None,
            smooth: int = 26) -> dict:
    """Backtest all models x scenarios, return {key: summary}."""
    models = build_value_models(df, launch, a=0.0, b=(b or FIT_B), diff_w=smooth)
    out = {}
    for mkey in ("dbtc", "wma", "spot"):   # wma200 is a chart reference, not a scenario
        m = models[mkey]
        merch = merchant_cashflow(m, df, launch, n_months, float_months)
        out[f"merchant:{mkey}"] = summary(merch, f"merchant:{mkey}")
        for renew, tag in ((False, "fixed"), (True, "yearly")):
            sal = salary_cashflow(m, df, launch, n_months, renew=renew)
            out[f"salary:{mkey}:{tag}"] = summary(sal, f"salary:{mkey}:{tag}")
    return out


def summary(scenario: pd.DataFrame, name: str) -> dict:
    """Headline against-USDT metrics for a merchant or salary frame."""
    has_float = "float_usd" in scenario
    cum = scenario["wealth"] if has_float else scenario["cumulative"]
    ref = scenario["usdt_wealth"] if has_float else scenario["usdt_cum"]
    variable = scenario["buffer_usd" if "buffer_usd" in scenario else (
        "income_usd")]
    # Max drawdown on the *variable* component (float / income), not cumulative.
    dd = variable / variable.cummax() - 1
    return {
        "scenario": name,
        "final": float(cum.iloc[-1]),
        "usdt_final": float(ref.iloc[-1]),
        "ratio_vs_usdt": float((cum / ref).iloc[-1]),
        "max_rel_dd": float(dd.min()),
        "monthly_std": float(variable.std()),
        "monthly_min": float(variable.min()),
        "monthly_max": float(variable.max()),
    }