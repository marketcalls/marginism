"""Exposure / Extreme-Loss Margin (ELM).

The SPAN file defines only the SPAN (scan + spread) risk.  A broker's total
*initial margin* on NSE is::

    initial_margin = SPAN margin + Exposure margin (ELM)

Exposure margin is set by the exchange as a percentage of notional and is **not**
part of the SPAN parameter file, so it must be supplied here.  These are the
standard NSE F&O defaults; override per-symbol as the circulars change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ExposureConfig:
    """Exposure-margin rates as a fraction of notional value.

    NSE defaults (subject to exchange circulars):
      * Index futures / options : 2%   (matches broker calculators)
      * Stock  futures / options: ~3.5% (higher of 3.5% or 1.5 * stdev)
    Short options attract exposure on the notional; long options none.
    """

    index_futures_pct: float = 0.02
    index_options_pct: float = 0.02
    stock_futures_pct: float = 0.035
    stock_options_pct: float = 0.035
    # explicit per-symbol ELM overrides (fraction of notional)
    overrides: Dict[str, float] = None  # type: ignore
    # Adhoc / additional margin imposed by the exchange (fraction of notional).
    # Not part of the SPAN file; published separately on a case-by-case basis.
    # Use adhoc_default for an across-the-board add-on, or per-symbol via adhoc.
    adhoc_default: float = 0.0
    adhoc: Dict[str, float] = None  # type: ignore
    index_symbols: tuple = (
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50",
    )

    def __post_init__(self) -> None:
        if self.overrides is None:
            self.overrides = {}
        if self.adhoc is None:
            self.adhoc = {}

    def rate(self, symbol: str, is_option: bool) -> float:
        """ELM (extreme-loss margin) rate as a fraction of notional."""
        sym = symbol.upper()
        if sym in self.overrides:
            return self.overrides[sym]
        is_index = sym in self.index_symbols
        if is_index:
            return self.index_options_pct if is_option else self.index_futures_pct
        return self.stock_options_pct if is_option else self.stock_futures_pct

    def adhoc_rate(self, symbol: str) -> float:
        """Adhoc / additional margin rate as a fraction of notional."""
        return self.adhoc.get(symbol.upper(), self.adhoc_default)
