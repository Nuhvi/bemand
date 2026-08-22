__all__ = ["download", "analyze", "plot"]

# Shared chart palette. Rule: spot is ALWAYS orange, DBTC/difficulty
# ALWAYS green; the rest are fixed so every diagram uses identical colours.
COLORS = {
    "dbtc": "#1f9d55",   # DBTC (difficulty/demand-derived)
    "wma":  "#7b1fa2",   # wma SMA-350 ≈ 50w accounting anchor
    "wma200": "#d6336c", # wma200 SMA-1400 ≈ 200w reference line
    "spot": "#f76707",   # raw BTC/USD
}


def shade(color: str, f: float = 0.35) -> str:
    """Blend a hex colour toward white by f (0..1) — a 'light' variant."""
    h = color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"