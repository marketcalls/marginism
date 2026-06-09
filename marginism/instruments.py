"""Instrument catalog for NSE, BSE, and MCX derivatives.

Downloads and merges two public instrument master CSVs:

1. **Kite** (``api.kite.trade/instruments``) — base master with lot_size,
   tick_size, tradingsymbol, exchange, segment, etc.
2. **Groww** (``growwapi-assets.groww.in/instruments/instrument.csv``) — adds
   two extra fields not present in the Kite CSV:
   - ``freeze_quantity``: SEBI-mandated order-freeze limit for the instrument.
   - ``underlying_exchange_token``: exchange token of the underlying asset,
     useful for resolving option/future → underlying relationships.

The merged catalog is cached at ``~/.marginism/data/instruments_catalog.json``
and reloaded automatically on the next call after it has been written.

No third-party dependencies — uses only ``urllib.request`` and ``csv`` from
the standard library.

Quick start
-----------
>>> from marginism.instruments import InstrumentDB
>>> db = InstrumentDB()
>>> db.update()                     # fetch Kite + Groww; saves to disk
>>> db.lot_size("NIFTY")            # defaults to exchange="NFO"
75
>>> db.lot_size("CRUDEOIL", exchange="MCX")
100
>>> db.freeze_quantity("NIFTY", exchange="NFO")
1800
>>> db.search("RELIANCE", exchange="NFO", instrument_type="FUT")
[InstrumentInfo(tradingsymbol='RELIANCE26JUNFUT', ...)]
"""

from __future__ import annotations

import csv
import json
import logging
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Public instrument master URLs — no authentication required.
KITE_INSTRUMENTS_URL: str = "https://api.kite.trade/instruments"
GROWW_INSTRUMENTS_URL: str = "https://growwapi-assets.groww.in/instruments/instrument.csv"

DEFAULT_DATA_DIR: Path = Path.home() / ".marginism" / "data"
CATALOG_FILENAME: str = "instruments_catalog.json"

# Only keep derivative segment instruments; skip EQ cash market, etc.
DERIVATIVE_EXCHANGES = frozenset({"NFO", "BFO", "CDS", "COM", "MCX", "BCD", "BCO"})

# Groww uses different exchange labels — map to Kite conventions.
_GROWW_EXCHANGE_MAP: Dict[tuple, str] = {
    ("NSE", "FNO"): "NFO",
    ("BSE", "FNO"): "BFO",
}


@dataclass
class InstrumentInfo:
    """Lightweight record for one derivative instrument."""

    tradingsymbol: str
    name: str
    exchange: str
    segment: str
    instrument_type: str        # CE, PE, FUT, …
    lot_size: int
    tick_size: float
    strike: float
    expiry: str                 # YYYY-MM-DD or empty string
    # --- Groww-enriched fields (0 / None when Groww data not available) ---
    freeze_quantity: int = 0    # SEBI order-freeze limit; 0 means unknown
    underlying_exchange_token: Optional[int] = None  # token of underlying asset


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(float(value))) if value not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default


def _safe_int_or_none(value: Any) -> Optional[int]:
    """Return int or None (no minimum clamp — used for token fields)."""
    try:
        if value in (None, "", "nan"):
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _parse_kite_csv(content: bytes) -> List[Dict[str, Any]]:
    """Parse Kite instrument CSV bytes, keeping only derivative exchanges."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    rows = []
    for row in reader:
        exchange = (row.get("exchange") or "").strip().upper()
        if exchange not in DERIVATIVE_EXCHANGES:
            continue
        rows.append(row)
    return rows


def _parse_groww_csv(content: bytes) -> Dict[int, Dict[str, Any]]:
    """Parse Groww instrument CSV, returning a dict keyed by exchange_token.

    Groww uses different exchange names (e.g. "NSE"/"BSE" with a "segment"
    column) — we map them to Kite-style exchange names so they can be joined.

    Returns a mapping:
        (kite_exchange.upper(), exchange_token) -> {freeze_quantity, underlying_exchange_token}
    """
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    lookup: Dict[tuple, Dict[str, Any]] = {}
    for row in reader:
        raw_token = row.get("exchange_token", "")
        token = _safe_int_or_none(raw_token)
        if token is None:
            continue
        exchange = (row.get("exchange") or "").strip().upper()
        segment = (row.get("segment") or "").strip().upper()
        kite_exchange = _GROWW_EXCHANGE_MAP.get((exchange, segment), exchange)
        if kite_exchange not in DERIVATIVE_EXCHANGES:
            continue
        fq_raw = row.get("freeze_quantity", "")
        fq = _safe_int_or_none(fq_raw)
        fq = max(0, fq - 1) if fq and fq > 0 else 0  # match margin-calculator logic
        uet = _safe_int_or_none(row.get("underlying_exchange_token", ""))
        lookup[(kite_exchange, token)] = {
            "freeze_quantity": fq,
            "underlying_exchange_token": uet,
        }
    return lookup


def _row_to_info(
    row: Dict[str, Any],
    groww_lookup: Optional[Dict[tuple, Dict[str, Any]]] = None,
) -> InstrumentInfo:
    exchange = (row.get("exchange") or "").strip().upper()
    token = _safe_int_or_none(row.get("exchange_token", ""))
    groww: Dict[str, Any] = {}
    if groww_lookup and token is not None:
        groww = groww_lookup.get((exchange, token), {})
    return InstrumentInfo(
        tradingsymbol=(row.get("tradingsymbol") or "").strip(),
        name=(row.get("name") or "").strip(),
        exchange=exchange,
        segment=(row.get("segment") or "").strip(),
        instrument_type=(row.get("instrument_type") or "").strip().upper(),
        lot_size=_safe_int(row.get("lot_size"), default=1),
        tick_size=_safe_float(row.get("tick_size"), default=0.05),
        strike=_safe_float(row.get("strike"), default=0.0),
        expiry=(row.get("expiry") or "").strip(),
        freeze_quantity=groww.get("freeze_quantity", 0),
        underlying_exchange_token=groww.get("underlying_exchange_token"),
    )


def _fetch_bytes(url: str, timeout: float = 60.0) -> Optional[bytes]:
    """Download *url* and return raw bytes, or ``None`` on any error."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "marginism/1.0 instrument-catalog"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None


class InstrumentDB:
    """Local instrument catalog backed by a JSON file.

    Merges data from two public sources:

    * **Kite** (``api.kite.trade/instruments``) — core fields (lot_size,
      tick_size, tradingsymbol, expiry, strike, …).
    * **Groww** (``growwapi-assets.groww.in/instruments/instrument.csv``) —
      enrichment fields: ``freeze_quantity`` (SEBI order-freeze limit) and
      ``underlying_exchange_token`` (links option/future to its underlying).

    On first use the catalog is loaded from
    ``~/.marginism/data/instruments_catalog.json`` (if it exists).
    Call :meth:`update` to download fresh data and refresh the cache.

    If the Groww download fails, the catalog is still built from Kite data
    alone — ``freeze_quantity`` will be 0 and
    ``underlying_exchange_token`` will be ``None`` for all instruments.

    Example
    -------
    >>> db = InstrumentDB()
    >>> db.update()
    >>> db.lot_size("NIFTY")                    # 75
    >>> db.lot_size("CRUDEOIL", exchange="MCX") # 100
    >>> db.freeze_quantity("NIFTY")             # e.g. 1800
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._dir: Path = data_dir if data_dir is not None else DEFAULT_DATA_DIR
        self._catalog_path: Path = self._dir / CATALOG_FILENAME
        self._instruments: List[InstrumentInfo] = []
        # (tradingsymbol.upper(), exchange.upper()) -> InstrumentInfo
        self._by_ts: Dict[tuple, InstrumentInfo] = {}
        # (name/root.upper(), exchange.upper()) -> lot_size from FUT row
        self._lot_by_root: Dict[tuple, int] = {}
        self._loaded: bool = False

    # ---------------------------------------------------------------- load

    def _load_from_catalog(self) -> bool:
        """Read the on-disk JSON catalog into memory. Returns ``True`` on success."""
        if not self._catalog_path.exists():
            return False
        try:
            with open(self._catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            instruments = [
                InstrumentInfo(**d) for d in data.get("instruments", [])
            ]
            self._set_instruments(instruments)
            logger.info(
                "Loaded %d instruments from catalog (fetched: %s)",
                len(instruments),
                data.get("fetched_at", "unknown"),
            )
            return True
        except Exception as e:
            logger.warning("Failed to load instrument catalog: %s", e)
            return False

    def _set_instruments(self, instruments: List[InstrumentInfo]) -> None:
        self._instruments = instruments
        self._by_ts = {}
        self._lot_by_root = {}
        for inst in instruments:
            key = (inst.tradingsymbol.upper(), inst.exchange.upper())
            self._by_ts[key] = inst
            # Build root→lot_size index from FUT rows (most reliable source).
            if inst.instrument_type == "FUT" and inst.name:
                root_key = (inst.name.upper(), inst.exchange.upper())
                self._lot_by_root[root_key] = inst.lot_size
        self._loaded = True

    def _save_catalog(self, instruments: List[InstrumentInfo]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "fetched_at": datetime.now().isoformat(),
            "count": len(instruments),
            "instruments": [asdict(i) for i in instruments],
        }
        with open(self._catalog_path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        logger.info(
            "Saved %d instruments to %s", len(instruments), self._catalog_path
        )

    # -------------------------------------------------------------- update

    def update(
        self,
        kite_url: str = KITE_INSTRUMENTS_URL,
        groww_url: str = GROWW_INSTRUMENTS_URL,
        timeout: float = 60.0,
    ) -> bool:
        """Download Kite + Groww instrument masters, merge, and refresh the cache.

        Parameters
        ----------
        kite_url:
            Override the Kite instruments endpoint (useful for tests).
        groww_url:
            Override the Groww instruments endpoint (useful for tests).
            Pass ``""`` to skip the Groww download entirely.
        timeout:
            HTTP read timeout in seconds.

        Returns
        -------
        ``True`` on success (at least Kite data loaded), ``False`` otherwise.
        """
        logger.info("Downloading Kite instrument master from %s", kite_url)
        kite_content = _fetch_bytes(kite_url, timeout=timeout)
        if kite_content is None:
            logger.error("Failed to download Kite instrument master")
            return False

        groww_lookup: Optional[Dict[tuple, Dict[str, Any]]] = None
        if groww_url:
            logger.info("Downloading Groww instrument master from %s", groww_url)
            groww_content = _fetch_bytes(groww_url, timeout=timeout)
            if groww_content:
                try:
                    groww_lookup = _parse_groww_csv(groww_content)
                    logger.info(
                        "Groww master loaded: %d derivative rows", len(groww_lookup)
                    )
                except Exception as e:
                    logger.warning("Failed to parse Groww CSV: %s — continuing without it", e)
            else:
                logger.warning("Groww download failed — freeze_quantity will be 0")

        kite_rows = _parse_kite_csv(kite_content)
        instruments: List[InstrumentInfo] = []
        for row in kite_rows:
            try:
                instruments.append(_row_to_info(row, groww_lookup))
            except Exception as e:
                logger.debug("Skipping bad row: %s", e)

        if not instruments:
            logger.error("No derivative instruments found in downloaded CSV")
            return False

        self._set_instruments(instruments)
        self._save_catalog(instruments)
        enriched = sum(1 for i in instruments if i.freeze_quantity > 0)
        logger.info(
            "Instrument catalog updated: %d instruments (%d with Groww freeze_quantity)",
            len(instruments),
            enriched,
        )
        return True

    def load_from_csv(
        self,
        kite_content: bytes,
        groww_content: Optional[bytes] = None,
    ) -> bool:
        """Populate the catalog from raw CSV bytes (e.g. from local files).

        Parameters
        ----------
        kite_content:
            Raw bytes of the Kite instrument CSV.
        groww_content:
            Raw bytes of the Groww instrument CSV.  Pass ``None`` (default) to
            skip Groww enrichment.  Does *not* persist to the JSON cache.

        Returns ``True`` if at least one instrument was loaded.
        """
        groww_lookup: Optional[Dict[tuple, Dict[str, Any]]] = None
        if groww_content:
            try:
                groww_lookup = _parse_groww_csv(groww_content)
            except Exception as e:
                logger.warning("Failed to parse Groww CSV: %s", e)

        rows = _parse_kite_csv(kite_content)
        instruments: List[InstrumentInfo] = []
        for row in rows:
            try:
                instruments.append(_row_to_info(row, groww_lookup))
            except Exception as e:
                logger.debug("Skipping bad row: %s", e)
        if instruments:
            self._set_instruments(instruments)
            return True
        return False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_from_catalog()

    # --------------------------------------------------------------- query

    def get(
        self, tradingsymbol: str, exchange: str = "NFO"
    ) -> Optional[InstrumentInfo]:
        """Return the :class:`InstrumentInfo` for *tradingsymbol* on *exchange*, or ``None``."""
        self._ensure_loaded()
        return self._by_ts.get(
            (tradingsymbol.upper(), exchange.upper())
        )

    def lot_size(
        self, symbol: str, exchange: str = "NFO"
    ) -> Optional[int]:
        """Return the lot size for *symbol* on *exchange*.

        Looks up the FUT root index first (``symbol`` should be the underlying
        name, e.g. ``"NIFTY"``).  Falls back to any instrument whose
        tradingsymbol starts with *symbol*.

        Returns ``None`` if no matching instrument is found.
        """
        self._ensure_loaded()
        key = (symbol.upper(), exchange.upper())
        lot = self._lot_by_root.get(key)
        if lot is not None:
            return lot
        # Fallback: scan tradingsymbols that start with the given symbol.
        exch_upper = exchange.upper()
        sym_upper = symbol.upper()
        for (ts, exch), inst in self._by_ts.items():
            if exch == exch_upper and ts.startswith(sym_upper):
                return inst.lot_size
        return None

    def freeze_quantity(
        self, symbol: str, exchange: str = "NFO"
    ) -> Optional[int]:
        """Return the SEBI order-freeze limit for *symbol* on *exchange*.

        Returns the freeze_quantity from the FUT row for the given underlying
        root (e.g. ``"NIFTY"``), or ``None`` if not found.  A value of 0
        means the Groww data was not available when the catalog was built.
        """
        self._ensure_loaded()
        exch_upper = exchange.upper()
        sym_upper = symbol.upper()
        # Prefer FUT row for the root symbol
        for inst in self._instruments:
            if (
                inst.exchange.upper() == exch_upper
                and inst.instrument_type == "FUT"
                and inst.name.upper() == sym_upper
            ):
                return inst.freeze_quantity
        # Fallback: any matching instrument
        for (ts, exch), inst in self._by_ts.items():
            if exch == exch_upper and ts.startswith(sym_upper):
                return inst.freeze_quantity
        return None

    def search(
        self,
        symbol: str,
        exchange: Optional[str] = None,
        instrument_type: Optional[str] = None,
    ) -> List[InstrumentInfo]:
        """Return instruments whose name or tradingsymbol matches *symbol*.

        Parameters
        ----------
        symbol:
            Underlying name or tradingsymbol prefix (case-insensitive).
        exchange:
            Filter by exchange (e.g. ``"NFO"``, ``"MCX"``).  ``None`` keeps all.
        instrument_type:
            Filter by instrument type (``"FUT"``, ``"CE"``, ``"PE"``).
            ``None`` keeps all.
        """
        self._ensure_loaded()
        sym_upper = symbol.upper()
        exch_upper = exchange.upper() if exchange else None
        itype_upper = instrument_type.upper() if instrument_type else None
        results = []
        for inst in self._instruments:
            name_match = (
                inst.name.upper() == sym_upper
                or inst.tradingsymbol.upper().startswith(sym_upper)
            )
            if not name_match:
                continue
            if exch_upper and inst.exchange.upper() != exch_upper:
                continue
            if itype_upper and inst.instrument_type.upper() != itype_upper:
                continue
            results.append(inst)
        return results

    def all_symbols(self, exchange: Optional[str] = None) -> List[str]:
        """Return a sorted list of unique underlying names for *exchange*.

        If *exchange* is ``None``, returns names across all derivative exchanges.
        """
        self._ensure_loaded()
        names = set()
        for inst in self._instruments:
            if exchange and inst.exchange.upper() != exchange.upper():
                continue
            if inst.name:
                names.add(inst.name.upper())
        return sorted(names)

    @property
    def is_loaded(self) -> bool:
        """``True`` if the catalog has been populated (from disk or network)."""
        return self._loaded and bool(self._instruments)

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._instruments)
