"""Shared fixtures and helpers for the test suite."""

import datetime
import pathlib

import pytest

from idi_company_facts.types import Filing, PipelineConfig, PipelineStats

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# ── Data helpers ──────────────────────────────────────────────────────────────


def load_fixture(name: str) -> bytes:
    """Return the raw bytes of a file from tests/fixtures/."""
    return (_FIXTURES_DIR / name).read_bytes()


def make_ixbrl_bytes(
    *,
    contexts: str = "",
    units: str = "",
    facts: str = "",
    dei_prefix: str = "dei",
    usgaap_prefix: str = "us-gaap",
    ixt_ns: str = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
) -> bytes:
    """Build minimal iXBRL XHTML bytes for use in unit tests.

    Args:
        contexts: XML string of xbrli:context elements to inject.
        units: XML string of xbrli:unit elements to inject.
        facts: XML string of ix:nonFraction/ix:nonNumeric elements to inject.
        dei_prefix: Namespace prefix for the DEI taxonomy (default ``dei``).
        usgaap_prefix: Namespace prefix for the US-GAAP taxonomy (default ``us-gaap``).
        ixt_ns: Namespace URI bound to the ``ixt`` prefix.  Defaults to the TR4
            URI.  Pass the TR3 URI to test 2015-era date transforms.

    Returns:
        Bytes of a well-formed iXBRL XHTML document.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html
  xmlns="http://www.w3.org/1999/xhtml"
  xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
  xmlns:ixt="{ixt_ns}"
  xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31"
  xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:{dei_prefix}="http://xbrl.sec.gov/dei/2024"
  xmlns:{usgaap_prefix}="http://fasb.org/us-gaap/2024">
<head><title>Test iXBRL</title></head>
<body>
<ix:header><ix:resources>
{contexts}
{units}
</ix:resources></ix:header>
{facts}
</body>
</html>""".encode()


# ── Core fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_filing() -> Filing:
    """A realistic Filing dataclass instance (Apple 10-K)."""
    return Filing(
        cik="0000320193",
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=datetime.date(2024, 11, 1),
        primary_s3_key="edgar/data/320193/000032019324000123/aapl-20240928.htm",
        primary_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        company_name="APPLE INC",
    )


@pytest.fixture
def pipeline_stats() -> PipelineStats:
    """A fresh PipelineStats instance."""
    return PipelineStats()


@pytest.fixture
def pipeline_config(tmp_path: pathlib.Path) -> PipelineConfig:
    """A PipelineConfig wired to temporary paths."""
    return PipelineConfig(
        sec_bucket="idi-sec-scraper",
        output_file=str(tmp_path / "output.parquet"),
        failure_file=str(tmp_path / "failures.json"),
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        num_workers=2,
        failure_flush_every=100,
    )
