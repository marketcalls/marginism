"""High-level SPAN margin calculator — the public entry point."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .algorithm import CommodityResult, ResolvedPosition, compute_commodity
from .exposure import ExposureConfig
from .model import OptionContract, SpanFile
from .parser import parse_spn
from .portfolio import Position, normalize_expiry


@dataclass
class PositionResult:
    position: Position
    matched: bool
    contract_price: float = 0.0
    notional: float = 0.0
    note: str = ""


@dataclass
class MarginResult:
    """Full margin breakdown for a portfolio."""

    span_margin: float = 0.0
    exposure_margin: float = 0.0
    adhoc_margin: float = 0.0
    expiry_day_elm: float = 0.0   # extra ELM on short options expiring today
    net_option_value: float = 0.0
    by_commodity: Dict[str, CommodityResult] = field(default_factory=dict)
    positions: List[PositionResult] = field(default_factory=list)
    unmatched: List[Position] = field(default_factory=list)

    @property
    def total_margin(self) -> float:
        """Upfront initial margin = SPAN + Exposure + Adhoc + expiry-day ELM.

        Premium for long options is collected separately, not part of margin.
        """
        return (self.span_margin + self.exposure_margin + self.adhoc_margin
                + self.expiry_day_elm)

    def summary(self) -> str:
        lines = [
            "SPAN Margin Summary",
            "=" * 52,
            f"  SPAN margin      : {self.span_margin:>16,.2f}",
            f"  Exposure margin  : {self.exposure_margin:>16,.2f}",
        ]
        if self.adhoc_margin:
            lines.append(f"  Adhoc margin     : {self.adhoc_margin:>16,.2f}")
        if self.expiry_day_elm:
            lines.append(f"  Expiry-day ELM   : {self.expiry_day_elm:>16,.2f}")
        lines += [
            f"  {'-'*46}",
            f"  Total margin     : {self.total_margin:>16,.2f}",
            f"  Net option value : {self.net_option_value:>16,.2f}",
            "",
            "Per combined commodity:",
        ]
        for sym, r in self.by_commodity.items():
            lines.append(f"  [{sym}]")
            lines.append(
                f"      scan risk        : {r.scan_risk:>14,.2f}"
                f"   (worst: scenario {r.worst_scenario} - {r.worst_scenario_label})"
            )
            lines.append(f"      calendar spread  : {r.calendar_spread_charge:>14,.2f}")
            if r.short_option_minimum:
                lines.append(
                    f"      short opt minimum: {r.short_option_minimum:>14,.2f}"
                )
            lines.append(f"      SPAN risk        : {r.span_risk:>14,.2f}")
        if self.unmatched:
            lines.append("")
            lines.append("Unmatched positions (no contract found in file):")
            for p in self.unmatched:
                lines.append(
                    f"  - {p.symbol} {p.instrument} "
                    f"{p.expiry or ''} {p.strike or ''} qty={p.quantity}"
                )
        return "\n".join(lines)


class SpanCalculator:
    """Load a SPAN file once, then evaluate many portfolios against it.

    Example
    -------
    >>> calc = SpanCalculator.from_file("nsccl.20260529.s.spn",
    ...                                 symbols=["NIFTY", "RELIANCE"])
    >>> result = calc.calculate([
    ...     Position("NIFTY", "FUT", quantity=-75, expiry="20260630"),
    ...     Position("NIFTY", "CE", quantity=75, expiry="20260630", strike=24000),
    ... ])
    >>> print(result.summary())
    """

    def __init__(
        self,
        span_file: SpanFile,
        exposure: Optional[ExposureConfig] = None,
    ) -> None:
        self.span_file = span_file
        self.exposure = exposure or ExposureConfig()

    @classmethod
    def from_file(
        cls,
        path: str,
        symbols: Optional[Iterable[str]] = None,
        exposure: Optional[ExposureConfig] = None,
    ) -> "SpanCalculator":
        return cls(parse_spn(path, symbols=symbols), exposure=exposure)

    # ------------------------------------------------------------------
    def _resolve(self, position: Position):
        """Match a position to a contract in the file. Returns (contract, note)."""
        cmty = self.span_file.get(position.symbol)
        if cmty is None:
            return None, f"symbol {position.symbol} not in SPAN file"

        if position.is_future:
            fut = cmty.find_future(position.expiry)
            if fut is None and len(cmty.futures) == 1 and not position.expiry:
                fut = cmty.futures[0]
            if fut is None:
                return None, (
                    f"future {position.symbol} {position.expiry} not found"
                )
            return fut, ""

        if position.is_option:
            opt = cmty.find_option(
                position.expiry, position.instrument, position.strike
            )
            if opt is None:
                return None, (
                    f"option {position.symbol} {position.instrument} "
                    f"{position.expiry} {position.strike} not found"
                )
            return opt, ""

        return None, f"unsupported instrument {position.instrument}"

    def calculate(
        self, positions: List[Position], as_of_date: Optional[str] = None
    ) -> MarginResult:
        """Compute margins for a list of positions.

        ``as_of_date`` is the trading date used to detect options expiring
        *today* (which attract an extra expiry-day ELM on short positions).
        It accepts any common date format; if omitted it defaults to the SPAN
        file's business date — so loading the expiry-day file applies the
        add-on automatically. Set ``ExposureConfig.expiry_day_elm_pct = 0`` to
        disable.
        """
        result = MarginResult()
        by_cmty: Dict[str, List[ResolvedPosition]] = {}
        today = normalize_expiry(as_of_date) if as_of_date else self.span_file.business_date

        for pos in positions:
            contract, note = self._resolve(pos)
            if contract is None:
                result.unmatched.append(pos)
                result.positions.append(
                    PositionResult(position=pos, matched=False, note=note)
                )
                continue
            qty = pos.quantity   # quantity is in units (e.g. 65 = 1 NIFTY lot)
            cmty = self.span_file.get(pos.symbol)
            # Exposure margin is charged on CONTRACT VALUE (NSE convention):
            #   futures -> futures price x qty
            #   options -> SPOT price  x qty   (underlying value, not premium)
            if pos.is_option and cmty is not None and cmty.underlying_price > 0:
                exposure_price = cmty.underlying_price
            else:
                exposure_price = contract.price
            notional = abs(qty) * exposure_price * contract.cvf
            result.positions.append(
                PositionResult(
                    position=pos,
                    matched=True,
                    contract_price=contract.price,
                    notional=notional,
                )
            )
            by_cmty.setdefault(pos.symbol, []).append(
                ResolvedPosition(contract=contract, quantity=qty)
            )
            # Exposure margin (% of notional) applies to futures and SHORT
            # options.  Long options carry no exposure margin (the buyer's
            # risk is capped at the premium paid).
            if pos.is_future or (pos.is_option and qty < 0):
                rate = self.exposure.rate(pos.symbol, is_option=pos.is_option)
                result.exposure_margin += rate * notional
                result.adhoc_margin += self.exposure.adhoc_rate(pos.symbol) * notional
                # Extra ELM on a SHORT option that expires on the trading date.
                if (pos.is_option and qty < 0 and today
                        and pos.expiry == today):
                    result.expiry_day_elm += (
                        self.exposure.expiry_day_elm_pct * notional
                    )

        for sym, resolved in by_cmty.items():
            cmty = self.span_file.get(sym)
            cres = compute_commodity(cmty, resolved)
            result.by_commodity[sym] = cres
            result.span_margin += cres.span_risk
            result.net_option_value += cres.net_option_value

        return result
