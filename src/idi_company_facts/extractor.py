"""Maps (Filing, InlineXbrlDocument) pairs to CompanyFactsRecord instances."""

import datetime
from decimal import Decimal

from idi_company_facts.failures import FailureType
from idi_company_facts.types import CompanyFactsRecord, Filing
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
from idi_company_facts.xbrl.parser import InlineXbrlDocument, _parse_date_text

_ANNUAL_MIN_DAYS = 340
_ANNUAL_MAX_DAYS = 380


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain integer string when the value is whole."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


class CompanyFactsExtractor:
    """Extract structured company facts from an annual-report iXBRL document."""

    def __init__(self) -> None:
        """Initialize the extractor."""

    def extract(
        self, filing: Filing, doc: InlineXbrlDocument
    ) -> tuple[list[CompanyFactsRecord], list[FailureType]]:
        """Map one (filing, document) pair to exactly one CompanyFactsRecord.

        Extracts entity-level (dimensionless) common stock facts only. Multi-class
        share structures and subsidiary breakdowns are deferred to a future pass.

        Args:
            filing: Metadata from the SEC scraper (CIK, dates, URLs).
            doc: Parsed iXBRL document for the filing's primary annual exhibit.

        Returns:
            A tuple of (records, failures) where records is a single-element list
            containing the extracted :class:`CompanyFactsRecord` and failures is a
            list of :class:`FailureType` values for non-fatal extraction issues.
        """
        period_end = self._period_end(doc)
        market_value, mv_date, mv_currency = self._market_value(doc)
        shell = self._shell_company(doc)
        revenue, rev_date, rev_currency, rev_ambiguous = self._revenue(doc, period_end)
        registrant = self._registrant_name(doc) or filing.company_name

        shares, shares_date, security_name, ticker, exchange = self._common_stock(doc)

        failures: list[FailureType] = []
        if period_end is None:
            # Missing anchor — don't also report NO_REVENUE_CONCEPT since the
            # revenue walk was never run against a valid period end.
            failures.append(FailureType.MISSING_PERIOD_END)
        elif revenue is None:
            failures.append(FailureType.NO_REVENUE_CONCEPT)
        elif rev_ambiguous:
            failures.append(FailureType.AMBIGUOUS_REVENUE)

        now = datetime.datetime.now(datetime.UTC)
        record = CompanyFactsRecord(
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
        return [record], failures

    def _period_end(self, doc: InlineXbrlDocument) -> datetime.date | None:
        """Return DocumentPeriodEndDate as a date, or None if absent or unparseable.

        Some filers omit the format= attribute on dei:DocumentPeriodEndDate, so
        the parser returns the raw text string instead of a datetime.date.  Try
        _parse_date_text as a fallback so those filings still anchor correctly.
        """
        fact = doc.single_fact(PERIOD_END)
        if fact is None:
            return None
        if isinstance(fact.value, datetime.date):
            return fact.value
        if isinstance(fact.value, str):
            parsed = _parse_date_text(fact.value)
            if isinstance(parsed, datetime.date):
                return parsed
        return None

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

        Prefers a dimensionless EntityCommonStockSharesOutstanding fact (the
        entity-level total).  When only per-class (dimensioned) facts exist —
        e.g. multi-class share structures — sums all classes at the latest
        reported instant.

        security_name comes from dei:Security12bTitle — the registered name on
        the filing cover page, e.g. "Common Stock, $0.001 par value per share".
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

        dim_shares_members: frozenset[str] = frozenset()

        if shares_facts:
            fact = max(shares_facts, key=lambda f: f.context.instant or datetime.date.min)
            shares_value: Decimal = fact.value
            shares_date = fact.context.instant
            anchor_ctx_id: str | None = fact.context.context_id
        else:
            # Fall back to summing per-class dimensioned facts at the latest instant.
            dim_facts = [
                f
                for f in doc.facts(SHARES_OUTSTANDING)
                if f.context.has_dimensions
                and isinstance(f.value, Decimal)
                and f.context.instant is not None
            ]
            if not dim_facts:
                return None, None, "", "", ""
            latest = max(f.context.instant for f in dim_facts)  # type: ignore[arg-type]
            at_latest = [f for f in dim_facts if f.context.instant == latest]
            shares_value = sum(f.value for f in at_latest)  # type: ignore[assignment]
            shares_date = latest
            anchor_ctx_id = None
            # Collect all dimension members present across the share-class contexts
            # so we can match security facts by shared member rather than context_id.
            dim_shares_members = frozenset(
                m for f in at_latest for m in f.context.dimension_members
            )

        by_ctx: dict[str, dict[str, str]] = {}
        for concept in (TRADING_SYMBOL, SECURITY_EXCHANGE_NAME, SECURITY_12B_TITLE):
            for f in doc.facts(concept):
                by_ctx.setdefault(f.context.context_id, {})[concept] = str(f.value)

        matched = by_ctx.get(anchor_ctx_id or "", {})

        # For multi-class filers the security facts may be in different contexts
        # that share a dimension member with the shares fact.  Merge all matches.
        if not matched and dim_shares_members:
            for concept in (TRADING_SYMBOL, SECURITY_EXCHANGE_NAME, SECURITY_12B_TITLE):
                for f in doc.facts(concept):
                    if f.context.dimension_members & dim_shares_members:
                        matched.setdefault(concept, str(f.value))

        ticker = matched.get(TRADING_SYMBOL, "")
        exchange = matched.get(SECURITY_EXCHANGE_NAME, "")
        security_name = matched.get(SECURITY_12B_TITLE, "")

        if not ticker:
            # Fallback: most common dimensionless value for each DEI concept.
            # When all dimensionless facts agree (the typical single-class case)
            # this returns that value; when they conflict it returns the first.
            def _dl_common(concept: str) -> str:
                vals = [str(f.value) for f in doc.facts(concept) if not f.context.has_dimensions]
                if not vals:
                    return ""
                # Return the unanimous value, or first as a best-effort pick.
                return vals[0]

            ticker = _dl_common(TRADING_SYMBOL)
            exchange = _dl_common(SECURITY_EXCHANGE_NAME)
            security_name = _dl_common(SECURITY_12B_TITLE)

        # Normalize placeholder tickers that indicate no listed security.
        if ticker.strip().lower() in ("none", "-", "n/a"):
            ticker = ""

        return shares_value, shares_date, security_name, ticker, exchange

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
    ) -> tuple[Decimal | None, datetime.date | None, str | None, bool]:
        """Return (revenue, period end date, currency, is_ambiguous).

        Walks REVENUE_CONCEPTS in priority order, collecting the first qualifying
        fact per concept. A fact qualifies when it is dimensionless, ends on
        period_end, and covers an annual duration (340–380 days).

        is_ambiguous is True when multiple concepts each yield a qualifying fact
        and their values disagree. Equal values across concepts are not ambiguous.
        The priority-order winner is always returned regardless of ambiguity.
        """
        if period_end is None:
            return None, None, None, False

        # Collect the first qualifying fact per concept in priority order
        concept_hits: list[tuple[Decimal, datetime.date, str | None]] = []
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
                concept_hits.append((fact.value, fact.context.end, fact.unit))
                break  # first qualifying fact per concept

        if not concept_hits:
            return None, None, None, False

        winner_value, winner_date, winner_unit = concept_hits[0]
        is_ambiguous = any(val != winner_value for val, _, _ in concept_hits[1:])
        return winner_value, winner_date, winner_unit, is_ambiguous
