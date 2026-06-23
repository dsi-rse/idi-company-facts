"""Company Facts Processor Failure Classification"""

# Standard library imports
from enum import StrEnum

# Third-party imports
from idi_ftm2j_shared.failures import FailureClassifier


class FailureType(StrEnum):
    """Failure types for the company facts processor pipeline."""

    # --- Storage / document retrieval ---
    STORAGE_ERROR = "storage_error"  # Transient S3 read failure
    MISSING_DOCUMENT = "missing_document"  # Primary 10K doc absent in S3
    EMPTY_DOCUMENT = "empty_document"  # Document exists, no content
    NO_INLINE_XBRL = "no_inline_xbrl"  # No embedded inline XBRL (pre-mandate filing)
    MALFORMED_XBRL = "malformed_xbrl"  # Inline XBRL present but structurally unparseable

    # --- XBRL fact extraction ---
    MISSING_PERIOD_END = "missing_period_end"  # dei:DocumentPeriodEndDate is missing, cannot anchor facts to this filing's period
    NO_REVENUE_CONCEPT = "no_revenue_concept"  # None of the top-line (i.e. total) revenue concepts are present for this filing's year
    AMBIGUOUS_REVENUE = "ambiguous_revenue"  # Multiple top-line revenue concepts for the same period with conflicting values


class CompanyFactsFailureClassifier(FailureClassifier):
    """Classifies failures for the company facts processor pipeline."""

    _DO_NOT_RETRY = frozenset(
        {
            FailureType.MISSING_DOCUMENT,
            FailureType.EMPTY_DOCUMENT,
            FailureType.NO_INLINE_XBRL,
            FailureType.MALFORMED_XBRL,
            FailureType.MISSING_PERIOD_END,
            FailureType.NO_REVENUE_CONCEPT,
            FailureType.AMBIGUOUS_REVENUE,
        }
    )

    @property
    def do_not_retry(self) -> frozenset[FailureType]:
        """Return the set of failure types that should not be retried."""
        return self._DO_NOT_RETRY

    def classify_from_response(self, response: dict, **kwargs) -> FailureType:
        """Not implemented yet, no HTTP requests."""
        raise NotImplementedError
