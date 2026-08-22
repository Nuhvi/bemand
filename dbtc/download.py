"""Download Bitcoin difficulty and market-price data from blockchain.info.

Data is cached on disk in ``data/``. If a cached file is younger than
``MAX_AGE`` it is reused; otherwise it is re-downloaded. This keeps repeated
runs of the analysis instant while still refreshing roughly once a day.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
API = "https://api.blockchain.info/charts/{chart}"

# Charts we want. Chosen because both have full history back to 2009.
CHARTS = {
    "difficulty": "difficulty",  # daily network difficulty (base-2 units)
    "market_price": "market-price",  # daily USD market price
}

# Re-download anything older than this (in seconds). ~22 hours.
MAX_AGE = int(22 * 3600)


def _cache_path(chart: str) -> Path:
    return DATA_DIR / f"{chart}.json"


def _is_fresh(path: Path, max_age: int = MAX_AGE) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < max_age


def fetch_chart(chart: str, force: bool = False, max_age: int = MAX_AGE) -> dict:
    """Return parsed chart JSON, downloading if missing or stale."""
    params = {"timespan": "all", "format": "json", "sampled": "false"}
    path = _cache_path(chart)
    if not force and _is_fresh(path, max_age):
        with open(path) as fh:
            return json.load(fh)

    url = f"https://api.blockchain.info/charts/{CHARTS[chart]}"
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


def stale_files() -> list[str]:
    """Names of cached charts that need refreshing (or that are missing)."""
    return [c for c in CHARTS if not _is_fresh(_cache_path(c))]


def all_charts(force: bool = False, max_age: int = MAX_AGE) -> dict[str, dict]:
    """Fetch every configured chart, returning {chart_key: chart_json}."""
    out = {}
    for chart in CHARTS:
        out[chart] = fetch_chart(chart, force=force, max_age=max_age)
    return out