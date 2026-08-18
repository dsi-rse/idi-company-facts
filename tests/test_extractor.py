"""Tests for extractor.CompanyFactsExtractor — uses sample_10k.htm fixture."""

import datetime
from decimal import Decimal

import pytest

from idi_company_facts.extractor import CompanyFactsExtractor, _classify_security
from idi_company_facts.failures import FailureType
from idi_company_facts.types import Filing, SecurityType
from idi_company_facts.xbrl.parser import InlineXbrlDocument
from tests.conftest import load_fixture, make_ixbrl_bytes

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
        primary = record.registered_securities[0]
        assert primary.security_name == "Common Stock, $0.00001 par value per share"
        assert primary.ticker == "AAPL"
        assert primary.exchange == "NASDAQ"
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
    def test_revenues_preferred_over_contract_concept(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Revenues is first in priority order — wins over RevenueFromContract
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
        assert revenue == Decimal("100")

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


# ── TestSharesAndSecurities ───────────────────────────────────────────────────

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

    def test_dimensioned_shares_summed_at_latest_instant(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # All share facts are per-class (dimensioned); shares should be summed
        # them at the latest instant rather than returning None.
        facts = (
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-a" unitRef="shares" decimals="0">5000000000</ix:nonFraction></p>'
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-class-b" unitRef="shares" decimals="0">900000000</ix:nonFraction></p>'
        )
        doc = InlineXbrlDocument(
            make_ixbrl_bytes(contexts=_CLASS_A_CTX + _CLASS_B_CTX, units=_SHARES_UNIT, facts=facts)
        )
        shares, date_, _ = extractor._shares_and_securities(doc)
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
        _, _, securities = extractor._shares_and_securities(doc)
        assert len(securities) == 1
        assert securities[0].security_name == "Common Stock"
        assert securities[0].ticker == "SONO"
        assert securities[0].exchange == "Nasdaq Global Select Market"

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
        shares, _, securities = extractor._shares_and_securities(doc)
        assert shares == Decimal("1000000")
        assert securities[0].ticker == "ADTA"
        assert securities[0].exchange == "NYSE"
        assert securities[0].security_name == "Class A Common Stock"

    def test_none_ticker_normalized_to_empty(self, extractor: CompanyFactsExtractor) -> None:
        # Filers with no listed security sometimes write "None" as TradingSymbol.
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


# ── TestRegisteredSecurities ──────────────────────────────────────────────────


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

    def test_ordinary_shares_are_shares_and_securities(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Dimensionless shares outstanding → dimensionless ORD is the common-stock class.
        # ADS (dimensional) is retained as a second security.
        shares, _, securities = extractor._shares_and_securities(self._ads_doc())
        assert shares == Decimal(2000000000)
        assert len(securities) == 2
        assert securities[0].ticker == "ORD"
        assert securities[0].exchange == "Euronext Paris"
        assert securities[0].security_type == SecurityType.COMMON
        # ADS retained; US-exchange listing sorts before home-country in remainder.
        assert securities[1].ticker == "ADSX"
        assert securities[1].exchange == "NYSE"
        assert securities[1].security_type == SecurityType.ADS

    def test_anchor_context_determines_primary_class(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # Dual-class doc where shares outstanding is dimensioned to Class B:
        # Class B should sort first regardless of document order.
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
        assert [s.ticker for s in securities] == ["DUAL.B", "DUAL.A"]
        assert all(s.security_type == SecurityType.COMMON for s in securities)

    def test_equity_ranks_before_registered_notes(self, extractor: CompanyFactsExtractor) -> None:
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
        # Ordinary shares are common stock (first), ADS (equity) before notes.
        assert securities[0].ticker == "ORD"
        assert securities[0].security_type == SecurityType.COMMON
        assert securities[1].ticker == "ADSX"
        assert securities[1].security_type == SecurityType.ADS
        assert len(securities) == 3
        assert securities[2].security_type == SecurityType.DEBT

    def test_duplicate_dimensional_and_dimensionless_kept_separate(
        self, extractor: CompanyFactsExtractor
    ) -> None:
        # A filer that tags the ticker both dimensionlessly and in a ClassOfStock
        # context produces two distinct entries — one per (ticker, members) key.
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
        # Foreign issuer with both ordinary shares and ADS sharing the same
        # trading symbol — they must NOT be merged into one security.
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
        types = {s.security_type for s in securities}
        assert types == {SecurityType.COMMON, SecurityType.ADS}

    def test_extract_multiple_securities_does_not_fail(
        self, extractor: CompanyFactsExtractor, sample_filing: Filing
    ) -> None:
        records, failures = extractor.extract(sample_filing, self._ads_doc())
        # Multiple securities is expected structure, not a failure.
        assert not any("multiple" in str(f).lower() for f in failures)
        record = records[0]
        # Ordinary shares are the common-stock class → sort first.
        primary = record.registered_securities[0]
        assert primary.ticker == "ORD"
        assert primary.exchange == "Euronext Paris"
        assert primary.security_type == SecurityType.COMMON
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
        assert records[0].registered_securities[0].security_type == SecurityType.COMMON

    def test_common_type_beats_sloppy_anchor_ads(self, extractor: CompanyFactsExtractor) -> None:
        # Sloppy filer tags EntityCommonStockSharesOutstanding in the ADS context.
        # Before this fix the ADS would win the flat columns via anchor matching;
        # now SecurityType.COMMON always leads regardless of anchor placement.
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
            # Shares outstanding sloppy-tagged in the ADS dimensional context.
            '<p><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c-ads-shares"'
            ' unitRef="shares" decimals="0">500000000</ix:nonFraction></p>'
            + _ORDINARY_FACTS.replace(
                # Drop the shares fact from _ORDINARY_FACTS (already provided above).
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
        _, _, securities = extractor._shares_and_securities(doc)
        assert securities[0].security_type == SecurityType.COMMON
        assert securities[0].ticker == "ORD"
        assert securities[1].security_type == SecurityType.ADS
        assert securities[1].ticker == "ADSX"

    def test_dedupe_member_type_beats_title_type(self, extractor: CompanyFactsExtractor) -> None:
        # Same ticker tagged dimensionlessly (title "Common Stock" → COMMON)
        # and dimensionally with AmericanDepositarySharesMember (→ ADS).
        # Each (ticker, members) key is distinct, so two entries are produced;
        # the ADS one ranks first because COMMON outranks ADS in _TYPE_ORDER
        # only when the COMMON entry is the anchor — here it is not.
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
        types = {s.security_type for s in securities}
        assert SecurityType.ADS in types
        assert SecurityType.COMMON in types


# ── TestSecurityClassifier ────────────────────────────────────────────────────


class TestSecurityClassifier:
    """Unit tests for _classify_security — member-first, title-fallback."""

    def test_member_beats_title(self) -> None:
        # AmericanDepositarySharesMember → ADS even though title says "Ordinary Shares".
        members = frozenset({"us-gaap:AmericanDepositarySharesMember"})
        assert _classify_security(members, "Ordinary Shares represented hereby") == SecurityType.ADS

    def test_title_fallback_ads(self) -> None:
        # ADS title mentions "ordinary shares" — ADS pattern must fire before COMMON.
        result = _classify_security(
            frozenset(),
            "American Depositary Shares, each representing eight Ordinary Shares",
        )
        assert result == SecurityType.ADS

    def test_title_fallback_ads_standalone(self) -> None:
        assert _classify_security(frozenset(), "ADS") == SecurityType.ADS

    def test_title_fallback_common_class_a(self) -> None:
        assert _classify_security(frozenset(), "Class A Common Stock") == SecurityType.COMMON

    def test_title_fallback_ordinary_shares(self) -> None:
        assert (
            _classify_security(frozenset(), "Ordinary Shares, nominal value €0.01")
            == SecurityType.COMMON
        )

    def test_title_fallback_debt(self) -> None:
        assert _classify_security(frozenset(), "0.875% Senior Notes due 2027") == SecurityType.DEBT

    def test_title_fallback_preferred_beats_common(self) -> None:
        # "Preferred Stock" contains "Stock" — PREFERRED must fire before COMMON.
        assert (
            _classify_security(frozenset(), "Preferred Stock, Series A") == SecurityType.PREFERRED
        )

    def test_title_fallback_warrant_beats_common(self) -> None:
        # "Warrants to purchase Common Stock" — WARRANT must fire before COMMON.
        assert (
            _classify_security(frozenset(), "Warrants to purchase Common Stock")
            == SecurityType.WARRANT
        )

    def test_word_boundary_community_is_not_warrant(self) -> None:
        # "community" contains "unit" as a substring but not at a word boundary.
        result = _classify_security(frozenset(), "Community Choice Bancorp Common Stock")
        assert result == SecurityType.COMMON

    def test_word_boundary_notes_in_compound_is_not_debt(self) -> None:
        # "noteworthy" should not trigger DEBT.
        result = _classify_security(frozenset(), "Noteworthy Holdings Common Stock")
        assert result == SecurityType.COMMON

    def test_member_ordinary_shares_is_common(self) -> None:
        members = frozenset({"us-gaap:OrdinarySharesMember"})
        assert _classify_security(members, "") == SecurityType.COMMON

    def test_member_senior_notes_is_debt(self) -> None:
        members = frozenset({"us-gaap:SeniorNotesMember"})
        assert _classify_security(members, "") == SecurityType.DEBT

    def test_member_word_boundary_ads_not_in_crossroads(self) -> None:
        # "crossroads" contains the substring "ads" but it is not a whole word
        # after camelCase splitting; the member must not classify as ADS.
        members = frozenset({"us-gaap:CrossroadsSystemsMember"})
        result = _classify_security(members, "")
        assert result != SecurityType.ADS

    def test_empty_both_is_other(self) -> None:
        assert _classify_security(frozenset(), "") == SecurityType.OTHER


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
        assert Decimal(records[0].revenue) == Decimal("100")  # priority winner (Revenues)

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
