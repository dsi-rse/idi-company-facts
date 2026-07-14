"""Inline XBRL parsing utilities."""

from idi_company_facts.xbrl.parser import (
    Context,
    Fact,
    InlineXbrlDocument,
    NotInlineXbrlError,
    XbrlParseError,
)

__all__ = [
    "Context",
    "Fact",
    "InlineXbrlDocument",
    "NotInlineXbrlError",
    "XbrlParseError",
]
