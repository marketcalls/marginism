"""User-side position representation and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union


def _norm_expiry(expiry: Union[str, None]) -> str:
    """Normalise an expiry to the file's 'YYYYMMDD' form.

    Accepts 'YYYYMMDD', 'YYYY-MM-DD', 'DD-MM-YYYY', or a date/datetime.
    """
    if expiry is None:
        return ""
    if hasattr(expiry, "strftime"):
        return expiry.strftime("%Y%m%d")
    s = str(expiry).strip()
    if not s:
        return ""
    digits = s.replace("-", "").replace("/", "")
    if len(digits) == 8 and digits.isdigit():
        # could be YYYYMMDD or DDMMYYYY
        if digits[:4] in ("19", "20") or digits[:2] in ("19", "20"):
            # heuristics: leading 20xx -> YYYYMMDD
            if 1900 <= int(digits[:4]) <= 2100:
                return digits
        # try DDMMYYYY
        if 1900 <= int(digits[4:]) <= 2100:
            return digits[4:] + digits[2:4] + digits[:2]
        return digits
    return s


@dataclass
class Position:
    """A single open position.

    Attributes
    ----------
    symbol:
        Combined-commodity / NSE trading symbol, e.g. ``"NIFTY"`` or
        ``"RELIANCE"``.
    instrument:
        ``"FUT"`` for futures, ``"CE"``/``"PE"`` (or ``"C"``/``"P"``) for
        options.
    quantity:
        *Signed* size in **underlying units** that you enter directly — for
        NIFTY (lot size 65) pass ``65`` for one lot, ``130`` for two, etc.
        Long is positive, short is negative.  :meth:`from_lots` is an optional
        helper if you would rather pass ``lots`` and a lot size.
    expiry:
        Expiry of the contract (any common format; see :func:`_norm_expiry`).
        Optional for a symbol that has a single series.
    strike:
        Option strike (ignored for futures).
    """

    symbol: str
    instrument: str
    quantity: float
    expiry: Optional[str] = None
    strike: float = 0.0

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        self.instrument = self.instrument.strip().upper()
        self.expiry = _norm_expiry(self.expiry)

    @property
    def is_option(self) -> bool:
        return self.instrument in ("CE", "PE", "C", "P", "CALL", "PUT", "OPT")

    @property
    def is_future(self) -> bool:
        return self.instrument in ("FUT", "FUTIDX", "FUTSTK", "F")

    @classmethod
    def from_lots(
        cls,
        symbol: str,
        instrument: str,
        lots: float,
        lot_size: int,
        expiry: Optional[str] = None,
        strike: float = 0.0,
    ) -> "Position":
        return cls(
            symbol=symbol,
            instrument=instrument,
            quantity=lots * lot_size,
            expiry=expiry,
            strike=strike,
        )


def normalize_expiry(expiry) -> str:
    return _norm_expiry(expiry)
