"""Data types for the company facts pipeline."""

import datetime
import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

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


class SecurityType(StrEnum):
    """high-level classification of a registered security."""

    COMMON = "common"
    ADS = "ads"
    PREFERRED = "preferred"
    DEBT = "debt"
    WARRANT = "warrant"
    OTHER = "other"


@dataclass(frozen=True)
class Context:
    """An iXBRL reporting context."""

    context_id: str
    instant: datetime.date | None
    start: datetime.date | None
    end: datetime.date | None
    has_dimensions: bool
    dimension_members: frozenset[str] = frozenset()


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
    multiple_registered_securities: int = 0
    recovered_parse: int = 0

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
    and dei:SecurityExchangeName respectively. Any field may be empty — e.g.
    registered debt securities often carry a title and exchange but no
    conventional trading symbol.
    """

    security_name: str = ""
    ticker: str = ""
    exchange: str = ""
    security_type: SecurityType = SecurityType.OTHER


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
    # All registered securities found on the cover page, ranked so the
    # common-stock class (anchored to EntityCommonStockSharesOutstanding) is first.
    registered_securities: list[RegisteredSecurity] = field(default_factory=list)
    market_value: str = ""
    market_value_as_of_date: date | None = None
    market_value_currency: str = ""
    shares_outstanding: str = ""
    shares_outstanding_as_of_date: date | None = None
    is_shell_company: str = ""
    revenue: str = ""
    revenue_as_of_date: date | None = None
    revenue_currency: str = ""
    last_accessed: datetime.datetime | None = None
