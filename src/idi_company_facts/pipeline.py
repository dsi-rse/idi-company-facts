"""Pipeline for the company facts processor."""

# Standard application imports
import datetime
import queue
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict

# Third party imports
import pandas as pd
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.sec import ScrapedDocument, ScrapedFiling, iter_filings_by_form_type
from idi_ftm2j_shared.storage import load_content

# Application imports
from idi_company_facts.extractor import CompanyFactsExtractor
from idi_company_facts.failures import CompanyFactsFailureClassifier, FailureType
from idi_company_facts.types import (
    TARGET_FORM_TYPES,
    CompanyFactsRecord,
    Filing,
    PipelineConfig,
    PipelineStats,
)
from idi_company_facts.xbrl.parser import InlineXbrlDocument, NotInlineXbrlError, XbrlParseError


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
    _LOG_EVERY = 100

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
        self.extractor = CompanyFactsExtractor()

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

    def _extract_worker(
        self,
        work_queue: queue.Queue,
        records: list[CompanyFactsRecord],
        records_lock: threading.Lock,
        total: int,
    ) -> None:
        """Worker thread that fetches, parses, and extracts facts for one filing.

        Runs as a daemon thread, consuming :class:`Filing` objects from
        ``work_queue`` and appending extracted :class:`CompanyFactsRecord` results
        directly to the shared ``records`` list under ``records_lock``.
        Failures are recorded inside :meth:`_process_one` so the worker loop
        always continues.

        Args:
            work_queue: Queue of :class:`Filing` objects to process.
            records: Shared list to which successfully extracted records are appended.
            records_lock: Lock protecting ``records``.
            total: Total number of filings queued, used for progress logging.
        """
        while True:
            filing = work_queue.get()
            try:
                new_records = self._process_one(filing)
                with records_lock:
                    records.extend(new_records)
                self.stats.increment("filings_processed")
                n = self.stats.filings_processed  # approximate read outside the lock
                if n % self._LOG_EVERY == 0:
                    self.logger.info(
                        "progress: %d/%d filings processed (%d extracted, %d failed)",
                        n,
                        total,
                        self.stats.extracted_documents,
                        self.stats.parse_failures + self.stats.storage_errors,
                    )
            finally:
                work_queue.task_done()

    def process(self, input_list: list[Filing]) -> list[CompanyFactsRecord]:
        """Fetch each filing's primary document from S3 and extract company facts.

        Extraction runs across :attr:`~PipelineConfig.num_workers` daemon threads.
        The main thread feeds filings into a bounded work queue; workers append
        extracted records directly to the shared result list.

        Args:
            input_list: Filings returned by :meth:`load_input`.

        Returns:
            Extracted :class:`CompanyFactsRecord` objects (failures are excluded).
        """
        work_queue: queue.Queue = queue.Queue(maxsize=self.config.num_workers * 2)
        records: list[CompanyFactsRecord] = []
        records_lock = threading.Lock()

        self.logger.info(
            "starting process stage: %d filings queued, %d workers",
            len(input_list),
            self.config.num_workers,
        )

        extract_workers = [
            threading.Thread(
                target=self._extract_worker,
                args=(work_queue, records, records_lock, len(input_list)),
                daemon=True,
                name=f"extract-worker-{i}",
            )
            for i in range(self.config.num_workers)
        ]
        for worker in extract_workers:
            worker.start()

        for filing in input_list:
            work_queue.put(filing)
            self.stats.increment("queued_documents")

        work_queue.join()

        return records

    def _process_one(self, filing: Filing) -> list[CompanyFactsRecord]:
        """Fetch, parse, and extract facts from one filing's primary document.

        Returns one record per security class; empty list on any failure.
        """
        s3_url = filing.primary_s3_key  # manifest s3_key is already a full s3:// URL
        try:
            html_bytes = load_content(s3_url)
            if not html_bytes:
                self.failures.add((filing.cik, filing.accession_number), FailureType.EMPTY_DOCUMENT)
                self.stats.increment("storage_errors")
                return []
            self.stats.increment("documents_fetched")
            doc = InlineXbrlDocument(html_bytes)
            if doc.used_recovery_parser:
                self.stats.increment("recovered_parse")
            records, extraction_failures = self.extractor.extract(filing, doc)
            for failure_type in extraction_failures:
                self.failures.add((filing.cik, filing.accession_number), failure_type)
                if failure_type == FailureType.MISSING_PERIOD_END:
                    self.stats.increment("missing_period_end")
                elif failure_type == FailureType.NO_REVENUE_CONCEPT:
                    self.stats.increment("no_revenue_concept")
                elif failure_type == FailureType.AMBIGUOUS_REVENUE:
                    self.stats.increment("ambiguous_revenue")
            self.stats.increment("extracted_documents", len(records))
            return records
        except NotInlineXbrlError:
            self.failures.add((filing.cik, filing.accession_number), FailureType.NO_INLINE_XBRL)
            self.stats.increment("parse_failures")
            return []
        except XbrlParseError:
            self.failures.add((filing.cik, filing.accession_number), FailureType.MALFORMED_XBRL)
            self.stats.increment("parse_failures")
            return []
        except Exception:
            self.logger.exception("Unexpected error processing %s", filing.accession_number)
            self.failures.add((filing.cik, filing.accession_number), FailureType.STORAGE_ERROR)
            self.stats.increment("storage_errors")
            return []

    def save_output(self, processed_list: list[CompanyFactsRecord]) -> None:
        """Persist extracted records to the configured output parquet file.

        Deduplicates within the current run on (company_cik, accession_number)
        and writes to the output path, overwriting any existing file.

        Args:
            processed_list: Records returned by :meth:`process`.
        """
        if not processed_list:
            self.logger.info("no records extracted; skipping output write")
            return
        df = pd.DataFrame([asdict(r) for r in processed_list])
        df = df.drop_duplicates(subset=["company_cik", "accession_number", "ticker"])
        df.to_parquet(self.config.output_file, index=False)
        self.logger.info("Saved %d records to %s", len(df), self.config.output_file)

    def display_stats(self) -> None:
        """Log a formatted summary of pipeline statistics on completion."""
        self.logger.info("=" * 40)
        self.logger.info("Pipeline Stats")
        self.logger.info("=" * 40)
        self.logger.info("  Filings")
        self.logger.info("    Total:              %d", self.stats.total_filings)
        self.logger.info("    Failed upstream:    %d", self.stats.failed_filings)
        self.logger.info("    No primary doc:     %d", self.stats.failed_primary_docs)
        self.logger.info("  Primary Documents")
        self.logger.info("    Loaded:             %d", self.stats.total_primary_docs)
        self.logger.info("    Queued:             %d", self.stats.queued_documents)
        self.logger.info("    Fetched from S3:    %d", self.stats.documents_fetched)
        self.logger.info("    Extracted:          %d", self.stats.extracted_documents)
        self.logger.info("    Parse failures:     %d", self.stats.parse_failures)
        self.logger.info("    Storage errors:     %d", self.stats.storage_errors)
        self.logger.info("    Recovery parses:    %d", self.stats.recovered_parse)
        self.logger.info("  Extraction Quality")
        self.logger.info("    Missing period end: %d", self.stats.missing_period_end)
        self.logger.info("    No revenue concept: %d", self.stats.no_revenue_concept)
        self.logger.info("    Ambiguous revenue:  %d", self.stats.ambiguous_revenue)
        self.logger.info("=" * 40)
