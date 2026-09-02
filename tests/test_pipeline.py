"""Tests for CompanyFactsPipeline.load_input and _select_primary_document."""

import datetime
import io
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from idi_ftm2j_shared.types import ScrapedDocument, ScrapedFiling
from pytest_mock import MockerFixture

from idi_company_facts.pipeline import CompanyFactsPipeline
from idi_company_facts.types import (
    CompanyFactsRecord,
    Filing,
    PipelineConfig,
    RegisteredSecurity,
    SecurityType,
)
from tests.conftest import make_ixbrl_bytes

# ── Shared iXBRL building blocks for process() tests ──────────────────────────

_INSTANT_CTX = """
<xbrli:context id="c-instant">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001234567</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2024-09-28</xbrli:instant></xbrli:period>
</xbrli:context>
"""
_USD_UNIT = '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'

_MINIMAL_IXBRL = make_ixbrl_bytes(
    contexts=_INSTANT_CTX,
    units=_USD_UNIT,
    facts='<p><ix:nonFraction name="dei:EntityPublicFloat" contextRef="c-instant" unitRef="USD" decimals="0">1000</ix:nonFraction></p>',
)


def _make_filing(i: int) -> Filing:
    """Return a minimal Filing with unique CIK and accession number."""
    return Filing(
        cik=str(i).zfill(10),
        accession_number=f"{str(i).zfill(10)}-24-000001",
        form_type="10-K",
        filing_date=datetime.date(2024, 1, 15),
        primary_s3_key=f"s3://bucket/filing_{i}.htm",
        primary_url=f"https://sec.gov/filing_{i}.htm",
        company_name=f"Corp {i}",
    )


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

    def test_picks_standard_20f(self, pipeline: CompanyFactsPipeline) -> None:
        """Selects a document whose type is exactly '20-F'."""
        manifest = make_manifest(documents=[make_doc(doc_type="20-F", seq="1")])
        result = pipeline._select_primary_document(manifest)
        assert result is not None
        assert result.type == "20-F"

    def test_handles_20f_a(self, pipeline: CompanyFactsPipeline) -> None:
        """'20-F/A' (amendment) is matched by the regex."""
        manifest = make_manifest(documents=[make_doc(doc_type="20-F/A", seq="1")])
        assert pipeline._select_primary_document(manifest) is not None

    def test_returns_none_when_no_primary_doc(self, pipeline: CompanyFactsPipeline) -> None:
        """Non-primary document types (exhibits, graphics, XML) return None."""
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

    def test_queries_filings_by_scraped_date(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """load_input passes search_by='scraped_date' to iter_filings_by_form_type."""
        mock_iter = mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([]),
        )
        pipeline.load_input()
        assert mock_iter.call_args.kwargs["search_by"] == "scraped_date"


# ---------------------------------------------------------------------------
# load_input — --ciks-override mode
# ---------------------------------------------------------------------------


def make_manifest_parquet(rows: list[tuple[str, str, str, str]]) -> bytes:
    """Serialize (form_type, filing_date, cik, accession_number) rows to parquet bytes."""
    df = pd.DataFrame(rows, columns=["form_type", "filing_date", "cik", "accession_number"])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


class TestCiksOverride:
    """Tests for latest-per-CIK selection and the override summary report."""

    def _override_pipeline(
        self,
        config: PipelineConfig,
        mocker: MockerFixture,
        manifest_rows: list[tuple[str, str, str, str]],
        ciks: tuple[str, ...],
    ) -> CompanyFactsPipeline:
        """Return a pipeline in override mode with a synthetic manifest parquet."""
        config.ciks = ciks
        manifest = make_manifest_parquet(manifest_rows)

        def fake_load_content(path: str) -> bytes:
            if path.endswith("manifest.parquet"):
                return manifest
            return Path(path).read_bytes() if Path(path).exists() else b""

        mocker.patch("idi_company_facts.pipeline.load_content", side_effect=fake_load_content)
        return CompanyFactsPipeline(config)

    def test_selects_latest_target_filing_per_cik(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """The max-filing_date target filing is selected; older ones are ignored."""
        rows = [
            ("10-K", "2023-03-01", "123", "0000000123-23-000001"),
            ("10-K", "2024-03-01", "123", "0000000123-24-000001"),
        ]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("123",))
        latest = make_manifest(
            cik="123", accession_number="0000000123-24-000001", filing_date="2024-03-01"
        )
        mock_iter = mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([latest])
        )

        filings = pipeline.load_input()

        assert [f.accession_number for f in filings] == ["0000000123-24-000001"]
        assert mock_iter.call_count == 1
        assert mock_iter.call_args.kwargs["search_by"] == "filing_date"
        assert mock_iter.call_args.kwargs["start_date"] == date(2024, 3, 1)
        assert mock_iter.call_args.kwargs["end_date"] == date(2024, 3, 1)

    def test_zero_padded_request_matches_unpadded_manifest(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """A zero-padded requested CIK resolves against the manifest's unpadded form."""
        rows = [("10-K", "2024-03-01", "123", "0000000123-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("0000000123",))
        latest = make_manifest(
            cik="123", accession_number="0000000123-24-000001", filing_date="2024-03-01"
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([latest])
        )

        filings = pipeline.load_input()

        assert len(filings) == 1
        assert "123" in pipeline.cik_report

    def test_non_target_forms_report_no_target_filing(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """A CIK with only non-target forms (e.g. 40-F) reports NO_TARGET_FILING_IN_MANIFEST."""
        rows = [("40-F", "2024-03-01", "123", "0000000123-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("123",))
        mock_iter = mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([])
        )

        filings = pipeline.load_input()

        assert filings == []
        assert pipeline.cik_report["123"].disposition == "NO_TARGET_FILING_IN_MANIFEST"
        mock_iter.assert_not_called()

    def test_cik_absent_from_manifest_reports_no_target_filing(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """A CIK with no manifest rows at all reports NO_TARGET_FILING_IN_MANIFEST."""
        rows = [("10-K", "2024-03-01", "999", "0000000999-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("123",))
        mocker.patch("idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([]))

        pipeline.load_input()

        assert pipeline.cik_report["123"].disposition == "NO_TARGET_FILING_IN_MANIFEST"

    def test_filing_already_in_output_is_skipped(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """A latest filing already present in the output parquet is not reprocessed."""
        pd.DataFrame(
            {"company_cik": ["0000000123"], "accession_number": ["0000000123-24-000001"]}
        ).to_parquet(config.output_file, index=False)
        rows = [("10-K", "2024-03-01", "123", "0000000123-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("123",))
        mock_iter = mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([])
        )

        filings = pipeline.load_input()

        assert filings == []
        assert pipeline.cik_report["123"].disposition == "already_in_output"
        mock_iter.assert_not_called()

    def test_process_one_reports_processed_disposition(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """A successfully extracted filing flips its CIK's disposition to processed."""
        rows = [("10-K", "2024-03-01", "1234567", "0001234567-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("1234567",))
        latest = make_manifest(
            cik="1234567", accession_number="0001234567-24-000001", filing_date="2024-03-01"
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([latest])
        )
        record = _make_record_with_securities([RegisteredSecurity(ticker="TST")])
        mocker.patch.object(pipeline.extractor, "extract", return_value=([record], []))
        mocker.patch(
            "idi_company_facts.pipeline.InlineXbrlDocument", return_value=mocker.MagicMock()
        )

        filings = pipeline.load_input()
        mocker.patch("idi_company_facts.pipeline.load_content", return_value=b"dummy")
        pipeline._process_one(filings[0])

        assert pipeline.cik_report["1234567"].disposition == "processed"

    def test_process_one_reports_failed_disposition(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """An empty primary document reports failed(empty_document) for its CIK."""
        rows = [("10-K", "2024-03-01", "1234567", "0001234567-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("1234567",))
        latest = make_manifest(
            cik="1234567", accession_number="0001234567-24-000001", filing_date="2024-03-01"
        )
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([latest])
        )

        filings = pipeline.load_input()
        # fake_load_content returns b"" for the (nonexistent) primary document path
        pipeline._process_one(filings[0])

        assert pipeline.cik_report["1234567"].disposition == "failed(empty_document)"

    def test_display_stats_logs_cik_report(
        self, config: PipelineConfig, mocker: MockerFixture
    ) -> None:
        """The override report table is logged with per-CIK dispositions."""
        rows = [("40-F", "2024-03-01", "123", "0000000123-24-000001")]
        pipeline = self._override_pipeline(config, mocker, rows, ciks=("123",))
        mocker.patch("idi_company_facts.pipeline.iter_filings_by_form_type", return_value=iter([]))
        pipeline.load_input()
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline.display_stats()

        logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "CIK Override Report" in logged
        assert "NO_TARGET_FILING_IN_MANIFEST" in logged


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

    def test_process_called_when_filings_present(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """process() is invoked for each valid filing returned by load_input."""
        good = make_manifest()
        mocker.patch(
            "idi_company_facts.pipeline.iter_filings_by_form_type",
            return_value=iter([good]),
        )
        process_one = mocker.patch.object(pipeline, "_process_one", return_value=[])

        pipeline.run()

        process_one.assert_called_once()

    def test_failures_flushed_even_when_process_raises(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """Failures from load_input are persisted even when process() raises.

        Provide both a no-primary-doc manifest (records MISSING_DOCUMENT) and a
        good manifest (causes process() to be invoked).  The MISSING_DOCUMENT
        failure must be flushed despite the RuntimeError from process.
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
        mocker.patch.object(pipeline, "process", side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
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

    def test_logs_extraction_quality_counts(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """New extraction quality counters appear in display_stats output."""
        pipeline.stats.increment("missing_period_end", 3)
        pipeline.stats.increment("no_revenue_concept", 5)
        pipeline.stats.increment("ambiguous_revenue", 1)
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline.display_stats()

        logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "3" in logged
        assert "5" in logged
        assert "1" in logged
        assert any("Extraction Quality" in str(c) for c in mock_logger.info.call_args_list)


# ---------------------------------------------------------------------------
# process() — concurrency and progress logging
# ---------------------------------------------------------------------------


class TestProcess:
    """Tests for CompanyFactsPipeline.process() threading and progress logging."""

    def test_no_lost_or_duplicate_records(
        self,
        pipeline: CompanyFactsPipeline,
        mocker: MockerFixture,
    ) -> None:
        """Exact record count is preserved under concurrent extraction (Items 3+6+7)."""
        mocker.patch("idi_company_facts.pipeline.load_content", return_value=_MINIMAL_IXBRL)
        n = 50
        filings = [_make_filing(i) for i in range(n)]

        # Use 4 workers to stress thread-safety of the direct-append pattern
        pipeline.config.num_workers = 4
        # Re-create workers list is part of process(), not init — just update config
        records = pipeline.process(filings)

        assert len(records) == n

    def test_process_logs_stage_start(
        self,
        pipeline: CompanyFactsPipeline,
        mocker: MockerFixture,
    ) -> None:
        """process() logs total filings and worker count at stage start."""
        mocker.patch("idi_company_facts.pipeline.load_content", return_value=_MINIMAL_IXBRL)
        mock_logger = mocker.patch.object(pipeline, "logger")
        filings = [_make_filing(0)]

        pipeline.process(filings)

        start_calls = [
            c for c in mock_logger.info.call_args_list if "starting process stage" in str(c)
        ]
        assert start_calls, "expected a 'starting process stage' log line"

    def test_process_logs_progress_at_log_every(
        self,
        pipeline: CompanyFactsPipeline,
        mocker: MockerFixture,
    ) -> None:
        """A progress line is emitted after every _LOG_EVERY filings processed."""
        mocker.patch("idi_company_facts.pipeline.load_content", return_value=_MINIMAL_IXBRL)
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline._LOG_EVERY = 3  # override for test
        filings = [_make_filing(i) for i in range(4)]

        pipeline.process(filings)

        progress_calls = [c for c in mock_logger.info.call_args_list if "progress:" in str(c)]
        assert progress_calls, "expected at least one progress log line"


# ---------------------------------------------------------------------------
# save_output — parquet column structure
# ---------------------------------------------------------------------------


def _make_record_with_securities(
    securities: list[RegisteredSecurity],
) -> CompanyFactsRecord:
    return CompanyFactsRecord(
        company_cik="0001234567",
        accession_number="0001234567-24-000001",
        form_type="10-K",
        doc_type="10-K",
        primary_url="https://sec.gov/test.htm",
        registered_securities=securities,
    )


class TestSaveOutput:
    """Tests for CompanyFactsPipeline.save_output() column structure."""

    def test_single_security_flattened_correctly(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """A record with one security produces pipe-delimited columns with a single entry."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        sec = RegisteredSecurity(
            security_name="Common Stock",
            ticker="AAPL",
            exchange="NASDAQ",
            security_type=SecurityType.COMMON,
        )
        record = _make_record_with_securities([sec])

        pipeline.save_output([record])

        df = pd.read_parquet(pipeline.config.output_file)
        assert df["all_tickers"].iloc[0] == "AAPL"
        assert df["all_security_names"].iloc[0] == "Common Stock"
        assert df["all_exchanges"].iloc[0] == "NASDAQ"
        assert df["all_security_types"].iloc[0] == "common"

    def test_multiple_securities_pipe_delimited(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Multiple securities produce pipe-delimited values, common stock first."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        common = RegisteredSecurity(
            security_name="Ordinary Shares",
            ticker="ORD",
            exchange="Euronext Paris",
            security_type=SecurityType.COMMON,
        )
        ads = RegisteredSecurity(
            security_name="American Depositary Shares",
            ticker="ADSX",
            exchange="NYSE",
            security_type=SecurityType.ADS,
        )
        record = _make_record_with_securities([common, ads])

        pipeline.save_output([record])

        df = pd.read_parquet(pipeline.config.output_file)
        assert df["all_tickers"].iloc[0] == "ORD | ADSX"
        assert df["all_security_names"].iloc[0] == "Ordinary Shares | American Depositary Shares"
        assert df["all_exchanges"].iloc[0] == "Euronext Paris | NYSE"
        assert df["all_security_types"].iloc[0] == "common | ads"

    def test_registered_securities_column_absent(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """The raw registered_securities list column is not written to the parquet output."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        record = _make_record_with_securities(
            [
                RegisteredSecurity(
                    ticker="AAPL", exchange="NASDAQ", security_type=SecurityType.COMMON
                )
            ]
        )

        pipeline.save_output([record])

        df = pd.read_parquet(pipeline.config.output_file)
        assert "registered_securities" not in df.columns

    def test_three_security_types_pipe_delimited(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Three-security fixture produces 'common | ads | debt' in all_security_types."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        secs = [
            RegisteredSecurity(ticker="ORD", security_type=SecurityType.COMMON),
            RegisteredSecurity(ticker="ADSX", security_type=SecurityType.ADS),
            RegisteredSecurity(ticker="ORD27", security_type=SecurityType.DEBT),
        ]
        record = _make_record_with_securities(secs)

        pipeline.save_output([record])

        df = pd.read_parquet(pipeline.config.output_file)
        assert df["all_security_types"].iloc[0] == "common | ads | debt"

    def test_override_mode_merges_with_existing_output(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """In --ciks-override mode existing rows are kept and new rows win collisions."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        kept = _make_record_with_securities([RegisteredSecurity(ticker="OLD")])
        kept.accession_number = "0001234567-23-000001"
        stale = _make_record_with_securities([RegisteredSecurity(ticker="STALE")])
        pipeline.save_output([kept, stale])  # daily mode: seeds the existing output

        pipeline.config.ciks = ("1234567",)
        fresh = _make_record_with_securities([RegisteredSecurity(ticker="FRESH")])
        pipeline.save_output([fresh])  # same keys as `stale` — must replace it

        df = pd.read_parquet(pipeline.config.output_file).sort_values("accession_number")
        assert len(df) == 2
        assert set(df["all_tickers"]) == {"OLD", "FRESH"}

    def test_daily_mode_overwrites_existing_output(
        self, pipeline: CompanyFactsPipeline, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Without --ciks-override, save_output overwrites as before."""
        pipeline.config.output_file = str(tmp_path / "out.parquet")
        old = _make_record_with_securities([RegisteredSecurity(ticker="OLD")])
        old.accession_number = "0001234567-23-000001"
        pipeline.save_output([old])

        new = _make_record_with_securities([RegisteredSecurity(ticker="NEW")])
        pipeline.save_output([new])

        df = pd.read_parquet(pipeline.config.output_file)
        assert list(df["all_tickers"]) == ["NEW"]

    def test_multiple_securities_increments_stat(
        self, pipeline: CompanyFactsPipeline, mocker: MockerFixture
    ) -> None:
        """_process_one increments multiple_registered_securities when > 1 security."""
        two_secs = [
            RegisteredSecurity(
                ticker="ORD", exchange="Euronext Paris", security_type=SecurityType.COMMON
            ),
            RegisteredSecurity(ticker="ADSX", exchange="NYSE", security_type=SecurityType.ADS),
        ]
        record = _make_record_with_securities(two_secs)
        mocker.patch.object(pipeline.extractor, "extract", return_value=([record], []))
        mocker.patch("idi_company_facts.pipeline.load_content", return_value=b"dummy")
        mocker.patch(
            "idi_company_facts.pipeline.InlineXbrlDocument", return_value=mocker.MagicMock()
        )

        pipeline._process_one(
            Filing(
                cik="0001234567",
                accession_number="0001234567-24-000001",
                form_type="10-K",
                filing_date=date(2024, 1, 15),
                primary_s3_key="s3://bucket/test.htm",
                primary_url="https://sec.gov/test.htm",
            )
        )

        assert pipeline.stats.multiple_registered_securities == 1
