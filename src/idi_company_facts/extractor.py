"""Maps (Filing, InlineXbrlDocument) pairs to CompanyFactsRecord instances."""

import datetime
from decimal import Decimal

from idi_company_facts.types import CompanyFactsRecord, Filing, PipelineStats
from idi_company_facts.xbrl.concepts import (
    PERIOD_END,
    PUBLIC_FLOAT,
    REGISTRANT_NAME,
    REVENUE_CONCEPTS,
    SECURITY_12B_TITLE,
    SECURITY_EXCHANGE_NAME,
    SHARES_OUTSTANDING,
    SHELL_COMPANY,
    TRADING_SYMBOL,
)
from idi_company_facts.xbrl.parser import InlineXbrlDocument

_ANNUAL_MIN_DAYS = 340
_ANNUAL_MAX_DAYS = 380


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain integer string when the value is whole."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


class CompanyFactsExtractor:
    """Extract structured company facts from a 10-K iXBRL document."""

    def __init__(self, stats: PipelineStats) -> None:
        """Initialize with a shared stats object for incrementing counters.

        Args:
            stats: Pipeline statistics object shared across worker threads.
        """
        self.stats = stats

    def extract(self, filing: Filing, doc: InlineXbrlDocument) -> list[CompanyFactsRecord]:
        """Map one (filing, document) pair to exactly one CompanyFactsRecord.

        Extracts entity-level (dimensionless) common stock facts only. Multi-class
        share structures and subsidiary breakdowns are deferred to a future pass.

        Args:
            filing: Metadata from the SEC scraper (CIK, dates, URLs).
            doc: Parsed iXBRL document for the filing's primary 10-K exhibit.

        Returns:
            A single-element list containing the extracted :class:`CompanyFactsRecord`.
        """
        period_end = self._period_end(doc)
        market_value, mv_date, mv_currency = self._market_value(doc)
        shell = self._shell_company(doc)
        revenue, rev_date, rev_currency = self._revenue(doc, period_end)
        registrant = self._registrant_name(doc) or filing.company_name

        shares, shares_date, security_name, ticker, exchange = self._common_stock(doc)

        now = datetime.datetime.now(datetime.UTC)
        return [
            CompanyFactsRecord(
                company_cik=filing.cik,
                accession_number=filing.accession_number,
                form_type=filing.form_type,
                doc_type=filing.form_type,
                primary_url=filing.primary_url,
                filing_date=filing.filing_date,
                report_date=period_end,
                company_name=registrant,
                security_name=security_name,
                ticker=ticker,
                exchange=exchange,
                market_value=_fmt(market_value) if market_value is not None else "",
                market_value_as_of_date=mv_date,
                market_value_currency=mv_currency or "",
                shares_outstanding=_fmt(shares) if shares is not None else "",
                shares_outstanding_as_of_date=shares_date,
                is_shell_company=str(shell).lower() if shell is not None else "",
                revenue=_fmt(revenue) if revenue is not None else "",
                revenue_as_of_date=rev_date,
                revenue_currency=rev_currency or "",
                last_accessed=now,
            )
        ]

    def _period_end(self, doc: InlineXbrlDocument) -> datetime.date | None:
        """Return DocumentPeriodEndDate as a date, or None if absent or non-date."""
        fact = doc.single_fact(PERIOD_END)
        return fact.value if fact is not None and isinstance(fact.value, datetime.date) else None

    def _registrant_name(self, doc: InlineXbrlDocument) -> str:
        """Return EntityRegistrantName text, normalised to a single line."""
        fact = doc.single_fact(REGISTRANT_NAME)
        if fact is None or not isinstance(fact.value, str):
            return ""
        return " ".join(fact.value.split())

    def _market_value(
        self, doc: InlineXbrlDocument
    ) -> tuple[Decimal | None, datetime.date | None, str | None]:
        """Return (public float, instant date, currency), all None if absent."""
        fact = doc.single_fact(PUBLIC_FLOAT)
        if fact is None or not isinstance(fact.value, Decimal):
            return None, None, None
        return fact.value, fact.context.instant, fact.unit

    def _common_stock(
        self, doc: InlineXbrlDocument
    ) -> tuple[Decimal | None, datetime.date | None, str, str, str]:
        """Return (shares, as_of_date, security_name, ticker, exchange) for common stock.

        Targets only dimensionless EntityCommonStockSharesOutstanding facts —
        the entity-level count that is not tied to a specific share class or
        subsidiary. Multi-class and subsidiary breakdowns are ignored here and
        will be handled in a future extraction pass.

        security_name comes from dei:Security12bTitle — the registered name on
        the 10-K cover page, e.g. "Common Stock, $0.001 par value per share".
        Ticker, exchange, and security_name are matched first by sharing the
        same contextRef as the shares fact, then fall back to a single
        dimensionless fact when the contexts differ (the common SEC pattern
        where those DEI facts sit in an annual duration context while shares
        outstanding uses a balance-sheet instant context).
        """
        shares_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if not f.context.has_dimensions and isinstance(f.value, Decimal)
        ]
        if not shares_facts:
            return None, None, "", "", ""

        fact = max(shares_facts, key=lambda f: f.context.instant or datetime.date.min)
        ctx_id = fact.context.context_id

        by_ctx: dict[str, dict[str, str]] = {}
        for concept in (TRADING_SYMBOL, SECURITY_EXCHANGE_NAME, SECURITY_12B_TITLE):
            for f in doc.facts(concept):
                by_ctx.setdefault(f.context.context_id, {})[concept] = str(f.value)

        matched = by_ctx.get(ctx_id, {})
        ticker = matched.get(TRADING_SYMBOL, "")
        exchange = matched.get(SECURITY_EXCHANGE_NAME, "")
        security_name = matched.get(SECURITY_12B_TITLE, "")

        if not ticker:
            # Fallback: single dimensionless fact for each DEI concept
            def _dl_single(concept: str) -> str:
                vals = [str(f.value) for f in doc.facts(concept) if not f.context.has_dimensions]
                return vals[0] if len(vals) == 1 else ""

            ticker = _dl_single(TRADING_SYMBOL)
            exchange = _dl_single(SECURITY_EXCHANGE_NAME)
            security_name = _dl_single(SECURITY_12B_TITLE)

        return fact.value, fact.context.instant, security_name, ticker, exchange

    def _shell_company(self, doc: InlineXbrlDocument) -> bool | None:
        """Return EntityShellCompany as a bool, or None if absent or unrecognised.

        Handles both ixt:booleanfalse/true typed values and plain-text fallbacks
        (some filers write 'No'/'Yes' without a format attribute).
        """
        fact = doc.single_fact(SHELL_COMPANY)
        if fact is None:
            return None
        if isinstance(fact.value, bool):
            return fact.value
        if isinstance(fact.value, str):
            lower = fact.value.strip().lower()
            if lower in ("no", "false"):
                return False
            if lower in ("yes", "true"):
                return True
        return None

    def _revenue(
        self,
        doc: InlineXbrlDocument,
        period_end: datetime.date | None,
    ) -> tuple[Decimal | None, datetime.date | None, str | None]:
        """Return (revenue, period end date, currency) for the best matching concept.

        Walks REVENUE_CONCEPTS in priority order. Accepts only facts that are:
        - dimensionless (excludes segment breakdowns)
        - ending on period_end (anchors to this filing's fiscal year)
        - annual duration (340–380 days)
        """
        if period_end is None:
            return None, None, None

        for concept in REVENUE_CONCEPTS:
            for fact in doc.facts(concept):
                if fact.context.has_dimensions:
                    continue
                if fact.context.end != period_end:
                    continue
                if fact.context.start is None:
                    continue
                duration = (fact.context.end - fact.context.start).days
                if not (_ANNUAL_MIN_DAYS <= duration <= _ANNUAL_MAX_DAYS):
                    continue
                if not isinstance(fact.value, Decimal):
                    continue
                return fact.value, fact.context.end, fact.unit

        return None, None, None
