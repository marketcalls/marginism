"""Instrument catalog for NSE, BSE, and MCX derivatives.

Downloads the Kite Connect public instrument master CSV and provides a local
cache with fast lot-size and segment lookups.  No third-party dependencies —
uses only ``urllib.request`` and ``csv`` from the standard library.

Quick start
-----------
>>> from marginism.instruments import InstrumentDB
>>> db = InstrumentDB()
>>> db.update()                     # fetch latest from Kite; saves to disk
>>> db.lot_size("NIFTY")            # defaults to exchange="NFO"
75
>>> db.lot_size("CRUDEOIL", exchange="MCX")
100
>>> db.search("RELIANCE", exchange="NFO", instrument_type="FUT")
[InstrumentInfo(tradingsymbol='RELIANCE26JUNFUT', ...)]

The catalog is stored as a JSON file at ``~/.marginism/data/instruments_catalog.json``
and is reloaded automatically on the next call after it has been written.
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

# Kite Connect public instrument master (no authentication required).
KITE_INSTRUMENTS_URL: str = "https://api.kite.trade/instruments"

DEFAULT_DATA_DIR: Path = Path.home() / ".marginism" / "data"
CATALOG_FILENAME: str = "instruments_catalog.json"

# Only keep derivative segment instruments; skip EQ cash market, etc.
DERIVATIVE_EXCHANGES = frozenset({"NFO", "BFO", "CDS", "COM", "MCX", "BCD", "BCO"})


@dataclass
class InstrumentInfo:
    """Lightweight record for one derivative instrument."""

    tradingsymbol: str
    name: str
    exchange: str
    segment: str
    instrument_type: str   # CE, PE, FUT, …
    lot_size: int
    tick_size: float
    strike: float
    expiry: str            # YYYY-MM-DD or empty string


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


def _row_to_info(row: Dict[str, Any]) -> InstrumentInfo:
    return InstrumentInfo(
        tradingsymbol=(row.get("tradingsymbol") or "").strip(),
        name=(row.get("name") or "").strip(),
        exchange=(row.get("exchange") or "").strip().upper(),
        segment=(row.get("segment") or "").strip(),
        instrument_type=(row.get("instrument_type") or "").strip().upper(),
        lot_size=_safe_int(row.get("lot_size"), default=1),
        tick_size=_safe_float(row.get("tick_size"), default=0.05),
        strike=_safe_float(row.get("strike"), default=0.0),
        expiry=(row.get("expiry") or "").strip(),
    )


class InstrumentDB:
    """Local instrument catalog backed by a JSON file.

    On first use the catalog is loaded from ``~/.marginism/data/instruments_catalog.json``
    (if it exists).  Call :meth:`update` to download the latest data from Kite
    and refresh the cache.

    Example
    -------
    >>> db = InstrumentDB()
    >>> db.update()                  # one-time download
    >>> db.lot_size("NIFTY")         # 75
    >>> db.lot_size("CRUDEOIL", exchange="MCX")
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
        url: str = KITE_INSTRUMENTS_URL,
        timeout: float = 60.0,
    ) -> bool:
        """Download the Kite instrument master and refresh the local catalog.

        Parameters
        ----------
        url:
            Override the default Kite instruments endpoint (useful for tests).
        timeout:
            HTTP read timeout in seconds.

        Returns
        -------
        ``True`` on success, ``False`` otherwise.
        """
        logger.info("Downloading instrument master from %s", url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "marginism/1.0 instrument-catalog"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read()
        except Exception as e:
            logger.error("Failed to download instrument master: %s", e)
            return False

        rows = _parse_kite_csv(content)
        instruments: List[InstrumentInfo] = []
        for row in rows:
            try:
                instruments.append(_row_to_info(row))
            except Exception as e:
                logger.debug("Skipping bad row: %s", e)

        if not instruments:
            logger.error("No derivative instruments found in downloaded CSV")
            return False

        self._set_instruments(instruments)
        self._save_catalog(instruments)
        logger.info(
            "Instrument catalog updated: %d instruments", len(instruments)
        )
        return True

    def load_from_csv(self, content: bytes) -> bool:
        """Populate the catalog from raw CSV bytes (e.g. from a local file).

        Useful when you already have the Kite CSV on disk and want to avoid
        a network call.  Does *not* persist to the JSON cache.

        Returns ``True`` if at least one instrument was loaded.
        """
        rows = _parse_kite_csv(content)
        instruments: List[InstrumentInfo] = []
        for row in rows:
            try:
                instruments.append(_row_to_info(row))
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
