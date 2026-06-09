"""Tests for the SPAN file downloader (marginism.downloader).

All network calls are patched; no real HTTP requests are made.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from marginism import downloader as dl
from marginism.segments import get_segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    """Minimal file-like object mimicking ``urllib.request.urlopen`` return."""

    def __init__(self, content: bytes, status: int = 200):
        self._content = content
        self.status = status
        self._stream = io.BytesIO(content)

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_zip(spn_content: bytes, inner_name: str = "test.spn") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, spn_content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_success_writes_content(self, tmp_path):
        dest = tmp_path / "out.zip"
        fake_resp = _FakeHTTPResponse(b"zip-content")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = dl.download_file("http://example.com/file.zip", dest)
        assert result is True
        assert dest.read_bytes() == b"zip-content"

    def test_http_error_returns_false(self, tmp_path):
        import urllib.error

        dest = tmp_path / "out.zip"
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://x.com/f.zip", 404, "Not Found", {}, None
            ),
        ):
            result = dl.download_file("http://x.com/f.zip", dest)
        assert result is False

    def test_url_error_returns_false(self, tmp_path):
        import urllib.error

        dest = tmp_path / "out.zip"
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("network unreachable"),
        ):
            result = dl.download_file("http://x.com/f.zip", dest)
        assert result is False

    def test_includes_user_agent_header(self, tmp_path):
        dest = tmp_path / "out.zip"
        fake_resp = _FakeHTTPResponse(b"data")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return fake_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dl.download_file("http://x.com/f.zip", dest)

        # urllib.request.Request stores headers with first-char-capitalized keys
        # e.g. "User-Agent" -> stored as "User-agent"
        header_keys_lower = {k.lower() for k in captured["req"].headers}
        assert "user-agent" in header_keys_lower
        assert "mozilla" in str(captured["req"].headers).lower()


# ---------------------------------------------------------------------------
# extract_zip
# ---------------------------------------------------------------------------


class TestExtractZip:
    def test_extracts_spn_payload(self, tmp_path):
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        zip_path = tmp_path / "nsccl.20250808.s.zip"
        zip_path.write_bytes(zip_bytes)

        spn_path = dl.extract_zip(zip_path, tmp_path)

        assert spn_path is not None
        assert spn_path.name == "nsccl.20250808.s.spn"
        assert spn_path.read_text() == "<spanFile/>"

    def test_extracts_xml_payload(self, tmp_path):
        """BSE zips use .XML extension."""
        zip_bytes = _make_zip(b"<spanFile/>", "BSERISK20250808-00.XML")
        zip_path = tmp_path / "BSERISK20250808-00.ZIP"
        zip_path.write_bytes(zip_bytes)

        spn_path = dl.extract_zip(zip_path, tmp_path)

        assert spn_path is not None
        assert spn_path.name == "BSERISK20250808-00.XML"

    def test_returns_none_for_empty_zip(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        zip_path = tmp_path / "empty.zip"
        zip_path.write_bytes(buf.getvalue())

        result = dl.extract_zip(zip_path, tmp_path)
        assert result is None

    def test_returns_none_for_bad_zip(self, tmp_path):
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"not a zip file")
        result = dl.extract_zip(zip_path, tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# download_span_file
# ---------------------------------------------------------------------------


class TestDownloadSpanFile:
    def test_nfo_settlement_success(self, tmp_path):
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")

        def fake_urlopen(req, timeout=None):
            return _FakeHTTPResponse(zip_bytes)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            spn = dl.download_span_file(
                suffix="s",
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
            )

        assert spn is not None
        assert spn.name.endswith(".spn")

    def test_returns_none_on_http_error(self, tmp_path):
        import urllib.error

        seg = get_segment("NFO")
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://x.com/f.zip", 404, "Not Found", {}, None
            ),
        ):
            spn = dl.download_span_file(
                suffix="s",
                date=datetime(2025, 8, 8),
                retry_count=1,
                segment=seg,
                data_dir=tmp_path,
            )
        assert spn is None

    def test_url_includes_date_and_suffix(self, tmp_path):
        seg = get_segment("NFO")
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            raise Exception("abort after capture")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dl.download_span_file(
                suffix="i3",
                date=datetime(2025, 8, 8),
                retry_count=1,
                segment=seg,
                data_dir=tmp_path,
            )

        assert len(captured_urls) == 1
        assert "20250808" in captured_urls[0]
        assert "i3" in captured_urls[0]
        assert "nsccl.20250808.i3.zip" in captured_urls[0]

    def test_bfo_base_snapshot_uses_override_url(self, tmp_path):
        """BFO suffix '00' must use the parent-directory URL override."""
        seg = get_segment("BFO")
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            raise Exception("abort after capture")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dl.download_span_file(
                suffix="00",
                date=datetime(2025, 8, 8),
                retry_count=1,
                segment=seg,
                data_dir=tmp_path,
            )

        assert len(captured_urls) >= 1
        # Base URL override for '00' does NOT contain /SPN
        assert "/SPN/" not in captured_urls[0]
        assert "Risk_Automate" in captured_urls[0]

    def test_mcx_url_contains_year_and_month(self, tmp_path):
        """MCX URL base has {year}/{month} path components."""
        seg = get_segment("MCX")
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            raise Exception("abort after capture")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dl.download_span_file(
                suffix="1300-04",
                date=datetime(2025, 8, 8),
                retry_count=1,
                segment=seg,
                data_dir=tmp_path,
            )

        assert captured_urls
        url = captured_urls[0]
        assert "2025" in url
        assert "august" in url.lower()


# ---------------------------------------------------------------------------
# download_latest_span_file
# ---------------------------------------------------------------------------


class TestDownloadLatestSpanFile:
    def test_returns_settlement_when_available(self, tmp_path):
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            called.append(url)
            if url.endswith(".s.zip"):
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
            )

        assert suffix == "s"
        assert path is not None
        assert called[0].endswith("nsccl.20250808.s.zip")

    def test_falls_back_through_suffixes(self, tmp_path):
        """When s/i5/i4/i3/i2 all fail, it should succeed with i1."""
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.i01.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            called.append(url)
            if url.endswith(".i1.zip"):
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
            )

        assert suffix == "i1"
        assert path is not None
        # Check that all 6 suffixes were tried in newest→oldest order
        tried = [u.rsplit(".", 2)[1] for u in called]
        assert tried == ["s", "i5", "i4", "i3", "i2", "i1"]

    def test_returns_none_none_when_nothing_available(self, tmp_path):
        seg = get_segment("NFO")

        def fake_urlopen(req, timeout=None):
            raise Exception("always fails")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
            )

        assert path is None
        assert suffix is None


# ---------------------------------------------------------------------------
# find_local_span_file
# ---------------------------------------------------------------------------


class TestFindLocalSpanFile:
    def test_finds_existing_zip(self, tmp_path):
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        (tmp_path / "nsccl.20250808.s.zip").write_bytes(zip_bytes)

        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
        )

        assert path is not None
        assert suffix == "s"

    def test_returns_none_none_when_nothing_local(self, tmp_path):
        seg = get_segment("NFO")
        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
        )
        assert path is None
        assert suffix is None

    def test_prefers_newer_suffix(self, tmp_path):
        """When both 's' and 'i1' are present, 's' (index 0) wins."""
        seg = get_segment("NFO")
        for sfx in ("s", "i1"):
            zip_bytes = _make_zip(
                b"<spanFile/>", f"nsccl.20250808.{sfx}.spn"
            )
            (tmp_path / f"nsccl.20250808.{sfx}.zip").write_bytes(zip_bytes)

        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
        )
        assert suffix == "s"


# ---------------------------------------------------------------------------
# download_mcx_daily_margin_file
# ---------------------------------------------------------------------------


class TestDownloadMcxDailyMarginFile:
    def test_success_writes_csv(self, tmp_path):
        response_json = json.dumps({
            "d": [
                {
                    "Date": "6/8/2026 12:00:00 AM",
                    "FileID": "4",
                    "InstrumentID": "FUTCOM",
                    "Symbol": "CRUDEOIL",
                    "ExpiryDate": "18JUN2026",
                    "InitialMargin": "30.0000",
                    "ELMLong": "1.25",
                    "ELMShort": "1.25",
                }
            ]
        }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeHTTPResponse(response_json)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path = dl.download_mcx_daily_margin_file(
                date=datetime(2026, 6, 8, 14, 21, 15),
                retry_count=1,
                data_dir=tmp_path,
            )

        assert path is not None
        assert path.name == "DailyMargin_20260608142115.csv"
        text = path.read_text(encoding="utf-8")
        assert "Expiry Date" in text
        assert "CRUDEOIL" in text

    def test_posts_to_correct_url(self, tmp_path):
        captured = {}
        response_json = json.dumps({"d": []}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.method
            captured["data"] = req.data
            return _FakeHTTPResponse(response_json)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dl.download_mcx_daily_margin_file(
                date=datetime(2026, 6, 8),
                retry_count=1,
                data_dir=tmp_path,
            )

        assert captured["url"] == dl.MCX_DAILY_MARGIN_URL
        assert captured["method"] == "POST"
        assert b"20260608" in captured["data"]

    def test_returns_none_on_failure(self, tmp_path):
        def fake_urlopen(req, timeout=None):
            raise Exception("server error")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path = dl.download_mcx_daily_margin_file(
                date=datetime(2026, 6, 8),
                retry_count=1,
                data_dir=tmp_path,
            )

        assert path is None

    def test_returns_none_when_no_rows(self, tmp_path):
        response_json = json.dumps({"d": []}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeHTTPResponse(response_json)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path = dl.download_mcx_daily_margin_file(
                date=datetime(2026, 6, 8),
                retry_count=1,
                data_dir=tmp_path,
            )

        assert path is None

    def test_handles_nested_d_payload(self, tmp_path):
        """Tests the response parsing for nested payloads."""
        # The 'd' key may contain a JSON-encoded string rather than a list
        inner = json.dumps([
            {
                "Symbol": "GOLD",
                "ExpiryDate": "05AUG2026",
                "InitialMargin": "4.0",
            }
        ])
        response_json = json.dumps({"d": inner}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeHTTPResponse(response_json)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path = dl.download_mcx_daily_margin_file(
                date=datetime(2026, 6, 8),
                retry_count=1,
                data_dir=tmp_path,
            )

        # Rows should have been parsed from the double-encoded JSON
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "GOLD" in text


# ---------------------------------------------------------------------------
# find_local_mcx_daily_margin_file
# ---------------------------------------------------------------------------


class TestFindLocalMcxDailyMarginFile:
    def test_finds_existing_csv(self, tmp_path):
        csv_path = tmp_path / "DailyMargin_20260608142115.csv"
        csv_path.write_text("Date,Symbol\n20260608,CRUDEOIL\n")

        found = dl.find_local_mcx_daily_margin_file(
            date=datetime(2026, 6, 8), data_dir=tmp_path
        )
        assert found is not None
        assert found.name.startswith("DailyMargin_20260608")

    def test_returns_none_when_nothing_local(self, tmp_path):
        result = dl.find_local_mcx_daily_margin_file(
            date=datetime(2026, 6, 8), data_dir=tmp_path
        )
        assert result is None
