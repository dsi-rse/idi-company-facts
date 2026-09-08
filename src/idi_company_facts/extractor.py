"""Maps (Filing, InlineXbrlDocument) pairs to CompanyFactsRecord instances."""

import dataclasses
import datetime
from decimal import Decimal

from idi_ftm2j_shared.logs import get_logger

from idi_company_facts.failures import FailureType
from idi_company_facts.types import (
    CompanyFactsRecord,
    Fact,
    Filing,
    RegisteredSecurity,
)
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
from idi_company_facts.xbrl.parser import InlineXbrlDocument, parse_date_text

_logger = get_logger(__name__)

_ANNUAL_MIN_DAYS = 340
_ANNUAL_MAX_DAYS = 380

# DEI concepts that together describe one registered security.
_SECURITY_CONCEPTS = (TRADING_SYMBOL, SECURITY_EXCHANGE_NAME, SECURITY_12B_TITLE)

# Placeholder TradingSymbol values that indicate no listed security.
_PLACEHOLDER_TICKERS = frozenset({"none", "-", "n/a", "not applicable"})

# Local name of the class-of-stock axis used to attribute share counts.
_CLASS_STOCK_AXIS_LOCAL = "StatementClassOfStockAxis"


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain integer string when the value is whole."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _normalize_ticker(raw: str) -> str:
    """Strip whitespace and map placeholder 'no listing' values to empty."""
    ticker = raw.strip()
    return "" if ticker.lower() in _PLACEHOLDER_TICKERS else ticker


def _class_member_from_dimensions(dimensions: frozenset[tuple[str, str]]) -> str:
    """Return the class-of-stock axis member QName, or empty if absent or ambiguous.

    Matches the axis by local name so non-standard namespace prefixes are handled.
    Returns empty if there are zero or more than one class-axis member pairs.
    """
    members = [m for axis, m in dimensions if axis.split(":")[-1] == _CLASS_STOCK_AXIS_LOCAL]
    return members[0] if len(members) == 1 else ""


class CompanyFactsExtractor:
    """Extract structured company facts from an annual-report iXBRL document."""

    def __init__(self) -> None:
        """Initialize the extractor."""

    def extract(
        self, filing: Filing, doc: InlineXbrlDocument
    ) -> tuple[list[CompanyFactsRecord], list[FailureType]]:
        """Map one (filing, document) pair to exactly one CompanyFactsRecord.

        All securities registered under Section 12(b) on the cover page are
        collected into ``registered_securities`` in extraction order (dimensionless
        first, dimensioned second, appended unmatched share rows last).

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

        shares, shares_date, securities = self._shares_and_securities(doc, filing.accession_number)

        failures: list[FailureType] = []
        if period_end is None:
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
            registered_securities=securities,
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
        """Return DocumentPeriodEndDate as a date, or None if absent or unparseable."""
        fact = doc.single_fact(PERIOD_END)
        if fact is None:
            return None
        if isinstance(fact.value, datetime.date):
            return fact.value
        if isinstance(fact.value, str):
            parsed = parse_date_text(fact.value)
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

    def _shares_and_securities(
        self, doc: InlineXbrlDocument, accession_number: str = ""
    ) -> tuple[Decimal | None, datetime.date | None, list[RegisteredSecurity]]:
        """Return (scalar_shares, as_of_date, securities_with_attributed_counts).

        Scalar: the value of a dimensionless EntityCommonStockSharesOutstanding
        fact at the latest instant.  Empty when no dimensionless fact exists —
        the scalar is NEVER computed by summing dimensioned facts.

        Securities: all Section 12(b) securities in extraction order, with
        dimensioned share counts attributed per class via
        :meth:`_attribute_shares`.  Unmatched dimensioned counts are appended
        as member-only rows after the table rows.
        """
        shares_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if not f.context.has_dimensions and isinstance(f.value, Decimal)
        ]

        if shares_facts:
            fact = max(shares_facts, key=lambda f: f.context.instant or datetime.date.min)
            shares_value: Decimal | None = fact.value
            shares_date: datetime.date | None = fact.context.instant
        else:
            shares_value = None
            shares_date = None

        entries = self._registered_securities(doc)
        securities = self._attribute_shares(doc, entries, accession_number, shares_value)
        return shares_value, shares_date, securities

    def _attribute_shares(
        self,
        doc: InlineXbrlDocument,
        entries: list[tuple[frozenset[str], RegisteredSecurity]],
        accession_number: str = "",
        scalar: Decimal | None = None,
    ) -> list[RegisteredSecurity]:
        """Attribute dimensioned share counts to securities; append unmatched rows.

        Groups dimensioned EntityCommonStockSharesOutstanding facts by their
        class-axis member (or full member set for opaque contexts).  Takes the
        latest-instant fact per group, then matches it to a registered security
        via exact class_member equality or member-set containment.  Unmatched
        groups are appended as stub securities; typed-member contexts are
        appended with an empty class_member.
        """
        dim_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if f.context.has_dimensions
            and isinstance(f.value, Decimal)
            and f.context.instant is not None
        ]

        if not dim_facts:
            return [sec for _, sec in entries]

        # Partition dim_facts by group type.
        class_axis_groups: dict[str, list] = {}
        opaque_groups: dict[frozenset[str], list] = {}
        typed_member_facts: list = []

        for f in dim_facts:
            ctx = f.context
            if not ctx.dimension_members:
                # typedMember context: has_dimensions=True but no explicit members
                typed_member_facts.append(f)
                continue
            cm = _class_member_from_dimensions(ctx.dimensions)
            if cm:
                class_axis_groups.setdefault(cm, []).append(f)
            else:
                opaque_groups.setdefault(ctx.dimension_members, []).append(f)

        def _best(facts: list[Fact]) -> Fact:
            return max(facts, key=lambda f: f.context.instant or datetime.date.min)

        sec_list: list[tuple[frozenset[str], RegisteredSecurity]] = list(entries)
        appended: list[RegisteredSecurity] = []
        n_unmatched = 0
        unmatched_details: list[str] = []

        # Match class-axis groups.
        for cm, facts in class_axis_groups.items():
            bf = _best(facts)
            matched_idx = None
            for i, (_members, sec) in enumerate(sec_list):
                if sec.class_member == cm:
                    matched_idx = i
                    break
            if matched_idx is None:
                candidates = [i for i, (members, sec) in enumerate(sec_list) if cm in members]
                if len(candidates) == 1:
                    matched_idx = candidates[0]
            if matched_idx is not None:
                old_members, old_sec = sec_list[matched_idx]
                sec_list[matched_idx] = (
                    old_members,
                    dataclasses.replace(
                        old_sec,
                        shares_outstanding=_fmt(bf.value),
                        shares_outstanding_as_of=bf.context.instant,
                    ),
                )
            else:
                appended.append(
                    RegisteredSecurity(
                        class_member=cm,
                        shares_outstanding=_fmt(bf.value),
                        shares_outstanding_as_of=bf.context.instant,
                    )
                )
                n_unmatched += 1
                unmatched_details.append(f"{cm}={_fmt(bf.value)}")

        # Match opaque groups (dimensioned, no class-axis member).
        for member_set, facts in opaque_groups.items():
            bf = _best(facts)
            matched_idx = None
            for i, (members, _s) in enumerate(sec_list):
                if members == member_set:
                    matched_idx = i
                    break
            if matched_idx is not None:
                old_members, old_sec = sec_list[matched_idx]
                sec_list[matched_idx] = (
                    old_members,
                    dataclasses.replace(
                        old_sec,
                        shares_outstanding=_fmt(bf.value),
                        shares_outstanding_as_of=bf.context.instant,
                    ),
                )
            else:
                opaque_cm = "; ".join(sorted(member_set))
                appended.append(
                    RegisteredSecurity(
                        class_member=opaque_cm,
                        shares_outstanding=_fmt(bf.value),
                        shares_outstanding_as_of=bf.context.instant,
                    )
                )
                n_unmatched += 1
                unmatched_details.append(f"opaque({opaque_cm})={_fmt(bf.value)}")

        # Typed-member-only contexts: append with empty class_member.
        if typed_member_facts:
            bf = _best(typed_member_facts)
            appended.append(
                RegisteredSecurity(
                    shares_outstanding=_fmt(bf.value),
                    shares_outstanding_as_of=bf.context.instant,
                )
            )
            _logger.info(
                "%s: typed-member-only share count %s; cannot attribute to a security",
                accession_number,
                _fmt(bf.value),
            )

        if n_unmatched > 0:
            _logger.info(
                "%s: %d unjoined share class(es): %s",
                accession_number,
                n_unmatched,
                "; ".join(unmatched_details),
            )

        # Optional: warn when scalar and summed dimensioned counts diverge.
        if scalar is not None:
            joined_sum = sum(
                Decimal(s.shares_outstanding) for _, s in sec_list if s.shares_outstanding
            )
            if joined_sum > 0 and joined_sum != scalar:
                _logger.info(
                    "%s: scalar shares (%s) != sum of dimensioned counts (%s)",
                    accession_number,
                    scalar,
                    joined_sum,
                )

        return [sec for _, sec in sec_list] + appended

    def _registered_securities(
        self, doc: InlineXbrlDocument
    ) -> list[tuple[frozenset[str], RegisteredSecurity]]:
        """Collect every registered security tagged on the cover page.

        Returns (dimension_members, security) tuples in extraction order:
        dimensionless groups first, then dimensioned groups in first-seen
        document order.  The dimension_members set is used by
        :meth:`_attribute_shares` for member-set-containment matching.
        """
        dim_groups: dict[frozenset[str], dict[str, str]] = {}
        dim_dimensions: dict[frozenset[str], frozenset[tuple[str, str]]] = {}
        dimless_groups: dict[str, dict[str, str]] = {}
        for concept in _SECURITY_CONCEPTS:
            for f in doc.facts(concept):
                ctx = f.context
                if ctx.has_dimensions:
                    slot = dim_groups.setdefault(ctx.dimension_members, {})
                    dim_dimensions.setdefault(ctx.dimension_members, ctx.dimensions)
                else:
                    slot = dimless_groups.setdefault(ctx.context_id, {})
                slot.setdefault(concept, str(f.value))

        # Merge dimensionless contexts when they don't conflict.
        if dimless_groups:
            conflicting = any(
                len({" ".join(slot[c].split()) for slot in dimless_groups.values() if c in slot})
                > 1
                for c in _SECURITY_CONCEPTS
            )
            if not conflicting:
                merged: dict[str, str] = {}
                for slot in dimless_groups.values():
                    for concept, value in slot.items():
                        merged.setdefault(concept, value)
                dimless_groups = {"|".join(sorted(dimless_groups)): merged}

        entries: list[tuple[frozenset[str], RegisteredSecurity]] = []

        # Dimensionless first.
        for slot in dimless_groups.values():
            sec = self._build_security(slot, frozenset())
            if sec is not None:
                entries.append((frozenset(), sec))

        # Dimensioned second, in first-seen document order.
        for members, slot in dim_groups.items():
            dimensions = dim_dimensions.get(members, frozenset())
            sec = self._build_security(slot, dimensions)
            if sec is not None:
                entries.append((members, sec))

        return self._dedupe_securities(entries)

    @staticmethod
    def _build_security(
        slot: dict[str, str],
        dimensions: frozenset[tuple[str, str]],
    ) -> RegisteredSecurity | None:
        """Build a RegisteredSecurity from a concept→value slot, or None if empty.

        Args:
            slot: Mapping of DEI concept name to string value for this security.
            dimensions: (axis, member) pairs from the security's XBRL context;
                empty for dimensionless (entity-level) facts.

        Returns:
            A populated RegisteredSecurity, or None when all fields are empty.
        """
        ticker = _normalize_ticker(slot.get(TRADING_SYMBOL, ""))
        exchange = slot.get(SECURITY_EXCHANGE_NAME, "").strip()
        name = " ".join(slot.get(SECURITY_12B_TITLE, "").split())
        if not (ticker or exchange or name):
            return None
        class_member = _class_member_from_dimensions(dimensions)
        return RegisteredSecurity(
            security_name=name,
            ticker=ticker,
            exchange=exchange,
            class_member=class_member,
        )

    @staticmethod
    def _dedupe_securities(
        entries: list[tuple[frozenset[str], RegisteredSecurity]],
    ) -> list[tuple[frozenset[str], RegisteredSecurity]]:
        """Collapse entries describing the same security.

        Keyed by (ticker, members) for ticker-bearing securities, or
        (name, exchange) for ticker-less ones.  When duplicates collide each
        field takes the first non-empty value; class_member likewise.
        """
        by_key: dict[tuple, tuple[frozenset[str], RegisteredSecurity]] = {}
        for members, sec in entries:
            if sec.ticker:
                key: tuple = ("ticker", sec.ticker.lower(), members)
            else:
                key = (sec.security_name.lower(), sec.exchange.lower())
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = (members, sec)
                continue
            ex_members, ex_sec = existing
            merged_sec = dataclasses.replace(
                ex_sec,
                security_name=ex_sec.security_name or sec.security_name,
                ticker=ex_sec.ticker or sec.ticker,
                exchange=ex_sec.exchange or sec.exchange,
                class_member=ex_sec.class_member or sec.class_member,
            )
            by_key[key] = (ex_members | members, merged_sec)
        return list(by_key.values())

    def _shell_company(self, doc: InlineXbrlDocument) -> bool | None:
        """Return EntityShellCompany as a bool, or None if absent or unrecognised."""
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
        """
        if period_end is None:
            return None, None, None, False

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
                break

        if not concept_hits:
            return None, None, None, False

        winner_value, winner_date, winner_unit = concept_hits[0]
        is_ambiguous = any(val != winner_value for val, _, _ in concept_hits[1:])
        return winner_value, winner_date, winner_unit, is_ambiguous
