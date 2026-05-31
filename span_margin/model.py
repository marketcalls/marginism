"""Data model for the NSCCL / CME SPAN risk-parameter file (fileFormat 4.00).

Every contract in a SPAN file ships with a *precomputed* 16-element risk array,
so margin evaluation never requires option pricing — it is pure arithmetic over
these arrays.  The classes below are plain containers for what the parser reads
out of the ``.spn`` XML; the SPAN algorithm itself lives in :mod:`algorithm`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# The 16 SPAN price/volatility scan scenarios, in file order.  Fraction is the
# move as a multiple of the price-scan range; "extreme" rows already carry the
# exchange's cover fraction baked into the array values.
SCENARIO_LABELS: List[str] = [
    "price unch / vol up",
    "price unch / vol down",
    "price +1/3 / vol up",
    "price +1/3 / vol down",
    "price -1/3 / vol up",
    "price -1/3 / vol down",
    "price +2/3 / vol up",
    "price +2/3 / vol down",
    "price -2/3 / vol up",
    "price -2/3 / vol down",
    "price +3/3 / vol up",
    "price +3/3 / vol down",
    "price -3/3 / vol up",
    "price -3/3 / vol down",
    "price +extreme (cover)",
    "price -extreme (cover)",
]


@dataclass
class RiskArray:
    """The 16 scenario loss values (per one unit, long) plus composite delta.

    Convention: a *positive* value is a loss to a one-unit long position under
    that scenario.  A short position's loss is the negative of these values, so
    portfolio loss in a scenario is ``sum(signed_qty * value)``.
    """

    values: List[float]
    composite_delta: float = 0.0

    def __post_init__(self) -> None:
        if len(self.values) != 16:
            raise ValueError(
                f"SPAN risk array must have 16 values, got {len(self.values)}"
            )


@dataclass
class Contract:
    """Base for a single tradable contract within a combined commodity."""

    cc: str                 # combined-commodity code (== trading symbol on NSE)
    pf_id: int              # SPAN portfolio id
    pf_type: str            # 'FUT', 'OOP' (options), 'PHY'
    contract_id: int        # cId, sequence within the portfolio
    expiry: str             # period end 'YYYYMMDD' ('00000000' for cash/phy)
    price: float
    delta: float            # per-unit delta (BS delta for options)
    volatility: float
    cvf: float              # contract value factor (1.0 for NSE equity F&O)
    risk_array: RiskArray


@dataclass
class FuturesContract(Contract):
    pass


@dataclass
class OptionContract(Contract):
    option_type: str = "C"  # 'C' or 'P'
    strike: float = 0.0


@dataclass
class SpreadLeg:
    cc: str
    expiry: str             # period end this leg refers to
    side: str               # 'A' or 'B'
    ratio: float


@dataclass
class CalendarSpread:
    """An intra-commodity (calendar / inter-month) spread definition."""

    priority: int           # 'spread' number — evaluation order
    charge_method: str      # 'F' flat, 'P' per-month, 'W' weighted-price
    rate: float             # charge per spread (currency, per matched delta unit)
    legs: List[SpreadLeg] = field(default_factory=list)


@dataclass
class CombinedCommodity:
    """The unit of SPAN margining: futures + options on one underlying."""

    cc: str
    name: str = ""
    currency: str = "INR"
    som_method: str = "GROSS"          # short-option-minimum aggregation method
    som_rate: float = 0.0              # SOM charge per short option unit
    spot_method: str = "NORMAL"
    risk_exponent: float = 0.0
    underlying_price: float = 0.0      # spot price (from the PHY portfolio)

    futures: List[FuturesContract] = field(default_factory=list)
    options: List[OptionContract] = field(default_factory=list)
    physicals: List[Contract] = field(default_factory=list)
    spreads: List[CalendarSpread] = field(default_factory=list)

    # ---- lookup helpers -------------------------------------------------
    def find_future(self, expiry: str) -> Optional[FuturesContract]:
        for f in self.futures:
            if f.expiry == expiry:
                return f
        # if only one future and no expiry match, fall back to nearest
        return None

    def find_option(
        self, expiry: str, option_type: str, strike: float, tol: float = 1e-4
    ) -> Optional[OptionContract]:
        ot = _norm_option_type(option_type)
        for o in self.options:
            if (
                o.expiry == expiry
                and o.option_type == ot
                and abs(o.strike - strike) <= tol
            ):
                return o
        return None

    @property
    def expiries(self) -> List[str]:
        seen = []
        for c in self.futures + self.options:
            if c.expiry not in seen and c.expiry != "00000000":
                seen.append(c.expiry)
        return sorted(seen)


@dataclass
class SpanFile:
    """Top-level parsed SPAN file."""

    file_format: str = ""
    created: str = ""
    business_date: str = ""
    is_settlement: bool = False
    clearing_org: str = ""
    currency_conversions: Dict[str, float] = field(default_factory=dict)
    commodities: Dict[str, CombinedCommodity] = field(default_factory=dict)

    def get(self, symbol: str) -> Optional[CombinedCommodity]:
        return self.commodities.get(symbol.upper())

    def __contains__(self, symbol: str) -> bool:
        return symbol.upper() in self.commodities

    @property
    def symbols(self) -> List[str]:
        return sorted(self.commodities)


def _norm_option_type(option_type: str) -> str:
    """Map CE/PE/CALL/PUT/C/P to the file's 'C'/'P'."""
    o = (option_type or "").strip().upper()
    if o in ("C", "CE", "CALL"):
        return "C"
    if o in ("P", "PE", "PUT"):
        return "P"
    raise ValueError(f"unknown option type: {option_type!r}")
