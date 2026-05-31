"""NFO tradingsymbol <-> SPAN contract resolution.

The SPAN file keys contracts by ``(symbol, expiry, strike, type)``; orders key
them by ``tradingsymbol`` (e.g. ``NIFTY26JUN24000CE``, ``NIFTY2660224000CE``
weekly, ``RELIANCE26JUNFUT``) using the standard NSE F&O convention. Rather than
fragile parsing, we *generate* the tradingsymbol for every contract present in
the loaded SPAN file and build a reverse index — robust because it only ever
maps symbols the file actually contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .model import SpanFile

_MMM = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
# weekly month code: 1-9 then O/N/D for Oct/Nov/Dec
_WCODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
          7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D"}


def _strike_str(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else str(strike)


@dataclass
class ResolvedSymbol:
    symbol: str           # combined-commodity / underlying (e.g. NIFTY)
    instrument: str       # 'FUT', 'CE', 'PE'
    expiry: str           # YYYYMMDD
    strike: float = 0.0


def _monthly_expiries(expiries: List[str]) -> set:
    """The monthly expiry of each (year, month) = the latest date in it."""
    by_month: Dict[str, str] = {}
    for e in expiries:
        if len(e) != 8:
            continue
        ym = e[:6]
        if ym not in by_month or e > by_month[ym]:
            by_month[ym] = e
    return set(by_month.values())


def _fut_symbol(root: str, expiry: str) -> str:
    yy, mm = expiry[2:4], int(expiry[4:6])
    return f"{root}{yy}{_MMM[mm]}FUT"


def _opt_symbol(root: str, expiry: str, strike: float, cp: str, monthly: bool) -> str:
    yy = expiry[2:4]
    mm = int(expiry[4:6])
    dd = expiry[6:8]
    ks = _strike_str(strike)
    suffix = "CE" if cp.upper().startswith("C") else "PE"  # file stores C/P
    if monthly:
        return f"{root}{yy}{_MMM[mm]}{ks}{suffix}"
    return f"{root}{yy}{_WCODE[mm]}{dd}{ks}{suffix}"


# ---- Full-date symbol format: [SYMBOL][DDMMMYY][STRIKE][CE/PE] ----------
# Uses the full DDMMMYY date for every expiry (no compressed weekly form),
# e.g. NIFTY30JUN26FUT, NIFTY30JUN2623700CE.
def _oa_fut_symbol(root: str, expiry: str) -> str:
    yy, mm, dd = expiry[2:4], int(expiry[4:6]), expiry[6:8]
    return f"{root}{dd}{_MMM[mm]}{yy}FUT"


def _oa_opt_symbol(root: str, expiry: str, strike: float, cp: str) -> str:
    yy, mm, dd = expiry[2:4], int(expiry[4:6]), expiry[6:8]
    suffix = "CE" if cp.upper().startswith("C") else "PE"
    return f"{root}{dd}{_MMM[mm]}{yy}{_strike_str(strike)}{suffix}"


def build_symbol_index(span_file: SpanFile) -> Dict[str, ResolvedSymbol]:
    """Map every generatable tradingsymbol -> ResolvedSymbol."""
    index: Dict[str, ResolvedSymbol] = {}
    for sym, cmty in span_file.commodities.items():
        for f in cmty.futures:
            rs = ResolvedSymbol(sym, "FUT", f.expiry)
            index[_fut_symbol(sym, f.expiry)] = rs      # compact style
            index[_oa_fut_symbol(sym, f.expiry)] = rs   # full-date style
        opt_expiries = sorted({o.expiry for o in cmty.options})
        monthly = _monthly_expiries(opt_expiries)
        for o in cmty.options:
            rs = ResolvedSymbol(sym, o.option_type, o.expiry, o.strike)
            index[_opt_symbol(sym, o.expiry, o.strike, o.option_type,
                              o.expiry in monthly)] = rs            # compact style
            index[_oa_opt_symbol(sym, o.expiry, o.strike, o.option_type)] = rs  # full-date style
    return index


class SymbolResolver:
    """Resolve tradingsymbols to SPAN contracts for a loaded file."""

    def __init__(self, span_file: SpanFile) -> None:
        self._index = build_symbol_index(span_file)

    def resolve(self, tradingsymbol: str) -> Optional[ResolvedSymbol]:
        return self._index.get(tradingsymbol.strip().upper())

    def tradingsymbols(self) -> List[str]:
        return sorted(self._index)
