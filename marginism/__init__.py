"""marginism — compute NSE/NSCCL SPAN margins from CME-SPAN ``.spn`` files.

Quick start
-----------
>>> from marginism import SpanCalculator, Position
>>> calc = SpanCalculator.from_file(
...     "nsccl.20260529.s/nsccl.20260529.s.spn", symbols=["NIFTY"])
>>> res = calc.calculate([
...     Position("NIFTY", "FUT", quantity=65, expiry="20260630"),
... ])
>>> print(res.summary())

The ``.spn`` file ships precomputed 16-scenario risk arrays, so margin is pure
arithmetic — no option pricing involved.  ``quantity`` is in underlying units
that you enter directly (NIFTY lot size 65 -> pass 65 for one lot, 130 for two);
long is positive, short negative.
"""

from .algorithm import CommodityResult, ResolvedPosition, compute_commodity
from .calculator import MarginResult, PositionResult, SpanCalculator
from .exposure import ExposureConfig
from .model import (
    CalendarSpread,
    CombinedCommodity,
    Contract,
    FuturesContract,
    OptionContract,
    RiskArray,
    SpanFile,
    SCENARIO_LABELS,
)
from .parser import parse_spn
from .portfolio import Position, normalize_expiry
from .api import RiskEngine
from .symbols import SymbolResolver, build_symbol_index
from .segments import (
    SpanSegment,
    SEGMENTS,
    DEFAULT_SEGMENT,
    NSE_SUFFIXES,
    BFO_SUFFIXES,
    MCX_SUFFIXES,
    get_segment,
)
from .instruments import InstrumentDB, InstrumentInfo
from .downloader import (
    download_file,
    download_span_file,
    download_latest_span_file,
    download_exposure_file,
    download_mcx_daily_margin_file,
    extract_zip,
    find_local_span_file,
    find_local_exposure_file,
    find_local_mcx_daily_margin_file,
    get_span_file,
    _suffixes_for_mode,
)

__version__ = "0.2.0"

__all__ = [
    # --- core SPAN calculator ---
    "SpanCalculator",
    "MarginResult",
    "PositionResult",
    "Position",
    "ExposureConfig",
    "RiskEngine",
    "SymbolResolver",
    "build_symbol_index",
    "SpanFile",
    "CombinedCommodity",
    "Contract",
    "FuturesContract",
    "OptionContract",
    "CalendarSpread",
    "RiskArray",
    "SCENARIO_LABELS",
    "ResolvedPosition",
    "CommodityResult",
    "compute_commodity",
    "parse_spn",
    "normalize_expiry",
    # --- exchange segment registry ---
    "SpanSegment",
    "SEGMENTS",
    "DEFAULT_SEGMENT",
    "NSE_SUFFIXES",
    "BFO_SUFFIXES",
    "MCX_SUFFIXES",
    "get_segment",
    # --- instrument catalog ---
    "InstrumentDB",
    "InstrumentInfo",
    # --- downloader ---
    "download_file",
    "download_span_file",
    "download_latest_span_file",
    "download_exposure_file",
    "download_mcx_daily_margin_file",
    "extract_zip",
    "find_local_span_file",
    "find_local_exposure_file",
    "find_local_mcx_daily_margin_file",
    "get_span_file",
    "__version__",
]
