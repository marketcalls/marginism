"""Tests for the instrument catalog (marginism.instruments)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from marginism.instruments import (
    DERIVATIVE_EXCHANGES,
    GROWW_INSTRUMENTS_URL,
    KITE_INSTRUMENTS_URL,
    InstrumentDB,
    InstrumentInfo,
    _parse_groww_csv,
    _parse_kite_csv,
    _row_to_info,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KITE_CSV_HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,"
    "strike,tick_size,lot_size,instrument_type,segment,exchange"
)

KITE_CSV_ROWS = [
    # NFO futures — exchange_token matches Groww rows below
    "12345,12345,NIFTY26JUNFUT,NIFTY,24350.5,2026-06-26,0,0.05,75,FUT,NFO-FUT,NFO",
    "12346,12346,NIFTY26JULFUT,NIFTY,24400.0,2026-07-31,0,0.05,75,FUT,NFO-FUT,NFO",
    # NFO options
    "20001,20001,NIFTY26JUN24000CE,NIFTY,150.5,2026-06-26,24000,0.05,75,CE,NFO-OPT,NFO",
    "20002,20002,NIFTY26JUN24000PE,NIFTY,80.25,2026-06-26,24000,0.05,75,PE,NFO-OPT,NFO",
    # MCX futures
    "30001,30001,CRUDEOIL26JUNFUT,CRUDEOIL,6500.0,2026-06-18,0,1.0,100,FUT,MCX-FUT,MCX",
    # BSE BFO
    "40001,40001,SENSEX26JUNFUT,SENSEX,80000.0,2026-06-27,0,0.01,10,FUT,BFO-FUT,BFO",
    # EQ (cash) — should be filtered out
    "99999,99999,RELIANCE,RELIANCE,2800.0,,0,0.05,1,EQ,NSE,NSE",
]

KITE_CSV = "\n".join([KITE_CSV_HEADER] + KITE_CSV_ROWS) + "\n"

# Groww CSV — provides freeze_quantity and underlying_exchange_token.
# Uses NSE/BSE + segment="FNO" → mapped to NFO/BFO.
GROWW_CSV_HEADER = (
    "exchange_token,exchange,segment,tradingsymbol,freeze_quantity,underlying_exchange_token"
)
GROWW_CSV_ROWS = [
    # NIFTY FUT row → exchange_token=12345, NSE+FNO → NFO; freeze=1801 → stored as 1800
    "12345,NSE,FNO,NIFTY26JUNFUT,1801,9999",
    # NIFTY JUL FUT
    "12346,NSE,FNO,NIFTY26JULFUT,1801,9999",
    # CRUDEOIL on MCX — segment is not FNO so maps as-is
    "30001,MCX,MCX-FUT,CRUDEOIL26JUNFUT,500,8888",
    # SENSEX BFO — BSE+FNO → BFO
    "40001,BSE,FNO,SENSEX26JUNFUT,50,7777",
]
GROWW_CSV = "\n".join([GROWW_CSV_HEADER] + GROWW_CSV_ROWS) + "\n"


class _FakeHTTPResponse:
    def __init__(self, content: bytes):
        self._content = content

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# _parse_kite_csv
# ---------------------------------------------------------------------------


class TestParseKiteCsv:
    def test_returns_list_of_dicts(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        assert isinstance(rows, list)
        assert len(rows) > 0
        assert isinstance(rows[0], dict)

    def test_filters_out_cash_segment(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        exchanges = {r["exchange"].upper() for r in rows}
        assert "NSE" not in exchanges  # RELIANCE EQ should be gone
        for exch in exchanges:
            assert exch in DERIVATIVE_EXCHANGES

    def test_keeps_nfo_mcx_bfo(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        exchanges = {r["exchange"].upper() for r in rows}
        assert "NFO" in exchanges
        assert "MCX" in exchanges
        assert "BFO" in exchanges


# ---------------------------------------------------------------------------
# _parse_groww_csv
# ---------------------------------------------------------------------------


class TestParseGrowwCsv:
    def test_returns_dict_keyed_by_exchange_and_token(self):
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert isinstance(lookup, dict)
        # NSE+FNO → NFO; token 12345
        assert ("NFO", 12345) in lookup

    def test_maps_nse_fno_to_nfo(self):
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert ("NFO", 12345) in lookup
        assert ("NSE", 12345) not in lookup

    def test_maps_bse_fno_to_bfo(self):
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert ("BFO", 40001) in lookup
        assert ("BSE", 40001) not in lookup

    def test_freeze_quantity_decremented_by_one(self):
        """Groww sends N+1; we store N (matching margin-calculator logic)."""
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert lookup[("NFO", 12345)]["freeze_quantity"] == 1800

    def test_underlying_exchange_token_parsed(self):
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert lookup[("NFO", 12345)]["underlying_exchange_token"] == 9999

    def test_mcx_rows_kept(self):
        lookup = _parse_groww_csv(GROWW_CSV.encode())
        assert ("MCX", 30001) in lookup

    def test_non_derivative_rows_excluded(self):
        csv_data = (
            "exchange_token,exchange,segment,tradingsymbol,freeze_quantity,underlying_exchange_token\n"
            "99,NSE,CM,RELIANCE,0,0\n"  # cash market — not a derivative exchange
        )
        lookup = _parse_groww_csv(csv_data.encode())
        assert ("NSE", 99) not in lookup


# ---------------------------------------------------------------------------
# _row_to_info
# ---------------------------------------------------------------------------


class TestRowToInfo:
    def setup_method(self):
        self.row = {
            "tradingsymbol": "NIFTY26JUNFUT",
            "name": "NIFTY",
            "exchange": "NFO",
            "exchange_token": "12345",
            "segment": "NFO-FUT",
            "instrument_type": "FUT",
            "lot_size": "75",
            "tick_size": "0.05",
            "strike": "0",
            "expiry": "2026-06-26",
        }

    def test_basic_conversion(self):
        info = _row_to_info(self.row)
        assert isinstance(info, InstrumentInfo)
        assert info.tradingsymbol == "NIFTY26JUNFUT"
        assert info.name == "NIFTY"
        assert info.exchange == "NFO"

    def test_lot_size_as_int(self):
        info = _row_to_info(self.row)
        assert isinstance(info.lot_size, int)
        assert info.lot_size == 75

    def test_handles_missing_optional_fields(self):
        minimal = {"tradingsymbol": "X", "exchange": "NFO"}
        info = _row_to_info(minimal)
        assert info.lot_size == 1
        assert info.tick_size == pytest.approx(0.05)
        assert info.strike == pytest.approx(0.0)

    def test_exchange_uppercased(self):
        row = dict(self.row, exchange="nfo")
        info = _row_to_info(row)
        assert info.exchange == "NFO"

    def test_instrument_type_uppercased(self):
        row = dict(self.row, instrument_type="fut")
        info = _row_to_info(row)
        assert info.instrument_type == "FUT"

    def test_groww_enrichment_applied(self):
        from marginism.instruments import _parse_groww_csv
        groww_lookup = _parse_groww_csv(GROWW_CSV.encode())
        info = _row_to_info(self.row, groww_lookup)
        assert info.freeze_quantity == 1800
        assert info.underlying_exchange_token == 9999

    def test_defaults_to_zero_freeze_without_groww(self):
        info = _row_to_info(self.row, groww_lookup=None)
        assert info.freeze_quantity == 0
        assert info.underlying_exchange_token is None


# ---------------------------------------------------------------------------
# InstrumentDB.update (mocked network)
# ---------------------------------------------------------------------------


class TestInstrumentDBUpdate:
    def _make_urlopen(self, kite_content: bytes, groww_content: bytes):
        """Return a side_effect function that serves kite then groww bytes."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "kite.trade" in req.full_url:
                return _FakeHTTPResponse(kite_content)
            return _FakeHTTPResponse(groww_content)

        return fake_urlopen

    def test_update_populates_db(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen",
            side_effect=self._make_urlopen(KITE_CSV.encode(), GROWW_CSV.encode()),
        ):
            result = db.update()
        assert result is True
        assert db.is_loaded
        assert len(db) > 0

    def test_update_saves_catalog_to_disk(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen",
            side_effect=self._make_urlopen(KITE_CSV.encode(), GROWW_CSV.encode()),
        ):
            db.update()
        catalog_path = tmp_path / "instruments_catalog.json"
        assert catalog_path.exists()
        with open(catalog_path) as f:
            data = json.load(f)
        assert data["count"] > 0
        assert "instruments" in data
        assert "fetched_at" in data

    def test_update_returns_false_on_kite_network_error(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen", side_effect=Exception("network error")
        ):
            result = db.update()
        assert result is False

    def test_update_returns_false_on_empty_csv(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        empty_csv = b"instrument_token,exchange_token,tradingsymbol\n"
        with patch(
            "urllib.request.urlopen",
            side_effect=self._make_urlopen(empty_csv, GROWW_CSV.encode()),
        ):
            result = db.update()
        assert result is False

    def test_update_succeeds_when_groww_fails(self, tmp_path):
        """Kite-only update should still succeed (freeze_quantity defaults to 0)."""
        db = InstrumentDB(data_dir=tmp_path)

        def fake_urlopen(req, timeout=None):
            if "kite.trade" in req.full_url:
                return _FakeHTTPResponse(KITE_CSV.encode())
            raise Exception("groww unavailable")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = db.update()
        assert result is True
        assert db.lot_size("NIFTY") == 75
        assert db.freeze_quantity("NIFTY") == 0  # Groww not available

    def test_update_enriches_with_groww_freeze_quantity(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen",
            side_effect=self._make_urlopen(KITE_CSV.encode(), GROWW_CSV.encode()),
        ):
            db.update()
        assert db.freeze_quantity("NIFTY") == 1800

    def test_update_enriches_underlying_exchange_token(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen",
            side_effect=self._make_urlopen(KITE_CSV.encode(), GROWW_CSV.encode()),
        ):
            db.update()
        info = db.get("NIFTY26JUNFUT", exchange="NFO")
        assert info is not None
        assert info.underlying_exchange_token == 9999

    def test_kite_url_and_groww_url_params(self, tmp_path):
        """Custom URL params should be used instead of defaults."""
        db = InstrumentDB(data_dir=tmp_path)
        called_urls = []

        def fake_urlopen(req, timeout=None):
            called_urls.append(req.full_url)
            if "custom-kite" in req.full_url:
                return _FakeHTTPResponse(KITE_CSV.encode())
            return _FakeHTTPResponse(GROWW_CSV.encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            db.update(kite_url="https://custom-kite/inst", groww_url="https://custom-groww/inst")

        assert any("custom-kite" in u for u in called_urls)
        assert any("custom-groww" in u for u in called_urls)

    def test_groww_skipped_when_url_empty(self, tmp_path):
        """Passing groww_url='' skips the Groww download entirely."""
        db = InstrumentDB(data_dir=tmp_path)
        called = []

        def fake_urlopen(req, timeout=None):
            called.append(req.full_url)
            return _FakeHTTPResponse(KITE_CSV.encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = db.update(groww_url="")

        assert result is True
        assert len(called) == 1  # only Kite


# ---------------------------------------------------------------------------
# InstrumentDB.load_from_csv
# ---------------------------------------------------------------------------


class TestInstrumentDBLoadFromCsv:
    def test_populates_from_csv_bytes(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        result = db.load_from_csv(KITE_CSV.encode())
        assert result is True
        assert db.is_loaded

    def test_empty_csv_returns_false(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        result = db.load_from_csv(b"tradingsymbol,exchange\n")
        assert result is False

    def test_groww_enrichment_via_load_from_csv(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        db.load_from_csv(KITE_CSV.encode(), groww_content=GROWW_CSV.encode())
        assert db.freeze_quantity("NIFTY") == 1800

    def test_no_groww_content_gives_zero_freeze(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        db.load_from_csv(KITE_CSV.encode())
        assert db.freeze_quantity("NIFTY") == 0


# ---------------------------------------------------------------------------
# InstrumentDB.lot_size
# ---------------------------------------------------------------------------


class TestInstrumentDBLotSize:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_nifty_lot_size_nfo(self):
        assert self.db.lot_size("NIFTY", exchange="NFO") == 75

    def test_crudeoil_lot_size_mcx(self):
        assert self.db.lot_size("CRUDEOIL", exchange="MCX") == 100

    def test_sensex_lot_size_bfo(self):
        assert self.db.lot_size("SENSEX", exchange="BFO") == 10

    def test_returns_none_for_unknown(self):
        assert self.db.lot_size("NONEXISTENT", exchange="NFO") is None

    def test_default_exchange_is_nfo(self):
        assert self.db.lot_size("NIFTY") == 75


# ---------------------------------------------------------------------------
# InstrumentDB.freeze_quantity
# ---------------------------------------------------------------------------


class TestInstrumentDBFreezeQuantity:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode(), groww_content=GROWW_CSV.encode())

    def test_nifty_freeze_quantity(self):
        assert self.db.freeze_quantity("NIFTY") == 1800

    def test_sensex_bfo_freeze_quantity(self):
        assert self.db.freeze_quantity("SENSEX", exchange="BFO") == 49  # 50-1

    def test_crudeoil_mcx_freeze_quantity(self):
        assert self.db.freeze_quantity("CRUDEOIL", exchange="MCX") == 499  # 500-1

    def test_returns_zero_without_groww(self):
        db = InstrumentDB()
        db.load_from_csv(KITE_CSV.encode())
        assert db.freeze_quantity("NIFTY") == 0

    def test_returns_none_for_unknown_symbol(self):
        assert self.db.freeze_quantity("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# InstrumentDB.get
# ---------------------------------------------------------------------------


class TestInstrumentDBGet:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode(), groww_content=GROWW_CSV.encode())

    def test_returns_instrument_info(self):
        info = self.db.get("NIFTY26JUNFUT", exchange="NFO")
        assert info is not None
        assert isinstance(info, InstrumentInfo)
        assert info.tradingsymbol == "NIFTY26JUNFUT"
        assert info.lot_size == 75

    def test_returns_none_for_unknown(self):
        assert self.db.get("UNKNOWN99FUT", exchange="NFO") is None

    def test_case_insensitive_lookup(self):
        info = self.db.get("nifty26junfut", exchange="nfo")
        assert info is not None
        assert info.tradingsymbol == "NIFTY26JUNFUT"

    def test_groww_fields_populated(self):
        info = self.db.get("NIFTY26JUNFUT", exchange="NFO")
        assert info is not None
        assert info.freeze_quantity == 1800
        assert info.underlying_exchange_token == 9999


# ---------------------------------------------------------------------------
# InstrumentDB.search
# ---------------------------------------------------------------------------


class TestInstrumentDBSearch:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_search_by_name(self):
        results = self.db.search("NIFTY", exchange="NFO")
        assert len(results) > 0
        for r in results:
            assert r.exchange == "NFO"
            assert r.name.upper() == "NIFTY" or r.tradingsymbol.startswith("NIFTY")

    def test_search_with_instrument_type_filter(self):
        futures = self.db.search("NIFTY", exchange="NFO", instrument_type="FUT")
        assert all(r.instrument_type == "FUT" for r in futures)

    def test_search_returns_empty_for_unknown(self):
        results = self.db.search("BANKNIFTY", exchange="NFO")
        assert results == []

    def test_search_across_all_exchanges(self):
        results = self.db.search("NIFTY")
        assert len(results) > 0


# ---------------------------------------------------------------------------
# InstrumentDB.all_symbols
# ---------------------------------------------------------------------------


class TestInstrumentDBAllSymbols:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_returns_sorted_list(self):
        symbols = self.db.all_symbols(exchange="NFO")
        assert symbols == sorted(symbols)

    def test_nifty_in_nfo_symbols(self):
        symbols = self.db.all_symbols(exchange="NFO")
        assert "NIFTY" in symbols

    def test_crudeoil_in_mcx_symbols(self):
        symbols = self.db.all_symbols(exchange="MCX")
        assert "CRUDEOIL" in symbols

    def test_no_exchange_filter_returns_all(self):
        all_sym = self.db.all_symbols()
        nfo_sym = self.db.all_symbols(exchange="NFO")
        mcx_sym = self.db.all_symbols(exchange="MCX")
        assert len(all_sym) >= len(nfo_sym)
        assert len(all_sym) >= len(mcx_sym)


# ---------------------------------------------------------------------------
# InstrumentDB — on-disk persistence
# ---------------------------------------------------------------------------


class TestInstrumentDBPersistence:
    def test_reload_from_disk_after_update(self, tmp_path):
        db1 = InstrumentDB(data_dir=tmp_path)

        def fake_urlopen(req, timeout=None):
            if "kite.trade" in req.full_url:
                return _FakeHTTPResponse(KITE_CSV.encode())
            return _FakeHTTPResponse(GROWW_CSV.encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            db1.update()

        # Create a fresh instance pointing at the same dir
        db2 = InstrumentDB(data_dir=tmp_path)
        assert not db2._loaded
        lot = db2.lot_size("NIFTY")  # triggers lazy load
        assert lot == 75
        # Groww fields should be persisted too
        assert db2.freeze_quantity("NIFTY") == 1800

    def test_is_loaded_false_when_no_catalog(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        assert not db.is_loaded

    def test_len_returns_instrument_count(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        db.load_from_csv(KITE_CSV.encode())
        # 6 derivative rows (RELIANCE EQ excluded)
        assert len(db) == 6


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KITE_CSV_HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,"
    "strike,tick_size,lot_size,instrument_type,segment,exchange"
)

KITE_CSV_ROWS = [
    # NFO futures
    "12345,12345,NIFTY26JUNFUT,NIFTY,24350.5,2026-06-26,0,0.05,75,FUT,NFO-FUT,NFO",
    "12346,12346,NIFTY26JULFUT,NIFTY,24400.0,2026-07-31,0,0.05,75,FUT,NFO-FUT,NFO",
    # NFO options
    "20001,20001,NIFTY26JUN24000CE,NIFTY,150.5,2026-06-26,24000,0.05,75,CE,NFO-OPT,NFO",
    "20002,20002,NIFTY26JUN24000PE,NIFTY,80.25,2026-06-26,24000,0.05,75,PE,NFO-OPT,NFO",
    # MCX futures
    "30001,30001,CRUDEOIL26JUNFUT,CRUDEOIL,6500.0,2026-06-18,0,1.0,100,FUT,MCX-FUT,MCX",
    # BSE BFO
    "40001,40001,SENSEX26JUNFUT,SENSEX,80000.0,2026-06-27,0,0.01,10,FUT,BFO-FUT,BFO",
    # EQ (cash) — should be filtered out
    "99999,99999,RELIANCE,RELIANCE,2800.0,,0,0.05,1,EQ,NSE,NSE",
]

KITE_CSV = "\n".join([KITE_CSV_HEADER] + KITE_CSV_ROWS) + "\n"


class _FakeHTTPResponse:
    def __init__(self, content: bytes):
        self._content = content

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# _parse_kite_csv
# ---------------------------------------------------------------------------


class TestParseKiteCsv:
    def test_returns_list_of_dicts(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        assert isinstance(rows, list)
        assert len(rows) > 0
        assert isinstance(rows[0], dict)

    def test_filters_out_cash_segment(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        exchanges = {r["exchange"].upper() for r in rows}
        assert "NSE" not in exchanges  # RELIANCE EQ should be gone
        for exch in exchanges:
            assert exch in DERIVATIVE_EXCHANGES

    def test_keeps_nfo_mcx_bfo(self):
        rows = _parse_kite_csv(KITE_CSV.encode())
        exchanges = {r["exchange"].upper() for r in rows}
        assert "NFO" in exchanges
        assert "MCX" in exchanges
        assert "BFO" in exchanges


# ---------------------------------------------------------------------------
# _row_to_info
# ---------------------------------------------------------------------------


class TestRowToInfo:
    def setup_method(self):
        self.row = {
            "tradingsymbol": "NIFTY26JUNFUT",
            "name": "NIFTY",
            "exchange": "NFO",
            "segment": "NFO-FUT",
            "instrument_type": "FUT",
            "lot_size": "75",
            "tick_size": "0.05",
            "strike": "0",
            "expiry": "2026-06-26",
        }

    def test_basic_conversion(self):
        info = _row_to_info(self.row)
        assert isinstance(info, InstrumentInfo)
        assert info.tradingsymbol == "NIFTY26JUNFUT"
        assert info.name == "NIFTY"
        assert info.exchange == "NFO"

    def test_lot_size_as_int(self):
        info = _row_to_info(self.row)
        assert isinstance(info.lot_size, int)
        assert info.lot_size == 75

    def test_handles_missing_optional_fields(self):
        minimal = {"tradingsymbol": "X", "exchange": "NFO"}
        info = _row_to_info(minimal)
        assert info.lot_size == 1
        assert info.tick_size == pytest.approx(0.05)
        assert info.strike == pytest.approx(0.0)

    def test_exchange_uppercased(self):
        row = dict(self.row, exchange="nfo")
        info = _row_to_info(row)
        assert info.exchange == "NFO"

    def test_instrument_type_uppercased(self):
        row = dict(self.row, instrument_type="fut")
        info = _row_to_info(row)
        assert info.instrument_type == "FUT"


# ---------------------------------------------------------------------------
# InstrumentDB.update (mocked network)
# ---------------------------------------------------------------------------


class TestInstrumentDBUpdate:
    def test_update_populates_db(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(KITE_CSV.encode()),
        ):
            result = db.update()

        assert result is True
        assert db.is_loaded
        assert len(db) > 0

    def test_update_saves_catalog_to_disk(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(KITE_CSV.encode()),
        ):
            db.update()

        catalog_path = tmp_path / "instruments_catalog.json"
        assert catalog_path.exists()
        with open(catalog_path) as f:
            data = json.load(f)
        assert data["count"] > 0
        assert "instruments" in data
        assert "fetched_at" in data

    def test_update_returns_false_on_network_error(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)

        with patch(
            "urllib.request.urlopen", side_effect=Exception("network error")
        ):
            result = db.update()

        assert result is False

    def test_update_returns_false_on_empty_csv(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        empty_csv = b"instrument_token,exchange_token,tradingsymbol\n"

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(empty_csv),
        ):
            result = db.update()

        assert result is False


# ---------------------------------------------------------------------------
# InstrumentDB.load_from_csv
# ---------------------------------------------------------------------------


class TestInstrumentDBLoadFromCsv:
    def test_populates_from_csv_bytes(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        result = db.load_from_csv(KITE_CSV.encode())
        assert result is True
        assert db.is_loaded

    def test_empty_csv_returns_false(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        result = db.load_from_csv(b"tradingsymbol,exchange\n")
        assert result is False


# ---------------------------------------------------------------------------
# InstrumentDB.lot_size
# ---------------------------------------------------------------------------


class TestInstrumentDBLotSize:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_nifty_lot_size_nfo(self):
        assert self.db.lot_size("NIFTY", exchange="NFO") == 75

    def test_crudeoil_lot_size_mcx(self):
        assert self.db.lot_size("CRUDEOIL", exchange="MCX") == 100

    def test_sensex_lot_size_bfo(self):
        assert self.db.lot_size("SENSEX", exchange="BFO") == 10

    def test_returns_none_for_unknown(self):
        assert self.db.lot_size("NONEXISTENT", exchange="NFO") is None

    def test_default_exchange_is_nfo(self):
        assert self.db.lot_size("NIFTY") == 75


# ---------------------------------------------------------------------------
# InstrumentDB.get
# ---------------------------------------------------------------------------


class TestInstrumentDBGet:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_returns_instrument_info(self):
        info = self.db.get("NIFTY26JUNFUT", exchange="NFO")
        assert info is not None
        assert isinstance(info, InstrumentInfo)
        assert info.tradingsymbol == "NIFTY26JUNFUT"
        assert info.lot_size == 75

    def test_returns_none_for_unknown(self):
        assert self.db.get("UNKNOWN99FUT", exchange="NFO") is None

    def test_case_insensitive_lookup(self):
        info = self.db.get("nifty26junfut", exchange="nfo")
        assert info is not None
        assert info.tradingsymbol == "NIFTY26JUNFUT"


# ---------------------------------------------------------------------------
# InstrumentDB.search
# ---------------------------------------------------------------------------


class TestInstrumentDBSearch:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_search_by_name(self):
        results = self.db.search("NIFTY", exchange="NFO")
        assert len(results) > 0
        for r in results:
            assert r.exchange == "NFO"
            assert r.name.upper() == "NIFTY" or r.tradingsymbol.startswith("NIFTY")

    def test_search_with_instrument_type_filter(self):
        futures = self.db.search("NIFTY", exchange="NFO", instrument_type="FUT")
        assert all(r.instrument_type == "FUT" for r in futures)

    def test_search_returns_empty_for_unknown(self):
        results = self.db.search("BANKNIFTY", exchange="NFO")
        assert results == []

    def test_search_across_all_exchanges(self):
        results = self.db.search("NIFTY")
        assert len(results) > 0


# ---------------------------------------------------------------------------
# InstrumentDB.all_symbols
# ---------------------------------------------------------------------------


class TestInstrumentDBAllSymbols:
    def setup_method(self):
        self.db = InstrumentDB()
        self.db.load_from_csv(KITE_CSV.encode())

    def test_returns_sorted_list(self):
        symbols = self.db.all_symbols(exchange="NFO")
        assert symbols == sorted(symbols)

    def test_nifty_in_nfo_symbols(self):
        symbols = self.db.all_symbols(exchange="NFO")
        assert "NIFTY" in symbols

    def test_crudeoil_in_mcx_symbols(self):
        symbols = self.db.all_symbols(exchange="MCX")
        assert "CRUDEOIL" in symbols

    def test_no_exchange_filter_returns_all(self):
        all_sym = self.db.all_symbols()
        nfo_sym = self.db.all_symbols(exchange="NFO")
        mcx_sym = self.db.all_symbols(exchange="MCX")
        # All-exchanges list should be >= any single exchange list
        assert len(all_sym) >= len(nfo_sym)
        assert len(all_sym) >= len(mcx_sym)


# ---------------------------------------------------------------------------
# InstrumentDB — on-disk persistence
# ---------------------------------------------------------------------------


class TestInstrumentDBPersistence:
    def test_reload_from_disk_after_update(self, tmp_path):
        db1 = InstrumentDB(data_dir=tmp_path)
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(KITE_CSV.encode()),
        ):
            db1.update()

        # Create a fresh instance pointing at the same dir
        db2 = InstrumentDB(data_dir=tmp_path)
        assert not db2._loaded  # not yet loaded
        lot = db2.lot_size("NIFTY")  # triggers lazy load
        assert lot == 75

    def test_is_loaded_false_when_no_catalog(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        assert not db.is_loaded

    def test_len_returns_instrument_count(self, tmp_path):
        db = InstrumentDB(data_dir=tmp_path)
        db.load_from_csv(KITE_CSV.encode())
        # 6 derivative rows (RELIANCE EQ excluded)
        assert len(db) == 6
