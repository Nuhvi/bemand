"""Daily FX rates (USD base) from the ECB via the Frankfurter API.

Frankfurter (https://frankfurter.app) is a free, key-less JSON mirror of the
ECB's daily reference rates, with full CORS support — so the tracking page can
also refresh FX straight from the user's browser. This module mirrors
``download.py``: cache to ``data/fx.json`` and only re-fetch when the newest
available date is stale.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
API = "https://api.frankfurter.app"
CACHE = "fx.json"

# Currencies tracked (all resolve as units of local currency per 1 USD).
BASE = "USD"
CURRENCIES = ["EUR", "GBP", "JPY", "CHF"]

MAX_AGE = int(22 * 3600)  # same policy as download.py


def _cache_path() -> Path:
    return DATA_DIR / CACHE


def _is_fresh(path: Path, max_age: int = MAX_AGE, tolerance: int = 3 * 86400) -> bool:
    """Fresh whenever the cached series contains a rate from ``max_age`` + tol
    of today (ECB is business-days only; weekends/holidays have no new rate)."""
    if not path.exists():
        return False
    try:
        with open(path) as fh:
            data = json.load(fh)
        last = max(data["rates"].keys())
    except (OSError, KeyError, ValueError):
        return False
    from datetime import datetime

    age = time.time() - datetime.strptime(last, "%Y-%m-%d").replace(
        tzinfo=__import__("datetime").timezone.utc
    ).timestamp()
    return age < max_age + tolerance


def fetch_series(start: str, end: str) -> dict:
    """Fetch {date: {CUR: rate}} for USD->CURRENCIES between start..end."""
    url = f"{API}/{start}..{end}"
    params = {"from": BASE, "to": ",".join(CURRENCIES)}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return {d: r for d, r in data.get("rates", {}).items()}


def load(start: str = "2014-01-01", force: bool = False) -> pd.Series:
    """Return daily USD->CUR rates as a DataFrame indexed by date.

    ``start`` only matters on first fetch; afterwards the cache is extended
    forward in time from its latest stored rate. Rates are forward-filled so
    every calendar day (including WE) has a value.
    """
    from datetime import date, datetime, timedelta

    path = _cache_path()
    rates: dict[str, dict] = {}
    if not force and path.exists():
        try:
            with open(path) as fh:
                cached = json.load(fh)
            rates = cached.get("rates", {})
        except (OSError, ValueError):
            rates = {}

    latest = max(rates.keys(), default=None)
    today = date.today().isoformat()
    if not _is_fresh(path) or force:
        lo = latest or start
        chunk = fetch_series(lo, today)
        rates.update(chunk)

    if not rates:
        raise RuntimeError(f"no FX rates available (cache at {path})")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"base": BASE, "currencies": CURRENCIES, "rates": rates}, fh)

    df = pd.DataFrame(rates).T
    df.index = pd.to_datetime(df.index)
    df = df.sort_index().ffill()
    return df


def stale() -> list[str]:
    """Names of feed components needing refresh (mirror download.stale_files)."""
    paths = [("fx", _cache_path())]
    return [name for name, p in paths if not _is_fresh(p)]


if __name__ == "__main__":
    fx = load(force=True)
    print(fx.tail(3))