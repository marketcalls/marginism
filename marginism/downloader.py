"""SPAN file auto-downloader for NSE (NFO/CDS/COM), BSE (BFO/BCD/BCO), and MCX.

Uses only the Python standard library (``urllib.request``, ``zipfile``,
``json``, ``csv``) — no third-party dependencies required.

Quick start
-----------
Download the **latest** NSE NFO SPAN file (settlement or newest intraday)::

    from marginism.downloader import download_latest_span_file
    from marginism.segments import get_segment

    spn_path, suffix = download_latest_span_file(segment=get_segment("NFO"))
    print(spn_path)   # /path/to/nsccl.YYYYMMDD.s.spn

Download the **first** (start-of-day) snapshot — pins margins to open-of-day
risk parameters, matching typical broker displays::

    from marginism.downloader import get_span_file

    spn_path, suffix = get_span_file()                 # NFO i1 / BFO 00 / MCX 0106-01
    spn_path, suffix = get_span_file(use_first=False)  # latest available

Download a specific BSE BFO snapshot::

    from marginism.downloader import download_span_file
    spn_path = download_span_file(suffix="04", segment=get_segment("BFO"))

Download MCX daily margin table::

    from marginism.downloader import download_mcx_daily_margin_file
    csv_path = download_mcx_daily_margin_file()

By default files are saved to ``~/.marginism/data/``.  Pass *data_dir* to
override the destination directory.

First vs latest SPAN
--------------------
Exchanges publish multiple intraday snapshots every trading day:

* NSE publishes six: ``i1`` (≈10:00 IST) through ``i5`` (≈15:30 IST) plus
  ``s`` (settlement, ≈18:00 IST).  ``s`` is "latest"; ``i1`` is "first".
* BSE BFO publishes five: ``00`` (base) plus ``01``..``04`` intraday.
* MCX publishes ten: ``0106-01`` (≈01:06) through ``2329-10`` (≈23:29).

**use_first=True** (``_suffixes_for_mode`` returns ``(segment.suffixes[-1],)``)
  Restricts the search to the earliest snapshot of the day.  Useful when you
  want stable, start-of-day margin parameters that do not change intraday —
  this matches the default broker order-margin display for NSE/MCX.

High-level ``get_span_file()`` defaults to ``use_first=True``.
The lower-level ``download_latest_span_file()`` and ``find_local_span_file()``
helpers still default to the newest snapshot when ``use_first=False``.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .segments import DEFAULT_SEGMENT, SpanSegment

logger = logging.getLogger(__name__)

# Default storage directory (created on first use).
DEFAULT_DATA_DIR: Path = Path.home() / ".marginism" / "data"

# MCXCCL WebMethod endpoint for the daily margin table.
MCX_DAILY_MARGIN_URL: str = (
    "https://www.mcxccl.com/backpage.aspx/GetDailyMargin"
)

MCX_DAILY_MARGIN_COLUMNS: Tuple[str, ...] = (
    "Date",
    "FileID",
    "InstrumentID",
    "Symbol",
    "Expiry Date",
    "Initial Margin(%)",
    "Tender Margin(%)",
    "Total Margin(%)",
    "Additional Long Margin(%)",
    "Additional Short Margin(%)",
    "Special Long Margin(%)",
    "Special Short Margin(%)",
    "ELM Long (%)",
    "ELM Short (%)",
    "Delivery Margin(%)",
    "Daily Volatility",
    "Annualized Volatility",
)

_MCX_DAILY_MARGIN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Date": ("Date", "TradeDate", "Trade Date"),
    "FileID": ("FileID", "FileId", "File ID"),
    "InstrumentID": ("InstrumentID", "InstrumentId", "Instrument ID"),
    "Symbol": ("Symbol", "Commodity", "Contract"),
    "Expiry Date": ("Expiry Date", "ExpiryDate", "Expiry"),
    "Initial Margin(%)": (
        "Initial Margin(%)",
        "InitialMargin",
        "InitialMarginPer",
    ),
    "Tender Margin(%)": (
        "Tender Margin(%)",
        "TenderMargin",
        "TenderMarginPer",
    ),
    "Total Margin(%)": ("Total Margin(%)", "TotalMargin", "TotalMarginPer"),
    "Additional Long Margin(%)": (
        "Additional Long Margin(%)",
        "AdditionalLongMargin",
        "AddLongMargin",
    ),
    "Additional Short Margin(%)": (
        "Additional Short Margin(%)",
        "AdditionalShortMargin",
        "AddShortMargin",
    ),
    "Special Long Margin(%)": (
        "Special Long Margin(%)",
        "SpecialLongMargin",
        "SplLongMargin",
    ),
    "Special Short Margin(%)": (
        "Special Short Margin(%)",
        "SpecialShortMargin",
        "SplShortMargin",
    ),
    "ELM Long (%)": ("ELM Long (%)", "ELMLong", "ElmLong", "ELM Long"),
    "ELM Short (%)": ("ELM Short (%)", "ELMShort", "ElmShort", "ELM Short"),
    "Delivery Margin(%)": (
        "Delivery Margin(%)",
        "DeliveryMargin",
        "DeliveryMarginPer",
    ),
    "Daily Volatility": ("Daily Volatility", "DailyVolatility"),
    "Annualized Volatility": (
        "Annualized Volatility",
        "AnnualizedVolatility",
    ),
}

_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _suffixes_for_mode(
    segment: SpanSegment, use_first: bool
) -> Tuple[str, ...]:
    """Return the suffix tuple appropriate for the requested SPAN mode.

    Parameters
    ----------
    segment:
        The exchange segment whose ``suffixes`` are used.
    use_first:
        * ``True``  — return only ``(segment.suffixes[-1],)``, i.e. the
          earliest (first-of-day) snapshot.  For NSE this is ``("i1",)``,
          for MCX ``("0106-01",)``, for BFO ``("00",)``.
        * ``False`` — return all ``segment.suffixes`` in newest-first order
          (standard "latest available" behaviour).

    Examples
    --------
    >>> from marginism.segments import get_segment
    >>> _suffixes_for_mode(get_segment("NFO"), use_first=True)
    ('i1',)
    >>> _suffixes_for_mode(get_segment("NFO"), use_first=False)
    ('s', 'i5', 'i4', 'i3', 'i2', 'i1')
    """
    if use_first and len(segment.suffixes) > 1:
        return (segment.suffixes[-1],)
    return segment.suffixes


def download_file(
    url: str,
    dest: Path,
    timeout: float = 120.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> bool:
    """Download *url* to *dest* using ``urllib.request``. Returns ``True`` on success."""
    headers: Dict[str, str] = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
        dest.write_bytes(content)
        logger.info(
            "Downloaded: %s → %s (%d bytes)", url, dest, len(content)
        )
        return True
    except urllib.error.HTTPError as e:
        logger.warning("HTTP %d for %s", e.code, url)
    except urllib.error.URLError as e:
        logger.warning("URL error for %s: %s", url, e)
    except Exception as e:
        logger.warning("Failed to download %s: %s", url, e)
    return False


def _format_url_base(url_base: str, date: datetime) -> str:
    """Expand ``{year}`` / ``{month}`` / ``{date}`` placeholders in a URL base."""
    return url_base.format(
        date=date.strftime("%Y%m%d"),
        year=date.strftime("%Y"),
        month=date.strftime("%B").lower(),
    )


def extract_zip(zip_path: Path, extract_dir: Path) -> Optional[Path]:
    """Extract a SPAN ZIP and return the path to the ``.spn`` or ``.xml`` payload.

    Returns ``None`` if the archive contains no recognisable SPAN payload or
    if extraction fails.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            span_files = [
                f
                for f in zf.namelist()
                if f.lower().endswith(".spn") or f.lower().endswith(".xml")
            ]
            if not span_files:
                logger.error("No SPAN XML payload found in %s", zip_path)
                return None
            zf.extractall(extract_dir)
            extracted = extract_dir / span_files[0]
            logger.info("Extracted: %s → %s", zip_path, extracted)
            return extracted
    except zipfile.BadZipFile:
        logger.error("Bad ZIP file: %s", zip_path)
    except Exception as e:
        logger.error("Failed to extract %s: %s", zip_path, e)
    return None


def download_span_file(
    suffix: Optional[str] = None,
    date: Optional[datetime] = None,
    retry_count: int = 3,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Download and extract one SPAN snapshot for *segment*.

    Parameters
    ----------
    suffix:
        One of ``segment.suffixes``. Defaults to the last entry (oldest/earliest
        snapshot) for deterministic behaviour.
    date:
        Trading date. Defaults to today.
    retry_count:
        Number of download attempts before giving up.
    segment:
        Exchange segment to download. Defaults to NSE NFO.
    data_dir:
        Directory to save files in. Defaults to ``~/.marginism/data``.

    Returns
    -------
    Path to the extracted ``.spn`` file, or ``None`` on failure.
    """
    if suffix is None:
        suffix = segment.suffixes[-1]
    if date is None:
        date = datetime.now()
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    _ensure_dir(data_dir)

    date_str = date.strftime("%Y%m%d")
    zip_name = segment.span_zip_template.format(date=date_str, suffix=suffix)
    raw_url_base = segment.span_url_base_overrides.get(
        suffix, segment.span_url_base
    )
    url_base = _format_url_base(raw_url_base, date)
    url = f"{url_base}/{zip_name}"
    zip_path = data_dir / zip_name

    for attempt in range(1, retry_count + 1):
        logger.info(
            "Downloading %s SPAN (%s) attempt %d/%d: %s",
            segment.code,
            suffix,
            attempt,
            retry_count,
            url,
        )
        if download_file(url, zip_path):
            spn_path = extract_zip(zip_path, data_dir)
            if spn_path:
                return spn_path
        if attempt < retry_count:
            time.sleep(2 ** attempt)

    logger.info(
        "%s SPAN suffix %s not available for %s",
        segment.code,
        suffix,
        date_str,
    )
    return None


def download_latest_span_file(
    date: Optional[datetime] = None,
    suffixes: Optional[Tuple[str, ...]] = None,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
    use_first: bool = False,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download the most recent SPAN snapshot available for *date* and *segment*.

    Tries suffixes newest → oldest (per ``segment.suffixes`` order). Returns
    ``(spn_path, suffix)`` on success, or ``(None, None)`` if nothing is
    available.

    Parameters
    ----------
    use_first:
        When ``True``, restrict the download to the earliest snapshot of the
        day (``segment.suffixes[-1]``).  This pins margins to start-of-day
        risk parameters — for NSE that is ``i1``, BFO ``00``, MCX ``0106-01``.
        When ``False`` (default), the newest available snapshot is downloaded.
        Ignored when *suffixes* is provided explicitly.
    """
    if suffixes is None:
        suffixes = _suffixes_for_mode(segment, use_first)
    for suffix in suffixes:
        path = download_span_file(
            suffix=suffix,
            date=date,
            retry_count=1,
            segment=segment,
            data_dir=data_dir,
        )
        if path is not None:
            return path, suffix
    logger.error(
        "No %s SPAN file available for %s (tried %s)",
        segment.code,
        (date or datetime.now()).strftime("%Y%m%d"),
        ",".join(suffixes),
    )
    return None, None


def find_local_span_file(
    date: Optional[datetime] = None,
    suffixes: Optional[Tuple[str, ...]] = None,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
    use_first: bool = False,
) -> Tuple[Optional[Path], Optional[str]]:
    """Return the best matching local SPAN payload already present on disk.

    Scans *data_dir* for ZIP archives matching *segment*'s template,
    extracts the best candidate, and returns ``(spn_path, suffix)``.
    Returns ``(None, None)`` if nothing matches.

    Parameters
    ----------
    use_first:
        When ``True``, look only for the earliest snapshot of the day
        (``segment.suffixes[-1]``).  When ``False`` (default), the newest
        locally available snapshot is returned.
        Ignored when *suffixes* is provided explicitly.
    """
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    if date is None:
        date = datetime.now()
    if suffixes is None:
        suffixes = _suffixes_for_mode(segment, use_first)

    suffix_order = {s: i for i, s in enumerate(suffixes)}
    escaped = re.escape(segment.span_zip_template)
    pattern_re = (
        "^"
        + escaped.replace(r"\{date\}", r"(?P<date>\d{8})").replace(
            r"\{suffix\}", r"(?P<suffix>[A-Za-z0-9-]+)"
        )
        + "$"
    )
    regex = re.compile(pattern_re)
    glob_pattern = segment.span_zip_template.format(date="*", suffix="*")

    candidates: List[Tuple[str, int, Path, str]] = []
    for zip_path in data_dir.glob(glob_pattern):
        m = regex.match(zip_path.name)
        if m is None:
            continue
        date_token = m.group("date")
        suffix = m.group("suffix")
        if suffix not in suffix_order:
            continue
        if date_token != date.strftime("%Y%m%d"):
            continue
        candidates.append((date_token, -suffix_order[suffix], zip_path, suffix))

    for _, _, zip_path, suffix in sorted(candidates, reverse=True):
        spn_path = extract_zip(zip_path, data_dir)
        if spn_path is not None:
            return spn_path, suffix
    return None, None


def get_span_file(
    date: Optional[datetime] = None,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
    use_first: bool = True,
    download: bool = True,
) -> Tuple[Optional[Path], Optional[str]]:
    """Get the SPAN file for *date*, honouring the first/latest *mode*.

    This is the preferred high-level entry point.  It checks local disk first
    and only hits the network when a matching file is not already present.

    Parameters
    ----------
    date:
        Trading date to fetch.  Defaults to today.
    segment:
        Exchange segment.  Defaults to NSE NFO.
    data_dir:
        Directory to scan for local files and to save new downloads.
        Defaults to ``~/.marginism/data``.
    use_first:
        * ``True``  — use/download the **first** (start-of-day) snapshot.
          For NSE this is ``i1`` (≈10:00 IST), for MCX ``0106-01`` (≈01:06),
          for BFO ``00`` (base snapshot).  Margins stay stable across the day.
          Default.
        * ``False`` — use/download the **latest** available snapshot
          (settlement > intraday, newest first).
    download:
        When ``True`` (default), download from the exchange archive if no
        local file matches.  Set to ``False`` to restrict to disk-only lookup.

    Returns
    -------
    ``(spn_path, suffix)`` on success, or ``(None, None)`` if unavailable.

    Examples
    --------
    >>> # First snapshot of the day — stable start-of-day margins (default)
    >>> spn, sfx = get_span_file()
    >>> print(sfx)  # 'i1' for NFO, '0106-01' for MCX, '00' for BFO

    >>> # Latest settlement or intraday snapshot
    >>> spn, sfx = get_span_file(use_first=False)

    >>> # BFO latest
    >>> from marginism.segments import get_segment
    >>> spn, sfx = get_span_file(segment=get_segment("BFO"))

    >>> # MCX first snapshot, no network
    >>> spn, sfx = get_span_file(segment=get_segment("MCX"),
    ...                          use_first=True, download=False)
    """
    # 1. Try local disk first.
    spn_path, suffix = find_local_span_file(
        date=date, segment=segment, data_dir=data_dir, use_first=use_first
    )
    if spn_path is not None:
        logger.info(
            "Using local %s SPAN file (use_first=%s, suffix=%s): %s",
            segment.code, use_first, suffix, spn_path.name,
        )
        return spn_path, suffix

    # 2. Optionally download.
    if not download:
        logger.info(
            "No local %s SPAN file found (use_first=%s); download=False",
            segment.code, use_first,
        )
        return None, None

    logger.info(
        "No local %s SPAN file; downloading (use_first=%s)…",
        segment.code, use_first,
    )
    return download_latest_span_file(
        date=date, segment=segment, data_dir=data_dir, use_first=use_first
    )


# ---------------------------------------------------------------------------
# MCX daily margin table (WebMethod / JSON endpoint)
# ---------------------------------------------------------------------------


def _mcx_daily_margin_payload_rows(payload: Any) -> List[Dict[str, Any]]:
    """Extract row dicts from the MCXCCL WebMethod JSON response."""
    data = (
        payload.get("d")
        if isinstance(payload, dict) and "d" in payload
        else payload
    )
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        for key in ("Table", "Data", "Rows", "dailyMargin"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _mcx_daily_margin_value(row: Dict[str, Any], column: str) -> Any:
    for key in _MCX_DAILY_MARGIN_ALIASES[column]:
        if key in row:
            return row[key]
    compact = {str(k).replace(" ", "").lower(): v for k, v in row.items()}
    for key in _MCX_DAILY_MARGIN_ALIASES[column]:
        value = compact.get(key.replace(" ", "").lower())
        if value is not None:
            return value
    return ""


def _write_mcx_daily_margin_csv(
    rows: List[Dict[str, Any]], dest: Path
) -> bool:
    if not rows:
        return False
    with open(dest, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MCX_DAILY_MARGIN_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    col: _mcx_daily_margin_value(row, col)
                    for col in MCX_DAILY_MARGIN_COLUMNS
                }
            )
    return True


def download_mcx_daily_margin_file(
    date: Optional[datetime] = None,
    retry_count: int = 3,
    data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Download the MCXCCL Daily Margin table as a CSV file.

    Posts ``{"Date": "YYYYMMDD"}`` to the Sitefinity WebMethod endpoint and
    writes the JSON rows to ``DailyMargin_YYYYMMDDHHMMSS.csv``.

    Returns the path to the CSV file, or ``None`` on failure.
    """
    if date is None:
        date = datetime.now()
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    _ensure_dir(data_dir)

    date_token = date.strftime("%Y%m%d")
    timestamp = date.strftime("%Y%m%d%H%M%S")
    dest = data_dir / f"DailyMargin_{timestamp}.csv"

    payload_bytes = json.dumps(
        {"Date": date_token}, separators=(",", ":")
    ).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "Origin": "https://www.mcxccl.com",
        "Referer": "https://www.mcxccl.com/risk-management/daily-margin",
        "X-Requested-With": "XMLHttpRequest",
    }

    for attempt in range(1, retry_count + 1):
        logger.info(
            "Downloading MCX daily margin attempt %d/%d for %s",
            attempt,
            retry_count,
            date_token,
        )
        try:
            req = urllib.request.Request(
                MCX_DAILY_MARGIN_URL,
                data=payload_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            response_data = json.loads(body)
            rows = _mcx_daily_margin_payload_rows(response_data)
            if _write_mcx_daily_margin_csv(rows, dest):
                logger.info(
                    "Downloaded MCX daily margin: %s (%d rows)",
                    dest,
                    len(rows),
                )
                return dest
            logger.warning("MCX daily margin response contained no rows")
        except Exception as exc:
            logger.warning("Failed to download MCX daily margin: %s", exc)
        if attempt < retry_count:
            time.sleep(2 ** attempt)
    return None


def find_local_mcx_daily_margin_file(
    date: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the newest local MCXCCL DailyMargin CSV export."""
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    date_token = date.strftime("%Y%m%d") if date is not None else "*"
    patterns = (
        f"DailyMargin_{date_token}*.csv",
        f"DailyMargin*{date_token}*.csv",
    )
    matches: List[Path] = []
    for pattern in patterns:
        matches.extend(data_dir.glob(pattern))
    unique = {p.resolve(): p for p in matches}
    if not unique:
        return None
    return max(unique.values(), key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Exposure margin file (NSE NFO publishes ael_DDMMYYYY.csv)
# ---------------------------------------------------------------------------


def download_exposure_file(
    date: Optional[datetime] = None,
    retry_count: int = 3,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Download the exposure-margin CSV for *segment*, if it publishes one.

    Returns the path to the CSV, or ``None`` if the segment has no exposure
    file or the download fails.
    """
    if segment.exposure_url_base is None or segment.exposure_template is None:
        logger.debug("Segment %s has no exposure file", segment.code)
        return None
    if date is None:
        date = datetime.now()
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    _ensure_dir(data_dir)

    date_str = date.strftime("%d%m%Y")
    file_name = segment.exposure_template.format(date=date_str)
    url = f"{segment.exposure_url_base}/{file_name}"
    dest = data_dir / file_name

    for attempt in range(1, retry_count + 1):
        logger.info(
            "Downloading %s exposure file attempt %d/%d: %s",
            segment.code,
            attempt,
            retry_count,
            url,
        )
        if download_file(url, dest):
            return dest
        if attempt < retry_count:
            time.sleep(2 ** attempt)

    logger.warning(
        "%s exposure file not available: %s", segment.code, url
    )
    return None


def find_local_exposure_file(
    date: Optional[datetime] = None,
    segment: SpanSegment = DEFAULT_SEGMENT,
    data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the newest matching local exposure CSV for *segment*."""
    if segment.exposure_template is None:
        return None
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    date_token = date.strftime("%d%m%Y") if date is not None else "*"
    pattern = segment.exposure_template.format(date=date_token)
    matches = sorted(data_dir.glob(pattern), reverse=True)
    return matches[0] if matches else None
