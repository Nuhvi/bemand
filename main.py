"""smooth-btc: difficulty-derived BTC price oracle analysis.

Usage:
    python main.py            # normal run (download if stale, analyze, plot)
    python main.py --force    # force re-download of all data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoothbtc import analyze, download, plot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="force re-download of all charts")
    parser.add_argument("--t0", default=analyze.DEFAULT_T0,
                        help=f"reference date for normalisation (default: {analyze.DEFAULT_T0})")
    args = parser.parse_args()

    # 1. Ensure fresh local data (re-download if older than ~1 day).
    stale = download.stale_files()
    if args.force:
        print(f"[download] forcing refresh of all charts")
        charts = download.all_charts(force=True)
    elif stale:
        print(f"[download] refreshing stale chart data: {', '.join(stale)}")
        charts = download.all_charts()
    else:
        print("[download] all chart data fresh (cached < ~22h)")
        charts = download.all_charts()

    names = ", ".join(sorted(k for k, v in charts.items()))
    n_pts = {k: len(v.get("values", [])) for k, v in charts.items()}
    print(f"[download] {names}")
    for k, v in n_pts.items():
        print(f"  {k}: {v} points")

    # 2. Load + analyse.
    df = analyze.load_data()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])

    results = analyze.run_all(df, t0=args.t0)
    if not results:
        print("no data — check token/cache.", file=sys.stderr)
        return 1
    best = analyze.best(results)
    print(f"\n[analysis] reference t0 = {args.t0}")
    fr = analyze.to_frame(results)
    print(fr.to_string(float_format=lambda x: f"{x:.4f}"))
    print(
        "\n[best]\n"
        f"  window            : {best.window} days\n"
        f"  corr(log ratios)  : {best.corr_log:.4f}\n"
        f"  R^2 (log fit)     : {best.r_squared:.4f}\n"
        f"  fitted slope a    : {best.intercept:.4f}\n"
        f"  fitted exponent b : {best.slope:.4f}\n"
        f"  median rel err    : {best.median_abs_error*100:.1f}%\n"
        f"  RMSE (log)        : {best.rmse_log:.4f}"
    )

    # Sensitivity: how stable is the best window?
    print("[top windows]")
    for r in results[:3]:
        print(f"  {r.window:>5}d  corr={r.corr_log:.4f}  R2={r.r_squared:.4f}  mederr={r.median_abs_error*100:.1f}%")

    # 3. Charts (updated every run).
    paths = [
        plot.chart_raw(df),
        plot.chart_smoothed_vs_price(df, best.window, args.t0),
        plot.chart_regression(df, best, args.t0),
        plot.chart_window_sweep(results),
        plot.chart_price_space(df, best, args.t0),
        plot.chart_deviation(df, best, args.t0),
        plot.chart_rolling_corr(df, best.window, args.t0),
    ]
    print(f"\n[charts] wrote {len(paths)} PNGs to {plot.OUT}/")
    for p in paths:
        print(f"  {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main())