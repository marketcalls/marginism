"""Tests for the SPAN margin calculator (SpanCalculator, RiskEngine, algorithm).

Uses in-memory SpanFile objects so no real .spn files are needed.
"""

from __future__ import annotations

import pytest

from marginism.algorithm import compute_commodity
from marginism.calculator import MarginResult, SpanCalculator
from marginism.api import RiskEngine
from marginism.exposure import ExposureConfig
from marginism.model import (
    CalendarSpread,
    CombinedCommodity,
    FuturesContract,
    OptionContract,
    RiskArray,
    SpanFile,
    SpreadLeg,
)
from marginism.portfolio import Position


# ---------------------------------------------------------------------------
# Minimal in-memory SPAN file builder
# ---------------------------------------------------------------------------

def _risk_array(base: float = 1000.0) -> RiskArray:
    """Create a 16-element risk array where scenario 11 has the largest loss."""
    values = [
        base * 0.1,   # 0: price unch / vol up
        base * 0.0,   # 1: price unch / vol down
        base * 0.3,   # 2: price +1/3 / vol up
        base * 0.2,   # 3: price +1/3 / vol down
        -base * 0.3,  # 4: price -1/3 / vol up  (gain)
        -base * 0.2,  # 5: price -1/3 / vol down
        base * 0.6,   # 6: price +2/3 / vol up
        base * 0.5,   # 7: price +2/3 / vol down
        -base * 0.6,  # 8: price -2/3 / vol up
        -base * 0.5,  # 9: price -2/3 / vol down
        base * 1.0,   # 10: price +3/3 / vol up  ← worst
        base * 0.9,   # 11: price +3/3 / vol down
        -base * 1.0,  # 12: price -3/3 / vol up  (gain)
        -base * 0.9,  # 13: price -3/3 / vol down
        base * 0.3,   # 14: +extreme
        base * 0.3,   # 15: -extreme
    ]
    return RiskArray(values=values, composite_delta=1.0)


def _option_risk_array(base: float = 800.0, delta: float = 0.45) -> RiskArray:
    values = [
        base * 0.08,  base * 0.00,
        base * 0.25,  base * 0.15,
        -base * 0.25, -base * 0.15,
        base * 0.50,  base * 0.40,
        -base * 0.50, -base * 0.40,
        base * 0.80,  base * 0.70,
        -base * 0.80, -base * 0.70,
        base * 0.25,  base * 0.25,
    ]
    return RiskArray(values=values, composite_delta=delta)


def _make_span_file(
    underlying_price: float = 24000.0,
    fut_price: float = 24050.0,
    expiry: str = "20261225",
    far_expiry: str = "20270327",
    lot_size_factor: float = 1.0,
    add_far_future: bool = True,
) -> SpanFile:
    """Build a minimal SpanFile with one underlying (TESTSYM)."""
    spread_legs = [
        SpreadLeg(cc="TESTSYM", expiry=expiry, side="A", ratio=1.0),
        SpreadLeg(cc="TESTSYM", expiry=far_expiry, side="B", ratio=1.0),
    ]
    spread = CalendarSpread(
        priority=1,
        charge_method="F",
        rate=200.0,
        legs=spread_legs,
    )

    futures = [
        FuturesContract(
            cc="TESTSYM",
            pf_id=1,
            pf_type="FUT",
            contract_id=1,
            expiry=expiry,
            price=fut_price,
            delta=1.0,
            volatility=0.15,
            cvf=1.0,
            risk_array=_risk_array(1000.0),
        ),
    ]
    if add_far_future:
        futures.append(
            FuturesContract(
                cc="TESTSYM",
                pf_id=1,
                pf_type="FUT",
                contract_id=2,
                expiry=far_expiry,
                price=fut_price + 50,
                delta=1.0,
                volatility=0.16,
                cvf=1.0,
                risk_array=_risk_array(1050.0),
            )
        )

    options = [
        OptionContract(
            cc="TESTSYM",
            pf_id=2,
            pf_type="OOP",
            contract_id=100,
            expiry=expiry,
            price=150.0,
            delta=0.45,
            volatility=0.18,
            cvf=1.0,
            risk_array=_option_risk_array(800.0, 0.45),
            option_type="C",
            strike=24000.0,
        ),
        OptionContract(
            cc="TESTSYM",
            pf_id=2,
            pf_type="OOP",
            contract_id=101,
            expiry=expiry,
            price=80.0,
            delta=-0.55,
            volatility=0.19,
            cvf=1.0,
            risk_array=_option_risk_array(750.0, -0.55),
            option_type="P",
            strike=24000.0,
        ),
    ]

    cmty = CombinedCommodity(
        cc="TESTSYM",
        name="Test Symbol",
        currency="INR",
        som_rate=0.0,
        underlying_price=underlying_price,
        futures=futures,
        options=options,
        spreads=[spread],
    )

    sf = SpanFile()
    sf.business_date = expiry[:8]
    sf.commodities["TESTSYM"] = cmty
    return sf


# ---------------------------------------------------------------------------
# Algorithm tests
# ---------------------------------------------------------------------------


class TestComputeCommodity:
    def setup_method(self):
        sf = _make_span_file()
        self.cmty = sf.get("TESTSYM")

    def test_single_long_future_scan_risk(self):
        from marginism.algorithm import ResolvedPosition

        fut = self.cmty.futures[0]
        positions = [ResolvedPosition(contract=fut, quantity=75)]
        result = compute_commodity(self.cmty, positions)

        # With qty=75 and base=1000, worst scenario (index 10) = 75*1000 = 75000
        assert result.scan_risk == pytest.approx(75000.0)

    def test_no_positions_returns_zero_risk(self):
        result = compute_commodity(self.cmty, [])
        assert result.span_risk == 0.0
        assert result.scan_risk == 0.0

    def test_short_future_scan_risk_opposite_sign(self):
        from marginism.algorithm import ResolvedPosition

        fut = self.cmty.futures[0]
        # Short: worst scenario for long is gain for short (negative)
        positions = [ResolvedPosition(contract=fut, quantity=-75)]
        result = compute_commodity(self.cmty, positions)
        # Short loses when price falls; scenario 12 = -1000 * (-75) = +75000
        assert result.scan_risk == pytest.approx(75000.0)

    def test_long_short_same_expiry_offset(self):
        """Long + short same size same expiry = zero scan risk."""
        from marginism.algorithm import ResolvedPosition

        fut = self.cmty.futures[0]
        positions = [
            ResolvedPosition(contract=fut, quantity=75),
            ResolvedPosition(contract=fut, quantity=-75),
        ]
        result = compute_commodity(self.cmty, positions)
        assert result.scan_risk == pytest.approx(0.0)

    def test_calendar_spread_reduces_margin(self):
        """Long near future + short far future should attract calendar spread
        charge but still be less than two isolated futures."""
        from marginism.algorithm import ResolvedPosition

        near = self.cmty.futures[0]
        far = self.cmty.futures[1]

        # Combined
        positions = [
            ResolvedPosition(contract=near, quantity=75),
            ResolvedPosition(contract=far, quantity=-75),
        ]
        result = compute_commodity(self.cmty, positions)
        assert result.calendar_spread_charge > 0  # spread charge applied
        # Scan risk of a calendar spread is very small (positions offset)
        assert result.scan_risk < 75000.0

    def test_worst_scenario_label_set(self):
        from marginism.algorithm import ResolvedPosition

        fut = self.cmty.futures[0]
        positions = [ResolvedPosition(contract=fut, quantity=75)]
        result = compute_commodity(self.cmty, positions)
        assert result.worst_scenario_label != ""
        assert result.worst_scenario >= 1


# ---------------------------------------------------------------------------
# SpanCalculator tests
# ---------------------------------------------------------------------------


class TestSpanCalculator:
    def setup_method(self):
        sf = _make_span_file()
        self.calc = SpanCalculator(sf)

    def test_calculate_long_future_returns_margin_result(self):
        pos = Position("TESTSYM", "FUT", quantity=75, expiry="20261225")
        result = self.calc.calculate([pos])
        assert isinstance(result, MarginResult)
        assert result.span_margin > 0

    def test_unmatched_position_in_unmatched_list(self):
        pos = Position("BANKNIFTY", "FUT", quantity=75, expiry="20261225")
        result = self.calc.calculate([pos])
        assert len(result.unmatched) == 1
        assert result.span_margin == 0.0

    def test_total_margin_is_sum_of_components(self):
        pos = Position("TESTSYM", "FUT", quantity=75, expiry="20261225")
        result = self.calc.calculate([pos])
        expected = (
            result.span_margin
            + result.exposure_margin
            + result.adhoc_margin
            + result.expiry_day_elm
        )
        assert result.total_margin == pytest.approx(expected)

    def test_exposure_margin_applied_for_futures(self):
        cfg = ExposureConfig(overrides={"TESTSYM": 0.05})
        calc = SpanCalculator(_make_span_file(), exposure=cfg)
        pos = Position("TESTSYM", "FUT", quantity=75, expiry="20261225")
        result = calc.calculate([pos])
        assert result.exposure_margin > 0

    def test_long_option_has_no_exposure_margin(self):
        """Long options: buyer's risk is capped at premium; no exposure margin."""
        cfg = ExposureConfig(overrides={"TESTSYM": 0.05})
        calc = SpanCalculator(_make_span_file(), exposure=cfg)
        pos = Position("TESTSYM", "CE", quantity=75, expiry="20261225", strike=24000.0)
        result = calc.calculate([pos])
        assert result.exposure_margin == pytest.approx(0.0)

    def test_short_option_has_exposure_margin(self):
        cfg = ExposureConfig(overrides={"TESTSYM": 0.05})
        calc = SpanCalculator(_make_span_file(), exposure=cfg)
        pos = Position("TESTSYM", "CE", quantity=-75, expiry="20261225", strike=24000.0)
        result = calc.calculate([pos])
        assert result.exposure_margin > 0

    def test_summary_string_nonempty(self):
        pos = Position("TESTSYM", "FUT", quantity=75, expiry="20261225")
        result = self.calc.calculate([pos])
        s = result.summary()
        assert "SPAN" in s
        assert "TESTSYM" in s


# ---------------------------------------------------------------------------
# RiskEngine / basket API tests
# ---------------------------------------------------------------------------


class TestRiskEngine:
    def setup_method(self):
        sf = _make_span_file()
        self.eng = RiskEngine(SpanCalculator(sf))

    def _fut_order(self, side: str = "BUY", qty: int = 75) -> dict:
        return {
            "tradingsymbol": "TESTSYM26DECFUT",
            "transaction_type": side,
            "quantity": qty,
            "exchange": "NFO",
        }

    def _explicit_order(self, side: str = "SELL") -> dict:
        return {
            "symbol": "TESTSYM",
            "instrument": "FUT",
            "expiry": "2026-12-25",
            "transaction_type": side,
            "quantity": 75,
            "exchange": "NFO",
        }

    def test_basket_returns_success_status(self):
        result = self.eng.basket([self._explicit_order("BUY")])
        assert result["status"] == "success"
        assert "data" in result

    def test_basket_data_has_required_keys(self):
        result = self.eng.basket([self._explicit_order("BUY")])
        data = result["data"]
        assert "initial" in data
        assert "final" in data
        assert "orders" in data
        assert "margin_benefit" in data

    def test_per_order_span_is_positive(self):
        result = self.eng.basket([self._explicit_order("BUY")])
        order = result["data"]["orders"][0]
        assert order["span"] > 0

    def test_final_span_lte_sum_of_order_spans(self):
        """Basket margin ≤ sum of individual margins (hedging can reduce it)."""
        orders = [
            self._explicit_order("BUY"),
            {
                "symbol": "TESTSYM",
                "instrument": "FUT",
                "expiry": "2026-12-25",
                "transaction_type": "SELL",
                "quantity": 75,
                "exchange": "NFO",
            },
        ]
        result = self.eng.basket(orders)
        data = result["data"]
        sum_spans = sum(o["span"] for o in data["orders"])
        final_span = data["final"]["span"]
        assert final_span <= sum_spans + 1e-6  # allow float rounding

    def test_margin_benefit_non_negative_for_opposite_legs(self):
        orders = [
            self._explicit_order("BUY"),
            {
                "symbol": "TESTSYM",
                "instrument": "FUT",
                "expiry": "2027-03-27",
                "transaction_type": "SELL",
                "quantity": 75,
                "exchange": "NFO",
            },
        ]
        result = self.eng.basket(orders)
        assert result["data"]["margin_benefit"] >= 0

    def test_orders_method_returns_per_leg_list(self):
        result = self.eng.orders([self._explicit_order("BUY")])
        assert result["status"] == "success"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 1

    def test_orders_span_positive(self):
        result = self.eng.orders([self._explicit_order("BUY")])
        assert result["data"][0]["span"] > 0

    def test_unknown_tradingsymbol_raises_key_error(self):
        order = {
            "tradingsymbol": "NONEXISTENT26DECFUT",
            "transaction_type": "BUY",
            "quantity": 75,
        }
        with pytest.raises(KeyError, match="tradingsymbol not found"):
            self.eng.basket([order])

    def test_explicit_ce_option_order(self):
        order = {
            "symbol": "TESTSYM",
            "instrument": "CE",
            "expiry": "2026-12-25",
            "strike": 24000.0,
            "transaction_type": "SELL",
            "quantity": 75,
            "exchange": "NFO",
        }
        result = self.eng.basket([order])
        assert result["status"] == "success"
        assert result["data"]["final"]["span"] > 0
