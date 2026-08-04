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
        assert record.security_name == "Common Stock, $0.00001 par value per share"
        assert record.ticker == "AAPL"
        assert record.exchange == "NASDAQ"
        assert record.last_accessed is not None

    def test_falls_back_to_filing_company_name(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        # Document with no registrant name — should fall back to filing.company_name
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
        # RevenueFromContractWithCustomerExcludingAssessedTax has higher priority than Revenues
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
        # Filers that tag only the IncludingAssessedTax variant should not return empty.
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


# ── TestCommonStock ───────────────────────────────────────────────────────────

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


class TestCommonStock:
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
        shares, date_, security_name, ticker, exchange = extractor._common_stock(doc)
        assert shares == Decimal("1000000")
        assert date_ == datetime.date(2024, 9, 28)
        assert security_name == "Common Stock, $0.001 par value"
        assert ticker == "AAPL"
        assert exchange == "NASDAQ"

    def test_no_ticker_returns_empty_strings(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_INSTANT_CTX,
                units=_SHARES_UNIT,
                facts='<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">500000</ix:nonFraction></p>',
            )
        )
        shares, _, security_name, ticker, exchange = extractor._common_stock(doc)
        assert shares == Decimal("500000")
        assert security_name == ""
        assert ticker == ""
        assert exchange == ""

    def test_dimensioned_shares_summed_at_latest_instant(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # All share facts are per-class (dimensioned). _common_stock should sum
        # them at the latest instant rather than returning None.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">5000000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">900000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_CLASS_A_CTX + _CLASS_B_CTX, units=_SHARES_UNIT, facts=facts)
        )
        shares, date_, _, _, _ = extractor._common_stock(doc)
        assert shares == Decimal("5900000000")
        assert date_ == datetime.date(2024, 9, 28)

    def test_ticker_fallback_when_contexts_differ(self, extractor: CompanyFactsExtractor) -> None:
        # TradingSymbol and Security12bTitle in duration ctx, shares in instant ctx.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:Security12bTitle" contextRef="c-duration">Common Stock</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-duration">SONO</ix:nonNumeric></p>'
            '<p><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c-duration">Nasdaq Global Select Market</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX + _DURATION_CTX, units=_SHARES_UNIT, facts=facts)
        )
        _, _, security_name, ticker, exchange = extractor._common_stock(doc)
        assert security_name == "Common Stock"
        assert ticker == "SONO"
        assert exchange == "Nasdaq Global Select Market"

    def test_security_matched_by_dimension_member_across_contexts(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Adtalem-style: shares in one context, ticker/exchange/title in a
        # different context, but both share the same explicitMember value.
        # The match must succeed via dimension member, not context_id.
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
        shares, _, security_name, ticker, exchange = extractor._common_stock(doc)
        assert shares == Decimal("1000000")
        assert ticker == "ADTA"
        assert exchange == "NYSE"
        assert security_name == "Class A Common Stock"

    def test_none_ticker_normalized_to_empty(self, extractor: CompanyFactsExtractor) -> None:
        # Filers with no listed security sometimes write "None" as TradingSymbol.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-instant" unitRef="shares" decimals="0">1000000</ix:nonFraction></p>'
            '<p><ix:nonNumeric name="dei:TradingSymbol" contextRef="c-instant">None</ix:nonNumeric></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_INSTANT_CTX, units=_SHARES_UNIT, facts=facts)
        )
        _, _, _, ticker, _ = extractor._common_stock(doc)
        assert ticker == ""

    def test_no_shares_returns_none(self, extractor: CompanyFactsExtractor) -> None:
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(
                contexts=_DURATION_CTX,
                facts='<p><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-duration">ACME</ix:nonNumeric></p>',
            )
        )
        shares, date_, security_name, ticker, exchange = extractor._common_stock(doc)
        assert shares is None
        assert date_ is None
        assert security_name == ""
        assert ticker == ""
        assert exchange == ""


# ── TestShellCompany ──────────────────────────────────────────────────────────


class TestShellCompany:
    def test_ixt_sec_booleanfalse(self, extractor: CompanyFactsExtractor) -> None:
        # Real SEC filings use ixt-sec:booleanfalse
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
        # Some filers write "No"/"Yes" without an ixt format attribute
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
        # No dei:DocumentPeriodEndDate → MISSING_PERIOD_END; record still produced.
        # NO_REVENUE_CONCEPT must NOT also appear (revenue was never queried).
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
        # Period end present but no qualifying revenue fact.
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
        # Two revenue concepts for the same annual period with conflicting values.
        # Priority winner (Revenues = 100) is returned; AMBIGUOUS_REVENUE is flagged.
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
        assert Decimal(records[0].revenue) == Decimal(
            "200"
        )  # priority winner (Revenues excluding tax)

    def test_equal_revenue_values_across_concepts_not_ambiguous(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        # Two concepts, same value — not ambiguous.
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
        # ifrs-full:Revenue (broad IFRS total) — reported in millions, scale=6
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
        # ifrs-full:RevenueFromContractsWithCustomers has higher priority than ifrs-full:Revenue
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
        # RevenueFromContractsWithCustomers wins (higher priority in REVENUE_CONCEPTS)
        assert Decimal(records[0].revenue) == Decimal("3000000000")
        assert FailureType.AMBIGUOUS_REVENUE in failures  # values differ

    def test_non_standard_ifrs_prefix_normalized(
        self, extractor: CompanyFactsExtractor, filing_20f: Filing
    ) -> None:
        # Some filers declare the IFRS namespace with a prefix like "ifrs" instead of "ifrs-full".
        # _build_prefix_map should remap it to "ifrs-full" so REVENUE_CONCEPTS still match.
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
        # Some 20-F filers omit format= on dei:DocumentPeriodEndDate; the parser
        # returns the raw text string. _period_end must still parse it via parse_date_text.
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
        # "December 31 2021" (no comma) — some filers omit the comma in addition to format=.
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
        # us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax is now in REVENUE_CONCEPTS.
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
