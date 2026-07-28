"""Tests for xbrl.parser — InlineXbrlDocument."""

import datetime
import logging
from decimal import Decimal

import pytest

from idi_company_facts.xbrl.parser import (
    InlineXbrlDocument,
    NotInlineXbrlError,
    XbrlParseError,
)
from tests.conftest import make_ixbrl_bytes

# ── Shared minimal iXBRL building blocks ─────────────────────────────────────

_INSTANT_CTX = """
<xbrli:context id="c-instant">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
  </xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""

_DURATION_CTX = """
<xbrli:context id="c-duration">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
  </xbrli:entity>
  <xbrli:period>
    <xbrli:startDate>2023-09-30</xbrli:startDate>
    <xbrli:endDate>2024-09-28</xbrli:endDate>
  </xbrli:period>
</xbrli:context>
"""

_SEGMENTED_CTX = """
<xbrli:context id="c-segment">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
    <xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:Axis">us-gaap:Member</xbrldi:explicitMember>
    </xbrli:segment>
  </xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""

_USD_UNIT = '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
_SHARES_UNIT = '<xbrli:unit id="shares"><xbrli:measure>shares</xbrli:measure></xbrli:unit>'


# ── TestContextParsing ────────────────────────────────────────────────────────


class TestContextParsing:
    def test_instant_context(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1</ix:nonFraction></p>',
            )
        )
        ctx = doc.single_fact("dei:EntityPublicFloat").context
        assert ctx.instant == datetime.date(2024, 9, 28)
        assert ctx.start is None
        assert ctx.end is None
        assert not ctx.has_dimensions

    def test_duration_context(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        ctx = doc.single_fact("dei:EntityRegistrantName").context
        assert ctx.start == datetime.date(2023, 9, 30)
        assert ctx.end == datetime.date(2024, 9, 28)
        assert ctx.instant is None

    def test_dimensioned_context_flagged(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _SEGMENTED_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-segment" unitRef="USD" decimals="0">100</ix:nonFraction></p>',
            )
        )
        ctx = doc.single_fact("us-gaap:Revenues", dimensionless=False).context
        assert ctx.has_dimensions


# ── TestNumericHandling ───────────────────────────────────────────────────────


class TestNumericHandling:
    def test_strips_commas_and_applies_scale(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="-6" scale="6">391,035</ix:nonFraction></p>',
            )
        )
        fact = doc.single_fact("us-gaap:Revenues")
        assert fact.value == Decimal("391035000000")

    def test_sign_negation(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0" sign="-">500</ix:nonFraction></p>',
            )
        )
        fact = doc.single_fact("us-gaap:Revenues")
        assert fact.value == Decimal("-500")

    def test_currency_unit_stripped(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1000</ix:nonFraction></p>',
            )
        )
        assert doc.single_fact("dei:EntityPublicFloat").unit == "USD"

    def test_shares_unit_preserved(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_SHARES_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>',
            )
        )
        assert doc.single_fact("dei:EntityCommonStockSharesOutstanding").unit == "shares"


# ── TestNonNumericTransforms ──────────────────────────────────────────────────


class TestNonNumericTransforms:
    def test_booleanfalse_sec_registry(self) -> None:
        # Real SEC filings use ixt-sec:booleanfalse (SEC transformation registry)
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityShellCompany" contextRef="c-duration" format="ixt-sec:booleanfalse">false</ix:nonNumeric></p>',
            )
        )
        assert doc.single_fact("dei:EntityShellCompany").value is False

    def test_booleantrue_sec_registry(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityShellCompany" contextRef="c-duration" format="ixt-sec:booleantrue">true</ix:nonNumeric></p>',
            )
        )
        assert doc.single_fact("dei:EntityShellCompany").value is True

    def test_date_tr4_format_parsed(self) -> None:
        # TR4 (2020) hyphenated date format — ixt bound to TR4 URI by default
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:date-monthname-day-year-en">September 28, 2024</ix:nonNumeric></p>',
            )
        )
        assert doc.single_fact("dei:DocumentPeriodEndDate").value == datetime.date(2024, 9, 28)

    def test_date_tr3_monthdayyearen_parsed(self) -> None:
        # TR3 (2015) non-hyphenated date format — ixt bound to TR3 URI
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:datemonthdayyearen">September 28, 2024</ix:nonNumeric></p>',
                ixt_ns="http://www.xbrl.org/inlineXBRL/transformation/2015-02-26",
            )
        )
        assert doc.single_fact("dei:DocumentPeriodEndDate").value == datetime.date(2024, 9, 28)

    def test_date_tr3_dateyearmonthday_parsed(self) -> None:
        # TR3 ISO date format
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:dateyearmonthday">2024-09-28</ix:nonNumeric></p>',
                ixt_ns="http://www.xbrl.org/inlineXBRL/transformation/2015-02-26",
            )
        )
        assert doc.single_fact("dei:DocumentPeriodEndDate").value == datetime.date(2024, 9, 28)

    def test_plain_text_returned_as_string(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">APPLE INC</ix:nonNumeric></p>',
            )
        )
        assert doc.single_fact("dei:EntityRegistrantName").value == "APPLE INC"


# ── TestNamespaceNormalization ────────────────────────────────────────────────


class TestNamespaceNormalization:
    def test_non_standard_usgaap_prefix(self) -> None:
        # Filer declares 'gaap' instead of 'us-gaap'
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                units=_USD_UNIT,
                usgaap_prefix="gaap",
                facts='<p><ix:nonFraction name="gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0">999</ix:nonFraction></p>',
            )
        )
        assert doc.facts("us-gaap:Revenues"), "should normalize 'gaap' prefix to 'us-gaap'"

    def test_non_standard_dei_prefix(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                dei_prefix="d",
                facts='<p><ix:nonNumeric name="d:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        assert doc.facts("dei:EntityRegistrantName"), "should normalize 'd' prefix to 'dei'"


# ── TestParseErrors ───────────────────────────────────────────────────────────


class TestParseErrors:
    def test_empty_bytes_raises(self) -> None:
        with pytest.raises(XbrlParseError):
            InlineXbrlDocument(b"")

    def test_garbage_bytes_raises(self) -> None:
        with pytest.raises(XbrlParseError):
            InlineXbrlDocument(b"\x00\xff\xfe garbage not xml")

    def test_html_without_ix_tags_raises_not_inline(self) -> None:
        html = b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body><p>hello</p></body></html>"
        with pytest.raises(NotInlineXbrlError):
            InlineXbrlDocument(html)

    def test_not_inline_xbrl_is_xbrl_parse_error(self) -> None:
        html = b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body/></html>"
        with pytest.raises(XbrlParseError):
            InlineXbrlDocument(html)

    def test_sgml_preamble_raises_not_inline_not_malformed(self) -> None:
        # Pre-inline EDGAR filings often have a plain-text SGML header before
        # the HTML root.  The recovery parser returns None for these, which
        # previously produced MALFORMED_XBRL.  The byte scan must catch them
        # first and raise NotInlineXbrlError instead.
        sgml_doc = b"ACCESSION NUMBER: 0001234567-20-000001\n<html><body><p>Annual report</p></body></html>"
        with pytest.raises(NotInlineXbrlError):
            InlineXbrlDocument(sgml_doc)

    def test_plain_text_doc_raises_not_inline(self) -> None:
        with pytest.raises(NotInlineXbrlError):
            InlineXbrlDocument(b"This is a plain text 10-K filing with no XBRL.")


# ── TestSingleFact ────────────────────────────────────────────────────────────


class TestSingleFact:
    def test_returns_none_for_unknown_concept(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">X</ix:nonNumeric></p>',
            )
        )
        assert doc.single_fact("us-gaap:Revenues") is None

    def test_dimensionless_filter_excludes_dimensioned(self) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _SEGMENTED_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-segment" unitRef="USD" decimals="0">999</ix:nonFraction></p>',
            )
        )
        assert doc.single_fact("dei:EntityPublicFloat", dimensionless=True) is None
        assert doc.single_fact("dei:EntityPublicFloat", dimensionless=False) is not None

    def test_returns_first_of_multiple_facts(self) -> None:
        # Two duration contexts — single_fact should return the first parsed
        two_ctxs = (
            _DURATION_CTX
            + """
        <xbrli:context id="c-other">
          <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2022-10-01</xbrli:startDate>
            <xbrli:endDate>2023-09-30</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        )
        facts = (
            '<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">FIRST</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-other">SECOND</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(make_ixbrl_bytes(contexts=two_ctxs, facts=facts))
        assert doc.single_fact("dei:EntityRegistrantName").value == "FIRST"


# ── TestSingleTraversal ───────────────────────────────────────────────────────


class TestSingleTraversal:
    """Item 1: _parse_all_facts returns saw_ix before filtering; semantics check."""

    def test_ix_element_with_unresolvable_context_does_not_raise(self) -> None:
        # ix:nonFraction references "c-missing" which is not declared — fact is
        # dropped by the context-resolution filter, but saw_ix is still True
        # because the element was encountered before the filter ran.
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts=(
                    # dropped fact (unresolvable context)
                    '<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-missing" unitRef="USD" decimals="0">1</ix:nonFraction></p>'
                    # valid fact — ensures the document produces at least one parseable fact
                    '<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">2</ix:nonFraction></p>'
                ),
            )
        )
        # No NotInlineXbrlError despite the dropped fact
        assert doc.single_fact("dei:EntityPublicFloat") is not None

    def test_all_ix_elements_dropped_still_not_raises(self) -> None:
        # Every ix: element references an unresolvable context — saw_ix is True
        # because elements were encountered, even though _fact_cache is empty.
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-missing" unitRef="USD" decimals="0">1</ix:nonFraction></p>',
            )
        )
        # saw_ix was True — NotInlineXbrlError must NOT be raised
        assert doc.facts("dei:EntityPublicFloat") == []

    def test_zero_ix_elements_raises_not_inline(self) -> None:
        # No ix: elements at all → saw_ix stays False → NotInlineXbrlError
        html = b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body><p>hello</p></body></html>"
        with pytest.raises(NotInlineXbrlError):
            InlineXbrlDocument(html)


# ── TestHtmlEntitiesRecovery ──────────────────────────────────────────────────


def _make_nbsp_ixbrl_bytes() -> bytes:
    """IXBRL document containing &nbsp; in body text, which breaks strict XML parsing."""
    base = make_ixbrl_bytes(
        contexts=_INSTANT_CTX,
        units=_USD_UNIT,
        facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1000</ix:nonFraction></p>',
    )
    # Inject bare HTML entity (invalid in strict XML without a DTD)
    return base.replace(b"</body>", b"<p>text&nbsp;with&nbsp;entities</p></body>")


class TestHtmlEntitiesRecovery:
    """Item 8: strict XML parse failure triggers lxml recovery parser."""

    def test_nbsp_document_parses_via_recovery(self) -> None:
        doc = InlineXbrlDocument(_make_nbsp_ixbrl_bytes())
        assert doc.used_recovery_parser is True
        # Numeric fact is unaffected by entity removal
        assert doc.single_fact("dei:EntityPublicFloat").value == Decimal("1000")

    def test_recovery_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            InlineXbrlDocument(_make_nbsp_ixbrl_bytes())
        assert any("recovered" in msg.lower() for msg in caplog.messages)

    def test_garbage_bytes_still_raises_xbrl_parse_error(self) -> None:
        with pytest.raises(XbrlParseError):
            InlineXbrlDocument(b"\x00\xff\xfe garbage not xml")

    def test_well_formed_document_does_not_use_recovery(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            doc = InlineXbrlDocument(
                make_ixbrl_bytes(
                    contexts=_INSTANT_CTX,
                    units=_USD_UNIT,
                    facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1</ix:nonFraction></p>',
                )
            )
        assert doc.used_recovery_parser is False
        assert not any("recovered" in msg.lower() for msg in caplog.messages)
