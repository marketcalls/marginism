"""Tests for the SPAN file parser (marginism.parser).

Uses inline XML strings as fixtures so no real SPAN files are needed.
Covers NSE (oopPf / futPf / phyPf / ccDef), BSE (same grammar), and
MCX RPF (oofPf — options on futures).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from textwrap import dedent

import pytest

from marginism.parser import parse_spn
from marginism.model import (
    FuturesContract,
    OptionContract,
    SpanFile,
)


# ---------------------------------------------------------------------------
# Minimal XML fixtures
# ---------------------------------------------------------------------------

NSE_NFO_XML = dedent("""\
    <?xml version="1.0" encoding="ISO-8859-1"?>
    <spanFile>
      <fileFormat>4.00</fileFormat>
      <created>20250808</created>
      <date>20250808</date>
      <isSetl>1</isSetl>
      <pointInTime>
        <clearingOrg>
          <ec>NSCCL</ec>
          <ccDef>
            <cc>NIFTY</cc>
            <name>NIFTY 50 Index</name>
            <currency>INR</currency>
            <somTiers>
              <tier>
                <rate><val>3500</val></rate>
              </tier>
            </somTiers>
            <dSpread>
              <spread>1</spread>
              <chargeMeth>F</chargeMeth>
              <rate><val>400</val></rate>
              <pLeg><rs>A</rs><pe>20250828</pe><i>1</i></pLeg>
              <pLeg><rs>B</rs><pe>20250925</pe><i>1</i></pLeg>
            </dSpread>
          </ccDef>
          <futPf>
            <pfCode>NIFTY</pfCode>
            <pfId>1</pfId>
            <cvf>1.0</cvf>
            <fut>
              <cId>1</cId>
              <pe>20250828</pe>
              <p>24350.5</p>
              <d>1.0</d>
              <v>0.15</v>
              <ra>
                <a>-1000</a><a>-800</a><a>-600</a><a>-400</a>
                <a>-200</a><a>0</a><a>200</a><a>400</a>
                <a>600</a><a>800</a><a>1000</a><a>1200</a>
                <a>-1500</a><a>-1200</a><a>500</a><a>700</a>
                <d>1.0</d>
              </ra>
            </fut>
            <fut>
              <cId>2</cId>
              <pe>20250925</pe>
              <p>24400.0</p>
              <d>1.0</d>
              <v>0.16</v>
              <ra>
                <a>-1100</a><a>-900</a><a>-700</a><a>-500</a>
                <a>-300</a><a>-100</a><a>100</a><a>300</a>
                <a>500</a><a>700</a><a>900</a><a>1100</a>
                <a>-1600</a><a>-1300</a><a>600</a><a>800</a>
                <d>1.0</d>
              </ra>
            </fut>
          </futPf>
          <oopPf>
            <pfCode>NIFTY</pfCode>
            <pfId>2</pfId>
            <cvf>1.0</cvf>
            <series>
              <pe>20250828</pe>
              <opt>
                <cId>100</cId>
                <o>C</o>
                <k>24000</k>
                <p>150.5</p>
                <d>0.45</d>
                <v>0.18</v>
                <ra>
                  <a>-900</a><a>-700</a><a>-500</a><a>-300</a>
                  <a>-100</a><a>100</a><a>300</a><a>500</a>
                  <a>-700</a><a>-900</a><a>800</a><a>1000</a>
                  <a>-1400</a><a>-1100</a><a>400</a><a>600</a>
                  <d>0.45</d>
                </ra>
              </opt>
              <opt>
                <cId>101</cId>
                <o>P</o>
                <k>24000</k>
                <p>80.25</p>
                <d>-0.55</d>
                <v>0.19</v>
                <ra>
                  <a>-500</a><a>-300</a><a>-200</a><a>-100</a>
                  <a>100</a><a>300</a><a>500</a><a>700</a>
                  <a>-600</a><a>-800</a><a>900</a><a>1100</a>
                  <a>-1300</a><a>-1000</a><a>350</a><a>550</a>
                  <d>-0.55</d>
                </ra>
              </opt>
            </series>
          </oopPf>
          <phyPf>
            <pfCode>NIFTY</pfCode>
            <pfId>3</pfId>
            <cvf>1.0</cvf>
            <phy>
              <cId>0</cId>
              <pe>00000000</pe>
              <p>24350.5</p>
              <tckSz>0.05</tckSz>
              <ra>
                <a>0</a><a>0</a><a>0</a><a>0</a>
                <a>0</a><a>0</a><a>0</a><a>0</a>
                <a>0</a><a>0</a><a>0</a><a>0</a>
                <a>0</a><a>0</a><a>0</a><a>0</a>
              </ra>
            </phy>
          </phyPf>
        </clearingOrg>
      </pointInTime>
    </spanFile>
""")

MCX_RPF_XML = dedent("""\
    <?xml version="1.0" encoding="ISO-8859-1"?>
    <spanFile>
      <fileFormat>4.00</fileFormat>
      <created>20260608</created>
      <date>20260608</date>
      <isSetl>0</isSetl>
      <pointInTime>
        <clearingOrg>
          <ec>MCXCCL</ec>
          <ccDef>
            <cc>CRUDEOIL</cc>
            <name>CRUDE OIL</name>
            <currency>INR</currency>
            <somTiers>
              <tier>
                <rate><val>5000</val></rate>
              </tier>
            </somTiers>
          </ccDef>
          <futPf>
            <pfCode>CRUDEOIL</pfCode>
            <pfId>10</pfId>
            <cvf>100.0</cvf>
            <fut>
              <cId>1</cId>
              <pe>20260618</pe>
              <p>6500.0</p>
              <d>1.0</d>
              <v>0.25</v>
              <ra>
                <a>-3000</a><a>-2500</a><a>-2000</a><a>-1500</a>
                <a>-1000</a><a>-500</a><a>500</a><a>1000</a>
                <a>1500</a><a>2000</a><a>2500</a><a>3000</a>
                <a>-4000</a><a>-3500</a><a>2000</a><a>2500</a>
                <d>1.0</d>
              </ra>
            </fut>
          </futPf>
          <oofPf>
            <pfCode>CRUDEOIL</pfCode>
            <pfId>11</pfId>
            <cvf>100.0</cvf>
            <series>
              <pe>20260618</pe>
              <opt>
                <cId>200</cId>
                <o>C</o>
                <k>6600</k>
                <p>120.0</p>
                <d>0.40</d>
                <v>0.28</v>
                <ra>
                  <a>-2500</a><a>-2000</a><a>-1800</a><a>-1200</a>
                  <a>-800</a><a>-400</a><a>400</a><a>800</a>
                  <a>1200</a><a>1800</a><a>2200</a><a>2800</a>
                  <a>-3500</a><a>-3000</a><a>1800</a><a>2200</a>
                  <d>0.40</d>
                </ra>
              </opt>
            </series>
          </oofPf>
        </clearingOrg>
      </pointInTime>
    </spanFile>
""")

MULTI_SYMBOL_XML = dedent("""\
    <?xml version="1.0" encoding="ISO-8859-1"?>
    <spanFile>
      <fileFormat>4.00</fileFormat>
      <date>20250808</date>
      <pointInTime>
        <clearingOrg>
          <futPf>
            <pfCode>NIFTY</pfCode>
            <pfId>1</pfId>
            <cvf>1.0</cvf>
            <fut>
              <cId>1</cId><pe>20250828</pe><p>24350.5</p><d>1.0</d>
              <ra>
                <a>-1</a><a>-1</a><a>-1</a><a>-1</a>
                <a>-1</a><a>-1</a><a>1</a><a>1</a>
                <a>1</a><a>1</a><a>2</a><a>2</a>
                <a>-2</a><a>-2</a><a>1</a><a>1</a>
                <d>1.0</d>
              </ra>
            </fut>
          </futPf>
          <futPf>
            <pfCode>RELIANCE</pfCode>
            <pfId>2</pfId>
            <cvf>1.0</cvf>
            <fut>
              <cId>1</cId><pe>20250828</pe><p>2800.0</p><d>1.0</d>
              <ra>
                <a>-50</a><a>-40</a><a>-30</a><a>-20</a>
                <a>-10</a><a>-5</a><a>5</a><a>10</a>
                <a>20</a><a>30</a><a>40</a><a>50</a>
                <a>-80</a><a>-70</a><a>30</a><a>40</a>
                <d>1.0</d>
              </ra>
            </fut>
          </futPf>
        </clearingOrg>
      </pointInTime>
    </spanFile>
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_spn(tmp_path: Path, xml: str, filename: str = "test.spn") -> Path:
    """Write XML to a .spn file and return its path."""
    spn = tmp_path / filename
    spn.write_text(xml, encoding="latin-1")
    return spn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseNfoSpanFile:
    def test_returns_span_file_object(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert isinstance(sf, SpanFile)

    def test_file_format_extracted(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert sf.file_format == "4.00"

    def test_business_date_extracted(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert sf.business_date == "20250808"

    def test_is_settlement_true(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert sf.is_settlement is True

    def test_nifty_in_commodities(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert "NIFTY" in sf.commodities

    def test_two_futures_parsed(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        cmty = sf.get("NIFTY")
        assert cmty is not None
        assert len(cmty.futures) == 2

    def test_futures_have_correct_prices(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        prices = {f.expiry: f.price for f in cmty.futures}
        assert prices["20250828"] == pytest.approx(24350.5)
        assert prices["20250925"] == pytest.approx(24400.0)

    def test_future_has_16_element_risk_array(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        for fut in cmty.futures:
            assert len(fut.risk_array.values) == 16

    def test_two_options_parsed(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        assert len(cmty.options) == 2

    def test_call_option_attributes(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        call = cmty.find_option("20250828", "C", 24000.0)
        assert call is not None
        assert isinstance(call, OptionContract)
        assert call.option_type == "C"
        assert call.strike == pytest.approx(24000.0)
        assert call.price == pytest.approx(150.5)
        assert call.delta == pytest.approx(0.45)

    def test_put_option_attributes(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        put = cmty.find_option("20250828", "P", 24000.0)
        assert put is not None
        assert put.option_type == "P"
        assert put.price == pytest.approx(80.25)

    def test_underlying_spot_price(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        assert cmty.underlying_price == pytest.approx(24350.5)

    def test_calendar_spread_parsed(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        assert len(cmty.spreads) == 1
        spread = cmty.spreads[0]
        assert spread.rate == pytest.approx(400.0)
        assert spread.charge_method == "F"

    def test_som_rate_from_ccdef(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        cmty = parse_spn(str(spn)).get("NIFTY")
        assert cmty.som_rate == pytest.approx(3500.0)


class TestParseMcxRpfFile:
    """MCX RPF files use <oofPf> (options on futures) instead of <oopPf>."""

    def test_crudeoil_in_commodities(self, tmp_path):
        spn = _write_spn(tmp_path, MCX_RPF_XML)
        sf = parse_spn(str(spn))
        assert "CRUDEOIL" in sf.commodities

    def test_futures_parsed(self, tmp_path):
        spn = _write_spn(tmp_path, MCX_RPF_XML)
        cmty = parse_spn(str(spn)).get("CRUDEOIL")
        assert len(cmty.futures) == 1
        assert cmty.futures[0].price == pytest.approx(6500.0)

    def test_options_on_futures_parsed(self, tmp_path):
        """oofPf options should be parsed the same way as oopPf."""
        spn = _write_spn(tmp_path, MCX_RPF_XML)
        cmty = parse_spn(str(spn)).get("CRUDEOIL")
        assert len(cmty.options) == 1
        opt = cmty.options[0]
        assert isinstance(opt, OptionContract)
        assert opt.option_type == "C"
        assert opt.strike == pytest.approx(6600.0)
        assert opt.price == pytest.approx(120.0)

    def test_option_risk_array_16_elements(self, tmp_path):
        spn = _write_spn(tmp_path, MCX_RPF_XML)
        cmty = parse_spn(str(spn)).get("CRUDEOIL")
        assert len(cmty.options[0].risk_array.values) == 16

    def test_cvf_propagated_from_portfolio(self, tmp_path):
        """MCX contracts carry a non-1 CVF (contract value factor)."""
        spn = _write_spn(tmp_path, MCX_RPF_XML)
        cmty = parse_spn(str(spn)).get("CRUDEOIL")
        assert cmty.futures[0].cvf == pytest.approx(100.0)


class TestParseWithSymbolFilter:
    def test_filter_keeps_only_wanted_symbol(self, tmp_path):
        spn = _write_spn(tmp_path, MULTI_SYMBOL_XML)
        sf = parse_spn(str(spn), symbols=["NIFTY"])
        assert "NIFTY" in sf.commodities
        assert "RELIANCE" not in sf.commodities

    def test_filter_case_insensitive(self, tmp_path):
        spn = _write_spn(tmp_path, MULTI_SYMBOL_XML)
        sf = parse_spn(str(spn), symbols=["nifty"])
        assert "NIFTY" in sf.commodities

    def test_no_filter_returns_all(self, tmp_path):
        spn = _write_spn(tmp_path, MULTI_SYMBOL_XML)
        sf = parse_spn(str(spn))
        assert "NIFTY" in sf.commodities
        assert "RELIANCE" in sf.commodities

    def test_empty_filter_returns_nothing(self, tmp_path):
        spn = _write_spn(tmp_path, MULTI_SYMBOL_XML)
        sf = parse_spn(str(spn), symbols=[])
        assert len(sf.commodities) == 0


class TestSpanFileAccessors:
    def test_get_returns_none_for_missing_symbol(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert sf.get("BANKNIFTY") is None

    def test_contains_check(self, tmp_path):
        spn = _write_spn(tmp_path, NSE_NFO_XML)
        sf = parse_spn(str(spn))
        assert "NIFTY" in sf
        assert "BANKNIFTY" not in sf

    def test_symbols_sorted(self, tmp_path):
        spn = _write_spn(tmp_path, MULTI_SYMBOL_XML)
        sf = parse_spn(str(spn))
        assert sf.symbols == sorted(sf.symbols)
