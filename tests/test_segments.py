"""Tests for the SPAN segment registry (marginism.segments)."""

import pytest

from marginism.segments import (
    BFO_SUFFIXES,
    BSE_SINGLE_SUFFIXES,
    DEFAULT_SEGMENT,
    MCX_SUFFIXES,
    NSE_SUFFIXES,
    SEGMENTS,
    SpanSegment,
    get_segment,
)


class TestGetSegment:
    def test_returns_nfo_by_default(self):
        seg = get_segment("NFO")
        assert seg.code == "NFO"

    def test_case_insensitive(self):
        assert get_segment("nfo").code == "NFO"
        assert get_segment("Bfo").code == "BFO"
        assert get_segment("mcx").code == "MCX"

    def test_raises_key_error_for_unknown(self):
        with pytest.raises(KeyError, match="Unknown SPAN segment"):
            get_segment("UNKNOWN")

    def test_all_known_segments_accessible(self):
        for code in ("NFO", "CDS", "COM", "BFO", "BCD", "BCO", "MCX"):
            seg = get_segment(code)
            assert seg.code == code


class TestDefaultSegment:
    def test_default_is_nfo(self):
        assert DEFAULT_SEGMENT.code == "NFO"


class TestNfoSegment:
    def setup_method(self):
        self.seg = get_segment("NFO")

    def test_url_base(self):
        assert "nseindia.com" in self.seg.span_url_base
        assert "nsccl/span" in self.seg.span_url_base

    def test_zip_template(self):
        assert "{date}" in self.seg.span_zip_template
        assert "{suffix}" in self.seg.span_zip_template
        assert self.seg.span_zip_template == "nsccl.{date}.{suffix}.zip"

    def test_suffixes_newest_first(self):
        assert self.seg.suffixes == NSE_SUFFIXES
        assert self.seg.suffixes[0] == "s"   # settlement is newest
        assert self.seg.suffixes[-1] == "i1"  # earliest intraday

    def test_has_exposure_file(self):
        assert self.seg.exposure_url_base is not None
        assert self.seg.exposure_template is not None
        assert "ael_{date}.csv" == self.seg.exposure_template

    def test_format_zip_name(self):
        name = self.seg.span_zip_template.format(date="20250808", suffix="s")
        assert name == "nsccl.20250808.s.zip"


class TestCdsSegment:
    def setup_method(self):
        self.seg = get_segment("CDS")

    def test_url_contains_cd_span(self):
        assert "/cd/span" in self.seg.span_url_base

    def test_zip_template_prefix(self):
        assert self.seg.span_zip_template.startswith("nsccl_x.")

    def test_no_exposure_file(self):
        assert self.seg.exposure_url_base is None


class TestBfoSegment:
    def setup_method(self):
        self.seg = get_segment("BFO")

    def test_url_base_contains_bseindia(self):
        assert "bseindia.com" in self.seg.span_url_base

    def test_zip_template(self):
        assert self.seg.span_zip_template == "BSERISK{date}-{suffix}.ZIP"

    def test_suffixes_are_bfo_suffixes(self):
        assert self.seg.suffixes == BFO_SUFFIXES
        assert "04" in self.seg.suffixes
        assert "00" in self.seg.suffixes

    def test_base_snapshot_uses_different_url(self):
        # BFO "00" (base snapshot) is served from the parent directory
        assert "00" in self.seg.span_url_base_overrides
        assert "Risk_Automate" in self.seg.span_url_base_overrides["00"]
        assert "SPN" not in self.seg.span_url_base_overrides["00"]

    def test_intraday_uses_spn_subdir(self):
        # intraday suffixes should use the main URL which includes /SPN
        assert "SPN" in self.seg.span_url_base


class TestMcxSegment:
    def setup_method(self):
        self.seg = get_segment("MCX")

    def test_url_contains_mcxccl(self):
        assert "mcxccl.com" in self.seg.span_url_base

    def test_url_has_placeholders(self):
        assert "{year}" in self.seg.span_url_base
        assert "{month}" in self.seg.span_url_base

    def test_zip_template(self):
        assert self.seg.span_zip_template == "mcxrpf-{date}-{suffix}-i.zip"

    def test_suffixes_are_mcx_suffixes(self):
        assert self.seg.suffixes == MCX_SUFFIXES
        # newest-first: 2329-10 is last snapshot of the day
        assert self.seg.suffixes[0] == "2329-10"
        assert self.seg.suffixes[-1] == "0106-01"

    def test_ten_snapshots(self):
        assert len(self.seg.suffixes) == 10

    def test_has_url_override_for_some_suffixes(self):
        assert "1100-03" in self.seg.span_url_base_overrides

    def test_format_zip_name(self):
        name = self.seg.span_zip_template.format(
            date="20260608", suffix="1300-04"
        )
        assert name == "mcxrpf-20260608-1300-04-i.zip"


class TestSpanSegmentDataclass:
    def test_frozen_cannot_modify(self):
        seg = get_segment("NFO")
        with pytest.raises((AttributeError, TypeError)):
            seg.code = "XXX"  # type: ignore[misc]

    def test_all_segments_have_required_fields(self):
        for code, seg in SEGMENTS.items():
            assert isinstance(seg, SpanSegment)
            assert seg.code == code
            assert seg.span_url_base
            assert seg.span_zip_template
            assert "{date}" in seg.span_zip_template
            assert "{suffix}" in seg.span_zip_template
            assert len(seg.suffixes) >= 1
