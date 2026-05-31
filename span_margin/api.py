"""High-level margins API — plain Python functions, computed locally.

Anyone with a SPAN ``.spn`` file can compute margins offline:

    from span_margin import RiskEngine
    eng = RiskEngine.from_file("nsccl.20260529.s.spn")
    result = eng.basket([
        {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL",
         "quantity": 65},
    ])

Works for a **single leg or many legs** — a single order is just a basket of
size one. ``basket()``/``orders()`` take a list of order dicts (``exchange``,
``tradingsymbol``, ``transaction_type``, ``quantity`` ...) and return per-leg
figures plus consolidated ``initial``/``final`` and the hedging
``margin_benefit``. The result dict uses a standard, broker-style field layout
(``span``/``exposure``/``option_premium``/``additional``/``total``) so it slots
into existing tooling — but it is pure local computation, no network, no service.

The engine is exchange-agnostic: point it at any CME-SPAN ``.spn`` file
(NFO / CDS / MCX) — the algorithm is the same; only the file differs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .calculator import SpanCalculator
from .portfolio import Position
from .symbols import SymbolResolver

_EMPTY_CHARGES = {
    "transaction_tax": 0.0,
    "transaction_tax_type": "",
    "exchange_turnover_charge": 0.0,
    "sebi_turnover_charge": 0.0,
    "brokerage": 0.0,
    "stamp_duty": 0.0,
    "gst": {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "total": 0.0},
    "total": 0.0,
}


def _leg_block(
    tradingsymbol: str,
    exchange: str,
    span: float,
    exposure: float,
    option_premium: float,
    additional: float,
    total: float,
) -> Dict[str, Any]:
    """One entry in the margins response (per-order or consolidated)."""
    return {
        "type": "equity",
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "span": round(span, 2),
        "exposure": round(exposure, 2),
        "option_premium": round(option_premium, 2),
        "additional": round(additional, 2),
        "bo": 0.0,
        "cash": 0.0,
        "var": 0.0,
        "pnl": {"realised": 0.0, "unrealised": 0.0},
        "leverage": 1.0,
        "charges": dict(_EMPTY_CHARGES),
        "total": round(total, 2),
    }


class RiskEngine:
    """Compute broker-style margins for baskets of orders against a SPAN file."""

    def __init__(self, calculator: SpanCalculator) -> None:
        self.calc = calculator
        self.resolver = SymbolResolver(calculator.span_file)

    @classmethod
    def from_file(cls, spn_path: str, **kw) -> "RiskEngine":
        return cls(SpanCalculator.from_file(spn_path, **kw))

    # -- order -> internal Position ------------------------------------
    def _to_position(self, order: Dict[str, Any]) -> Position:
        """Accept either a tradingsymbol OR explicit (symbol, expiry, ...).

        Two equivalent ways to specify a leg:

          {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL",
           "quantity": 65}

          {"symbol": "NIFTY", "instrument": "CE", "expiry": "2026-06-30",
           "strike": 23700, "transaction_type": "SELL", "quantity": 65}
        """
        side = str(order.get("transaction_type", "BUY")).upper()
        sign = -1 if side in ("SELL", "S") else 1
        qty = abs(float(order["quantity"])) * sign  # quantity is in units

        ts = order.get("tradingsymbol")
        if ts:
            rs = self.resolver.resolve(ts)
            if rs is None:
                raise KeyError(f"tradingsymbol not found in SPAN file: {ts}")
            instr = "FUT" if rs.instrument == "FUT" else (
                "CE" if rs.instrument == "C" else "PE"
            )
            return Position(rs.symbol, instr, quantity=qty,
                            expiry=rs.expiry, strike=rs.strike)

        # explicit fields (trader-friendly: symbol + expiry + strike + type)
        symbol = order.get("symbol")
        if not symbol:
            raise KeyError("order needs either 'tradingsymbol' or 'symbol'")
        instr = str(order.get("instrument") or order.get("option_type")
                    or "FUT").upper()
        return Position(symbol, instr, quantity=qty,
                        expiry=order.get("expiry"),
                        strike=float(order.get("strike", 0) or 0))

    @staticmethod
    def _label(order: Dict[str, Any]) -> str:
        """Display name for a leg (tradingsymbol, or built from fields)."""
        ts = order.get("tradingsymbol")
        if ts:
            return ts
        parts = [str(order.get("symbol", "")), str(order.get("expiry", "")),
                 str(order.get("strike", "") or ""),
                 str(order.get("instrument") or order.get("option_type") or "")]
        return " ".join(p for p in parts if p)

    @staticmethod
    def _split(result):
        """Map a MarginResult to (span, exposure, option_premium, additional)."""
        span = result.span_margin
        exposure = result.exposure_margin
        additional = result.adhoc_margin
        # premium *payable* only for net-long options (a debit, not margin)
        option_premium = max(0.0, result.net_option_value)
        return span, exposure, option_premium, additional

    def basket(
        self,
        orders: List[Dict[str, Any]],
        consider_positions: bool = True,
    ) -> Dict[str, Any]:
        """Consolidated basket margin with hedging benefit (1..N legs)."""
        positions: List[Position] = []
        leg_blocks: List[Dict[str, Any]] = []

        # ---- per-leg (standalone) margins -> orders[] ----------------
        sum_margin_only = 0.0   # span+exposure+additional, excl. premium
        for order in orders:
            pos = self._to_position(order)
            positions.append(pos)
            r = self.calc.calculate([pos])
            span, exposure, prem, add = self._split(r)
            # per-order: a long option's "total" is its premium payable
            total = span + exposure + add + prem
            leg = _leg_block(self._label(order), order.get("exchange", "NFO"),
                             span, exposure, prem, add, total)
            leg_blocks.append(leg)
            sum_margin_only += span + exposure + add

        # ---- consolidated basket (with hedging benefit) -> final -----
        R = self.calc.calculate(positions)
        c_span, c_exp, c_prem, c_add = self._split(R)
        # Basket margin = SPAN + exposure + additional. Long-option premium is
        # already netted into SPAN via -NOV (matches the broker basket total);
        # it is reported separately in `option_premium`.
        final_total = c_span + c_exp + c_add
        final = _leg_block("", "", c_span, c_exp, c_prem, c_add, final_total)
        initial = dict(final)  # single-snapshot file: initial == final

        # Hedging benefit = margin saved vs holding each leg outright
        # (margin-only; option premium is a cost, not a margin, so excluded).
        margin_benefit = round(sum_margin_only - final_total, 2)

        return {
            "status": "success",
            "data": {
                "initial": initial,
                "final": final,
                "orders": leg_blocks,
                "margin_benefit": margin_benefit,
            },
        }

    def orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Per-order margins, no netting (each leg standalone)."""
        out = []
        for order in orders:
            pos = self._to_position(order)
            r = self.calc.calculate([pos])
            span, exposure, prem, add = self._split(r)
            total = span + exposure + add + prem
            out.append(_leg_block(self._label(order), order.get("exchange", "NFO"),
                                  span, exposure, prem, add, total))
        return {"status": "success", "data": out}
