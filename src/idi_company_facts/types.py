"""Data types for the company facts pipeline."""

import datetime
import threading
from dataclasses import dataclass
from datetime import date

# Single source of truth for which filings carry a primary document
TARGET_FORM_TYPES = ["10-K", "10-K/A", "10-KT", "10-KT/A"]


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
    """Configuration for the Company Facts pipeline."""

    sec_bucket: str
    output_file: str
    failure_file: str
    start_date: datetime.date
    end_date: datetime.date
    failure_flush_every: int = 50
    num_workers: int = 10


@dataclass
class PipelineStats:
    """Thread-safe counters tracking pipeline progress and failures."""

    total_filings: int = 0
    failed_filings: int = 0
    total_primary_docs: int = 0
    failed_primary_docs: int = 0
    timeout_primary_docs: int = 0
    extracted_documents: int = 0

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
    tickers: str = ""
    securities: str = ""
    exchanges: str = ""
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
