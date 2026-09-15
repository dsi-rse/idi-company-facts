"""Data types for the company facts pipeline."""

import datetime
import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Single source of truth for which filings carry a primary document
TARGET_FORM_TYPES = [
    # Domestic filer
    "10-K",
    "10-K/A",
    "10-KT",
    "10-KT/A",
    # Foreign filer
    "20-F",
    "20-F/A",
    "20FR12B",
    "20FR12B/A",
    "20FR12G",
    "20FR12G/A",
]


@dataclass(frozen=True)
class Dimension:
    """One (axis, member) pair from an iXBRL context dimension.

    Both explicit and typed members are represented identically.  The only
    difference is the member value: for explicit members it is a QName
    referencing a named concept in a taxonomy (``prefix:localName`` format);
    for typed members it is a free-form string constrained by an XML Schema
    type.
    """

    axis: str  # axis QName, e.g. "us-gaap:StatementClassOfStockAxis"
    member: str  # taxonomy concept QName (explicit) or free-form string (typed)
    is_typed: bool = False


@dataclass(frozen=True)
class Context:
    """An iXBRL reporting context."""

    context_id: str
    as_of_date: datetime.date | None
    start: datetime.date | None
    end: datetime.date | None
    has_dimensions: bool
    dimensions: frozenset[Dimension] = frozenset()


@dataclass(frozen=True)
class Fact:
    """A single iXBRL fact with a normalized concept name and typed value."""

    concept: str  # canonical "prefix:LocalName" e.g. "dei:DocumentPeriodEndDate"
    value: Decimal | bool | datetime.date | str
    context: Context
    unit: str | None  # ISO 4217 currency, "shares", or None


@dataclass(frozen=True, slots=True)
class Filing:
    """One SEC 10-K filing with its metadata and primary document URL."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: date
    primary_s3_key: str  # S3 key for the primary 10-K document
    primary_url: str  # original SEC EDGAR URL (feeds output url column)
    company_name: str = ""


@dataclass
class PipelineConfig:
    """Configuration for the Company Facts pipeline.

    Exactly one discovery mode applies, checked in this order: ``ciks``
    (latest target filing per CIK), ``allow_list`` (explicit filings), or the
    ``start_date``/``end_date`` window. The dates may be None only in the
    first two modes.
    """

    sec_bucket: str
    output_file: str
    failure_file: str
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    failure_flush_every: int = 50
    num_workers: int = 10
    allow_list: dict[datetime.date, frozenset[tuple[str, str]]] | None = None
    form_types: tuple[str, ...] | None = None
    ciks: tuple[str, ...] | None = None


@dataclass
class CikOverrideSummary:
    """Per-CIK outcome of a --ciks-override run, for the end-of-run report."""

    cik: str  # normalized (no leading zeros)
    form_type: str = ""
    filing_date: str = ""  # ISO YYYY-MM-DD, as stored in the manifest
    accession_number: str = ""
    disposition: str = "pending"


@dataclass
class PipelineStats:
    """Thread-safe counters tracking pipeline progress and failures."""

    # load_input counters
    total_filings: int = 0
    skipped_filings: int = 0  # already present in the output parquet (resume)
    failed_filings: int = 0
    total_primary_docs: int = 0
    failed_primary_docs: int = 0
    # process counters
    queued_documents: int = 0
    documents_fetched: int = 0
    extracted_documents: int = 0
    parse_failures: int = 0
    storage_errors: int = 0
    filings_processed: int = 0
    # extraction quality counters
    missing_period_end: int = 0
    no_revenue_concept: int = 0
    ambiguous_revenue: int = 0
    ambiguous_shares_outstanding: int = 0
    multiple_registered_securities: int = 0
    recovered_parse: int = 0
    unmatched_explicit: int = 0
    unmatched_typed_member: int = 0

    def __post_init__(self) -> None:
        """Initialize the pipeline stats."""
        self._lock = threading.Lock()

    def increment(self, field_name: str, n: int = 1) -> None:
        """Atomically add n to the named counter.

        Args:
            field_name: The field to increment.
            n: Amount to add to the field. Default is 1.
        """
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + n)


@dataclass(frozen=True, slots=True)
class RegisteredSecurity:
    """One security registered under Section 12(b), from the 10K/20F cover page.

    Fields map to the DEI concepts dei:Security12bTitle, dei:TradingSymbol,
    and dei:SecurityExchangeName respectively. Any field may be empty.

    ``dimensioned_members`` holds all explicit (axis, member) pairs from the
    security's XBRL context, formatted as ``"axis=member"`` and joined by
    ``"; "`` in sorted order.  Empty for dimensionless securities and
    typed-member stub rows.

    ``shares_outstanding`` and ``shares_outstanding_as_of`` are populated by
    the attribution step when a dimensioned EntityCommonStockSharesOutstanding
    fact is matched to this security.  Not populated for dimensionless
    securities even when a dimensionless share count exists — that count appears
    in ``CompanyFactsRecord.shares_outstanding`` instead.  A missing value here
    does not imply the security has no share count.
    """

    security_name: str = ""
    ticker: str = ""
    exchange: str = ""
    dimensioned_members: str = ""
    shares_outstanding: str = ""
    shares_outstanding_as_of: datetime.date | None = None


@dataclass
class CompanyFactsRecord:
    """Company facts extracted from a 10-K inline XBRL document."""

    company_cik: str
    accession_number: str
    form_type: str
    doc_type: str
    primary_url: str
    filing_date: date | None = None
    report_date: date | None = None  # Fiscal year end of the report
    company_name: str = ""
    # All registered securities found on the cover page in extraction order
    # (dimensionless first, then dimensioned, then appended unmatched share rows).
    registered_securities: list[RegisteredSecurity] = field(default_factory=list)
    market_value: str = ""
    market_value_as_of_date: date | None = None
    market_value_currency: str = ""
    # Dimensionless EntityCommonStockSharesOutstanding fact (entity-level count).
    # This scalar has no guaranteed relationship to per-class counts in
    # registered_securities: it may be a total, it may cover only one class, or
    # it may refer to a non-publicly-traded class not broken out separately.
    shares_outstanding: str = ""
    shares_outstanding_as_of_date: date | None = None
    is_shell_company: str = ""
    revenue: str = ""
    revenue_as_of_date: date | None = None
    revenue_currency: str = ""
    last_accessed: datetime.datetime | None = None
