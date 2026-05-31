"""The SPAN risk algorithm, evaluated over precomputed risk arrays.

Per combined commodity the SPAN risk requirement is::

    span_risk = max( scan_risk
                     + intracommodity (calendar) spread charge
                     + spot/delivery charge
                     - intercommodity spread credit ,
                     short_option_minimum )

For NSCCL equity & index F&O the file carries no intercommodity credits and a
zero spot charge, so those terms are zero unless present.  Scan risk and the
calendar-spread charge are the active components.

Inputs are *resolved positions*: each open position already matched to a
contract's risk array, with a signed quantity in underlying units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .model import (
    CalendarSpread,
    CombinedCommodity,
    Contract,
    OptionContract,
    SCENARIO_LABELS,
)


@dataclass
class ResolvedPosition:
    contract: Contract
    quantity: float          # signed, in underlying units


@dataclass
class CommodityResult:
    symbol: str
    scan_risk: float = 0.0
    worst_scenario: int = 0
    worst_scenario_label: str = ""
    calendar_spread_charge: float = 0.0
    spot_charge: float = 0.0
    intercommodity_credit: float = 0.0
    short_option_minimum: float = 0.0
    span_risk: float = 0.0           # the SPAN margin for this commodity
    net_option_value: float = 0.0    # market value of option positions
    scenario_losses: List[float] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _scan_risk(positions: List[ResolvedPosition]) -> Tuple[float, int, List[float]]:
    """Largest portfolio loss across the 16 scenarios.

    Returns (scan_risk, worst_scenario_index, per_scenario_losses).
    """
    losses = [0.0] * 16
    for p in positions:
        ra = p.contract.risk_array.values
        q = p.quantity
        for j in range(16):
            losses[j] += q * ra[j]
    worst = max(range(16), key=lambda j: losses[j])
    scan = max(0.0, losses[worst])
    return scan, worst, losses


def _net_delta_by_expiry(positions: List[ResolvedPosition]) -> Dict[str, float]:
    """Composite-delta-weighted net position per expiry (in delta units)."""
    deltas: Dict[str, float] = {}
    for p in positions:
        pe = p.contract.expiry
        cd = p.contract.risk_array.composite_delta
        deltas[pe] = deltas.get(pe, 0.0) + p.quantity * cd
    return deltas


def _calendar_spread_charge(
    spreads: List[CalendarSpread], net_delta: Dict[str, float]
) -> float:
    """Flat-rate intra-commodity (calendar) spread charge.

    Spreads are evaluated in priority order.  A spread between expiry A and B
    forms only when the two legs carry *opposite* signed delta (one net long,
    one net short).  The number of spreads is the smaller of the two
    ratio-adjusted leg deltas; the charge is ``spreads * rate`` (method 'F').
    Matched delta is consumed so later spreads see the remainder.
    """
    remaining = dict(net_delta)
    total = 0.0
    for spread in spreads:
        if spread.charge_method not in ("F", "P"):
            # 'W' (weighted-price) not used by NSCCL files; skip if it appears.
            continue
        if len(spread.legs) < 2:
            continue
        leg_a = next((l for l in spread.legs if l.side == "A"), spread.legs[0])
        leg_b = next((l for l in spread.legs if l.side == "B"), spread.legs[1])
        da = remaining.get(leg_a.expiry, 0.0)
        db = remaining.get(leg_b.expiry, 0.0)
        if da == 0.0 or db == 0.0:
            continue
        # opposite signs => a genuine calendar spread
        if (da > 0) == (db > 0):
            continue
        ratio_a = leg_a.ratio or 1.0
        ratio_b = leg_b.ratio or 1.0
        n = min(abs(da) / ratio_a, abs(db) / ratio_b)
        if n <= 0:
            continue
        total += n * spread.rate
        # consume matched delta toward zero
        remaining[leg_a.expiry] = da - (1 if da > 0 else -1) * n * ratio_a
        remaining[leg_b.expiry] = db - (1 if db > 0 else -1) * n * ratio_b
    return total


def _short_option_minimum(
    commodity: CombinedCommodity, positions: List[ResolvedPosition]
) -> float:
    """SOM = som_rate * total short option units (NSCCL ships som_rate=0)."""
    if commodity.som_rate <= 0:
        return 0.0
    short_units = 0.0
    for p in positions:
        if isinstance(p.contract, OptionContract) and p.quantity < 0:
            short_units += abs(p.quantity)
    return commodity.som_rate * short_units


def _net_option_value(positions: List[ResolvedPosition]) -> float:
    nov = 0.0
    for p in positions:
        if isinstance(p.contract, OptionContract):
            nov += p.quantity * p.contract.price * p.contract.cvf
    return nov


def compute_commodity(
    commodity: CombinedCommodity, positions: List[ResolvedPosition]
) -> CommodityResult:
    """Run the SPAN algorithm for one combined commodity."""
    res = CommodityResult(symbol=commodity.cc)
    if not positions:
        return res

    scan, worst, losses = _scan_risk(positions)
    res.scan_risk = scan
    res.worst_scenario = worst + 1  # 1-based for reporting
    res.worst_scenario_label = SCENARIO_LABELS[worst]
    res.scenario_losses = losses

    net_delta = _net_delta_by_expiry(positions)
    res.calendar_spread_charge = _calendar_spread_charge(
        commodity.spreads, net_delta
    )
    res.short_option_minimum = _short_option_minimum(commodity, positions)
    res.net_option_value = _net_option_value(positions)

    risk = (
        res.scan_risk
        + res.calendar_spread_charge
        + res.spot_charge
        - res.intercommodity_credit
    )
    risk = max(risk, res.short_option_minimum)
    # CME SPAN: Total requirement = SPAN risk - Net Option Value.
    # NOV is negative for net-short options (premium owed) -> raises margin;
    # positive for net-long options (premium owned) -> lowers margin toward 0,
    # which is why long options need only the premium, not SPAN.
    res.span_risk = max(0.0, risk - res.net_option_value)
    return res
