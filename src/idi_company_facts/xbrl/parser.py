"""Inline XBRL (iXBRL) document parser built on lxml."""

import datetime
import re
from decimal import Decimal, InvalidOperation

from idi_ftm2j_shared.logs import get_logger
from lxml import etree

from idi_company_facts.types import Context, Fact

# Namespace URIs
_XBRLI_NS = "http://www.xbrl.org/2003/instance"
_IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLDI_NS = "http://xbrl.org/2006/xbrldi"

_DEI_URI_RE = re.compile(r"https?://xbrl\.sec\.gov/dei/")
_USGAAP_URI_RE = re.compile(r"https?://fasb\.org/us-gaap/")
_IFRSFULL_URI_RE = re.compile(r"https?://xbrl\.ifrs\.org/.+/ifrs-full$")

# iXBRL transformation registry namespace URIs
# Booleans live in the SEC registry; dates live in TR3 (2015) or TR4 (2020).
_IXT_SEC_NS = "http://www.sec.gov/inlineXBRL/transformation/2015-08-31"
_IXT_TR3_NS = "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
_IXT_TR4_NS = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"

# Date formats commonly used in SEC iXBRL filings
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%B %d %Y", "%b %d %Y")

_logger = get_logger(__name__)


class XbrlParseError(Exception):
    """Raised when a document cannot be parsed as iXBRL."""


class NotInlineXbrlError(XbrlParseError):
    """Raised when the document is valid HTML but contains no ix:* tags."""


class InlineXbrlDocument:
    """Parsed inline XBRL document.

    All parsing (contexts, units, and facts) happens eagerly in ``__init__``.
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

        # Fast path: if the inline XBRL namespace URI is absent from the raw
        # bytes, the document cannot contain ix:* elements regardless of whether
        # it parses.  This prevents pre-inline plain-HTML filings (which often
        # have SGML/text preambles that make the recovery parser return None)
        # from being misclassified as MALFORMED_XBRL.
        # Note: byte-level search assumes an ASCII compatible encoding. EDGAR only accepts
        # documents submitted in HTML or ASCII (plain text).
        if b"inlineXBRL" not in html_bytes:
            raise NotInlineXbrlError("no inline XBRL namespace declared")

        self.used_recovery_parser = False
        try:
            root = etree.fromstring(html_bytes)
        except etree.XMLSyntaxError:
            try:
                root = etree.fromstring(html_bytes, parser=etree.XMLParser(recover=True))
            except etree.XMLSyntaxError as exc:
                raise XbrlParseError(str(exc)) from exc
            if root is None:
                raise XbrlParseError("unparseable document even in recovery mode") from None
            _logger.warning("strict XML parse failed; recovered with lenient parser")
            self.used_recovery_parser = True
        # if not root.nsmap:
        #     raise NotInlineXbrlError("No namespaces declared in the document")
        self._prefix_map = _build_prefix_map(root.nsmap)
        # if prefix map is empty (no uri map to dei or us-gaap), then throw error
        self._contexts = _parse_contexts(root)
        self._units = _parse_units(root)
        self._fact_cache, saw_ix = _parse_all_facts(
            root, self._prefix_map, self._contexts, self._units
        )
        # not necessarily no inline xbrl - just none of the facts we're interested in
        if not saw_ix:  # may be inline xbrl but no namespaces we care about?
            raise NotInlineXbrlError("no ix:nonFraction or ix:nonNumeric elements found")
        # could check for dei and us-gaap namespaces

    def facts(self, concept: str) -> list[Fact]:
        """Return all facts for the given canonical concept name.

        Args:
            concept: Canonical concept name, e.g. ``"dei:DocumentPeriodEndDate"``.

        Returns:
            List of matching :class:`Fact` objects; empty if none found.
        """
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
        elif _IFRSFULL_URI_RE.match(uri):
            result[prefix] = "ifrs-full"
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
        explicit_members = frozenset(
            el.text.strip()
            for el in ctx.findall(f".//{{{_XBRLDI_NS}}}explicitMember")
            if el.text and el.text.strip()
        )
        has_dims = bool(explicit_members) or ctx.find(f".//{{{_XBRLI_NS}}}typedMember") is not None
        contexts[ctx_id] = Context(
            context_id=ctx_id,
            instant=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}instant")),
            start=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}startDate")),
            end=_parse_date_el(ctx.find(f".//{{{_XBRLI_NS}}}endDate")),
            has_dimensions=has_dims,
            dimension_members=explicit_members,
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
    """Transform an ix:nonNumeric element's text using its format attribute.

    Resolves the format prefix to its namespace URI before dispatching, so the
    transform fires regardless of what prefix the filer chose (e.g. ``ixt``,
    ``ixt-sec``, ``transform``).  The SEC boolean registry and the XBRL TR3/TR4
    date registries are handled; everything else falls through to the raw text.
    """
    raw_fmt = el.get("format") or ""
    text = "".join(el.itertext()).strip()

    if not raw_fmt or ":" not in raw_fmt:
        return text

    prefix, local = raw_fmt.split(":", 1)
    ns_uri = el.nsmap.get(prefix, "")
    local_lower = local.lower()

    # Boolean transforms live in the SEC transformation registry.
    if ns_uri == _IXT_SEC_NS:
        if local_lower == "booleanfalse":
            return False
        if local_lower == "booleantrue":
            return True

    # Date transforms live in TR3 (2015) or TR4 (2020).
    if ns_uri in (_IXT_TR3_NS, _IXT_TR4_NS):
        fmts = _IXT_DATE_LOCAL_FORMATS.get(local_lower)
        if fmts is not None:
            for fmt in fmts:
                try:
                    return datetime.datetime.strptime(text.strip(), fmt).date()
                except ValueError:
                    continue
        # Unknown date local name — fall back to heuristic strptime.
        return _parse_date_text(text)

    return text


def _parse_all_facts(
    root: etree._Element,
    prefix_map: dict[str, str],
    contexts: dict[str, Context],
    units: dict[str, str],
) -> tuple[dict[str, list[Fact]], bool]:
    """Build a canonical concept → [Fact, ...] mapping from the document tree.

    Returns:
        Tuple of (fact_cache, saw_ix) where saw_ix is True if any ix:nonFraction
        or ix:nonNumeric element was encountered, even if it was later dropped by
        filtering (e.g. unresolvable context).
    """
    result: dict[str, list[Fact]] = {}
    saw_ix = False

    for el in root.iter(
        f"{{{_IX_NS}}}nonFraction",
        f"{{{_IX_NS}}}nonNumeric",
    ):
        saw_ix = True  # set before any filtering — existence of the element is enough

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

    return result, saw_ix
