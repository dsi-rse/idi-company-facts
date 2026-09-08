"""Tests for extractor.CompanyFactsExtractor — uses sample_10k.htm fixture."""

import datetime
from decimal import Decimal

import pytest

from idi_company_facts.extractor import CompanyFactsExtractor
from idi_company_facts.failures import FailureType
from idi_company_facts.types import Filing
from idi_company_facts.xbrl.parser import InlineXbrlDocument
from tests.conftest import load_fixture, make_ifrs_ixbrl_bytes, make_ixbrl_bytes

# ── Helpers ───────────────────────────────────────────────────────────────────

_INSTANT_CTX = """
<xbrli:context id="c-instant">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""
_DURATION_CTX = """
<xbrli:context id="c-duration">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
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
  <xbrli:period>
    <xbrli:startDate>2023-09-30</xbrli:startDate>
    <xbrli:endDate>2024-09-28</xbrli:endDate>
  </xbrli:period>
</xbrli:context>
"""
_PRIOR_YEAR_CTX = """
<xbrli:context id="c-prior">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
  <xbrli:period>
    <xbrli:startDate>2022-10-01</xbrli:startDate>
    <xbrli:endDate>2023-09-30</xbrli:endDate>
  </xbrli:period>
</xbrli:context>
"""
_USD_UNIT = '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
_SHARES_UNIT = '<xbrli:unit id="shares"><xbrli:measure>shares</xbrli:measure></xbrli:unit>'

_CLASS_A_CTX = """
<xbrli:context id="c-class-a">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
    <xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
    </xbrli:segment>
  </xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""
_CLASS_B_CTX = """
<xbrli:context id="c-class-b">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
    <xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassBMember</xbrldi:explicitMember>
    </xbrli:segment>
  </xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""

_ADS_CTX = """
<xbrli:context id="c-ads">
  <xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
    <xbrli:segment>
      <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:AmericanDepositarySharesMember</xbrldi:explicitMember>
    </xbrli:segment>
  </xbrli:entity>
  <xbrli:period>
    <xbrli:startDate>2023-09-30</xbrli:startDate>
    <xbrli:endDate>2024-09-28</xbrli:endDate>
  </xbrli:period>
</xbrli:context>"""

_ORDINARY_FACTS = (
    '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">2000000000</ix:nonFraction></p>'
    '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-duration">Ordinary Shares, nominal value €0.01</ix:nonNumeric></p>'
    '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">ORD</ix:nonNumeric></p>'
    '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-duration">Euronext Paris</ix:nonNumeric></p>'
)
_ADS_FACTS = (
    '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-ads">American Depositary Shares</ix:nonNumeric></p>'
    '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-ads">ADSX</ix:nonNumeric></p>'
    '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-ads">NYSE</ix:nonNumeric></p>'
)


@pytest.fixture
def extractor() -> CompanyFactsExtractor:
    return CompanyFactsExtractor()


@pytest.fixture
def fixture_doc() -> InlineXbrlDocument:
    """Full sample_10k.htm fixture parsed into an InlineXbrlDocument."""
    return InlineXbrlDocument(load_fixture("sample_10k.htm"))


# ── TestExtract (full integration via fixture) ────────────────────────────────


class TestExtract:
    def test_extracts_record_from_fixture(
        self,
        extractor: CompanyFactsExtractor,
        fixture_doc: InlineXbrlDocument,
        sample_filing: Filing,
    ) -> None:
        records, _ = extractor.extract(sample_filing, fixture_doc)
        assert len(records) == 1
        record = records[0]
        assert record.company_cik == "0000320193"
        assert record.company_name == "APPLE INC"
        assert record.report_date == datetime.date(2024, 9, 28)
        assert Decimal(record.revenue) == Decimal("391035000000")
        assert record.revenue_currency == "USD"
        assert record.is_shell_company == "false"
        primary = record.registered_securities[0]
        assert primary.security_name == "Common Stock, $0.00001 par value per share"
        assert primary.ticker == "AAPL"
        assert primary.exchange == "NASDAQ"
        assert record.last_accessed is not None

    def test_falls_back_to_filing_company_name(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1</ix:nonFraction></p>',
            )
        )
        records, _ = extractor.extract(sample_filing, doc)
        assert records[0].company_name == sample_filing.company_name


# ── TestRevenue ───────────────────────────────────────────────────────────────


class TestRevenue:
    def test_contract_concept_preferred_over_revenues(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        facts = (
            '<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0">100</ix:nonFraction></p>'
            '<p><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-duration" unitRef="USD" decimals="0">200</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                units=_USD_UNIT,
                facts=facts,
            )
        )
        period_end = datetime.date(2024, 9, 28)
        revenue, _, _, _ = extractor._revenue(doc, period_end)
        assert revenue == Decimal("200")

    def test_including_assessed_tax_concept_extracted(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        facts = '<p><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax" contextRef="c-duration" unitRef="USD" decimals="0">555</ix:nonFraction></p>'
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_DURATION_CTX, units=_USD_UNIT, facts=facts)
        )
        revenue, _, _, _ = extractor._revenue(doc, datetime.date(2024, 9, 28))
        assert revenue == Decimal("555")

    def test_excludes_dimensioned_context(self, extractor: CompanyFactsExtractor) -> None:
        facts = '<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-segment" unitRef="USD" decimals="0">999</ix:nonFraction></p>'
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX + _SEGMENTED_CTX,
                units=_USD_UNIT,
                facts=facts,
            )
        )
        revenue, _, _, _ = extractor._revenue(doc, datetime.date(2024, 9, 28))
        assert revenue is None

    def test_excludes_prior_year(self, extractor: CompanyFactsExtractor) -> None:
        facts = '<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-prior" unitRef="USD" decimals="0">500</ix:nonFraction></p>'
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_PRIOR_YEAR_CTX,
                units=_USD_UNIT,
                facts=facts,
            )
        )
        revenue, _, _, _ = extractor._revenue(doc, datetime.date(2024, 9, 28))
        assert revenue is None

    def test_returns_none_when_period_end_absent(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0">100</ix:nonFraction></p>',
            )
        )
        revenue, _, _, _ = extractor._revenue(doc, period_end=None)
        assert revenue is None


# ── TestMarketValue ───────────────────────────────────────────────────────────


class TestMarketValue:
    def test_extracts_public_float(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">5000000000</ix:nonFraction></p>',
            )
        )
        value, date_, currency = extractor._market_value(doc)
        assert value == Decimal("5000000000")
        assert date_ == datetime.date(2024, 9, 28)
        assert currency == "USD"

    def test_returns_none_when_absent(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        assert extractor._market_value(doc) == (None, None, None)


# ── TestSharesAndSecurities ───────────────────────────────────────────────────


class TestSharesAndSecurities:
    def test_extracts_shares_name_ticker_and_exchange(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-instant">Common Stock, $0.001 par value</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">AAPL</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-instant">NASDAQ</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX, units=_SHARES_UNIT, facts=facts)
        )
        shares, date_, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("1000000")
        assert date_ == datetime.date(2024, 9, 28)
        assert len(securities) == 1
        assert securities[0].security_name == "Common Stock, $0.001 par value"
        assert securities[0].ticker == "AAPL"
        assert securities[0].exchange == "NASDAQ"
        # Dimensionless context: dimensioned_members is empty; shares attributed to scalar only.
        assert securities[0].dimensioned_members == ""
        assert securities[0].shares_outstanding == ""

    def test_no_ticker_returns_empty_strings(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_SHARES_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">500000</ix:nonFraction></p>',
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("500000")
        assert securities == []

    def test_dimensioned_only_scalar_empty_shares_attributed(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # No dimensionless shares fact → scalar is None.
        # Dimensioned facts per class → attributed to appended rows (no 12b securities).
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">5000000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">900000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_CLASS_A_CTX + _CLASS_B_CTX, units=_SHARES_UNIT, facts=facts)
        )
        shares, date_, securities = extractor._shares_and_securities(doc)
        assert shares is None
        assert date_ is None
        # Both counts are unmatched (no 12b securities) → two appended rows.
        assert len(securities) == 2
        dim_members = {s.dimensioned_members for s in securities}
        assert "us-gaap:CommonClassAMember" in dim_members
        assert "us-gaap:CommonClassBMember" in dim_members
        for s in securities:
            assert s.shares_outstanding != ""

    def test_ticker_fallback_when_contexts_differ(self, extractor: CompanyFactsExtractor) -> None:
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-duration">Common Stock</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">SONO</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-duration">Nasdaq Global Select Market</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX + _DURATION_CTX, units=_SHARES_UNIT, facts=facts)
        )
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 1
        assert securities[0].security_name == "Common Stock"
        assert securities[0].ticker == "SONO"
        assert securities[0].exchange == "Nasdaq Global Select Market"

    def test_dimensioned_shares_attributed_via_dimensioned_members(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Shares outstanding in dimensional class-A context; DEI facts in a
        # separate class-A context (different ctx_id, same member).
        # The attribution must match by dimensioned_members equality, not ctx_id.
        class_a_shares_ctx = """
        <xbrli:context id="c-shares-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        class_a_dei_ctx = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-shares-a" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">ADTA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-dei-a">NYSE</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-dei-a">Class A Common Stock</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=class_a_shares_ctx + class_a_dei_ctx,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        # Dimensioned-only fact → scalar is None.
        assert shares is None
        assert len(securities) == 1
        assert securities[0].ticker == "ADTA"
        assert securities[0].exchange == "NYSE"
        assert securities[0].security_name == "Class A Common Stock"
        assert securities[0].dimensioned_members == "us-gaap:CommonClassAMember"
        # Shares attributed to the matched security.
        assert securities[0].shares_outstanding == "1000000"
        assert securities[0].shares_outstanding_as_of == datetime.date(2024, 9, 28)

    def test_none_ticker_normalized_to_empty(self, extractor: CompanyFactsExtractor) -> None:
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">None</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX, units=_SHARES_UNIT, facts=facts)
        )
        _, _, securities = extractor._shares_and_securities(doc)
        assert securities == []

    def test_no_shares_returns_none(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        shares, date_, securities = extractor._shares_and_securities(doc)
        assert shares is None
        assert date_ is None
        assert securities == []

    def test_multiple_dimensionless_counts_latest_wins(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        earlier_ctx = """
        <xbrli:context id="c-earlier">
          <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:instant>2024-03-31</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-earlier" unitRef="shares" decimals="0">111111</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">999999</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX + earlier_ctx, units=_SHARES_UNIT, facts=facts)
        )
        shares, date_, _ = extractor._shares_and_securities(doc)
        assert shares == Decimal("999999")
        assert date_ == datetime.date(2024, 9, 28)


# ── TestAttributionCases ──────────────────────────────────────────────────────


class TestAttributionCases:
    """Unit tests for the share-count attribution logic (plan table cases)."""

    def test_single_class_dimensionless_count(self, extractor: CompanyFactsExtractor) -> None:
        # 1 dimensionless security, 1 dimensionless count → scalar=count; share slot empty.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">AAPL</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-instant">NASDAQ</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX, units=_SHARES_UNIT, facts=facts)
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("1000000")
        assert len(securities) == 1
        assert securities[0].shares_outstanding == ""
        assert securities[0].shares_outstanding_as_of is None

    def test_fully_dimensioned_multi_class_alphabet(self, extractor: CompanyFactsExtractor) -> None:
        # 2 registered dimensional securities + 1 extra class with count.
        # Expected: 2 filled slots + 1 appended member-only row; scalar EMPTY.
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        class_b_dei = class_a_dei.replace("c-dei-a", "c-dei-b").replace(
            "CommonClassAMember", "CommonClassBMember"
        )
        class_c_shares = """
        <xbrli:context id="c-class-c">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassCMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        class_ab_shares = _CLASS_A_CTX + _CLASS_B_CTX
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">GOOGL</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-dei-a">NASDAQ</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-b">GOOG</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-dei-b">NASDAQ</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">5000000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">900000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-c" unitRef="shares" decimals="0">50000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=class_a_dei + class_b_dei + class_ab_shares + class_c_shares,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        # No dimensionless count → scalar empty.
        assert shares is None
        # 2 matched + 1 appended.
        assert len(securities) == 3
        googl = next(s for s in securities if s.ticker == "GOOGL")
        goog = next(s for s in securities if s.ticker == "GOOG")
        appended = next(s for s in securities if not s.ticker)
        assert googl.shares_outstanding == "5000000000"
        assert goog.shares_outstanding == "900000000"
        assert appended.dimensioned_members == "us-gaap:CommonClassCMember"
        assert appended.shares_outstanding == "50000000"

    def test_snail_shape(self, extractor: CompanyFactsExtractor) -> None:
        # 1 dimensionless security (ticker SNAL); 2 dimensioned counts with no
        # matching security → 2 appended rows; scalar EMPTY; unjoined logged.
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">SNAL</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-instant">NYSE</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">9032061</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">28748580</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _CLASS_A_CTX + _CLASS_B_CTX,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        # Original security has ticker but no shares (dimensionless, no dim match).
        snal = next(s for s in securities if s.ticker == "SNAL")
        assert snal.shares_outstanding == ""
        # 2 appended rows for the dimensioned counts.
        appended = [s for s in securities if not s.ticker]
        assert len(appended) == 2
        assert len(securities) == 3

    def test_dimensionless_total_multi_class_table(self, extractor: CompanyFactsExtractor) -> None:
        # 2 registered dimensional securities + 1 dimensionless count.
        # Both share slots empty; scalar=count.
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        class_b_dei = class_a_dei.replace("c-dei-a", "c-dei-b").replace(
            "CommonClassAMember", "CommonClassBMember"
        )
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">CLA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-b">CLB</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">5000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + class_a_dei + class_b_dei,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("5000000")
        assert len(securities) == 2
        for s in securities:
            assert s.shares_outstanding == ""

    def test_both_tagged(self, extractor: CompanyFactsExtractor) -> None:
        # 2 dimensional securities with matching counts + dimensionless total.
        # Slots filled per class; scalar=dimensionless value.
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        class_b_dei = class_a_dei.replace("c-dei-a", "c-dei-b").replace(
            "CommonClassAMember", "CommonClassBMember"
        )
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">CLA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-b">CLB</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">6000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">4000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">2000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _CLASS_A_CTX + _CLASS_B_CTX + class_a_dei + class_b_dei,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("6000000")
        assert len(securities) == 2
        cla = next(s for s in securities if s.ticker == "CLA")
        clb = next(s for s in securities if s.ticker == "CLB")
        assert cla.shares_outstanding == "4000000"
        assert clb.shares_outstanding == "2000000"

    def test_registered_non_stock_notes(self, extractor: CompanyFactsExtractor) -> None:
        # 1 dimensionless common + note rows, 1 dimensionless count.
        # Note rows: name/exchange filled, shares empty; scalar=count.
        notes_ctx = _ADS_CTX.replace("c-ads", "c-notes").replace(
            "AmericanDepositarySharesMember", "SeniorNotesMember"
        )
        notes_facts = (
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-notes">0.875% Senior Notes due 2027</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-notes">ORD27</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-notes">New York Stock Exchange</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _DURATION_CTX + _ADS_CTX + notes_ctx,
                units=_SHARES_UNIT,
                facts=_ORDINARY_FACTS + _ADS_FACTS + notes_facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("2000000000")
        # ORD (dimensionless, first), ADSX (dimensional), notes (dimensional).
        assert len(securities) == 3
        for s in securities:
            assert s.shares_outstanding == ""

    def test_per_class_counts_at_different_instants(self, extractor: CompanyFactsExtractor) -> None:
        # 2 classes with counts at different dates; each slot carries its own as_of.
        class_a_later = """
        <xbrli:context id="c-class-a-later">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        class_b_earlier = """
        <xbrli:context id="c-class-b-earlier">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassBMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-06-30</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        class_b_dei = class_a_dei.replace("c-dei-a", "c-dei-b").replace(
            "CommonClassAMember", "CommonClassBMember"
        )
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">CLA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-b">CLB</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a-later" unitRef="shares" decimals="0">4000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b-earlier" unitRef="shares" decimals="0">2000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=class_a_later + class_b_earlier + class_a_dei + class_b_dei,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        cla = next(s for s in securities if s.ticker == "CLA")
        clb = next(s for s in securities if s.ticker == "CLB")
        assert cla.shares_outstanding == "4000000"
        assert cla.shares_outstanding_as_of == datetime.date(2024, 9, 28)
        assert clb.shares_outstanding == "2000000"
        assert clb.shares_outstanding_as_of == datetime.date(2024, 6, 30)

    def test_ambiguous_intersection_no_match_appended(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # True member-set ambiguity: an opaque count matches multiple securities.
        opaque_ctx = """
        <xbrli:context id="c-opaque">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:LegalEntityAxis">us-gaap:SubsidiaryMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        # Opaque count (legal entity axis, no class-axis member) with no matching security.
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">TICK</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-opaque" unitRef="shares" decimals="0">99999</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + opaque_ctx,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        # Opaque count unmatched → appended row with dimensioned_members="us-gaap:SubsidiaryMember".
        appended = [s for s in securities if not s.ticker]
        assert len(appended) == 1
        assert appended[0].shares_outstanding == "99999"
        assert "SubsidiaryMember" in appended[0].dimensioned_members

    def test_class_axis_extraction_ignores_other_axes(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Context has class-axis member + second unrelated axis member.
        # dimensioned_members should only capture the StatementClassOfStockAxis member.
        ctx_two_axes = """
        <xbrli:context id="c-two-axes">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
              <xbrldi:explicitMember dimension="us-gaap:LegalEntityAxis">us-gaap:ParentMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        facts = '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-two-axes">CLA</ix:nonNumeric></p>'
        doc = InlineXbrlDocument(make_ixbrl_bytes(contexts=ctx_two_axes, facts=facts))
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 1
        assert securities[0].dimensioned_members == "us-gaap:CommonClassAMember"

    def test_typed_member_only_count_appended_with_axis_name(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # A shares count in a typed-member context cannot be attributed → appended
        # with dimensioned_members set to the typed axis QName.
        typed_ctx = """
        <xbrli:context id="c-typed">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrli:typedMember dimension="us-gaap:Axis"><us-gaap:Val>1</us-gaap:Val></xbrli:typedMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">TICK</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-typed" unitRef="shares" decimals="0">55555</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX + typed_ctx, units=_SHARES_UNIT, facts=facts)
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        appended = [s for s in securities if not s.ticker]
        assert len(appended) == 1
        assert appended[0].dimensioned_members == "us-gaap:Axis"
        assert appended[0].shares_outstanding == "55555"

    def test_opaque_non_class_axis_count_appended_sorted_members(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Shares count on a non-class-axis only, no matching security.
        # Appended row; dimensioned_members = sorted members joined "; ".
        opaque_ctx = """
        <xbrli:context id="c-opaque">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:LegalEntityAxis">us-gaap:SubsidiaryMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-opaque" unitRef="shares" decimals="0">77777</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">TICK</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX + opaque_ctx, units=_SHARES_UNIT, facts=facts)
        )
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        appended = [s for s in securities if not s.ticker]
        assert len(appended) == 1
        # dimensioned_members must not contain " | " (opaque separator is "; ").
        assert " | " not in appended[0].dimensioned_members
        assert appended[0].shares_outstanding == "77777"

    def test_alignment_invariant(self, extractor: CompanyFactsExtractor) -> None:
        # All securities lists have equal lengths regardless of mix.
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">TICK</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">CLA</ix:nonNumeric></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">100</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _CLASS_A_CTX + class_a_dei,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        # All per-security attributes must have equal count.
        fields = [
            [s.security_name for s in securities],
            [s.ticker for s in securities],
            [s.exchange for s in securities],
            [s.dimensioned_members for s in securities],
            [s.shares_outstanding for s in securities],
            [s.shares_outstanding_as_of for s in securities],
        ]
        lengths = [len(f) for f in fields]
        assert len(set(lengths)) == 1, f"alignment broken: {lengths}"


# ── TestRegisteredSecurities ──────────────────────────────────────────────────


class TestRegisteredSecurities:
    def _ads_doc(self) -> InlineXbrlDocument:
        """Build a 20-F-style doc: dimensionless ordinary shares, dimensional ADS."""
        return InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _DURATION_CTX + _ADS_CTX,
                units=_SHARES_UNIT,
                facts=_ORDINARY_FACTS + _ADS_FACTS,
            )
        )

    def test_dimensionless_before_dimensioned(self, extractor: CompanyFactsExtractor) -> None:
        # Dimensionless ORD comes before dimensional ADSX in extraction order.
        shares, _, securities = extractor._shares_and_securities(self._ads_doc())
        assert shares == Decimal(2000000000)
        assert len(securities) == 2
        assert securities[0].ticker == "ORD"
        assert securities[0].exchange == "Euronext Paris"
        assert securities[0].dimensioned_members == ""  # dimensionless
        assert securities[1].ticker == "ADSX"
        assert securities[1].exchange == "NYSE"
        assert securities[1].dimensioned_members == "us-gaap:AmericanDepositarySharesMember"

    def test_document_order_for_dimensioned(self, extractor: CompanyFactsExtractor) -> None:
        # Two dimensioned securities; document order (first seen) determines order.
        class_a_dei = """
        <xbrli:context id="c-dei-a">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:startDate>2023-09-30</xbrli:startDate><xbrli:endDate>2024-09-28</xbrli:endDate></xbrli:period>
        </xbrli:context>"""
        class_b_dei = class_a_dei.replace("c-dei-a", "c-dei-b").replace(
            "CommonClassAMember", "CommonClassBMember"
        )
        class_b_shares = """
        <xbrli:context id="c-shares-b">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassBMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        # ClassA DEI facts appear first → ClassA is first in extraction order.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-shares-b" unitRef="shares" decimals="0">1000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-a">DUAL.A</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-dei-a">Class A Common Stock</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-dei-a">NYSE</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-dei-b">DUAL.B</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-dei-b">Class B Common Stock</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-dei-b">NYSE</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=class_a_dei + class_b_dei + class_b_shares,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        # Class A DEI facts appear first in document → Class A is first.
        assert [s.ticker for s in securities] == ["DUAL.A", "DUAL.B"]
        # Class B gets its share count attributed via dimensioned_members match.
        clb = next(s for s in securities if s.ticker == "DUAL.B")
        assert clb.shares_outstanding == "1000"

    def test_equity_before_non_stock_in_document_order(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        notes_ctx = _ADS_CTX.replace("c-ads", "c-notes").replace(
            "AmericanDepositarySharesMember", "SeniorNotesMember"
        )
        notes_facts = (
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-notes">0.875% Senior Notes due 2027</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-notes">ORD27</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-notes">New York Stock Exchange</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _DURATION_CTX + _ADS_CTX + notes_ctx,
                units=_SHARES_UNIT,
                facts=_ORDINARY_FACTS + _ADS_FACTS + notes_facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        # ORD (dimensionless), ADSX (first dimensional), notes (second dimensional).
        assert securities[0].ticker == "ORD"
        assert securities[1].ticker == "ADSX"
        assert len(securities) == 3

    def test_duplicate_dimensional_and_dimensionless_kept_separate(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        dup_facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">AAPL</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-duration">NASDAQ</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-ads">AAPL</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _DURATION_CTX + _ADS_CTX,
                units=_SHARES_UNIT,
                facts=dup_facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 2
        tickers = {s.ticker for s in securities}
        assert tickers == {"AAPL"}

    def test_same_ticker_distinct_members_kept_separate(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        ord_ctx = """
        <xbrli:context id="c-ord">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:OrdinarySharesMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period>
            <xbrli:startDate>2023-09-30</xbrli:startDate>
            <xbrli:endDate>2024-09-28</xbrli:endDate>
          </xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-ord">Ordinary Shares</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-ord">BABA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-ord">NYSE</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-ads">American Depositary Shares</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-ads">BABA</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-ads">NYSE</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=ord_ctx + _ADS_CTX,
                facts=facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 2
        members = {s.dimensioned_members for s in securities}
        assert "us-gaap:OrdinarySharesMember" in members
        assert "us-gaap:AmericanDepositarySharesMember" in members

    def test_extract_multiple_securities_does_not_fail(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        records, failures = extractor.extract(sample_filing, self._ads_doc())
        assert not any("multiple" in str(f).lower() for f in failures)
        record = records[0]
        # Dimensionless ORD is first; ADSX follows.
        primary = record.registered_securities[0]
        assert primary.ticker == "ORD"
        assert primary.exchange == "Euronext Paris"
        assert len(record.registered_securities) == 2
        assert {s.ticker for s in record.registered_securities} == {"ADSX", "ORD"}

    def test_extract_single_security(
        self,
        extractor: CompanyFactsExtractor,
        fixture_doc: InlineXbrlDocument,
        sample_filing: Filing,
    ) -> None:
        records, _ = extractor.extract(sample_filing, fixture_doc)
        assert len(records[0].registered_securities) == 1
        assert records[0].registered_securities[0].ticker == "AAPL"

    def test_dimensioned_shares_in_ads_context_attributed_to_ads(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Shares outstanding in the ADS context → attributed to ADS, not ORD.
        ads_shares_ctx = """
        <xbrli:context id="c-ads-shares">
          <xbrli:entity>
            <xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier>
            <xbrli:segment>
              <xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:AmericanDepositarySharesMember</xbrldi:explicitMember>
            </xbrli:segment>
          </xbrli:entity>
          <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
        </xbrli:context>"""
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-ads-shares"'
            ' unitRef="shares" decimals="0">500000000</ix:nonFraction></p>'
            + _ORDINARY_FACTS.replace(
                '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">2000000000</ix:nonFraction></p>',
                "",
            )
            + _ADS_FACTS
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX + _ADS_CTX + ads_shares_ctx,
                units=_SHARES_UNIT,
                facts=facts,
            )
        )
        _, shares_date, securities = extractor._shares_and_securities(doc)
        # No dimensionless shares fact → scalar is None.
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares is None
        # ORD (dimensionless) appears first.
        assert securities[0].ticker == "ORD"
        assert securities[0].shares_outstanding == ""
        # ADSX gets the dimensioned count attributed.
        assert securities[1].ticker == "ADSX"
        assert securities[1].shares_outstanding == "500000000"

    def test_dedupe_keeps_dimensioned_members(self, extractor: CompanyFactsExtractor) -> None:
        # Ticker XYZ tagged dimensionlessly and dimensionally; two distinct entries.
        mixed_facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant"'
            ' unitRef="shares" decimals="0">1000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-duration">Common Stock</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">XYZ</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-ads">XYZ</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-ads">NYSE</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX + _DURATION_CTX + _ADS_CTX,
                units=_SHARES_UNIT,
                facts=mixed_facts,
            )
        )
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 2
        # Dimensionless entry has empty dimensioned_members; dimensional has ADS member.
        members = {s.dimensioned_members for s in securities}
        assert "" in members
        assert "us-gaap:AmericanDepositarySharesMember" in members


# ── TestShellCompany ──────────────────────────────────────────────────────────


class TestShellCompany:
    def test_ixt_sec_booleanfalse(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityShellCompany" contextRef="c-duration" format="ixt-sec:booleanfalse">false</ix:nonNumeric></p>',
            )
        )
        assert extractor._shell_company(doc) is False

    def test_ixt_sec_booleantrue(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityShellCompany" contextRef="c-duration" format="ixt-sec:booleantrue">true</ix:nonNumeric></p>',
            )
        )
        assert extractor._shell_company(doc) is True

    def test_plain_text_no(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityShellCompany" contextRef="c-duration">No</ix:nonNumeric></p>',
            )
        )
        assert extractor._shell_company(doc) is False

    def test_returns_none_when_absent(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        assert extractor._shell_company(doc) is None


# ── TestExtractionFailures ────────────────────────────────────────────────────


class TestExtractionFailures:
    """Items 6+7: extract() reports non-fatal XBRL failure conditions."""

    def test_missing_period_end_reported(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_USD_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1</ix:nonFraction></p>',
            )
        )
        records, failures = extractor.extract(sample_filing, doc)
        assert len(records) == 1
        assert FailureType.MISSING_PERIOD_END in failures
        assert FailureType.NO_REVENUE_CONCEPT not in failures

    def test_no_revenue_concept_reported(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:date-monthname-day-year-en">September 28, 2024</ix:nonNumeric>',
            )
        )
        records, failures = extractor.extract(sample_filing, doc)
        assert len(records) == 1
        assert FailureType.NO_REVENUE_CONCEPT in failures
        assert FailureType.MISSING_PERIOD_END not in failures

    def test_ambiguous_revenue_reported_and_priority_winner_returned(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:date-monthname-day-year-en">September 28, 2024</ix:nonNumeric>'
            '<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-duration" unitRef="USD" decimals="0">200</ix:nonFraction>'
            '<ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0">100</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_DURATION_CTX, units=_USD_UNIT, facts=facts)
        )
        records, failures = extractor.extract(sample_filing, doc)
        assert FailureType.AMBIGUOUS_REVENUE in failures
        assert Decimal(records[0].revenue) == Decimal("200")

    def test_equal_revenue_values_across_concepts_not_ambiguous(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration" format="ixt:date-monthname-day-year-en">September 28, 2024</ix:nonNumeric>'
            '<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-duration" unitRef="USD" decimals="0">300</ix:nonFraction>'
            '<ix:nonFraction name="us-gaap:Revenues" contextRef="c-duration" unitRef="USD" decimals="0">300</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_DURATION_CTX, units=_USD_UNIT, facts=facts)
        )
        _, failures = extractor.extract(sample_filing, doc)
        assert FailureType.AMBIGUOUS_REVENUE not in failures


# ── TestExtract20F ────────────────────────────────────────────────────────────

_20F_INSTANT_CTX = """
<xbrli:context id="c-instant">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009876543</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period>
</xbrli:context>
"""
_20F_DURATION_CTX = """
<xbrli:context id="c-duration">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009876543</xbrli:identifier></xbrli:entity>
  <xbrli:period>
    <xbrli:startDate>2024-01-01</xbrli:startDate>
    <xbrli:endDate>2024-12-31</xbrli:endDate>
  </xbrli:period>
</xbrli:context>
"""
_EUR_UNIT = '<xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>'


@pytest.fixture
def filing_20f() -> Filing:
    return Filing(
        cik="0009876543",
        accession_number="0009876543-25-000001",
        form_type="20-F",
        filing_date=datetime.date(2025, 4, 1),
        primary_s3_key="s3://bucket/form20f.htm",
        primary_url="https://www.sec.gov/Archives/edgar/data/9876543/000987654325000001/form20f.htm",
        company_name="ACME INTERNATIONAL PLC",
    )


class TestExtract20F:
    """Extraction from IFRS-based 20-F filings."""

    def test_extracts_ifrs_revenue_broad(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration"'
            ' format="ixt:date-monthname-day-year-en">December 31, 2024</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME INTERNATIONAL PLC</ix:nonNumeric>'
            '<ix:nonFraction name="ifrs-full:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="-6" scale="6">2500</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert len(records) == 1
        assert records[0].revenue == "2500000000"
        assert records[0].revenue_currency == "EUR"
        assert records[0].report_date == datetime.date(2024, 12, 31)
        assert records[0].form_type == "20-F"
        assert FailureType.NO_REVENUE_CONCEPT not in failures

    def test_extracts_ifrs15_revenue_concept(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration"'
            ' format="ixt:date-monthname-day-year-en">December 31, 2024</ix:nonNumeric>'
            '<ix:nonFraction name="ifrs-full:RevenueFromContractsWithCustomers" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">3000000000</ix:nonFraction>'
            '<ix:nonFraction name="ifrs-full:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">3100000000</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert len(records) == 1
        assert Decimal(records[0].revenue) == Decimal("3000000000")
        assert FailureType.AMBIGUOUS_REVENUE in failures

    def test_non_standard_ifrs_prefix_normalized(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration"'
            ' format="ixt:date-monthname-day-year-en">December 31, 2024</ix:nonNumeric>'
            '<ix:nonFraction name="ifrs:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">1800000000</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
                ifrs_prefix="ifrs",
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert len(records) == 1
        assert records[0].revenue == "1800000000"
        assert FailureType.NO_REVENUE_CONCEPT not in failures

    def test_period_end_text_date_no_format_attribute(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration">'
            "December 31, 2024"
            "</ix:nonNumeric>"
            '<ix:nonFraction name="ifrs-full:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">500000000</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert records[0].report_date == datetime.date(2024, 12, 31)
        assert records[0].revenue == "500000000"
        assert FailureType.MISSING_PERIOD_END not in failures
        assert FailureType.NO_REVENUE_CONCEPT not in failures

    def test_period_end_text_date_no_comma(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration">'
            "December 31 2024"
            "</ix:nonNumeric>"
            '<ix:nonFraction name="ifrs-full:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">300000000</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert records[0].report_date == datetime.date(2024, 12, 31)
        assert records[0].revenue == "300000000"
        assert FailureType.MISSING_PERIOD_END not in failures

    def test_revenue_including_assessed_tax(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration"'
            ' format="ixt:date-monthname-day-year-en">December 31, 2024</ix:nonNumeric>'
            '<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"'
            ' contextRef="c-duration" unitRef="USD" decimals="0">74569867</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units='<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
                facts=facts,
            )
        )
        records, failures = extractor.extract(filing_20f, doc)
        assert records[0].revenue == "74569867"
        assert records[0].revenue_currency == "USD"
        assert FailureType.NO_REVENUE_CONCEPT not in failures

    def test_20f_no_counts_all_slots_empty(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        # Securities only, no share counts → scalar empty, all share slots empty.
        facts = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-duration"'
            ' format="ixt:date-monthname-day-year-en">December 31, 2024</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">ACME</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-duration">NYSE</ix:nonNumeric>'
            '<ix:nonFraction name="ifrs-full:Revenue" contextRef="c-duration"'
            ' unitRef="EUR" decimals="0">1000000000</ix:nonFraction>'
        )
        doc = InlineXbrlDocument(
            make_ifrs_ixbrl_bytes(
                contexts=_20F_DURATION_CTX,
                units=_EUR_UNIT,
                facts=facts,
            )
        )
        records, _ = extractor.extract(filing_20f, doc)
        record = records[0]
        assert record.shares_outstanding == ""
        assert record.shares_outstanding_as_of_date is None
        for s in record.registered_securities:
            assert s.shares_outstanding == ""
