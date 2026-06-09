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


# ---------------------------------------------------------------------------
# _suffixes_for_mode
# ---------------------------------------------------------------------------


class TestSuffixesForMode:
    def test_use_first_false_returns_all_suffixes(self):
        seg = get_segment("NFO")
        result = dl._suffixes_for_mode(seg, use_first=False)
        assert result == seg.suffixes
        assert result[0] == "s"       # newest first
        assert result[-1] == "i1"     # oldest last

    def test_use_first_true_returns_only_earliest(self):
        seg = get_segment("NFO")
        result = dl._suffixes_for_mode(seg, use_first=True)
        assert result == ("i1",)

    def test_use_first_true_mcx_returns_first_rpf(self):
        seg = get_segment("MCX")
        result = dl._suffixes_for_mode(seg, use_first=True)
        assert result == ("0106-01",)

    def test_use_first_true_bfo_returns_base_snapshot(self):
        seg = get_segment("BFO")
        result = dl._suffixes_for_mode(seg, use_first=True)
        assert result == ("00",)

    def test_use_first_true_cds_returns_i1(self):
        seg = get_segment("CDS")
        result = dl._suffixes_for_mode(seg, use_first=True)
        assert result == ("i1",)

    def test_single_suffix_segment_unaffected_by_use_first(self):
        """BCD has only ('00',); use_first=True should not change anything."""
        seg = get_segment("BCD")
        # only one suffix → use_first has no effect
        assert len(seg.suffixes) == 1
        assert dl._suffixes_for_mode(seg, use_first=True) == seg.suffixes
        assert dl._suffixes_for_mode(seg, use_first=False) == seg.suffixes


# ---------------------------------------------------------------------------
# download_latest_span_file with use_first
# ---------------------------------------------------------------------------


class TestDownloadLatestSpanFileWithUseFirst:
    def test_use_first_only_tries_earliest_suffix(self, tmp_path):
        """With use_first=True only the first-of-day suffix is attempted."""
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.i01.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            called.append(url)
            if "i1.zip" in url:
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=True,
            )

        assert suffix == "i1"
        assert path is not None
        # Only ONE URL should have been tried
        assert len(called) == 1
        assert "i1.zip" in called[0]
        # Settlement file must NOT have been attempted
        assert not any("s.zip" in u for u in called)

    def test_use_first_false_tries_settlement_first(self, tmp_path):
        """Default (use_first=False): settlement 's' is the first URL tried."""
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            called.append(req.full_url)
            if called[-1].endswith(".s.zip"):
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=False,
            )

        assert suffix == "s"
        assert called[0].endswith("nsccl.20250808.s.zip")

    def test_mcx_use_first_targets_0106_01(self, tmp_path):
        seg = get_segment("MCX")
        zip_bytes = _make_zip(b"<spanFile/>", "mcxrpf-20260608-0106-01-i.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            called.append(req.full_url)
            if "0106-01" in req.full_url:
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2026, 6, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=True,
            )

        assert suffix == "0106-01"
        assert path is not None
        assert len(called) == 1

    def test_explicit_suffixes_override_use_first(self, tmp_path):
        """Explicit suffixes= takes precedence over use_first."""
        seg = get_segment("NFO")
        zip_bytes = _make_zip(b"<spanFile/>", "nsccl.20250808.i3.spn")

        def fake_urlopen(req, timeout=None):
            if "i3.zip" in req.full_url:
                return _FakeHTTPResponse(zip_bytes)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.download_latest_span_file(
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
                suffixes=("i3",),     # explicit override
                use_first=True,       # would normally give ("i1",) but overridden
            )

        assert suffix == "i3"


# ---------------------------------------------------------------------------
# find_local_span_file with use_first
# ---------------------------------------------------------------------------


class TestFindLocalSpanFileWithUseFirst:
    def test_use_first_finds_earliest_suffix(self, tmp_path):
        seg = get_segment("NFO")
        # Put both 's' and 'i1' on disk
        for sfx in ("s", "i1"):
            zb = _make_zip(b"<spanFile/>", f"nsccl.20250808.{sfx}.spn")
            (tmp_path / f"nsccl.20250808.{sfx}.zip").write_bytes(zb)

        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            use_first=True,
        )

        assert suffix == "i1"

    def test_use_first_false_finds_newest_suffix(self, tmp_path):
        seg = get_segment("NFO")
        for sfx in ("s", "i1"):
            zb = _make_zip(b"<spanFile/>", f"nsccl.20250808.{sfx}.spn")
            (tmp_path / f"nsccl.20250808.{sfx}.zip").write_bytes(zb)

        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            use_first=False,
        )

        assert suffix == "s"

    def test_use_first_returns_none_when_only_latest_present(self, tmp_path):
        """If only 's' is on disk but use_first=True, return (None, None)."""
        seg = get_segment("NFO")
        zb = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        (tmp_path / "nsccl.20250808.s.zip").write_bytes(zb)

        path, suffix = dl.find_local_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            use_first=True,
        )

        assert path is None
        assert suffix is None


# ---------------------------------------------------------------------------
# get_span_file — high-level function
# ---------------------------------------------------------------------------


class TestGetSpanFile:
    def test_returns_local_file_without_network(self, tmp_path):
        seg = get_segment("NFO")
        zb = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")
        (tmp_path / "nsccl.20250808.s.zip").write_bytes(zb)

        # No network mock needed — local file should be found
        path, suffix = dl.get_span_file(
            date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
        )

        assert path is not None
        assert suffix == "s"

    def test_downloads_when_not_local(self, tmp_path):
        seg = get_segment("NFO")
        zb = _make_zip(b"<spanFile/>", "nsccl.20250808.s.spn")

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(".s.zip"):
                return _FakeHTTPResponse(zb)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.get_span_file(
                date=datetime(2025, 8, 8), segment=seg, data_dir=tmp_path
            )

        assert path is not None
        assert suffix == "s"

    def test_download_false_returns_none_when_not_local(self, tmp_path):
        seg = get_segment("NFO")

        path, suffix = dl.get_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            download=False,
        )

        assert path is None
        assert suffix is None

    def test_use_first_true_prefers_earliest_local_file(self, tmp_path):
        seg = get_segment("NFO")
        for sfx in ("s", "i1"):
            zb = _make_zip(b"<spanFile/>", f"nsccl.20250808.{sfx}.spn")
            (tmp_path / f"nsccl.20250808.{sfx}.zip").write_bytes(zb)

        path, suffix = dl.get_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            use_first=True,
        )

        assert suffix == "i1"

    def test_use_first_false_prefers_latest_local_file(self, tmp_path):
        seg = get_segment("NFO")
        for sfx in ("s", "i1"):
            zb = _make_zip(b"<spanFile/>", f"nsccl.20250808.{sfx}.spn")
            (tmp_path / f"nsccl.20250808.{sfx}.zip").write_bytes(zb)

        path, suffix = dl.get_span_file(
            date=datetime(2025, 8, 8),
            segment=seg,
            data_dir=tmp_path,
            use_first=False,
        )

        assert suffix == "s"

    def test_use_first_downloads_i1_when_not_local(self, tmp_path):
        seg = get_segment("NFO")
        zb = _make_zip(b"<spanFile/>", "nsccl.20250808.i01.spn")
        called = []

        def fake_urlopen(req, timeout=None):
            called.append(req.full_url)
            if "i1.zip" in req.full_url:
                return _FakeHTTPResponse(zb)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.get_span_file(
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=True,
            )

        assert suffix == "i1"
        assert len(called) == 1, "Only the first-of-day suffix should be tried"
        assert "i1.zip" in called[0]

    def test_mcx_use_first_gets_first_rpf(self, tmp_path):
        seg = get_segment("MCX")
        zb = _make_zip(b"<spanFile/>", "mcxrpf-20260608-0106-01-i.spn")

        def fake_urlopen(req, timeout=None):
            if "0106-01" in req.full_url:
                return _FakeHTTPResponse(zb)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.get_span_file(
                date=datetime(2026, 6, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=True,
            )

        assert suffix == "0106-01"
        assert path is not None

    def test_bfo_use_first_gets_base_snapshot(self, tmp_path):
        seg = get_segment("BFO")
        zb = _make_zip(b"<spanFile/>", "BSERISK20250808-00.spn")

        def fake_urlopen(req, timeout=None):
            if "BSERISK20250808-00" in req.full_url:
                return _FakeHTTPResponse(zb)
            raise Exception("not available")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            path, suffix = dl.get_span_file(
                date=datetime(2025, 8, 8),
                segment=seg,
                data_dir=tmp_path,
                use_first=True,
            )

        assert suffix == "00"
