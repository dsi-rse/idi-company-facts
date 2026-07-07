"""Tests for CompanyFactsPipeline.load_input and _select_primary_document."""

from datetime import date

import pytest
from idi_ftm2j_shared.types import ScrapedDocument, ScrapedFiling
from pytest_mock import MockerFixture

from idi_company_facts.pipeline import CompanyFactsPipeline
from idi_company_facts.types import PipelineConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(
    doc_type: str = "10-K",
    seq: str = "1",
    s3_key: str = "sec/test.htm",
    url: str = "https://sec.gov/test.htm",
) -> ScrapedDocument:
    """Return a minimal ScrapedDocument for use in tests."""
    return ScrapedDocument(filename="test.htm", url=url, type=doc_type, seq=seq, s3_key=s3_key)


def make_manifest(**kwargs: object) -> ScrapedFiling:
    """Return a ScrapedFiling with sensible defaults; override via kwargs."""
    defaults: dict[str, object] = dict(
        cik="1234567890",
        accession_number="0001234567-24-000001",
        form_type="10-K",
        filing_date="2024-01-15",
        last_scraped_at="2024-01-16T00:00:00",
        index_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1234567890",
        company_name="Test Corp",
        report_date="2023-12-31",
        failure_reason="",
        documents=[make_doc()],
    )
    defaults.update(kwargs)
    return ScrapedFiling(**defaults)


@pytest.fixture()
def config(tmp_path: pytest.TempPathFactory) -> PipelineConfig:
    """Return a minimal PipelineConfig pointing at a temp failure file."""
    return PipelineConfig(
        sec_bucket="test-bucket",
        output_file=str(tmp_path / "output.parquet"),
        failure_file=str(tmp_path / "failures.json"),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )


@pytest.fixture()
def pipeline(config: PipelineConfig) -> CompanyFactsPipeline:
    """Return a CompanyFactsPipeline using the temp config."""
    return CompanyFactsPipeline(config)


# ---------------------------------------------------------------------------
# _select_primary_document
# ---------------------------------------------------------------------------


class TestSelectPrimaryDocument:
    """Tests for the typed primary-document selection logic."""

    def test_picks_standard_10k(self, pipeline: CompanyFactsPipeline) -> None:
        """Selects a document whose type is exactly '10-K'."""
        manifest = make_manifest(documents=[make_doc(doc_type="10-K", seq="1")])
        result = pipeline._select_primary_document(manifest)
        assert result is not None
        assert result.type == "10-K"

    def test_handles_10k_no_dash(self, pipeline: CompanyFactsPipeline) -> None:
        """'10K' (no hyphen) is matched by the regex."""
        manifest = make_manifest(documents=[make_doc(doc_type="10K", seq="1")])
        assert pipeline._select_primary_document(manifest) is not None

    def test_handles_10k_a(self, pipeline: CompanyFactsPipeline) -> None:
        """'10-K/A' (amendment) is matched by the regex."""
        manifest = make_manifest(documents=[make_doc(doc_type="10-K/A", seq="1")])
        assert pipeline._select_primary_document(manifest) is not None

    def test_handles_10kt(self, pipeline: CompanyFactsPipeline) -> None:
        """'10-KT' (transition report) is matched by the regex."""
        manifest = make_manifest(documents=[make_doc(doc_type="10-KT", seq="1")])
        assert pipeline._select_primary_document(manifest) is not None

    def test_handles_10kt_a(self, pipeline: CompanyFactsPipeline) -> None:
        """'10-KT/A' (transition amendment) is matched by the regex."""
        manifest = make_manifest(documents=[make_doc(doc_type="10-KT/A", seq="1")])
        assert pipeline._select_primary_document(manifest) is not None

    def test_returns_none_when_no_primary_doc(self, pipeline: CompanyFactsPipeline) -> None:
        """Non-10-K document types (exhibits, graphics, XML) return None."""
        manifest = make_manifest(
            documents=[
                make_doc(doc_type="EX-21.1", seq="1"),
                make_doc(doc_type="GRAPHIC", seq="2"),
                make_doc(doc_type="XML", seq="3"),
            ]
        )
        assert pipeline._select_primary_document(manifest) is None


# ---------------------------------------------------------------------------
# load_input
# ---------------------------------------------------------------------------


class TestLoadInput:
    """Tests for manifest iteration, failure handling, and Filing construction."""

    def test_skips_upstream_failures(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Manifests with a non-empty failure_reason are skipped; no failure recorded."""
        failing = make_manifest(failure_reason="scraper timed out", documents=[])
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([failing])
        )

        filings = pipeline.load_input()

        assert len(filings) == 0
        assert pipeline.stats.total_filings == 1
        assert pipeline.stats.failed_filings == 1
        assert pipeline.stats.failed_primary_docs == 0
        assert (failing.cik, failing.accession_number) not in pipeline.failures

    def test_records_missing_document_failure(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """No typed primary doc → MISSING_DOCUMENT recorded and filing excluded."""
        no_doc = make_manifest(documents=[make_doc(doc_type="EX-21.1")])
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([no_doc])
        )

        filings = pipeline.load_input()

        assert len(filings) == 0
        assert pipeline.stats.failed_primary_docs == 1
        assert (no_doc.cik, no_doc.accession_number) in pipeline.failures

    def test_filing_field_mapping(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """All Filing fields are populated correctly from the manifest and selected doc."""
        manifest = make_manifest(
            cik="9876543210",
            accession_number="0009876543-24-000001",
            form_type="10-K",
            filing_date="2024-03-15",
            company_name="ACME Corp",
            documents=[
                make_doc(
                    doc_type="10-K", seq="1", s3_key="sec/10k.htm", url="https://sec.gov/10k.htm"
                )
            ],
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([manifest])
        )

        filings = pipeline.load_input()

        assert len(filings) == 1
        f = filings[0]
        assert f.cik == "9876543210"
        assert f.accession_number == "0009876543-24-000001"
        assert f.form_type == "10-K"
        assert f.filing_date == date(2024, 3, 15)
        assert f.company_name == "ACME Corp"
        assert f.primary_s3_key == "sec/10k.htm"
        assert f.primary_url == "https://sec.gov/10k.htm"

    def test_total_primary_docs_reflects_loaded_count(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """total_primary_docs equals the number of filings with a primary document."""
        manifests = [
            make_manifest(cik=str(i), accession_number=f"000000000{i}-24-000001") for i in range(3)
        ]
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter(manifests)
        )

        filings = pipeline.load_input()

        assert len(filings) == 3
        assert pipeline.stats.total_primary_docs == 3

    def test_total_filings_counts_all_manifests(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """total_filings is incremented for every manifest regardless of its state."""
        good = make_manifest(cik="111", accession_number="0000000001-24-000001")
        failed_upstream = make_manifest(
            cik="222",
            accession_number="0000000002-24-000001",
            failure_reason="scraper error",
            documents=[],
        )
        no_doc = make_manifest(
            cik="333",
            accession_number="0000000003-24-000001",
            documents=[make_doc(doc_type="EX-21.1")],
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([good, failed_upstream, no_doc]),
        )

        pipeline.load_input()

        assert pipeline.stats.total_filings == 3
        assert pipeline.stats.failed_filings == 1
        assert pipeline.stats.failed_primary_docs == 1
        assert pipeline.stats.total_primary_docs == 1


# ---------------------------------------------------------------------------
# run() — failure flushing and lifecycle
# ---------------------------------------------------------------------------


class TestRun:
    """Tests for Pipeline.run() lifecycle behaviour (corporate structure pattern).

    CompanyFactsPipeline.run() calls super().run() in a try/finally that flushes
    failures.  The base Pipeline.run() only calls process() when load_input()
    returns a non-empty list, so tests that want to exercise the process() stub
    must provide at least one valid primary-document manifest.
    """

    def test_no_filings_completes_without_calling_process(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """run() completes normally when load_input returns an empty list."""
        failing = make_manifest(failure_reason="scraper timed out", documents=[])
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([failing]),
        )

        pipeline.run()  # must not raise

    def test_missing_doc_failure_flushed_on_run(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """A MISSING_DOCUMENT failure recorded in load_input is persisted after run()."""
        no_doc = make_manifest(documents=[make_doc(doc_type="EX-21.1")])
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([no_doc]),
        )

        pipeline.run()  # empty filings → process() never called

        assert (no_doc.cik, no_doc.accession_number) in pipeline.failures

    def test_process_raises_when_filings_present(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """process() is called (and raises NotImplementedError) when filings are found."""
        good = make_manifest()
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([good]),
        )

        with pytest.raises(NotImplementedError):
            pipeline.run()

    def test_failures_flushed_even_when_process_raises(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Failures from load_input are flushed even when process() later raises.

        Provide both a no-primary-doc manifest (records MISSING_DOCUMENT) and a
        good manifest (causes process() to be invoked).  The MISSING_DOCUMENT
        failure must survive the NotImplementedError from the stub.
        """
        no_doc = make_manifest(
            cik="1111111111",
            accession_number="0001111111-24-000001",
            documents=[make_doc(doc_type="EX-21.1")],
        )
        good = make_manifest(
            cik="2222222222",
            accession_number="0002222222-24-000001",
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([no_doc, good]),
        )

        with pytest.raises(NotImplementedError):
            pipeline.run()

        assert (no_doc.cik, no_doc.accession_number) in pipeline.failures


# ---------------------------------------------------------------------------
# display_stats
# ---------------------------------------------------------------------------


class TestDisplayStats:
    """Tests for CompanyFactsPipeline.display_stats()."""

    def test_logs_filing_counts(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Filing stat values appear in the logged output."""
        pipeline.stats.increment("total_filings", 10)
        pipeline.stats.increment("failed_filings", 2)
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline.display_stats()

        logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "10" in logged
        assert "2" in logged

    def test_logs_primary_doc_counts(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Primary document stat values appear in the logged output."""
        pipeline.stats.increment("total_primary_docs", 7)
        pipeline.stats.increment("failed_primary_docs", 1)
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline.display_stats()

        logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "7" in logged
        assert "1" in logged

    def test_logs_section_headers(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Section headers and separators appear in the output."""
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline.display_stats()

        logged_args = [call.args[0] for call in mock_logger.info.call_args_list]
        assert any("Filings" in arg for arg in logged_args)
        assert any("Primary Documents" in arg for arg in logged_args)
        assert any("=" in arg for arg in logged_args)
