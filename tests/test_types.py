"""Tests for the pipeline data types."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from idi_company_facts.types import TARGET_FORM_TYPES, Filing, PipelineConfig, PipelineStats


class TestTargetFormTypes:
    """Tests for the TARGET_FORM_TYPES module constant."""

    def test_includes_10k(self) -> None:
        assert "10-K" in TARGET_FORM_TYPES

    def test_includes_10k_amendment(self) -> None:
        assert "10-K/A" in TARGET_FORM_TYPES

    def test_includes_transition_report(self) -> None:
        assert "10-KT" in TARGET_FORM_TYPES

    def test_includes_transition_amendment(self) -> None:
        assert "10-KT/A" in TARGET_FORM_TYPES


class TestFiling:
    """Tests for the Filing frozen dataclass."""

    @pytest.fixture()
    def filing(self) -> Filing:
        """Return a minimal valid Filing."""
        return Filing(
            cik="0000320193",
            accession_number="0000320193-24-000123",
            form_type="10-K",
            filing_date=date(2024, 11, 1),
            primary_s3_key="sec/2024-11-01/10-K/320193/0000320193-24-000123/filing.htm",
            primary_url="https://sec.gov/Archives/edgar/data/320193/000032019324000123/filing.htm",
        )

    def test_fields_accessible(self, filing: Filing) -> None:
        """All required fields can be read back after construction."""
        assert filing.cik == "0000320193"
        assert filing.form_type == "10-K"
        assert filing.filing_date == date(2024, 11, 1)

    def test_company_name_defaults_to_empty_string(self, filing: Filing) -> None:
        """company_name defaults to empty string when omitted."""
        assert filing.company_name == ""

    def test_company_name_can_be_set(self) -> None:
        """company_name is stored when explicitly provided."""
        f = Filing(
            cik="0000000001",
            accession_number="0000000001-24-000001",
            form_type="10-K",
            filing_date=date(2024, 1, 1),
            primary_s3_key="sec/test.htm",
            primary_url="https://sec.gov/test.htm",
            company_name="APPLE INC",
        )
        assert f.company_name == "APPLE INC"

    def test_is_immutable(self, filing: Filing) -> None:
        """Assigning to any field raises FrozenInstanceError."""
        with pytest.raises(FrozenInstanceError):
            filing.cik = "mutated"  # type: ignore[misc]


class TestPipelineConfig:
    """Tests for PipelineConfig field defaults."""

    @pytest.fixture()
    def config(self, tmp_path: pytest.TempPathFactory) -> PipelineConfig:
        """Return a minimal PipelineConfig using only required fields."""
        return PipelineConfig(
            sec_bucket="test-bucket",
            output_file=str(tmp_path / "output.parquet"),
            failure_file=str(tmp_path / "failures.json"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

    def test_num_workers_default(self, config: PipelineConfig) -> None:
        """num_workers defaults to 10."""
        assert config.num_workers == 10

    def test_failure_flush_every_default(self, config: PipelineConfig) -> None:
        """failure_flush_every defaults to 50."""
        assert config.failure_flush_every == 50

    def test_sec_bucket_stored(self, config: PipelineConfig) -> None:
        """sec_bucket is stored as provided."""
        assert config.sec_bucket == "test-bucket"


class TestPipelineStats:
    """Tests for PipelineStats counter defaults and increment."""

    def test_all_counters_start_at_zero(self) -> None:
        """A freshly created PipelineStats has all counters at zero."""
        stats = PipelineStats()
        assert stats.total_filings == 0
        assert stats.failed_filings == 0
        assert stats.total_primary_docs == 0
        assert stats.failed_primary_docs == 0
        assert stats.queued_documents == 0
        assert stats.documents_fetched == 0
        assert stats.extracted_documents == 0
        assert stats.parse_failures == 0
        assert stats.storage_errors == 0

    def test_counters_are_mutable(self) -> None:
        """PipelineStats is not frozen — counters can be assigned directly."""
        stats = PipelineStats()
        stats.total_filings += 5
        assert stats.total_filings == 5

    def test_increment_method_adds_to_field(self) -> None:
        """increment() adds n to the named field atomically."""
        stats = PipelineStats()
        stats.increment("extracted_documents")
        assert stats.extracted_documents == 1
        stats.increment("extracted_documents", n=4)
        assert stats.extracted_documents == 5

    def test_increment_is_thread_safe(self) -> None:
        """Concurrent increment() calls produce the correct final total."""
        import threading

        stats = PipelineStats()
        threads = [
            threading.Thread(target=stats.increment, args=("total_filings",)) for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert stats.total_filings == 50
