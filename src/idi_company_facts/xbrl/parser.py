"""Inline XBRL (iXBRL) document parser built on lxml."""

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from lxml import etree

# Namespace URIs
_XBRLI_NS = "http://www.xbrl.org/2003/instance"
_IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLDI_NS = "http://xbrl.org/2006/xbrldi"

_DEI_URI_RE = re.compile(r"https?://xbrl\.sec\.gov/dei/")
_USGAAP_URI_RE = re.compile(r"https?://fasb\.org/us-gaap/")

# iXBRL transformation registry format values for booleans
_IXT_BOOLEANFALSE = "ixt:booleanfalse"
_IXT_BOOLEANTRUE = "ixt:booleantrue"
_IXT_DATE_PREFIX = "ixt:date-"

# Date formats commonly used in SEC iXBRL filings
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d")


class XbrlParseError(Exception):
    """Raised when a document cannot be parsed as iXBRL."""


class NotInlineXbrlError(XbrlParseError):
    """Raised when the document is valid XML/HTML but contains no ix:* tags."""


@dataclass(frozen=True)
class Context:
    """An iXBRL reporting context."""

    context_id: str
    instant: datetime.date | None
    start: datetime.date | None
    end: datetime.date | None
    has_dimensions: bool


@dataclass(frozen=True)
class Fact:
    """A single iXBRL fact with a normalized concept name and typed value."""

    concept: str  # canonical "prefix:LocalName" e.g. "dei:DocumentPeriodEndDate"
    value: Decimal | bool | datetime.date | str
    context: Context
    unit: str | None  # ISO 4217 currency, "shares", or None


class InlineXbrlDocument:
    """Parsed inline XBRL document.

    Contexts and units are parsed eagerly; facts are parsed on first access.
    """

    def __init__(self, html_bytes: bytes) -> None:
        """Parse an iXBRL document from raw HTML/XHTML bytes.

        Args:
            html_bytes: Raw bytes of an iXBRL-embedded HTML document.

        Raises:
            XbrlParseError: If the bytes cannot be parsed as XML or are empty.
            NotInlineXbrlError: If the document is valid XML but has no ix:* tags.
        """
        if not html_bytes:
            raise XbrlParseError("empty document")
        try:
            root = etree.fromstring(html_bytes)
        except etree.XMLSyntaxError as exc:
            raise XbrlParseError(str(exc)) from exc

        self._prefix_map = _build_prefix_map(root.nsmap)
        self._contexts = _parse_contexts(root)
        self._units = _parse_units(root)
        self._root = root
        self._fact_cache: dict[str, list[Fact]] | None = None

        has_ix = any(
            True
            for _ in root.iter(
                f"{{{_IX_NS}}}nonFraction",
                f"{{{_IX_NS}}}nonNumeric",
            )
        )
        if not has_ix:
            raise NotInlineXbrlError("no ix:nonFraction or ix:nonNumeric elements found")

    def facts(self, concept: str) -> list[Fact]:
        """Return all facts for the given canonical concept name.

        Args:
            concept: Canonical concept name, e.g. ``"dei:DocumentPeriodEndDate"``.

        Returns:
            List of matching :class:`Fact` objects; empty if none found.
        """
        if self._fact_cache is None:
            self._fact_cache = _parse_all_facts(
                self._root, self._prefix_map, self._contexts, self._units
            )
        return self._fact_cache.get(concept, [])

    def single_fact(self, concept: str, *, dimensionless: bool = True) -> "Fact | None":
        """Return the first matching fact, optionally restricted to dimensionless contexts.

        Args:
            concept: Canonical concept name.
            dimensionless: If True (default), skip facts with dimensional contexts.

        Returns:
            First matching :class:`Fact`, or ``None`` if none found.
        """
        candidates = self.facts(concept)
        if dimensionless:
            candidates = [f for f in candidates if not f.context.has_dimensions]
        return candidates[0] if candidates else None


# ── Module-level helpers (not part of the public API) ─────────────────────────


def _build_prefix_map(nsmap: dict) -> dict[str, str]:
    """Map document namespace prefixes to canonical ones ('dei', 'us-gaap').

    Handles filers that use non-standard prefixes (e.g. 'd' or 'gaap') by
    matching against the full namespace URI.
    """
    result: dict[str, str] = {}
    for prefix, uri in nsmap.items():
        if not prefix:
            continue
        if _DEI_URI_RE.match(uri):
            result[prefix] = "dei"
        elif _USGAAP_URI_RE.match(uri):
            result[prefix] = "us-gaap"
    return result


def _normalize_concept(name: str, prefix_map: dict[str, str]) -> str:
    """Rewrite a filer prefix to its canonical form using the prefix map."""
    if ":" not in name:
        return name
    prefix, local = name.split(":", 1)
    return f"{prefix_map.get(prefix, prefix)}:{local}"


def _parse_date_text(text: str) -> datetime.date | str:
    """Parse common SEC date text formats; return original string on failure."""
    cleaned = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return cleaned


def _parse_date_el(el: etree._Element | None) -> datetime.date | None:
    """Parse an ISO date from an element's text content; return None on failure."""
    if el is None or not el.text:
        return None
    try:
        return datetime.date.fromisoformat(el.text.strip())
    except ValueError:
        return None


def _parse_contexts(root: etree._Element) -> dict[str, Context]:
    """Extract all xbrli:context elements from the document tree."""
    contexts: dict[str, Context] = {}
    for ctx in root.iter(f"{{{_XBRLI_NS}}}context"):
        ctx_id = ctx.get("id", "")
        has_dims = (
            ctx.find(f".//{{{_XBRLDI_NS}}}explicitMember") is not None
            or ctx.find(f".//{{{_XBRLI_NS}}}typedMember") is not None
        )
        contexts[ctx_id] = Context(
            context_id=ctx_id,
            instant=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}instant")),
            start=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}startDate")),
            end=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}endDate")),
            has_dimensions=has_dims,
        )
    return contexts


def _parse_units(root: etree._Element) -> dict[str, str]:
    """Extract all xbrli:unit elements; strip the iso4217: prefix from currency codes."""
    units: dict[str, str] = {}
    for unit in root.iter(f"{{{_XBRLI_NS}}}unit"):
        uid = unit.get("id", "")
        measure_el = unit.find(f"{{{_XBRLI_NS}}}measure")
        if measure_el is not None and measure_el.text:
            measure = measure_el.text.strip()
            if measure.startswith("iso4217:"):
                measure = measure[8:]
            units[uid] = measure
    return units


def _parse_numeric(el: etree._Element) -> Decimal | None:
    """Extract and transform the numeric value from an ix:nonFraction element.

    Strips commas and whitespace, applies ix:scale (powers of 10), and
    applies ix:sign negation.
    """
    text = "".join(el.itertext()).strip().replace(",", "").replace("\xa0", "").replace(" ", "")
    if not text or text in ("-", "—"):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None

    scale_attr = el.get("scale")
    if scale_attr:
        try:
            value *= Decimal(10) ** int(scale_attr)
        except (ValueError, InvalidOperation):
            pass

    if el.get("sign") == "-":
        value = -value

    return value


def _parse_non_numeric(el: etree._Element) -> bool | datetime.date | str:
    """Transform an ix:nonNumeric element's text using its format attribute."""
    fmt = (el.get("format") or "").lower()
    text = "".join(el.itertext()).strip()

    if fmt == _IXT_BOOLEANFALSE:
        return False
    if fmt == _IXT_BOOLEANTRUE:
        return True
    if fmt.startswith(_IXT_DATE_PREFIX):
        return _parse_date_text(text)
    return text


def _parse_all_facts(
    root: etree._Element,
    prefix_map: dict[str, str],
    contexts: dict[str, Context],
    units: dict[str, str],
) -> dict[str, list[Fact]]:
    """Build a canonical concept → [Fact, ...] mapping from the document tree."""
    result: dict[str, list[Fact]] = {}

    for el in root.iter(
        f"{{{_IX_NS}}}nonFraction",
        f"{{{_IX_NS}}}nonNumeric",
    ):
        raw_name = el.get("name", "")
        concept = _normalize_concept(raw_name, prefix_map)
        ctx_id = el.get("contextRef", "")
        context = contexts.get(ctx_id)
        if context is None:
            continue

        tag_local = el.tag.split("}")[-1]
        if tag_local == "nonFraction":
            value: Decimal | bool | datetime.date | str | None = _parse_numeric(el)
            if value is None:
                continue
            unit_ref = el.get("unitRef", "")
            unit: str | None = units.get(unit_ref)
        else:
            value = _parse_non_numeric(el)
            unit = None

        result.setdefault(concept, []).append(
            Fact(concept=concept, value=value, context=context, unit=unit)
        )

    return result
