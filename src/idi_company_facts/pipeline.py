"""Pipeline for the company facts processor."""

# Standard application imports
import datetime
import re
from abc import ABC, abstractmethod

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.sec import ScrapedDocument, ScrapedFiling, iter_filings_by_form_type

# Application imports
from idi_company_facts.failures import CompanyFactsFailureClassifier, FailureType
from idi_company_facts.types import (
    TARGET_FORM_TYPES,
    CompanyFactsRecord,
    Filing,
    PipelineConfig,
    PipelineStats,
)


class Pipeline(ABC):
    """Baseline class for processing piplines."""

    def __init__(
        self,
        config: PipelineConfig,
    ) -> None:
        """Initialize the pipeline with config.

        Args:
            config: Pipeline configuration including input/output paths and tuning
                parameters.
        """
        self.config = config
        self.stats = PipelineStats()
        self.logger = get_logger(type(self).__name__)

    @abstractmethod
    def load_input(self) -> list:
        """Load input data and return a list of items to process.

        Returns:
            List of input items. The concrete element type is defined by each
            subclass (e.g. ``list[Filing]``).
        """
        ...

    @abstractmethod
    def process(self, input_list: list) -> list:
        """Process each item in the input list and return a list of results.

        Args:
            input_list: Items returned by :meth:`load_input`.

        Returns:
            List of processed results. The concrete element type is defined by
            each subclass (e.g. ``list[CompanyFactsRecord]``).
        """
        ...

    @abstractmethod
    def save_output(self, processed_list: list) -> None:
        """Persist the processed results to the configured output destination.

        Args:
            processed_list: Items returned by :meth:`process`.

        Returns:
            None
        """
        ...

    @abstractmethod
    def display_stats(self) -> None:
        """Log or display a summary of pipeline processing statistics.

        Returns:
            None
        """

    def run(self) -> None:
        """Execute the full pipeline: load → process → save → display stats.

        Calls :meth:`load_input`, :meth:`process`, :meth:`save_output`, and
        :meth:`display_stats` in sequence, then logs the total elapsed time.

        Returns:
            None
        """
        start_time = datetime.datetime.now()

        input_data = self.load_input()
        self.logger.info("Located %d primary documents to process", len(input_data))

        if input_data:
            results = self.process(input_data)
            self.save_output(results)
            self.display_stats()
        else:
            self.logger.info("No primary documents found, skipping pipeline")

        end_time = datetime.datetime.now()
        self.logger.info("Elapsed time: %s", end_time - start_time)


class CompanyFactsPipeline(Pipeline):
    """Fetches 10-K primary documents from S3 and extracts company facts."""

    # Matches 10-K, 10K, 10-K/A, 10K/A, 10-KT, 10KT, 10-KT/A, 10KT/A — case-insensitive.
    _PRIMARY_TYPE_RE: re.Pattern[str] = re.compile(r"^10-?KT?(/A)?$", re.IGNORECASE)
    _LOG_EVERY = 5

    def __init__(self, config: PipelineConfig) -> None:
        """Initialise the pipeline with config and a failure registry.

        Args:
            config: Pipeline configuration including input/output paths,
                worker count, and failure flush threshold.
        """
        super().__init__(config)
        self.failures = FailureRegistry(
            config.failure_file,
            classifier=CompanyFactsFailureClassifier(),
            flush_every=config.failure_flush_every,
        )

    def run(self) -> None:
        """Run the pipeline, flushing any buffered failures on completion."""
        try:
            super().run()
        finally:
            self.failures.flush()

    def load_input(self) -> list[Filing]:
        """Load input data from the SEC and return a list of filings.

        Filings with no matching primary documents are recorded as
        ``MISSING_DOCUMENT`` failures and excluded from the returned list.

        Returns:
            A list of Filing objects that have associated primary documents
        """
        scraped_filings = iter_filings_by_form_type(
            form_types=TARGET_FORM_TYPES,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            bucket=self.config.sec_bucket,
            include_failures=True,
        )

        filings: list[Filing] = []
        for scraped_filing in scraped_filings:
            self.stats.increment("total_filings")

            if scraped_filing.failure_reason:
                self.stats.increment("failed_filings")
                continue  # scraper-side failure — nothing actionable on our end

            doc = self._select_primary_document(scraped_filing)
            if doc is None:
                self.stats.increment("failed_primary_docs")
                self.failures.add(
                    (scraped_filing.cik, scraped_filing.accession_number),
                    FailureType.MISSING_DOCUMENT,
                )
                continue

            filings.append(
                Filing(
                    cik=scraped_filing.cik,
                    accession_number=scraped_filing.accession_number,
                    form_type=scraped_filing.form_type,
                    filing_date=datetime.date.fromisoformat(scraped_filing.filing_date),
                    primary_s3_key=doc.s3_key,
                    primary_url=doc.url,
                    company_name=scraped_filing.company_name,
                )
            )

        self.stats.increment("total_primary_docs", len(filings))
        return filings

    @classmethod
    def _select_primary_document(cls, scraped_filing: ScrapedFiling) -> ScrapedDocument | None:
        """Return the primary 10-K document from the filing, or None if absent."""
        typed = [d for d in scraped_filing.documents if cls._PRIMARY_TYPE_RE.match(d.type or "")]
        return typed[0] if typed else None

    def process(self, input_list: list[Filing]) -> list[CompanyFactsRecord]:
        """Fetch and parse each filing's primary document; return extracted records.

        Not yet implemented — deferred to the XBRL extraction PR.
        """
        raise NotImplementedError

    def save_output(self, processed_list: list[CompanyFactsRecord]) -> None:
        """Persist records to the configured output parquet file.

        Not yet implemented — deferred to the XBRL extraction PR.
        """
        raise NotImplementedError

    def display_stats(self) -> None:
        """Log a formatted summary of pipeline statistics on completion."""
        self.logger.info("=" * 40)
        self.logger.info("Pipeline Stats")
        self.logger.info("=" * 40)
        self.logger.info("  Filings")
        self.logger.info("    Total:           %d", self.stats.total_filings)
        self.logger.info("    Failed upstream: %d", self.stats.failed_filings)
        self.logger.info("    No primary doc:  %d", self.stats.failed_primary_docs)
        self.logger.info("    Timeout:         %d", self.stats.timeout_primary_docs)
        self.logger.info("  Primary Documents")
        self.logger.info("    Loaded:          %d", self.stats.total_primary_docs)
        self.logger.info("    Extracted:       %d", self.stats.extracted_documents)
        self.logger.info("=" * 40)
