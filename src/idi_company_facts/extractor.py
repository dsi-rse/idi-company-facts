"""Maps (Filing, InlineXbrlDocument) pairs to CompanyFactsRecord instances."""

import dataclasses
import datetime
from decimal import Decimal

from idi_ftm2j_shared.logs import get_logger

from idi_company_facts.failures import FailureType
from idi_company_facts.types import (
    CompanyFactsRecord,
    Dimension,
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


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain integer string when the value is whole."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _normalize_ticker(raw: str) -> str:
    """Strip whitespace and map placeholder 'no listing' values to empty."""
    ticker = raw.strip()
    return "" if ticker.lower() in _PLACEHOLDER_TICKERS else ticker


def _format_dimensions(dimensions: frozenset[Dimension]) -> str:
    """Format Dimension pairs as a sorted ``axis=member`` string.

    Multiple pairs are joined by ``"; "``.  Empty frozenset returns ``""``.

    The ``=`` separator is safe for both explicit and typed members because the
    axis is always a QName (NCNames cannot contain ``=``).  To parse back, split
    each ``"; "``-delimited token on the **first** ``=`` only (``str.split("=", 1)``)
    — typed member values may themselves contain ``=``.
    """
    return "; ".join(f"{d.axis}={d.member}" for d in sorted(dimensions, key=lambda d: d.axis))


class CompanyFactsExtractor:
    """Extract structured company facts from an annual-report iXBRL document."""

    def __init__(self) -> None:
        """Initialize the extractor."""

    def extract(
        self, filing: Filing, doc: InlineXbrlDocument
    ) -> tuple[CompanyFactsRecord, list[FailureType], int, int]:
        """Map one (filing, document) pair to exactly one CompanyFactsRecord.

        All securities registered under Section 12(b) on the cover page are
        collected into ``registered_securities`` in extraction order.
        Unmatched share count rows (i.e. only share count reported, no security with matching
        dimension) appended at the end of the securities extracted from the Section 12(b) table.

        Args:
            filing: Metadata from the SEC scraper (CIK, dates, URLs).
            doc: Parsed iXBRL document for the filing's primary annual exhibit.

        Returns:
            A tuple of (record, failures, n_unmatched_explicit, n_unmatched_typed).

            ``n_unmatched_explicit``: count of explicit-member share groups with no matching
            registered security.  An explicit-member context tags its dimension using a
            standard QName member (``xbrli:explicitMember``), so each (axis, member) pair
            is a known concept.  Groups are keyed by the full frozenset of (axis, member)
            pairs; a group with no matching security becomes a stub row.

            ``n_unmatched_typed``: count of typed-member share groups with no matching
            registered security.  A typed-member context (``xbrli:typedMember``) has the
            same (axis, member) structure but the member is a free-form string rather than
            a taxonomy QName.  Matched against typed-member securities by exact pair-set
            equality; unmatched groups become stubs.

            Both counters are incremented once per unmatched group, not once per fact.
        """
        period_end = self._period_end(doc)
        market_value, mv_date, mv_currency = self._market_value(doc)
        shell = self._shell_company(doc)
        revenue, rev_date, rev_currency, rev_ambiguous = self._revenue(doc, period_end)
        registrant = self._registrant_name(doc) or filing.company_name

        (
            shares,
            shares_date,
            securities,
            ambiguous_shares,
            n_unmatched_explicit,
            n_unmatched_typed,
        ) = self._shares_and_securities(doc, filing.accession_number)

        failures: list[FailureType] = []
        if period_end is None:
            # No period_end to anchor the revenue concept
            failures.append(FailureType.MISSING_PERIOD_END)
        elif revenue is None:
            failures.append(FailureType.NO_REVENUE_CONCEPT)
        elif rev_ambiguous:
            failures.append(FailureType.AMBIGUOUS_REVENUE)
        if ambiguous_shares:
            failures.append(FailureType.AMBIGUOUS_SHARES_OUTSTANDING)

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
        return record, failures, n_unmatched_explicit, n_unmatched_typed

    def _period_end(self, doc: InlineXbrlDocument) -> datetime.date | None:
        """Return DocumentPeriodEndDate as a date, or None if absent or unparseable.

        Some filers omit the format= attribute on dei:DocumentPeriodEndDate, so
        the parser returns the raw text string instead of a datetime.date.  Try
        parse_date_text as a fallback so those filings still anchor correctly.
        """
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
        """Return (public float, as-of date, currency), all None if absent."""
        fact = doc.single_fact(PUBLIC_FLOAT)
        if fact is None or not isinstance(fact.value, Decimal):
            return None, None, None
        return fact.value, fact.context.as_of_date, fact.unit

    def _shares_and_securities(
        self, doc: InlineXbrlDocument, accession_number: str = ""
    ) -> tuple[Decimal | None, datetime.date | None, list[RegisteredSecurity], bool, int, int]:
        """Takes security facts from the document and matches them per dimension.

        Return (scalar, as_of_date, securities, ambiguous_shares, n_unmatched_explicit, n_unmatched_typed).

        scalar: dimensionless EntityCommonStockSharesOutstanding at the latest as-of date,
        or None.  Its relationship to per-security counts is unresolvable here.

        ambiguous_shares: True when two dimensionless facts share the same latest instant
        but disagree on value.

        securities: all Section 12(b) securities in extraction order, with dimensioned
        share counts attributed by :meth:`_attribute_shares`.  Unmatched groups are
        appended as stub rows with empty name/ticker/exchange.

        n_unmatched_explicit: count of explicit-member share groups with no matching
        registered security, incremented once per group.

        n_unmatched_typed: count of typed-member share groups (distinct (axis, member string)
        pair sets) with no matching registered security, incremented once per group.  A typed
        security tagged with the same (axis, member string) pair set will match.
        """
        shares_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if not f.context.has_dimensions and isinstance(f.value, Decimal)
        ]

        ambiguous_shares = False
        if shares_facts:
            latest_as_of = max(f.context.as_of_date or datetime.date.min for f in shares_facts)
            latest_facts = [
                f
                for f in shares_facts
                if (f.context.as_of_date or datetime.date.min) == latest_as_of
            ]
            if len({f.value for f in latest_facts}) > 1:
                ambiguous_shares = True
                _logger.info(
                    "%s: ambiguous dimensionless shares at %s — %s; using first value",
                    accession_number,
                    latest_as_of,
                    ", ".join(_fmt(f.value) for f in latest_facts),
                )
            fact = latest_facts[0]
            shares_value: Decimal | None = fact.value
            shares_date: datetime.date | None = fact.context.as_of_date
        else:
            shares_value = None
            shares_date = None

        explicit_entries, typed_entries = self._registered_securities(doc)
        securities, n_unmatched_explicit, n_unmatched_typed = self._attribute_shares(
            doc, explicit_entries, typed_entries, accession_number, shares_value
        )
        return (
            shares_value,
            shares_date,
            securities,
            ambiguous_shares,
            n_unmatched_explicit,
            n_unmatched_typed,
        )

    def _attribute_shares(
        self,
        doc: InlineXbrlDocument,
        explicit_entries: list[tuple[frozenset[Dimension], RegisteredSecurity]],
        typed_entries: list[tuple[frozenset[Dimension], RegisteredSecurity]],
        accession_number: str = "",
        scalar: Decimal | None = None,
    ) -> tuple[list[RegisteredSecurity], int, int]:
        """Attribute dimensioned share counts to securities; append unmatched rows as stubs.

        Both explicit-member and typed-member contexts are treated symmetrically as
        (axis, member) pairs grouped by their full frozenset, then matched against their
        respective security pool by exact pair-set equality:

        - Explicit (``xbrli:explicitMember``): member is a QName referencing a
          named concept in a taxonomy (``prefix:localName`` format).
          Matched against ``explicit_entries``.
        - Typed (``xbrli:typedMember``): member is a free-form string constrained only
          by an XML Schema type, not a fixed taxonomy.  Matched against
          ``typed_entries``.

        Keeping the pools separate prevents cross-matching between an explicit-member
        share count and a typed-member security (or vice versa) whose (axis, value)
        strings happen to coincide.  Unmatched groups in either pool become stub rows
        appended after all security rows, with empty name/ticker/exchange and
        ``dimensioned_members`` set to the ``"axis=member"``-formatted pairs.

        Returns:
            (securities, n_unmatched_explicit, n_unmatched_typed) where each counter is
            incremented once per unmatched group, not once per fact.
        """
        dim_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if f.context.has_dimensions
            and isinstance(f.value, Decimal)
            and f.context.as_of_date is not None
        ]

        all_entries = explicit_entries + typed_entries
        if not dim_facts:
            return [sec for _, sec in all_entries], 0, 0

        # Partition share count facts by member type.
        # Explicit-member facts keyed by their explicit Dimension pair-set.
        # Typed-member facts keyed by their typed Dimension pair-set.
        explicit_groups: dict[frozenset[Dimension], list] = {}
        typed_groups: dict[frozenset[Dimension], list] = {}

        for f in dim_facts:
            explicit_dims = frozenset(d for d in f.context.dimensions if not d.is_typed)
            typed_dims = frozenset(d for d in f.context.dimensions if d.is_typed)
            if explicit_dims:
                explicit_groups.setdefault(explicit_dims, []).append(f)
            elif typed_dims:
                typed_groups.setdefault(typed_dims, []).append(f)

        def _best(facts: list[Fact]) -> Fact:
            return max(facts, key=lambda f: f.context.as_of_date or datetime.date.min)

        explicit_sec_list = list(explicit_entries)
        typed_sec_list = list(typed_entries)
        appended: list[RegisteredSecurity] = []

        def _write_or_stub(
            sec_list: list[tuple[frozenset[Dimension], RegisteredSecurity]],
            matched_idx: int | None,
            bf: Fact,
            dm: str,
        ) -> bool:
            """Write share count into matched security row, or append a stub. Returns True if stub."""
            if matched_idx is not None:
                old_members, old_sec = sec_list[matched_idx]
                sec_list[matched_idx] = (
                    old_members,
                    dataclasses.replace(
                        old_sec,
                        shares_outstanding=_fmt(bf.value),
                        shares_outstanding_as_of=bf.context.as_of_date,
                    ),
                )
                return False
            appended.append(
                RegisteredSecurity(
                    dimensioned_members=dm,
                    shares_outstanding=_fmt(bf.value),
                    shares_outstanding_as_of=bf.context.as_of_date,
                )
            )
            return True

        def _match_and_attribute(
            groups: dict[frozenset[Dimension], list],
            sec_list: list[tuple[frozenset[Dimension], RegisteredSecurity]],
            label: str,
        ) -> int:
            n_unmatched = 0
            for pair_set, facts in groups.items():
                bf = _best(facts)
                matched_idx = next(
                    (i for i, (pairs, _) in enumerate(sec_list) if pairs == pair_set), None
                )
                dm = _format_dimensions(pair_set)
                if _write_or_stub(sec_list, matched_idx, bf, dm):
                    n_unmatched += 1
                    _logger.info(
                        "%s: unmatched %s share group (%s)=%s",
                        accession_number,
                        label,
                        dm,
                        _fmt(bf.value),
                    )
            return n_unmatched

        n_unmatched_explicit = _match_and_attribute(
            explicit_groups, explicit_sec_list, "explicit-member"
        )
        n_unmatched_typed = _match_and_attribute(typed_groups, typed_sec_list, "typed-member")

        return (
            [sec for _, sec in explicit_sec_list] + [sec for _, sec in typed_sec_list] + appended,
            n_unmatched_explicit,
            n_unmatched_typed,
        )

    def _registered_securities(
        self, doc: InlineXbrlDocument
    ) -> tuple[
        list[tuple[frozenset[Dimension], RegisteredSecurity]],
        list[tuple[frozenset[Dimension], RegisteredSecurity]],
    ]:
        """Collect every registered security tagged on the cover page.

        Returns ``(explicit_entries, typed_entries)`` where each is a list of
        ``(dimensions, security)`` tuples in extraction order.

        Explicit entries (dimensionless and explicit-member) come first;
        typed entries (typed-member contexts) are returned separately so
        :meth:`_attribute_shares` can match each pool against its own share
        count groups.  Both use ``frozenset[Dimension]`` as the dimension key —
        ``is_typed=False`` for explicit members, ``is_typed=True`` for typed.
        """
        explicit_dim_groups: dict[frozenset[Dimension], dict[str, str]] = {}
        typed_dim_groups: dict[frozenset[Dimension], dict[str, str]] = {}
        dimless_groups: dict[str, dict[str, str]] = {}
        for concept in _SECURITY_CONCEPTS:
            for f in doc.facts(concept):
                ctx = f.context
                if ctx.has_dimensions:
                    explicit_dims = frozenset(d for d in ctx.dimensions if not d.is_typed)
                    typed_dims = frozenset(d for d in ctx.dimensions if d.is_typed)
                    if explicit_dims:
                        slot = explicit_dim_groups.setdefault(explicit_dims, {})
                    else:
                        slot = typed_dim_groups.setdefault(typed_dims, {})
                else:
                    slot = dimless_groups.setdefault(ctx.context_id, {})
                # Keep the first value per concept within a group.
                slot.setdefault(concept, str(f.value))

        # Merge dimensionless contexts when they don't conflict.
        # typically, each fact within a 12b security row are tagged with the same context ID.
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

        explicit_entries: list[tuple[frozenset[Dimension], RegisteredSecurity]] = []

        # Dimensionless first, then explicit-dimension in first-seen document order.
        for slot in dimless_groups.values():
            sec = self._build_security(slot, frozenset())
            if sec is not None:
                explicit_entries.append((frozenset(), sec))
        for dims, slot in explicit_dim_groups.items():
            sec = self._build_security(slot, dims)
            if sec is not None:
                explicit_entries.append((dims, sec))

        typed_entries: list[tuple[frozenset[Dimension], RegisteredSecurity]] = []
        for typed_dims, slot in typed_dim_groups.items():
            sec = self._build_security(slot, typed_dims)
            if sec is not None:
                typed_entries.append((typed_dims, sec))

        return self._dedupe_securities(explicit_entries), typed_entries

    @staticmethod
    def _build_security(
        slot: dict[str, str],
        dimensions: frozenset[Dimension],
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
        dim_members = _format_dimensions(dimensions)
        return RegisteredSecurity(
            security_name=name,
            ticker=ticker,
            exchange=exchange,
            dimensioned_members=dim_members,
        )

    @staticmethod
    def _dedupe_securities(
        entries: list[tuple[frozenset[Dimension], RegisteredSecurity]],
    ) -> list[tuple[frozenset[Dimension], RegisteredSecurity]]:
        """Collapse entries describing the same security.

        Keyed by (ticker, dimensions) for ticker-bearing securities, or
        (name, exchange, dimensions) for ticker-less ones.  Including the full
        dimension pair-set in the ticker-less key ensures two distinct classes
        that share the same name+exchange (e.g. different class-axis members,
        no ticker) are kept as separate rows rather than collapsed.

        When duplicates do collide each field takes the first non-empty value.

        This function is applied to explicit entries only; typed entries are
        already keyed by unique dimension sets in _registered_securities and
        do not need deduplication.
        """
        by_key: dict[tuple, tuple[frozenset[Dimension], RegisteredSecurity]] = {}
        for dimensions, sec in entries:
            if sec.ticker:
                key: tuple = ("ticker", sec.ticker.lower(), dimensions)
            else:
                key = (sec.security_name.lower(), sec.exchange.lower(), dimensions)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = (dimensions, sec)
                continue
            ex_dims, ex_sec = existing
            merged_sec = dataclasses.replace(
                ex_sec,
                security_name=ex_sec.security_name or sec.security_name,
                ticker=ex_sec.ticker or sec.ticker,
                exchange=ex_sec.exchange or sec.exchange,
                dimensioned_members=ex_sec.dimensioned_members or sec.dimensioned_members,
            )
            by_key[key] = (ex_dims | dimensions, merged_sec)
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
                break

        if not concept_hits:
            return None, None, None, False

        winner_value, winner_date, winner_unit = concept_hits[0]
        is_ambiguous = any(val != winner_value for val, _, _ in concept_hits[1:])
        return winner_value, winner_date, winner_unit, is_ambiguous
