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
```

Outputs:

- `data/*.json` — raw blockchain.info series (cached; auto-refreshed after ~22h)
- `out/*.png` — 7 charts regenerated on every run
- terminal table of fit quality across smoothing windows (1d … 1400d)

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
smoothbtc/analyze.py # load data, MA windows, OLS fit, metrics
smoothbtc/plot.py     # PNG charts
smoothbtc/download.py # blockchain.info fetcher with ~1-day cache
data/out/             (generated)
```