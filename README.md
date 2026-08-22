# SmoothBTC

A lending protocol concept that uses **Bitcoin network difficulty** as an independent, on-chain, fiat-agnostic oracle for BTC demand.

## The idea

Oracle-free demand index. Bitcoin's difficulty is set by the network itself
(every 2016 blocks) purely from the hashing power miners are spending. It is
not a price measured in any currency and needs no trusted oracle.

The claim: **`D(t) / D(t0)` — network difficulty normalised back to a
reference point `t0` — is a proxy for total BTC demand growth**, and smoothed
difficulty at `t` tracks what `P(t) / P(t0)` is, regardless of unit.

Protocol loop (RBTC):

  1. Deposit BTC/RBTC → mint **SmoothBTC** worth `S(tS) = D_s(t) / D_s(t0)`
     (D_s = smoothed difficulty).
  2. If smoothed difficulty rises, you return SmoothBTC, take back RBTC, and
     re-mint at the new higher anchor →*long the difficulty of the BTC network*.
  3. If smoothed difficulty falls sharply below the mint level, collateral is
     liquidated.

Because difficulty is a pure protocol-internal measure, SmoothBTC's value stays
anchored to the **50WMA/200WMA USD analogue** of the difficulty curve for
accounting, but never depends on an external feed to compute it.

The repo currently does the numerical homework (download + correlation search).

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./.venv/bin/python main.py            # analyze (re-downloads if data older than ~1 day)
./.venv/bin/python main.py --force    # force refresh all data
./.venv/bin/python backtest.py                        # 10y what-if (charts in out/backtest/)
./.venv/bin/python backtest.py --years 5              # 5y horizon
./.venv/bin/python backtest.py --launch 2016-08-01    # fixed launch date
./.venv/bin/python backtest.py --since 2016-01-01 --smooth 270   # merchant/collateral sweeps
./.venv/bin/python backtest.py --since 2016-01-01 --smooth 365 --law-b 0.73
```

Outputs:

- `data/*.json` — raw blockchain.info series (cached; auto-refreshed after ~22h)
- `out/*.png` — 7 analysis charts, regenerated on every run
- `out/backtest/*.png` — 9 backtest charts (values, merchant, salary, table,
  float sweep, cumulative, W-sweep merchant/collateral, merchant-window bar, collateral ratio TS)
- terminal table of fit quality across smoothing windows (1d … 1400d)

## Merchant & collateral questions (`backtest.py`)

### Does a merchant with USD costs lose money accepting SmoothBTC?

It depends on *how* they hold it. If they convert as soon as revenue lands
(standard POS crypto handling), the only exposure is a small working-capital
float (backtest: a 1-month float was **+18% over 10y** — you win, not lose, in
a rising regime). If they price goods in SmoothBTC and convert on some
cadence, the risk is a drawdown of the smoothed difficulty value, which since
2014 is strikingly small:

| smoothing W | worst month | worst 12-mo | months < launch | max price DD |
|-------------|-------------|--------------|-----------------|--------------|
| 180d | −3.5% | +4% | 0% | −11% |
| **270d** | **−2.1%** | **+9.6%** | **0%** | **−3.2%** |
| **365d** | **−0.7%** | **+12.6%** | **0%** | **−1.2%** |
| 540d | −0.1% | +17.5% | 0% | −0.1% |

Since 2014, **smoothed difficulty (W≥270) has a max sustained drawdown of only
~4%** — it's essentially monotonic upward at any ≥1-year horizon. So a USD-cost
merchant **does not lose money** on a ≥12-month view at W≈270–365: 0% of months
are below launch parity, and the worst year is still positive. This holds
robustly whether you anchor at 2014, 2015, or 2016.

### What collateral ratio (BTC vs SmoothBTC) never gets liquidated?

The honest answer: **there is no single number.** The required backing is
dominated by the *long-run drift* between difficulty and spot, which shifts
with the anchor date and the fitted exponent `b`:

| anchor `since` | b | never-liquidation backing |
|----------------|----|---------------------------|
| 2016-01-01 | 0.73 | **≈2.9x** (2.3x at p1) |
| 2015-01-01 | 0.73 | ≈4.3x (3.6x at p1) |
| 2014-01-01 | 0.62 | ≈47x |
| 2010-07-18 | 0.49 | essentially unbounded |

Why: anchored at 2014, difficulty grew ~1000× while spot grew far less, so the
smoothed oracle *overshoots* spot and the spot/oracle ratio collapses (0.02),
needing astronomically high backing. Anchored at 2016 (mature market, `b≈0.73`
fits) the two track each other and you need only **~2.9x** (`--smooth 270`).

**The structural takeaway:** difficulty tracks demand *returns* well over long
windows (R²≈0.9), but its *level* drifts relative to spot depending on the
epoch. A liquidation trigger on raw difficulty does NOT fire in bear markets —
the worst collateral ratio on record is **March 2020**, when spot crashed
while miners kept hashing (difficulty kept climbing). So difficulty values the
collateral *too high* exactly when you'd want it too low. Robust lending
therefore needs **secure ~3x + a spot-linked floor**, or a re-anchoring
mechanism — not a static "never liquidate" ratio. See
`out/backtest/07_w_sweep.png`, `08_merchant_window.png`, `09_collateral_ts.png`.

### Is pre-2013 too noisy?

Confirmed, on both fit quality and level drift:

| since | b | R² | median err |
|-------|----|------|-----------|
| 2010-07-18 | 0.49 | 0.94 | 55% |
| 2013-01-01 | 0.48 | 0.85 | 57% |
| 2014-01-01 | 0.62 | 0.89 | 44% |
| 2016-01-01 | 0.73 | 0.90 | 33% |

Pre-2013/2014 data drags the fitted exponent down (0.49→0.62) and inflates
error (55%→44%). **Use `--since 2016-01-01` (b≈0.73) for protocol design**; the
2014-anchored 48x collateral is an artifact of that earlier, noisier regime.

## Backtesting

`backtest.py` answers *"what if SmoothBTC launched N years ago?"*. It evaluates
three candidate USD prices for 1 SmoothBTC (all calibrated to the SMA-350
anchor at launch):

- **oracle** — pure difficulty-derived value (the real design, no USD inputs)
- **wma** — the accounting anchor SmoothBTC is meant to track
- **spot** — raw BTC/USD (conventional crypto payment, for reference)

Each is run through two cashflow simulations, every scenario normalised to
the same USDT baseline of $1/month (1.0x = parity with USDT):

- **merchant** — business priced in USD, gets paid in SmoothBTC, restocks
  inventory in USD monthly. Only its working-capital buffer (default 1 month
  of spend) is exposed.
- **salary** — yearly contract paid a fixed SmoothBTC amount monthly;
  each year it may re-sign at the current rate (`yearly`) or stay fixed
  for the whole horizon (`fixed`).

### Launching 2016-08 (10 years through today)

Token prices: oracle **23.4x**, wma 146x, spot 111x (difficulty is the
smoothest of the three by far: std 7.3 vs 51.7 $/mo).

| scenario | vs USDT | max income DD | $/mo range |
|---|---|---|---|
| merchant / oracle | 1.18x | −22% (buffer) | 0.99–25.1 |
| merchant / spot | 1.82x | −75% (buffer) | 0.91–185.5 |
| salary oracle / **yearly** | 1.21x | −60% within-year | 0.99–2.47 |
| salary oracle / **fixed** | 11.2x | −22% of income | 0.99–25.1 |
| salary spot / fixed | 54.7x | −75% of income | 0.91–185.5 |

Readings:

- **Merchant ≈ neutral.** A USD-priced merchant passes revenue straight
  through, so the only exposure is the float. A 1-month float wins ~+18% over
  10y because the asset appreciated; a larger float magnifies both win and
  drawdown (`05_float_sensitivity.png`).
- **Salary depends on renewal.** A yearly re-signing contract in SmoothBTC
  lands at **~1.2x USDT** — SmoothBTC behaves like a gently-upward, low-vol
  USDT (income std 0.25 $/mo). A fixed long contract captures the asset
  appreciation (11x oracle) but also its full swings.
- **SmoothBTC is ~7× smoother than spot** but still trails BTC-spot on pure
  return in a bull decade. Its value is: *difficulty-derived (no trusted
  oracle), low-volatility, stablecoin-like accounting* — not max return.
- Front-loading matters: launching near a top (2021) the oracle only does
  ~2.9x and salary-yearly lands ~1.15x; launching near a base (2016) is the
  bull case shown above.

### Caveats

- Backtest assumes the fitted **price ∝ difficulty^0.49** law holds out of
  sample. It historically tracks relative price well (R²≈0.94) but undershoots
  top multipliers (difficulty is sticky at peaks).
- No fees, slippage, liquidation, borrowing, or the synthetic's own market
  mechanics are modelled — this is value-evenness math only.
- The "oracle" model is anchored to the SMA-350 at launch; choosing a
  different anchor or a different fitted exponent changes absolute price but
  not the ratio-based verdicts above (they are scale-invariant).

## Findings (2026-08, from blockchain.info)

`ln(price ratio)` vs `ln(difficulty ratio)`:

| smoothing | corr | R² | slope | median \|err\| |
|-----------|------|-----|-------|----------------|
| 1d        | .972 | .945 | .49   | 54%             |
| 7d        | .972 | .945 | .49   | 54%             |
| 30d       | .971 | .943 | .49   | 55%             |
| 365d      | .967 | .935 | .47   | 55%             |
| 1095d     | .968 | .938 | .47   | 53%             |

- Correlation is **very high (~0.97)** irrespective of window — difficulty is
  a real proxy for relative price, even though both are noisy.
- The fitted **exponent b ≈ 0.5** means `price ∝ sqrt(difficulty)` over the
  long run — smoothing helps little because the protocol already steps
  difficulty every ~2 weeks.
- Median relative error ~54%: relative price is *correlated* with relative
  difficulty but not tightly explained by it; difficulty overshoots
  (speculative, euphoria) and lags (cap-exit) market price.
- Largest deviations cluster around 2011/2013/2017/2021 peaks and 2015/2018/2022
  drawdowns (see `05_price_proxy.png`, `06_deviation.png`).

**Conclusion for the protocol:** difficulty is a valid *independent oracle*,
but for accounting you'd anchor SmoothBTC to smoothed difficulty (the "50WMA of
difficulty"), not to a spot price. Liquidation thresholds must tolerate the
>~50% model noise around peaks; the deviation chart informs where to set them.

## Layout

```
main.py              # download → analyse → plot
backtest.py          # "what if we launched N years ago" + merchant/collateral sweeps
smoothbtc/analyze.py # load data, MA windows, OLS fit, metrics
smoothbtc/backtest.py# value models + cashflow simulators + merchant/collateral metrics
smoothbtc/plot.py    # PNG charts (analysis)
smoothbtc/download.py # blockchain.info fetcher with ~1-day cache
data/out/             (generated)
```