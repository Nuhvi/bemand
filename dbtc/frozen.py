"""Frozen difficulty->price law (dbtc/frozen.py).

The DBTC price law is fitted ONCE from the cached data that existed on
2026-08-22 (our last calculation) and then locked into data/frozen_law.json.
After freezing, only network difficulty (and FX) move; the law parameters
(anchor, window, b, a, P0, D0) are immutable. Nothing in this repo ever
re-fits them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import analyze  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FROZEN_PATH = DATA_DIR / "frozen_law.json"

# Defaults for the tracking page: the fiat set and the currency-basket weights.
# Basket weights must sum to 1.0.
DEFAULT = {
    "anchor": "2016-01-01",
    "smooth": 26,
    "currencies": ["USD", "EUR", "GBP", "JPY", "CHF"],
    "basket": {"USD": 0.2, "EUR": 0.2, "GBP": 0.2, "JPY": 0.2, "CHF": 0.2},
}


def compute(df: pd.DataFrame) -> dict:
    """Fit the law on ``df`` and return the dict to freeze.

    Only intended to be run once, against the month-old cached data that the
    repo shipped with — after that, the result is stored and never recomputed.
    """
    from . import backtest as bt  # noqa: PLC0415

    # Identical data prep to `lending.py`: forward-fill the price series and
    # drop day-rows where either series is missing, so the fitted law is
    # exactly the one lending.py used on the day it was last run.
    df = df.copy()
    df["price"] = df["price"].ffill()
    df = df.dropna(subset=["difficulty", "price"])

    anchor = DEFAULT["anchor"]
    smooth = DEFAULT["smooth"]
    fl = bt.fit_law(df, anchor, smooth)
    b = float(fl["b"])
    a = float(fl["a"])
    sd = analyze.sliding_smoothed_diff(df, smooth)
    anchor_ts = pd.Timestamp(anchor)
    idx = df.index.searchsorted(anchor_ts)
    D0 = float(sd.iloc[idx])
    P0 = float(df["price"].iloc[idx]) * np.exp(a)
    law = {
        "calc_date": str(df.index.max().date()),
        "anchor": anchor,
        "smooth": smooth,
        "b": b,
        "a": a,
        "P0": P0,
        "D0": D0,
    }
    return {**law, **DEFAULT}


def load(path: Path | None = None) -> dict:
    """Return the frozen law, defaults applied. Raises if not yet frozen."""
    path = path or FROZEN_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"no frozen law at {path} — run `python -m dbtc.frozen` once, "
            "using the original (month-old) cached data"
        )
    with open(path) as fh:
        law = json.load(fh)
    out = {**DEFAULT, **law}
    assert abs(sum(out["basket"].values()) - 1.0) < 1e-9, "basket weights must sum to 1"
    return out


def save(law: dict, path: Path | None = None) -> Path:
    """Persist a frozen law dict to disk."""
    path = path or FROZEN_PATH
    law = {**DEFAULT, **law}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(law, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


if __name__ == "__main__":
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dbtc import analyze as _a  # noqa: PLC0415

    print(f"[freeze] loading cached data from {DATA_DIR}/ ...")
    df = _a.load_data(data_dir=str(DATA_DIR))
    law = compute(df)
    print(f"[freeze] calc_date={law['calc_date']}  anchor={law['anchor']}  W={law['smooth']}p")
    print(f"         b={law['b']:.6f}  a={law['a']:.6f}")
    print(f"         P0=${law['P0']:,.2f}  D0={law['D0']:,.0f}")
    print(f"[freeze] writing {FROZEN_PATH}")
    save(law)