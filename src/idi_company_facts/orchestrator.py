"""Pipeline Orchestrator - Runs the company facts pipeline for the specified input file.

The orchestrator is responsible for running the pipeline.
"""

# Standard imports
import argparse
import datetime
import io

# Third party imports
import pandas as pd
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import load_content

# Application imports
from idi_company_facts.pipeline import SEC_MANIFEST_KEY, CompanyFactsPipeline, normalize_cik
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
    if args.ciks_override and args.end_date:
        parser.error("--end-date cannot be used with --ciks-override")
    if args.ciks_override and args.look_back is not None:
        parser.error("--look-back cannot be used with --ciks-override")
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


def _get_latest_scraped_date(bucket: str) -> datetime.date:
    """Return the most recent date_scraped from the SEC manifest parquet.

    Args:
        bucket: S3 bucket name containing the SEC scraper data.

    Returns:
        The latest ``date_scraped`` value as a :class:`datetime.date`.

    Raises:
        ValueError: If the manifest has no usable ``date_scraped`` values.
    """
    raw = load_content(f"s3://{bucket}/{SEC_MANIFEST_KEY}")
    df = pd.read_parquet(io.BytesIO(raw))
    latest = df["date_scraped"].max()
    if pd.isna(latest):
        raise ValueError("manifest.parquet has no usable date_scraped values")
    return pd.to_datetime(latest).date()


def get_dates(args: argparse.Namespace) -> tuple[datetime.date, datetime.date]:
    """Resolve start/end dates from parsed arguments.

    In daily mode: end = latest date_scraped from the SEC bucket's manifest.parquet,
    start = end - look_back days.
    In explicit mode: pass through the parsed --start-date and --end-date.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Tuple of ``(start_date, end_date)``.
    """
    if not args.daily:
        return args.start_date, args.end_date
    end = _get_latest_scraped_date(args.sec_bucket)
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
    date_group.add_argument(
        "--ciks-override",
        metavar="PATH",
        help=(
            "Local or s3:// file of CIKs (one per line, '#' comments allowed); "
            "process each CIK's most recent scraped target filing instead of a date window"
        ),
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


def load_ciks_file(path: str) -> tuple[str, ...]:
    """Read a --ciks-override file into a deduplicated tuple of normalized CIKs.

    One CIK per line; blank lines and '#' comments are ignored. CIKs are
    normalized (leading zeros stripped) so spreadsheet-padded values match the
    manifest's representation.

    Args:
        path: Local or s3:// path to the CIK list file.

    Returns:
        Normalized CIKs in first-seen order.

    Raises:
        ValueError: If the file is missing/empty, a line is not a decimal
            integer, or the file has no CIKs.
    """
    raw = load_content(path)
    if not raw:
        raise ValueError(f"CIK file not found or empty: {path}")
    ciks: list[str] = []
    seen: set[str] = set()
    for line in raw.decode().splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        try:
            cik = normalize_cik(entry)
        except ValueError as e:
            raise ValueError(f"Invalid CIK {entry!r} in {path}") from e
        if cik not in seen:
            seen.add(cik)
            ciks.append(cik)
    if not ciks:
        raise ValueError(f"No CIKs found in {path}")
    return tuple(ciks)


def main() -> None:
    """Run the company facts pipeline from the CLI."""
    args = get_args()

    logger = get_logger("orchestrator")
    for key, value in vars(args).items():
        logger.info("%s = %r", key, value)

    if args.ciks_override:
        ciks = load_ciks_file(args.ciks_override)
        start_date = end_date = None
        logger.info("CIK override mode: %d CIKs from %s", len(ciks), args.ciks_override)
    else:
        ciks = None
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
        ciks=ciks,
    )

    pipeline = CompanyFactsPipeline(config)
    pipeline.run()


if __name__ == "__main__":
    main()
