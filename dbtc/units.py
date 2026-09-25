"""Units & notation (dbtc/units.py).

DBTC is a *Bemand* (Bi`t`coin demand). The unit apps show is the **bem**:

    1 DBTC   = 10^6 bem   (the metric *micro*DBTC)
    1 bem    = 10^-6 DBTC

On-chain the token uses EVM ``decimals() = 18``, so 1 DBTC = 10^18 base units
and 1 bem = 10^12 base units (a *picobem*). The bem is the app/accounting unit
(``Ƃ1.00``) but it is NOT atomic on-chain: fractions of a bem, down to 10^-12,
are fully transactable — exactly like fractional USDT/USD amounts. Apps render
two decimals by default and round only at display time. The 64.64 fixed-point
oracle is ~10^13x finer than a bem (one 64.64 quantum ≈ 0.054 base units), so a
ratio -> base-units conversion rounds to < 1 base unit and loses nothing.

The display glyph is ``Ƃ`` (U+0182, "BCU"), so amounts read ``Ƃ1.00``.
"""

from __future__ import annotations

#: Bems per DBTC — the metric prefix: bem = *micro*DBTC.
BEMS_PER_DBTC = 10**6
#: ERC-20 decimals() convention for the token (EVM style, e.g. RBTC): keeps the
#: bem fractional — 1 base unit = 10^-12 bem.
TOKEN_DECIMALS = 18
#: On-chain base (atomic) units per bem == 10^18 / 10^6.
BASE_UNITS_PER_BEM = 10**12
#: Display glyph for the bem: U+0182 LATIN CAPITAL LETTER B WITH TOPBAR.
BCU = "\u0182"


def to_bem(dbtc: float) -> float:
    """Convert a DBTC quantity into bems."""
    return dbtc * BEMS_PER_DBTC


def bem_per_dbtc_usd(dbtc_usd: float) -> float:
    """USD value of one bem, given the USD value of one DBTC."""
    return dbtc_usd / BEMS_PER_DBTC


def to_base_units(bems: float) -> int:
    """Convert a (possibly fractional) bem amount into integer on-chain base
    units (rounds; 1 base unit = 10^-12 bem)."""
    return int(round(bems * BASE_UNITS_PER_BEM))


def fmt_bem(bems: float, decimals: int = 2) -> str:
    """Format a bem quantity as ``Ƃ1,234.56`` with thousands grouping."""
    return f"{BCU}{bems:,.{decimals}f}"


def fmt_usd_bem(dbtc_usd: float, decimals: int = 2) -> str:
    """``1 bem = $0.09`` for a given DBTC/USD price."""
    return f"${bem_per_dbtc_usd(dbtc_usd):,.{decimals}f}"