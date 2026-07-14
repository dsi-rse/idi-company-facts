"""Tests for the failure classification module."""

import pytest

from idi_company_facts.failures import CompanyFactsFailureClassifier, FailureType


@pytest.fixture()
def classifier() -> CompanyFactsFailureClassifier:
    """Return a fresh classifier instance."""
    return CompanyFactsFailureClassifier()


class TestFailureType:
    """Tests for FailureType enum values."""

    def test_storage_error_value(self) -> None:
        assert FailureType.STORAGE_ERROR == "storage_error"

    def test_missing_document_value(self) -> None:
        assert FailureType.MISSING_DOCUMENT == "missing_document"

    def test_empty_document_value(self) -> None:
        assert FailureType.EMPTY_DOCUMENT == "empty_document"

    def test_no_inline_xbrl_value(self) -> None:
        assert FailureType.NO_INLINE_XBRL == "no_inline_xbrl"

    def test_malformed_xbrl_value(self) -> None:
        assert FailureType.MALFORMED_XBRL == "malformed_xbrl"

    def test_missing_period_end_value(self) -> None:
        assert FailureType.MISSING_PERIOD_END == "missing_period_end"

    def test_no_revenue_concept_value(self) -> None:
        assert FailureType.NO_REVENUE_CONCEPT == "no_revenue_concept"

    def test_ambiguous_revenue_value(self) -> None:
        assert FailureType.AMBIGUOUS_REVENUE == "ambiguous_revenue"


class TestCompanyFactsFailureClassifier:
    """Tests for do_not_retry and retryable classification."""

    def test_storage_error_is_retryable(self, classifier: CompanyFactsFailureClassifier) -> None:
        """STORAGE_ERROR is the only transient failure."""
        assert classifier.is_retryable(FailureType.STORAGE_ERROR)

    def test_missing_document_is_not_retryable(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        assert not classifier.is_retryable(FailureType.MISSING_DOCUMENT)

    def test_empty_document_is_not_retryable(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        assert not classifier.is_retryable(FailureType.EMPTY_DOCUMENT)

    def test_no_inline_xbrl_is_not_retryable(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        assert not classifier.is_retryable(FailureType.NO_INLINE_XBRL)

    def test_malformed_xbrl_is_not_retryable(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        assert not classifier.is_retryable(FailureType.MALFORMED_XBRL)

    def test_do_not_retry_excludes_storage_error(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        """STORAGE_ERROR must not appear in do_not_retry."""
        assert FailureType.STORAGE_ERROR not in classifier.do_not_retry

    def test_every_type_is_retryable_or_permanent(
        self, classifier: CompanyFactsFailureClassifier
    ) -> None:
        """Every FailureType is either retryable or in do_not_retry — no gaps."""
        for failure_type in FailureType:
            in_do_not_retry = failure_type in classifier.do_not_retry
            is_retryable = classifier.is_retryable(failure_type)
            assert in_do_not_retry != is_retryable, (
                f"{failure_type} is both in do_not_retry and retryable, or neither"
            )
