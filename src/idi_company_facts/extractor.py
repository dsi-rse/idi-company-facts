"""Maps (Filing, InlineXbrlDocument) pairs to CompanyFactsRecord instances."""

import dataclasses
import datetime
import re
from decimal import Decimal

from idi_company_facts.failures import FailureType
from idi_company_facts.types import (
    CompanyFactsRecord,
    Filing,
    RegisteredSecurity,
    SecurityType,
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
from idi_company_facts.xbrl.parser import InlineXbrlDocument

_ANNUAL_MIN_DAYS = 340
_ANNUAL_MAX_DAYS = 380

# DEI concepts that together describe one registered security.
_SECURITY_CONCEPTS = (TRADING_SYMBOL, SECURITY_EXCHANGE_NAME, SECURITY_12B_TITLE)

# Placeholder TradingSymbol values that indicate no listed security.
_PLACEHOLDER_TICKERS = frozenset({"none", "-", "n/a", "not applicable"})

# Substrings identifying a US exchange in dei:SecurityExchangeName.
_US_EXCHANGE_MARKERS = ("nasdaq", "nyse", "new york stock exchange", "cboe", "bats")

# Sort position for each SecurityType in _rank_securities — lower is earlier.
_TYPE_ORDER: dict[SecurityType, int] = {
    SecurityType.COMMON: 0,
    SecurityType.ADS: 1,
    SecurityType.PREFERRED: 2,
    SecurityType.WARRANT: 3,
    SecurityType.DEBT: 4,
    SecurityType.OTHER: 5,
}

# Splits a camelCase/PascalCase local name into lowercase words so that word-
# boundary patterns in _MEMBER_PATTERNS cannot fire on accidental substrings.
# Example: "CrossroadsSystemsMember" → "crossroads systems member" (so \bads\b
# does not match the "ads" hidden inside "crossroads").
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")

# Patterns applied to the space-joined, lowercase word blob of local dimension-
# member names.  Evaluated in order; first match wins.
# COMMON last to catch special cases before the common stock that may be wrappers or in combination
# with common stocks/shares
_MEMBER_PATTERNS: tuple[tuple[SecurityType, re.Pattern[str]], ...] = (
    (SecurityType.ADS, re.compile(r"american\s+depositary|\bdepositary\s+receipt|\bads\b|\badr\b")),
    (SecurityType.PREFERRED, re.compile(r"\bpreferred\b|\bpreference\b")),
    (SecurityType.WARRANT, re.compile(r"\bwarrant\b")),
    (SecurityType.DEBT, re.compile(r"\bnotes?\b|\bdebenture\b|\bbond\b")),
    (SecurityType.COMMON, re.compile(r"\bcommon\b|\bordinary\b")),
)

# Patterns applied to the lowercased, whitespace-normalised Security12bTitle.
# Word-boundary anchors guard against false hits.
_TITLE_PATTERNS: tuple[tuple[SecurityType, re.Pattern[str]], ...] = (
    (SecurityType.ADS, re.compile(r"american\s+depositary|\bads\b|\badr\b")),
    (SecurityType.PREFERRED, re.compile(r"preferred|preference")),
    (SecurityType.WARRANT, re.compile(r"\bwarrant|\bright\b|\brights\b|\bunit\b|\bunits\b")),
    (SecurityType.DEBT, re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b")),
    (SecurityType.COMMON, re.compile(r"\bcommon\b|\bordinary\b")),
)


def _fmt(value: Decimal) -> str:
    """Format a Decimal as a plain integer string when the value is whole."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _normalize_ticker(raw: str) -> str:
    """Strip whitespace and map placeholder 'no listing' values to empty."""
    ticker = raw.strip()
    return "" if ticker.lower() in _PLACEHOLDER_TICKERS else ticker


def _is_us_exchange(exchange: str) -> bool:
    """Heuristic: True when SecurityExchangeName looks like a US exchange."""
    lowered = exchange.lower()
    return any(marker in lowered for marker in _US_EXCHANGE_MARKERS)


def _classify_security(members: frozenset[str], title: str) -> SecurityType:
    """Classify a security as COMMON, ADS, PREFERRED, DEBT, WARRANT, or OTHER.

    Tries XBRL dimension-member names first (structured, filer-declared), then
    falls back to dei:Security12bTitle text. A member-derived result always
    outranks a title-derived result — member names are unambiguous declarations;
    ADS titles, for example, mention the ordinary shares they represent.

    Args:
        members: Explicit dimension members from the security's XBRL context.
            May be empty for dimensionless (entity-level) facts.
        title: Value of dei:Security12bTitle. May be empty.

    Returns:
        The best-matching SecurityType, or OTHER when nothing matches.
    """
    if members:
        blob = " ".join(
            t.lower() for m in sorted(members) for t in _CAMEL.findall(m.split(":")[-1])
        )
        for sec_type, pattern in _MEMBER_PATTERNS:
            if pattern.search(blob):
                return sec_type

    if title:
        title_norm = " ".join(title.lower().split())
        for sec_type, pattern in _TITLE_PATTERNS:
            if pattern.search(title_norm):
                return sec_type

    return SecurityType.OTHER


def _reconcile_types(
    type_a: SecurityType,
    has_members_a: bool,
    type_b: SecurityType,
    has_members_b: bool,
) -> SecurityType:
    """Pick the better type when two deduplicated entries disagree.

    Non-OTHER beats OTHER; among two non-OTHER types, the member-derived
    classification (structured) beats the title-derived one.

    Args:
        type_a: Classification of the first (existing) entry.
        has_members_a: True when the first entry came from a dimensional context.
        type_b: Classification of the second (incoming) entry.
        has_members_b: True when the second entry came from a dimensional context.

    Returns:
        The resolved SecurityType.
    """
    if type_a == SecurityType.OTHER:
        return type_b
    if type_b == SecurityType.OTHER:
        return type_a
    # Both non-OTHER: member-derived wins over title-derived.
    if has_members_a and not has_members_b:
        return type_a
    if has_members_b and not has_members_a:
        return type_b
    return type_a  # tie: keep the existing entry's type


class CompanyFactsExtractor:
    """Extract structured company facts from a 10-K iXBRL document."""

    def __init__(self) -> None:
        """Initialize the extractor."""

    def extract(
        self, filing: Filing, doc: InlineXbrlDocument
    ) -> tuple[list[CompanyFactsRecord], list[FailureType]]:
        """Map one (filing, document) pair to exactly one CompanyFactsRecord.

        All securities registered under Section 12(b) on the cover page are
        collected into ``registered_securities`` and ranked so the common-stock
        class (the one whose share count is reported by
        ``EntityCommonStockSharesOutstanding``) sorts first. Multiple securities
        (ADS + ordinary shares, dual-class, listed notes) are expected and are
        not treated as failures.

        Args:
            filing: Metadata from the SEC scraper (CIK, dates, URLs).
            doc: Parsed iXBRL document for the filing's primary 10-K exhibit.

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

        shares, shares_date, securities = self._shares_and_securities(doc)

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

    def _shares_and_securities(
        self, doc: InlineXbrlDocument
    ) -> tuple[Decimal | None, datetime.date | None, list[RegisteredSecurity]]:
        """Return (total_shares, as_of_date, ranked registered securities).

        Shares: prefers a dimensionless EntityCommonStockSharesOutstanding fact
        (the entity-level total). When only per-class (dimensioned) facts exist
        — e.g. dual-class share structures — sums all classes at the latest
        reported instant.

        Securities: collects *all* Section 12(b) securities from the cover page
        (see :meth:`_registered_securities`), then ranks them via
        :meth:`_rank_securities` using the shares context as an anchor. The
        anchor identifies which security is the common-stock class so it sorts
        first in the returned list; all other registered securities (ADS, second
        share class, listed notes) are retained in order behind it.
        """
        shares_facts = [
            f
            for f in doc.facts(SHARES_OUTSTANDING)
            if not f.context.has_dimensions and isinstance(f.value, Decimal)
        ]

        shares_value: Decimal | None
        dim_shares_members: frozenset[str] = frozenset()
        anchor_ctx_id: str | None = None

        if shares_facts:
            fact = max(shares_facts, key=lambda f: f.context.instant or datetime.date.min)
            shares_value = fact.value
            shares_date = fact.context.instant
            anchor_ctx_id = fact.context.context_id
        else:
            # Fall back to summing per-class dimensioned facts at the latest instant.
            dim_facts = [
                f
                for f in doc.facts(SHARES_OUTSTANDING)
                if f.context.has_dimensions
                and isinstance(f.value, Decimal)
                and f.context.instant is not None
            ]
            if dim_facts:
                latest = max(f.context.instant for f in dim_facts)  # type: ignore[arg-type]
                at_latest = [f for f in dim_facts if f.context.instant == latest]
                shares_value = sum(f.value for f in at_latest)  # type: ignore[assignment]
                shares_date = latest
                # Dimension members across the share-class contexts, used to
                # identify which security group anchors the shares fact.
                dim_shares_members = frozenset(
                    m for f in at_latest for m in f.context.dimension_members
                )
            else:
                shares_value = None
                shares_date = None

        securities = self._registered_securities(doc)
        ranked = self._rank_securities(
            securities,
            anchor_ctx_id=anchor_ctx_id,
            anchor_members=dim_shares_members,
        )
        return shares_value, shares_date, ranked

    def _registered_securities(
        self, doc: InlineXbrlDocument
    ) -> list[tuple[frozenset[str], frozenset[str], RegisteredSecurity]]:
        """Collect every registered security tagged on the cover page.

        Grouping rules:
          * Dimensional facts are grouped by their explicit-member set — each
            distinct member set (e.g. ``AmericanDepositarySharesMember`` vs
            ``OrdinarySharesMember``, or Class A vs Class B) is one security.
          * Dimensionless facts are grouped per context. When the dimensionless
            facts are mutually consistent (at most one distinct value per
            concept) they are merged into a single security — the common
            single-class pattern where DEI facts span several contexts.

        Duplicate triples (the same security tagged both dimensionally and
        dimensionlessly) are collapsed, preferring the entry with more fields.

        Returns:
            List of (context_ids, dimension_members, security) tuples in
            document order. The first two elements let the caller match a
            security back to the shares-outstanding anchor context.
        """
        dim_groups: dict[frozenset[str], dict[str, str]] = {}
        dim_ctx_ids: dict[frozenset[str], set[str]] = {}
        dimless_groups: dict[str, dict[str, str]] = {}
        for concept in _SECURITY_CONCEPTS:
            for f in doc.facts(concept):
                ctx = f.context
                if ctx.has_dimensions:
                    slot = dim_groups.setdefault(ctx.dimension_members, {})
                    dim_ctx_ids.setdefault(ctx.dimension_members, set()).add(ctx.context_id)
                else:
                    slot = dimless_groups.setdefault(ctx.context_id, {})
                # Keep the first value per concept within a group (repeated
                # cover-page tags of the same fact are common).
                slot.setdefault(concept, str(f.value))

        # Merge dimensionless contexts when they don't conflict — the typical
        # single-class filer tags ticker in one duration context and title in
        # another, all describing the same security.
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

        entries: list[tuple[frozenset[str], frozenset[str], RegisteredSecurity]] = []
        for members, slot in dim_groups.items():
            sec = self._build_security(slot, members)
            if sec is not None:
                entries.append((frozenset(dim_ctx_ids[members]), members, sec))
        for ctx_key, slot in dimless_groups.items():
            sec = self._build_security(slot, frozenset())
            if sec is not None:
                entries.append((frozenset(ctx_key.split("|")), frozenset(), sec))

        return self._dedupe_securities(entries)

    @staticmethod
    def _build_security(slot: dict[str, str], members: frozenset[str]) -> RegisteredSecurity | None:
        """Build a RegisteredSecurity from a concept→value slot, or None if empty.

        Args:
            slot: Mapping of DEI concept name to string value for this security.
            members: Dimension members from the security's XBRL context; empty
                for dimensionless (entity-level) facts.

        Returns:
            A populated RegisteredSecurity, or None when all fields are empty.
        """
        ticker = _normalize_ticker(slot.get(TRADING_SYMBOL, ""))
        exchange = slot.get(SECURITY_EXCHANGE_NAME, "").strip()
        name = " ".join(slot.get(SECURITY_12B_TITLE, "").split())
        if not (ticker or exchange or name):
            return None
        return RegisteredSecurity(
            security_name=name,
            ticker=ticker,
            exchange=exchange,
            security_type=_classify_security(members, name),
        )

    @staticmethod
    def _dedupe_securities(
        entries: list[tuple[frozenset[str], frozenset[str], RegisteredSecurity]],
    ) -> list[tuple[frozenset[str], frozenset[str], RegisteredSecurity]]:
        """Collapse entries describing the same security.

        Entries are keyed by (ticker, members) for ticker-bearing securities,
        or (name, exchange) for ticker-less ones. Two entries with the same
        ticker but different dimension members (e.g. ordinary shares and ADS
        both trading as "BABA") are never conflated. Dimensionless entries
        (members = frozenset()) only merge with other dimensionless entries for
        the same ticker.

        When duplicates collide, each field takes the first non-empty value.
        The security_type is reconciled: member-derived beats title-derived,
        non-OTHER beats OTHER.
        """
        by_key: dict[tuple, tuple[frozenset[str], frozenset[str], RegisteredSecurity]] = {}
        for ctx_ids, members, sec in entries:
            if sec.ticker:
                key: tuple = ("ticker", sec.ticker.lower(), members)
            else:
                key = (sec.security_name.lower(), sec.exchange.lower())
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = (ctx_ids, members, sec)
                continue
            ex_ctx, ex_members, ex_sec = existing
            resolved_type = _reconcile_types(
                ex_sec.security_type,
                bool(ex_members),
                sec.security_type,
                bool(members),
            )
            merged_sec = dataclasses.replace(
                ex_sec,
                security_name=ex_sec.security_name or sec.security_name,
                ticker=ex_sec.ticker or sec.ticker,
                exchange=ex_sec.exchange or sec.exchange,
                security_type=resolved_type,
            )
            by_key[key] = (ex_ctx | ctx_ids, ex_members | members, merged_sec)
        return list(by_key.values())

    @staticmethod
    def _rank_securities(
        entries: list[tuple[frozenset[str], frozenset[str], RegisteredSecurity]],
        *,
        anchor_ctx_id: str | None,
        anchor_members: frozenset[str],
    ) -> list[RegisteredSecurity]:
        """Order securities so the common-stock class sorts first.

        Tier 1 — SecurityType: COMMON always leads, regardless of anchor context.
        This overrides the previous anchor-only behaviour and prevents a sloppy
        filer (one who tags EntityCommonStockSharesOutstanding in the ADS context)
        from promoting the ADS into the flat ticker/exchange columns.

        Tier 2 — anchor match: among COMMON securities, the one sharing a context
        (or dimension member) with EntityCommonStockSharesOutstanding sorts first.
        The dimensionless↔dimensionless pairing rule still applies: a dimensionless
        anchor matches any dimensionless security group.

        Tiers 3–6 — UI convenience order for the remaining (non-primary) securities:
        ADS < PREFERRED < WARRANT < DEBT < OTHER by type; listed before unlisted;
        US-resolvable exchanges before home-country; document order as stable fallback.

        Edge case: if no COMMON security exists (e.g. filer registers only preferred
        or notes), the best available by the remaining keys fills the flat columns.
        """

        def sort_key(
            indexed: tuple[int, tuple[frozenset[str], frozenset[str], RegisteredSecurity]],
        ) -> tuple:
            idx, (ctx_ids, members, sec) = indexed
            is_common = (
                (anchor_ctx_id is not None and anchor_ctx_id in ctx_ids)
                or bool(members & anchor_members)
                # The typical single-entity pattern: shares outstanding in a
                # dimensionless instant context, cover-page DEI facts in a
                # dimensionless duration context. A dimensionless anchor pairs
                # with the dimensionless (entity-level) security group.
                or (anchor_ctx_id is not None and not members)
            )
            return (
                sec.security_type != SecurityType.COMMON,  # COMMON first (tier 1)
                not is_common,  # anchor-matched COMMON first (tier 2)
                _TYPE_ORDER[sec.security_type],  # ADS < PREFERRED < WARRANT < DEBT < OTHER
                not sec.ticker,  # listed before unlisted
                not _is_us_exchange(sec.exchange),  # US listings before home-country
                idx,  # stable: document order
            )

        return [sec for _, (_, _, sec) in sorted(enumerate(entries), key=sort_key)]

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
