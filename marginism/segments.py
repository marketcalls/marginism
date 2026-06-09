"""Exchange segment registry for SPAN file downloads.

NSE and BSE publish CME-SPAN format risk-parameter files for several
derivative segments. MCX publishes RPF files for commodity derivatives.
All use the same ``.spn`` XML grammar — only the URL pattern, filename
prefix, and intraday snapshot suffixes differ per segment.

Segment catalogue
-----------------
NFO  — NSE equity/index F&O        (archives.nseindia.com)
CDS  — NSE currency derivatives    (archives.nseindia.com)
COM  — NSE commodity derivatives   (archives.nseindia.com)
BFO  — BSE equity/index F&O        (www.bseindia.com)
BCD  — BSE currency derivatives    (www.bseindia.com)
BCO  — BSE commodity derivatives   (www.bseindia.com)
MCX  — MCX commodity derivatives   (www.mcxccl.com)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# NSE: six snapshots per day, newest → oldest
NSE_SUFFIXES: tuple = ("s", "i5", "i4", "i3", "i2", "i1")

# BSE BFO: one base snapshot (00) plus four intraday snapshots
BFO_SUFFIXES: tuple = ("04", "03", "02", "01", "00")

# BSE BCD/BCO: single snapshot until intraday cadence is confirmed
BSE_SINGLE_SUFFIXES: tuple = ("00",)

# MCX: ten RPF snapshots per day, newest → oldest
MCX_SUFFIXES: tuple = (
    "2329-10",
    "2230-09",
    "2030-08",
    "1900-07",
    "1700-06",
    "1500-05",
    "1300-04",
    "1100-03",
    "0930-02",
    "0106-01",
)


@dataclass(frozen=True)
class SpanSegment:
    """Descriptor for one SPAN-publishing market segment."""

    code: str
    """Short segment code, e.g. ``"NFO"`` / ``"BFO"``."""

    span_url_base: str
    """Base URL holding SPAN zip archives.

    May use ``{year}`` and ``{month}`` placeholders for archives that embed
    calendar path components (e.g. MCX Sitefinity).
    """

    span_zip_template: str
    """ZIP filename template; accepts ``{date}`` (YYYYMMDD) and ``{suffix}``."""

    suffixes: tuple = field(default_factory=lambda: NSE_SUFFIXES)
    """Snapshot suffixes ordered newest → oldest."""

    span_url_base_overrides: Dict[str, str] = field(default_factory=dict)
    """Optional per-suffix base-URL overrides (e.g. BSE BFO base-snapshot)."""

    exposure_url_base: Optional[str] = None
    """Base URL for the exposure-margin CSV, if the segment publishes one."""

    exposure_template: Optional[str] = None
    """Exposure CSV filename template; accepts ``{date}`` (DDMMYYYY)."""


SEGMENTS: Dict[str, SpanSegment] = {
    # ----------------------------------------------------------------- NSE
    "NFO": SpanSegment(
        code="NFO",
        span_url_base="https://archives.nseindia.com/archives/nsccl/span",
        span_zip_template="nsccl.{date}.{suffix}.zip",
        suffixes=NSE_SUFFIXES,
        exposure_url_base="https://archives.nseindia.com/archives/exp_lim",
        exposure_template="ael_{date}.csv",
    ),
    "CDS": SpanSegment(
        code="CDS",
        span_url_base="https://archives.nseindia.com/archives/cd/span",
        span_zip_template="nsccl_x.{date}.{suffix}.zip",
        suffixes=NSE_SUFFIXES,
    ),
    "COM": SpanSegment(
        code="COM",
        span_url_base="https://archives.nseindia.com/archives/com/span",
        span_zip_template="nsccl_o.{date}.{suffix}.zip",
        suffixes=NSE_SUFFIXES,
    ),
    # ----------------------------------------------------------------- BSE
    # BFO intraday snapshots (01..04) live in /SPN; the base (00) is served
    # from the parent Risk_Automate directory.
    "BFO": SpanSegment(
        code="BFO",
        span_url_base="https://www.bseindia.com/bsedata/Risk_Automate/SPN",
        span_zip_template="BSERISK{date}-{suffix}.ZIP",
        suffixes=BFO_SUFFIXES,
        span_url_base_overrides={
            "00": "https://www.bseindia.com/bsedata/Risk_Automate",
        },
    ),
    "BCD": SpanSegment(
        code="BCD",
        span_url_base=(
            "https://www.bseindia.com/bsedata/Risk_Automate/CURRENCY/SPN"
        ),
        span_zip_template="BSECDXRISK{date}-{suffix}.ZIP",
        suffixes=BSE_SINGLE_SUFFIXES,
    ),
    "BCO": SpanSegment(
        code="BCO",
        span_url_base=(
            "https://www.bseindia.com/bsedata/Risk_Automate/commodity/SPN"
        ),
        span_zip_template="BCXRISK{date}-{suffix}.ZIP",
        suffixes=BSE_SINGLE_SUFFIXES,
    ),
    # ----------------------------------------------------------------- MCX
    # MCXCCL Sitefinity archive; URL includes year + month path components.
    "MCX": SpanSegment(
        code="MCX",
        span_url_base=(
            "https://www.mcxccl.com/docs/default-source/market-operations/"
            "daily-span-risk-parameter-file/{year}/{month}"
        ),
        span_zip_template="mcxrpf-{date}-{suffix}-i.zip",
        suffixes=MCX_SUFFIXES,
        span_url_base_overrides={
            # Some MCX entries are hosted in the default document library.
            "1100-03": (
                "https://www.mcxccl.com/docs/default-source/"
                "default-document-library"
            ),
        },
    ),
}

DEFAULT_SEGMENT: SpanSegment = SEGMENTS["NFO"]


def get_segment(code: str) -> SpanSegment:
    """Return a registered segment by code (case-insensitive).

    Raises ``KeyError`` for unknown codes.
    """
    key = code.upper()
    if key not in SEGMENTS:
        raise KeyError(
            f"Unknown SPAN segment {code!r}; known: {sorted(SEGMENTS)}"
        )
    return SEGMENTS[key]
