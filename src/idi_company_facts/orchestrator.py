"""Pipeline Orchestrator - Runs the company facts pipeline for the specified input file.

The orchestrator is responsible for running the pipeline.
"""

# Standard imports
import argparse
import datetime

# Third party imports
from idi_ftm2j_shared.logs import get_logger

# Application imports
from idi_company_facts.pipeline import CompanyFactsPipeline
from idi_company_facts.types import PipelineConfig

DEFAULT_LOOK_BACK: int = 7


def valid_date(s: str) -> datetime.date:
    """Parse a YYYY-MM-DD string into a date for argparse.

    Args:
        s: Date string to parse.

    Returns:
        The parsed date.

    Raises:
        argparse.ArgumentTypeError: If "s" is not a valid ISO date.
    """
    try:
        return datetime.date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Not a valid date: {s!r} — expected YYYY-MM-DD") from e


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Enforce argument pairing rules that argparse cannot express alone.

    Args:
        args: Parsed command-line arguments to validate, mutated in place to
            apply the ``--look-back`` default in daily mode.
        parser: Parser used to report validation errors via ``parser.error``,
            which prints usage and exits.

    Returns:
        None
    """
    if args.start_date and not args.end_date:
        parser.error("--end-date is required when --start-date is given")
    if args.daily and args.end_date:
        parser.error("--end-date cannot be used with --daily")
    if args.start_date and args.end_date and args.end_date < args.start_date:
        parser.error("--end-date must not be before --start-date")
    if args.look_back is not None and not args.daily:
        parser.error("--look-back can only be used with --daily")
    if args.daily and args.look_back is None:
        args.look_back = DEFAULT_LOOK_BACK


def get_dates(
    args: argparse.Namespace, today: datetime.date | None = None
) -> tuple[datetime.date, datetime.date]:
    """Resolve start/end dates from parsed CLI args.

    In daily mode: end = today, start = end - look_back days.
    In explicit mode: pass through the parsed --start-date and --end-date.

    Args:
        args: Parsed command-line arguments.
        today: if daily mode, the end date to parse.
            Use look back to determine start date.

    Returns:
        Tuple of ``(start_date, end_date)``.


    """
    if not args.daily:
        return args.start_date, args.end_date
    end = today if today is not None else datetime.date.today()
    start = end - datetime.timedelta(days=args.look_back)
    return start, end


def get_args() -> argparse.Namespace:
    """Parse and return CLI arguments."""
    parser = argparse.ArgumentParser(description="Company Facts Pipeline — extract 10-K data")

    parser.add_argument(
        "--sec-bucket",
        required=True,
        metavar="BUCKET",
        help="S3 bucket name for the SEC scraper data",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        metavar="PATH",
        help="Local or s3:// path for the output parquet file",
    )
    parser.add_argument(
        "--failure-file",
        required=True,
        metavar="PATH",
        help="Local or s3:// path for the failure registry",
    )

    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--daily",
        action="store_true",
        help="Resolve date window as [today - look-back, today]",
    )
    date_group.add_argument(
        "--start-date",
        type=valid_date,
        metavar="YYYY-MM-DD",
        help="Start of the filing date window (requires --end-date)",
    )

    parser.add_argument(
        "--end-date",
        type=valid_date,
        metavar="YYYY-MM-DD",
        help="End of the filing date window (required with --start-date)",
    )
    parser.add_argument(
        "--look-back",
        type=int,
        default=None,
        metavar="N",
        help=f"Days to look back in --daily mode (default: {DEFAULT_LOOK_BACK})",
    )
    parser.add_argument(
        "--failure-flush-every",
        type=int,
        default=50,
        metavar="N",
        help="Flush failure registry to disk every N failures (default: 50)",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=10,
        metavar="N",
        help="Worker count for document fetching (default: 10)",
    )

    args = parser.parse_args()
    validate_args(args, parser)
    return args


def main() -> None:
    """Run the company facts pipeline from the CLI."""
    args = get_args()

    logger = get_logger("orchestrator")
    for key, value in vars(args).items():
        logger.info("%s = %r", key, value)

    start_date, end_date = get_dates(args)
    logger.info("Searching for date range: %s - %s", start_date, end_date)

    config = PipelineConfig(
        sec_bucket=args.sec_bucket,
        output_file=args.output_file,
        failure_file=args.failure_file,
        start_date=start_date,
        end_date=end_date,
        failure_flush_every=args.failure_flush_every,
        num_workers=args.num_workers,
    )

    pipeline = CompanyFactsPipeline(config)
    pipeline.run()


if __name__ == "__main__":
    main()
