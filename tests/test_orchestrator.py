"""Tests for orchestrator CLI argument parsing and date resolution."""

import argparse
import sys
from datetime import date, timedelta

import pytest

from idi_company_facts.orchestrator import (
    DEFAULT_LOOK_BACK,
    get_args,
    get_dates,
    valid_date,
    validate_args,
)

# Full set of required CLI args used as the baseline for TestGetArgs.
_FULL_ARGS = [
    "orchestrator.py",
    "--sec-bucket",
    "test-bucket",
    "--output-file",
    "out.parquet",
    "--failure-file",
    "fail.json",
    "--daily",
]


def make_args(**kwargs: object) -> argparse.Namespace:
    """Return an argparse.Namespace with sensible defaults; override via kwargs."""
    defaults: dict[str, object] = dict(
        daily=False,
        start_date=None,
        end_date=None,
        look_back=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestValidateArgs:
    """Tests for validate_args() cross-argument validation."""

    def _parser(self) -> argparse.ArgumentParser:
        return argparse.ArgumentParser()

    def test_start_date_without_end_date_raises(self) -> None:
        """--start-date requires --end-date."""
        args = make_args(start_date=date(2024, 1, 1), end_date=None)
        with pytest.raises(SystemExit):
            validate_args(args, self._parser())

    def test_daily_with_end_date_raises(self) -> None:
        """--end-date is incompatible with --daily."""
        args = make_args(daily=True, end_date=date(2024, 1, 31))
        with pytest.raises(SystemExit):
            validate_args(args, self._parser())

    def test_end_date_before_start_date_raises(self) -> None:
        """--end-date cannot precede --start-date."""
        args = make_args(start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))
        with pytest.raises(SystemExit):
            validate_args(args, self._parser())

    def test_look_back_without_daily_raises(self) -> None:
        """--look-back requires --daily."""
        args = make_args(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31), look_back=7)
        with pytest.raises(SystemExit):
            validate_args(args, self._parser())

    def test_daily_none_look_back_defaults_to_constant(self) -> None:
        """--daily with no --look-back sets args.look_back to DEFAULT_LOOK_BACK."""
        args = make_args(daily=True, look_back=None)
        validate_args(args, self._parser())
        assert args.look_back == DEFAULT_LOOK_BACK

    def test_daily_custom_look_back_preserved(self) -> None:
        """An explicit --look-back value in daily mode is not overridden."""
        args = make_args(daily=True, look_back=14)
        validate_args(args, self._parser())
        assert args.look_back == 14

    def test_valid_explicit_dates_passes(self) -> None:
        """Matching start/end dates with no look-back passes without error."""
        args = make_args(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        validate_args(args, self._parser())  # must not raise


class TestGetDates:
    """Tests for get_dates() date-window resolution."""

    def test_explicit_dates_pass_through(self) -> None:
        """Explicit --start-date and --end-date are returned unchanged."""
        args = make_args(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        start, end = get_dates(args)
        assert start == date(2024, 1, 1)
        assert end == date(2024, 1, 31)

    def test_daily_end_equals_today(self) -> None:
        """In daily mode, end is the value passed as today."""
        today = date(2024, 6, 15)
        args = make_args(daily=True, look_back=DEFAULT_LOOK_BACK)
        _, end = get_dates(args, today=today)
        assert end == today

    def test_daily_start_is_end_minus_look_back(self) -> None:
        """In daily mode, start is exactly look_back days before end."""
        today = date(2024, 6, 15)
        args = make_args(daily=True, look_back=7)
        start, end = get_dates(args, today=today)
        assert start == end - timedelta(days=7)

    def test_daily_custom_look_back(self) -> None:
        """look_back controls the window width in daily mode."""
        today = date(2024, 6, 15)
        args = make_args(daily=True, look_back=30)
        start, end = get_dates(args, today=today)
        assert (end - start).days == 30

    def test_daily_default_look_back_is_seven(self) -> None:
        """DEFAULT_LOOK_BACK is 7 and produces a 7-day window."""
        today = date(2024, 6, 15)
        args = make_args(daily=True, look_back=DEFAULT_LOOK_BACK)
        start, end = get_dates(args, today=today)
        assert (end - start).days == DEFAULT_LOOK_BACK


class TestValidDate:
    """Tests for the valid_date argparse type converter."""

    def test_valid_iso_string_returns_date(self) -> None:
        """A well-formed YYYY-MM-DD string returns the corresponding date object."""
        assert valid_date("2024-06-15") == date(2024, 6, 15)

    def test_invalid_string_raises_argument_type_error(self) -> None:
        """A non-date string raises argparse.ArgumentTypeError."""
        with pytest.raises(argparse.ArgumentTypeError):
            valid_date("not-a-date")

    def test_wrong_format_raises_argument_type_error(self) -> None:
        """A date in the wrong format (e.g. MM/DD/YYYY) raises ArgumentTypeError."""
        with pytest.raises(argparse.ArgumentTypeError):
            valid_date("06/15/2024")


class TestGetArgs:
    """Tests for the CLI argument parser — required args and mutual exclusion."""

    def test_succeeds_with_all_required_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_args() returns a Namespace when all required arguments are present."""
        monkeypatch.setattr(sys, "argv", _FULL_ARGS)
        args = get_args()
        assert args.sec_bucket == "test-bucket"
        assert args.output_file == "out.parquet"
        assert args.failure_file == "fail.json"
        assert args.daily is True

    def test_requires_sec_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing --sec-bucket causes a SystemExit."""
        argv = [a for a in _FULL_ARGS if a not in ("--sec-bucket", "test-bucket")]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            get_args()

    def test_requires_daily_or_start_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting both --daily and --start-date causes a SystemExit."""
        argv = [a for a in _FULL_ARGS if a != "--daily"]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            get_args()

    def test_explicit_dates_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--start-date is accepted as the alternative to --daily."""
        argv = [
            "orchestrator.py",
            "--sec-bucket",
            "test-bucket",
            "--output-file",
            "out.parquet",
            "--failure-file",
            "fail.json",
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-31",
        ]
        monkeypatch.setattr(sys, "argv", argv)
        args = get_args()
        assert args.start_date == date(2024, 1, 1)
        assert args.end_date == date(2024, 1, 31)
        assert args.daily is False

    def test_look_back_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """look_back defaults to DEFAULT_LOOK_BACK in daily mode when not specified."""
        monkeypatch.setattr(sys, "argv", _FULL_ARGS)
        args = get_args()
        assert args.look_back == DEFAULT_LOOK_BACK

    def test_failure_flush_every_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """failure_flush_every defaults to 50 when not specified."""
        monkeypatch.setattr(sys, "argv", _FULL_ARGS)
        args = get_args()
        assert args.failure_flush_every == 50

