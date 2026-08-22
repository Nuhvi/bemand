# DBTC

**D**emand / **D**ifficulty BTC — a lending/accounting unit minted from Bitcoin network
difficulty. It uses difficulty as an independent, on-chain, fiat-agnostic signal for BTC
demand. Still very much research, model-first: the repo does the numerical homework and
every claim below is backed by a committed chart under `out/`.

## The idea

Bitcoin's difficulty is set by the network itself (every 2016 blocks) purely from the
hashing power miners are spending. It is **not a price** measured in any currency and
needs no trusted oracle. The claim:

> **`D(t) / D(t0)` — smoothed network difficulty normalised to a reference point `t0` —
> is a proxy for *relative* BTC price, unit-free.**

A protocol loop (RBTC):

1. Deposit BTC/RBTC → mint **DBTC** worth `S(t0) = D_s(t) / D_s(t0)` (D_s = smoothed
   difficulty).
2. If smoother difficulty rises, return DBTC, take back RBTC, re-mint at the higher
   anchor → *long the difficulty of the BTC network*.
3. If smoothed difficulty falls sharply below mint level, the vault is liquidated.

Because difficulty is a pure protocol-internal measure, DBTC can be *accounted* in
USD via its 50WMA/200WMA analogue for reference, but the protocol value never depends on
an external feed.

## Raw data (since 2009)

![raw data](out/01_raw.png)

Both series span 2009→today (daily, blockchain.info). Price spans $0 → ~$65k, difficulty
spans 1 → ~110T: two power-law-ish curves. The two track each other well on a log scale —
that visual correlation is the entire thesis. → `main.py`, `dbtc/plot.py` `chart_raw`.

## Does difficulty track relative price? (analysis, `main.py`)

### Normalised log-ratios

The analysis picks a reference `t0`, computes `ln(price/price_t0)` and
`ln(diff_sm/diff_sm_t0)` for every day, and asks how well the two move together
(`02_ratios_log.png`, `03_regression.png`):

![norm log-ratios vs t0](out/02_ratios_log.png)

![regression ln(price_ratio) ~ ln(diff_ratio)](out/03_regression.png)

Fit (full-sample, best window):

| smoothing | corr | R² | slope b | median \|err\| |
|-----------|------|-----|---------|----------------|
| 1d        | .972 | .945 | .49 | 54% |
| 7d        | .972 | .945 | .49 | 54% |
| 30d       | .972 | .945 | .49 | 54% |
| 365d      | .967 | .935 | .47 | 55% |
| 1095d     | .968 | .938 | .47 | 53% |

Readings:

- **correlation ≈ 0.97 regardless of window** — difficulty is a real proxy for *relative*
  price, even over daily data. Smoothing barely matters: the protocol already steps
  difficulty inherently every ~2 weeks.
- The fitted exponent `b ≈ 0.5` (`price ∝ difficulty^0.5`): doubling difficulty → ~1.4×
  price. Consistent across all windows (see `04_window_metrics.png`).
- **R² ≈ 0.94 but median |err| ≈ 54%** — the two are *correlated*, not tightly explained.
  At peaks difficulty overshoots (ebullient hashing) and at drawdowns it lags (capacity
  exit), so a *level* bet on difficulty carries ~±50% noise. `06_deviation.png` shows the
  largest deviations cluster at 2011/2013/2017/2021 peaks and 2015/2018/2022 drawdowns;
  `05_price_proxy.png` shows the reconstructed proxy price in log space:

![price proxy from smoothed difficulty](out/05_price_proxy.png)

![pred/actual ratio = collateral guardrail](out/06_deviation.png)

![rolling correlation](out/07_rolling_corr.png)

Fit-quality vs the smoothing window (`04_window_metrics.png`) is essentially a flat line:
once per-block stepping exists, extra smoothing buys nothing and loses nothing. That keeps
the protocol simple (raw difficulty, no WMA parameter to tune).

```bash
./.venv/bin/python main.py            # re-download if stale, fit, plot all of the above
```

## The price models (`backtest.py`)

`backtest.py` answers *"what if we launched N years ago?"*. Four candidate USD prices for
1 DBTC, all normalised to a common launch anchor (SMA-350):

- **DBTC** — pure difficulty-derived (the real design: `s0 = base · (D/D0)^b`)
- **wma (≈50w)** — SMA-350 of spot, the accounting anchor DBTC is meant to track
- **wma200 (≈200w)** — SMA-1400, the long-run reference line (chart only, not a scenario)
- **spot** — raw BTC/USD (conventional crypto payment baseline)

### Candidate values, and their volatility

`out/backtest/01_values.png` — top: the four price series (log). Bottom: rolling 90-day
annualised volatility of all four (log scale):

![candidate prices + rolling volatility](out/backtest/01_values.png)

**The headline result:** DBTC is dramatically *smooth*. Rolling vol peaks in the
early 2012​–2015 era at ~10–20% for all series, but after ~2018 DBTC sits reliably
**under 5% (and often <2%)** while spot and even the 50w/200w SMAs swing 30–80%. DBTC's
daily jumps are pegged to the 2-week-per-2016-blocks difficulty cadence, not market micro,
so it is a genuine low-vol stable accounting unit — at the cost of trailing spot's returns.

Run and see: `./.venv/bin/python backtest.py --years 10` → regenerates `out/backtest/*.png`.

### Merchant: a USD-priced business accepting DBTC

![merchant cashflows](out/backtest/02_merchant.png)

A business that passes revenue straight through (prices in USD, restocks in USD monthly)
has only its working-capital buffer exposed. Top panel: the $1 float's USD value. Bottom:
cumulative wealth vs USDT baseline.

Key figure: **DBTC** holds the float's max drawdown at only **−3.2%** vs **−75%** for
spot (the 50w SMA anchor sits between at −54%). A monthly-converted merchant is effectively
neutral-to-mildly-positive, because DBTC appreciates while barely ever drawing down
(`05_float_sensitivity.png` shows bigger floats magnify both win and risk).

### Salary: a worker paid fixed DBTC/month

![salary panel](out/backtest/03_salary.png)

A yearly-re-signing salary lands at **~1.2x USDT** — DBTC behaves like a gently-upward,
low-vol USDT (income std ~0.4 $/mo). A fixed lead contract captures the underlying asset
appreciation, but inherits its full swings. `06_cumulative.png` collapses this to cumulative
multiple-vs-USDT across the four model×renewal combos.

### Collateral & liquidation (`lending.py`)

The vault's liquidation trigger is **pure difficulty**: you're liquidated when smoothed
difficulty falls to `(1/CR)^(1/b)` of its mint-time level (≈ −30% at CR=3). The worst
historical drawdown of the 270d-smoothed difficulty is **−3.2%**:

| smoothing W | worst month | worst 12-mo | months < launch | max price DD |
|-------------|-------------|--------------|-----------------|--------------|
| 180d | −3.5% | +4% | 0% | −11% |
| **270d** | **−2.1%** | **+9.6%** | **0%** | **−3.2%** |
| **365d** | **−0.7%** | **+12.6%** | **0%** | **−1.2%** |

Since 2014 smoothed difficulty (W≥270) is **monotonic upward at any ≥1-year horizon**, so a
Pure-difficulty floor *never* bites. The real risk is the **spot/difficulty spread**: on
`2020-03-13` spot crashed to 0.375× while difficulty kept climbing. That deviation — not
difficulty itself — sets the collateral requirement:

![collateral ratio over time](out/backtest/09_collateral_ts.png)

Across W it looks like (`07_w_sweep.png`, `08_merchant_window.png`): every W keeps the
worst-month drawdown above 0%, and the never-liquidation backing is driven by how much
*smooth* `spot/DBTC` diverges. Anchored 2016 you need `≈2.9x` backing for "never"; the
switch is that "never" depends sensitivity on the *anchor epoch*, because difficulty's
*level* can drift relative to spot (see table + charts at `07_w_sweep.png`, `08`).

Run: `./.venv/bin/python backtest.py --since 2016-01-01 --smooth 270` — prints all metrics.

## `lending.py` — live protocol numbers

Frozen defaults: `--since 2016-01-01`, `--smooth 365`, `--law-b` auto-fitted ≈0.69,
`--collat 3.0`. Once frozen, USD is never consulted at runtime; every number below is
difficulty arithmetic.

1. **Mint** — per 1 locked BTC, `mint = (D_ratio)^b / CR`. Today ≈ **48.9 DBTC/BTC**
   (`D_ratio ≈ 1340`, `b ≈ 0.693`, `CR=3`).
2. **Liquidation** — pure-difficulty floor at `(1/CR)^(1/b) ≈ 20.5%` of mint difficulty:
   never reached historically (worst smoothed difficulty drawdown ≈ −3%). BUT the 
   `2020-03-13` spot crash was the worst *spot/DBTC* deviation (0.375) → at CR=3 only
   +12% margin; at CR=2.5 it's _negative_ — the thin spot deviation is the binding tail.
3. **USD value** — `SmoothUSD = P0 × D_ratio^b` with `P0` frozen once at the anchor
   (≈ $92k today), purely difficulty-driven thereafter.
4. **Prices** — `out/lending_price.png` shows 30-day & 12-month DBTC/USD and the
   difficulty ratio vs its floor (`./.venv/bin/python lending.py`)

![lending price history](out/lending_price.png)

## What this is, and what it is not

- **DBTC is a stable-ish, difficulty-anchored accounting unit** — ~7× lower vol than
  spot since ~2016, ~monotonic at ≥1y horizons, no external price feed required to compute.
- It is **not** a get-wealthy instrument: over a bull decade margins clearly trail spot
  (`04_table.png`, `06_cumulative.png`). The pitch is *low-volatile stable pricing*, not
  alpha.
- **No fees/slippage/liquidation mechanics/S-M pool dynamics are modelled** — clean
  value-evenness math only.
- The difficulty law itself (price ∝ difficulty^b) has ±~55% median noise near peaks;
  liquidation triggers must tolerate that.

## Layout

```
main.py                  # download → analyse (difficulty vs price) → plot
backtest.py              # "what-if we launched N years ago" + merchant/collateral sweeps
lending.py               # live: mint / liquidation / USD value / 30d+12m prices
dbtc/analyze.py     # load data, smoothing windows, OLS fit, metrics
dbtc/backtest.py    # value models + cashflow simulators + merchant/collateral metrics
dbtc/plot.py        # analysis charts → out/*.png
dbtc/download.py     # blockchain.info fetcher, ~1-day cache
data/                    # cached blockchain.info JSON (auto-refreshed)
out/*.png                # committed charts referenced by this README
```