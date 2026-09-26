"""
HTML -> ProseMirror JSON conversion.

Everything the system generates is rendered to HTML first (assignments by
``AssignmentProcessingService.format_assignment_standard_html``, submissions by
``students.services.student_submission_to_html``) and only then converted into
the ProseMirror document the rich-text editor loads. This module owns that
conversion: the schema, the sanitisation applied on the way in, and the two
public entry points.

Three things here are deliberate and worth not "simplifying" away:

1. The schema is built **once**, at import, from *copies* of prosemirror-py's
   shared spec dicts. Building per call is wasteful, but the real hazard is
   that ``basic_schema.spec["marks"]`` is a module-level dict owned by the
   library - updating it in place silently reconfigures every other schema in
   the process.

2. A parse rule may declare static ``attrs`` **or** a ``getAttrs`` callable,
   never both: prosemirror-py *overwrites* ``rule.attrs`` with whatever
   ``getAttrs`` returns (see ``from_dom.DOMParser.match_tag``). Any attribute a
   rule needs must therefore be produced by ``getAttrs`` itself - which is why
   heading levels are baked into a closure per level rather than declared as
   ``attrs``.

3. Conversion sanitises its own input. Callers are *supposed* to sanitise
   first, but this function is reached from four modules and the output is
   stored and later rendered in a teacher's browser, so the boundary is
   enforced here rather than assumed.
"""

import json
import logging
import re
from functools import lru_cache
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer
from lxml import html as lxml_html
from prosemirror.model import DOMParser, Schema
from prosemirror.schema.basic import schema as basic_schema
from prosemirror.schema.list import add_list_nodes

logger = logging.getLogger(__name__)


class ProseMirrorConversionError(RuntimeError):
    """
    Raised when well-formed input cannot be turned into a ProseMirror document.

    Subclasses RuntimeError so existing callers that catch RuntimeError keep
    working, while new code can catch the specific failure.
    """


# Control characters that are illegal in XML 1.0 and make lxml refuse to parse.
# \t (\x09), \n (\x0a) and \r (\x0d) are legal and deliberately excluded.
INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def strip_control_chars(value):
    """Remove XML-illegal control characters. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    return INVALID_XML_CHARS.sub("", value)


# ---------------------------------------------------------------------------
# Sanitisation of HTML on its way into the converter
# ---------------------------------------------------------------------------
# Wider than AI_HTML_ALLOWED_TAGS in services.py, and intentionally so. That
# allowlist governs *AI-authored fragments*; this one governs the *assembled
# document*, which additionally contains the structural wrappers and question
# images our own renderers emit. What it still refuses is the executable
# surface: script/iframe/object/form/style elements, event handlers, and any
# URL scheme other than http(s)/mailto.
CONVERTER_ALLOWED_TAGS = frozenset(
    {
        # structure our renderers emit (dropped by the schema, but stripping
        # them here would change how their children are grouped)
        "div",
        "section",
        "article",
        # block
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "hr",
        "br",
        # lists
        "ul",
        "ol",
        "li",
        # tables
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        # inline
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "del",
        "strike",
        "sub",
        "sup",
        "code",
        "span",
        "a",
        "img",
    }
)

_STYLED = ["style", "class"]

CONVERTER_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "style", "class"],
    "img": ["src", "alt", "title", "style", "class"],
    "td": ["colspan", "rowspan", "align", "valign", "style", "class"],
    "th": ["colspan", "rowspan", "align", "valign", "style", "class"],
    "table": ["border", "cellpadding", "cellspacing", "width", "style", "class"],
    "ol": ["start"],
    "p": _STYLED,
    "h1": _STYLED,
    "h2": _STYLED,
    "h3": _STYLED,
    "h4": _STYLED,
    "h5": _STYLED,
    "h6": _STYLED,
    "span": _STYLED,
    "div": _STYLED,
    "blockquote": _STYLED,
    "li": _STYLED,
    "ul": _STYLED,
    "tr": _STYLED,
    "s": [],
    "del": [],
    "strike": [],
}

# Presentational properties only. Nothing here can load a URL, position an
# element over the page, or execute - so a hostile `style` attribute that
# survives is inert.
CONVERTER_ALLOWED_CSS_PROPERTIES = frozenset(
    {
        "background-color",
        "border",
        "border-bottom",
        "border-collapse",
        "border-color",
        "border-left",
        "border-right",
        "border-style",
        "border-top",
        "border-width",
        "color",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "line-height",
        "list-style-type",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-width",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration",
        "vertical-align",
        "white-space",
        "width",
    }
)

URL_ALLOWED_SCHEMES = ("http", "https", "mailto")
# Links may be mailto:; an image source may not.
LINK_ALLOWED_SCHEMES = URL_ALLOWED_SCHEMES
IMAGE_ALLOWED_SCHEMES = ("http", "https")

_CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=sorted(CONVERTER_ALLOWED_CSS_PROPERTIES)
)

# bleach.Cleaner is reusable and avoids re-parsing the allowlists per call.
_CLEANER = bleach.sanitizer.Cleaner(
    tags=CONVERTER_ALLOWED_TAGS,
    attributes=CONVERTER_ALLOWED_ATTRIBUTES,
    protocols=list(URL_ALLOWED_SCHEMES),
    css_sanitizer=_CSS_SANITIZER,
    strip=True,
    strip_comments=True,
)


# bleach removes disallowed *tags* but keeps their text. For the handful of
# elements whose content is raw text rather than markup, that leaks script or
# stylesheet source into the document as visible prose, so they are dropped
# whole first. A regex is adequate (and only used) here precisely because the
# HTML spec defines these elements' content as running verbatim to the close
# tag - there is no nesting to get wrong.
RAW_TEXT_ELEMENTS = ("script", "style", "template", "textarea", "title", "noscript")
_RAW_TEXT_BLOCK = re.compile(
    r"<\s*(" + "|".join(RAW_TEXT_ELEMENTS) + r")\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)


def strip_raw_text_elements(value):
    """
    Drop raw-text elements *and their content*. Non-strings pass through.

    Shared with AssignmentProcessingService.sanitize_ai_html: both allowlists
    remove the tags, and without this both would leave the script or stylesheet
    source behind as visible prose in the document.
    """
    if not isinstance(value, str):
        return value
    return _RAW_TEXT_BLOCK.sub("", value)


def sanitize_editor_html(html_string: str) -> str:
    """Strip control characters and everything outside the converter allowlist."""
    return _CLEANER.clean(strip_raw_text_elements(strip_control_chars(html_string)))


# ---------------------------------------------------------------------------
# parseDOM helpers
# ---------------------------------------------------------------------------

# A declaration containing a quote, angle bracket or url() is never something
# our renderers emit. bleach keeps such a value inside the `style` attribute
# (correctly - it escapes on output), but this value is stored as JSON and
# re-rendered by a frontend we do not control, so it is dropped rather than
# passed along.
_UNSAFE_STYLE_CHARS = re.compile(
    r"""["'<>\\]|url\s*\(|expression\s*\(""", re.IGNORECASE
)


def safe_style(dom):
    """Return the element's inline style, or None if absent or suspicious."""
    style = dom.get("style")
    if not style or not style.strip():
        return None
    if _UNSAFE_STYLE_CHARS.search(style):
        return None
    return style


TEXT_ALIGN_VALUES = frozenset({"left", "right", "center", "justify", "start", "end"})
DEFAULT_TEXT_ALIGN = "left"


def text_align_from_style(dom, default: str = DEFAULT_TEXT_ALIGN) -> str:
    """
    Read `text-align` out of an inline style attribute.

    Uses partition rather than split(":")[1] because a declaration without a
    colon (`style="text-align"`) is valid-enough HTML but would raise
    IndexError - and this runs inside grading, *after* the AI call has been
    billed, so one malformed attribute must not fail the run. Matches the
    property name exactly so `-webkit-text-align-last` doesn't false-positive,
    and validates the value so only real alignments reach the document.
    """
    style = dom.get("style") or ""
    for declaration in style.split(";"):
        name, separator, value = declaration.partition(":")
        if not separator:
            continue
        if name.strip().lower() != "text-align":
            continue
        candidate = value.strip().lower()
        if candidate in TEXT_ALIGN_VALUES:
            return candidate
    return default


def _span_count(dom, name: str) -> int:
    """colspan/rowspan as a sane positive int; anything else falls back to 1."""
    raw = dom.get(name)
    if raw is None:
        return 1
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    # An upper bound keeps a hostile or hallucinated `colspan="99999999"` from
    # turning into a table the editor spends minutes laying out.
    return value if 1 <= value <= 1000 else 1


def _colwidth(dom):
    """prosemirror-tables stores colwidth as a list of ints, or None."""
    raw = dom.get("data-colwidth")
    if not raw:
        return None
    widths = []
    for part in str(raw).split(","):
        try:
            widths.append(int(part.strip()))
        except (TypeError, ValueError):
            return None
    return widths or None


def _cell_attrs(dom):
    return {
        "colspan": _span_count(dom, "colspan"),
        "rowspan": _span_count(dom, "rowspan"),
        "colwidth": _colwidth(dom),
        "style": safe_style(dom),
    }


def _cell_dom(tag):
    def to_dom(node):
        attrs = {}
        if node.attrs.get("colspan", 1) != 1:
            attrs["colspan"] = node.attrs["colspan"]
        if node.attrs.get("rowspan", 1) != 1:
            attrs["rowspan"] = node.attrs["rowspan"]
        if node.attrs.get("colwidth"):
            attrs["data-colwidth"] = ",".join(str(w) for w in node.attrs["colwidth"])
        if node.attrs.get("style"):
            attrs["style"] = node.attrs["style"]
        return [tag, attrs, 0]

    return to_dom


def safe_link_attrs(dom):
    """
    getAttrs for the `link` mark. Returning False tells ProseMirror the rule
    didn't match, so an unsafe href (javascript:, data:) drops the mark instead
    of preserving it verbatim. bleach already filters these; this is the second
    lock on the same door, because the mark is what ends up in stored JSON.
    """
    href = (dom.get("href") or "").strip()
    scheme = urlparse(href).scheme.lower()
    if scheme and scheme not in LINK_ALLOWED_SCHEMES:
        return False

    return {
        "href": href,
        "title": dom.get("title"),
        "style": safe_style(dom),
        "class": dom.get("class"),
    }


def safe_image_attrs(dom):
    """
    getAttrs for the `image` node. Question images are real (see
    format_assignment_standard_html), so images must survive - but only as
    absolute http(s) URLs. A relative, data: or javascript: src drops the node.
    """
    src = (dom.get("src") or "").strip()
    parsed = urlparse(src)
    if parsed.scheme.lower() not in IMAGE_ALLOWED_SCHEMES or not parsed.netloc:
        return False

    return {"src": src, "alt": dom.get("alt"), "title": dom.get("title")}


def _heading_rule(level: int):
    """
    One parse rule per heading level.

    The level is captured in a closure and returned *from getAttrs*, not
    declared as the rule's static `attrs`: prosemirror-py replaces `rule.attrs`
    wholesale with the getAttrs result, so a statically declared level is
    discarded and every heading silently collapses to the default (1).
    """

    def get_attrs(dom):
        return {
            "level": level,
            "textAlign": text_align_from_style(dom),
            "style": safe_style(dom),
        }

    return {"tag": f"h{level}", "getAttrs": get_attrs}


def _block_style_dom(tag_for):
    def to_dom(node):
        attrs = {}
        style = node.attrs.get("style")
        align = node.attrs.get("textAlign", DEFAULT_TEXT_ALIGN)
        if style and "text-align" in style.lower():
            attrs["style"] = style
        elif style:
            # Preserve the author's style *and* the parsed alignment. The old
            # `style or text-align` short-circuit dropped alignment whenever
            # any other style was present.
            attrs["style"] = f"{style.rstrip(';')}; text-align: {align}"
        elif align != DEFAULT_TEXT_ALIGN:
            attrs["style"] = f"text-align: {align}"
        return [tag_for(node), attrs, 0]

    return to_dom


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def build_schema() -> Schema:
    """
    Assemble the editor schema.

    Public so tests can build an independent instance and assert that doing so
    leaves prosemirror-py's shared specs untouched.
    """
    # add_list_nodes copies internally; marks must be copied explicitly or the
    # updates below mutate the library's module-level dict for the process.
    nodes_spec = add_list_nodes(basic_schema.spec["nodes"], "paragraph block*", "block")
    marks_spec = dict(basic_schema.spec["marks"])

    nodes_spec["paragraph"] = {
        "content": "inline*",
        "group": "block",
        "attrs": {
            "textAlign": {"default": DEFAULT_TEXT_ALIGN},
            "style": {"default": None},
        },
        "parseDOM": [
            {
                "tag": "p",
                "getAttrs": lambda dom: {
                    "textAlign": text_align_from_style(dom),
                    "style": safe_style(dom),
                },
            }
        ],
        "toDOM": _block_style_dom(lambda node: "p"),
    }

    nodes_spec["heading"] = {
        "attrs": {
            "level": {"default": 1},
            "textAlign": {"default": DEFAULT_TEXT_ALIGN},
            "style": {"default": None},
        },
        "content": "inline*",
        "group": "block",
        "defining": True,
        "parseDOM": [_heading_rule(level) for level in range(1, 7)],
        "toDOM": _block_style_dom(lambda node: f"h{node.attrs['level']}"),
    }

    nodes_spec["image"] = {
        "inline": True,
        "group": "inline",
        "draggable": True,
        "attrs": {
            "src": {},
            "alt": {"default": None},
            "title": {"default": None},
        },
        "parseDOM": [{"tag": "img[src]", "getAttrs": safe_image_attrs}],
        "toDOM": lambda node: [
            "img",
            {
                "src": node.attrs["src"],
                "alt": node.attrs["alt"],
                "title": node.attrs["title"],
            },
        ],
    }

    # Annotated as a plain dict: `tableRole` is a prosemirror-tables convention
    # that the library's NodeSpec TypedDict does not declare, and the schema
    # compiler passes unknown keys through untouched.
    table_nodes: dict = {
        "table": {
            "content": "table_row+",
            "tableRole": "table",
            "group": "block",
            "isolating": True,
            "parseDOM": [{"tag": "table"}],
            "toDOM": lambda _: ["table", ["tbody", 0]],
        },
        "table_row": {
            "content": "(table_cell | table_header)*",
            "tableRole": "row",
            "parseDOM": [{"tag": "tr"}],
            "toDOM": lambda _: ["tr", 0],
        },
        "table_cell": {
            "content": "block+",
            "attrs": {
                "colspan": {"default": 1},
                "rowspan": {"default": 1},
                "colwidth": {"default": None},
                "style": {"default": None},
            },
            "tableRole": "cell",
            "isolating": True,
            "parseDOM": [{"tag": "td", "getAttrs": _cell_attrs}],
            "toDOM": _cell_dom("td"),
        },
        "table_header": {
            "content": "block+",
            "attrs": {
                "colspan": {"default": 1},
                "rowspan": {"default": 1},
                "colwidth": {"default": None},
                "style": {"default": None},
            },
            "tableRole": "header_cell",
            "isolating": True,
            "parseDOM": [{"tag": "th", "getAttrs": _cell_attrs}],
            "toDOM": _cell_dom("th"),
        },
    }
    nodes_spec.update(table_nodes)

    marks_spec["link"] = {
        "attrs": {
            "href": {},
            "title": {"default": None},
            "style": {"default": None},
            "class": {"default": None},
        },
        "inclusive": False,
        "parseDOM": [{"tag": "a[href]", "getAttrs": safe_link_attrs}],
        "toDOM": lambda mark: [
            "a",
            {
                "href": mark.attrs["href"],
                "title": mark.attrs["title"],
                "style": mark.attrs["style"],
                "class": mark.attrs["class"],
            },
            0,
        ],
    }

    # `class` matters as much as `style` here: sanitize_ai_html permits
    # class="math-block" on spans (and permits no style at all), so a mark that
    # only carried `style` threw away the one attribute that actually arrives.
    marks_spec["textStyle"] = {
        "attrs": {"style": {"default": None}, "class": {"default": None}},
        "parseDOM": [
            {
                "tag": "span",
                "getAttrs": lambda dom: (
                    False
                    if not (dom.get("style") or dom.get("class"))
                    else {"style": safe_style(dom), "class": dom.get("class")}
                ),
            }
        ],
        "toDOM": lambda mark: [
            "span",
            {"style": mark.attrs["style"], "class": mark.attrs["class"]},
            0,
        ],
    }

    # Without these three marks `<sup>`, `<sub>` and `<u>` are allowed through
    # sanitisation and then flattened to bare text - turning x² into x2 and
    # H₂O into H2O in answers that are subsequently graded.
    marks_spec["underline"] = {
        "parseDOM": [
            {"tag": "u"},
            {"style": "text-decoration=underline"},
            {"style": "text-decoration-line=underline"},
        ],
        "toDOM": lambda _mark: ["u", 0],
    }
    # Named "strike", not "strikethrough": Tiptap's stock strike extension
    # (@tiptap/extension-strike) registers its mark under that name, and a
    # document must use the same node/mark names as the editor loading it -
    # otherwise the frontend either drops the mark on load or throws.
    marks_spec["strike"] = {
        "parseDOM": [{"tag": "s"}, {"tag": "del"}, {"tag": "strike"}],
        "toDOM": lambda _mark: ["s", 0],
    }
    marks_spec["superscript"] = {
        "excludes": "superscript subscript",
        "parseDOM": [{"tag": "sup"}],
        "toDOM": lambda _mark: ["sup", 0],
    }
    marks_spec["subscript"] = {
        "excludes": "superscript subscript",
        "parseDOM": [{"tag": "sub"}],
        "toDOM": lambda _mark: ["sub", 0],
    }

    return Schema({"nodes": nodes_spec, "marks": marks_spec})


# Built once, at import: schema construction is pure and deterministic, and a
# single shared instance is what makes the parse rules above safe to define as
# module-level closures.
PROSEMIRROR_SCHEMA = build_schema()

EMPTY_DOCUMENT = {"type": "doc", "content": [{"type": "paragraph"}]}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def html_to_prosemirror_json(html_string: str) -> dict:
    """
    Convert an HTML string into a ProseMirror document.

    Args:
        html_string: Raw HTML. Sanitised here; callers need not pre-clean it.

    Returns:
        The ProseMirror document as a dict.

    Raises:
        ValueError: input is not a non-empty string.
        ProseMirrorConversionError: the HTML could not be parsed or converted.
            Subclasses RuntimeError.
    """
    if not isinstance(html_string, str) or not html_string.strip():
        raise ValueError("Input must be a non-empty string.")

    try:
        safe_html = sanitize_editor_html(html_string)
        # Wrapped in a div so a multi-root fragment parses as one tree; lxml
        # would otherwise take the first element as the root and discard its
        # siblings.
        dom = lxml_html.fromstring(f"<div>{safe_html}</div>")
        document = DOMParser.from_schema(PROSEMIRROR_SCHEMA).parse(dom)
        return dict(document.to_json())
    except Exception as exc:
        # Deliberately broad, but no longer silent: this runs inside Celery
        # grading tasks where the only signal is what gets logged.
        logger.exception(
            "HTML -> ProseMirror conversion failed (%s chars, starts %r)",
            len(html_string),
            html_string[:200],
        )
        raise ProseMirrorConversionError(
            f"Failed to convert HTML to ProseMirror JSON: {exc}"
        ) from exc


# Conversion is a pure function of the HTML string (asserted by
# SchemaIsolationTest.test_repeated_conversions_are_deterministic), so identical
# input can safely return a cached result and no invalidation is needed - a
# changed assignment produces changed HTML, which is a different cache key.
#
# This matters because AssignmentSerializer regenerates the student-facing
# document on *every read* rather than storing it, and a 20-question assignment
# costs ~220ms to convert. The bound keeps worst-case memory around a few MB.
CONVERSION_CACHE_SIZE = 64


@lru_cache(maxsize=CONVERSION_CACHE_SIZE)
def _cached_prosemirror_text(html_string: str) -> str:
    return json.dumps(html_to_prosemirror_json(html_string))


def html_to_prosemirror_text(html_string: str) -> str:
    """
    Convert HTML to a ProseMirror document serialised as a JSON string.

    This is what callers persisting to `raw_input` should use. Both raw_input
    columns are TextFields, and assigning the dict form lets Django coerce it
    with str(), storing a Python repr ("{'type': 'doc'...}") that no JSON
    parser will read back.

    Repeat conversions of identical HTML are served from a process-local cache.
    """
    # Validated before the cache lookup so an unhashable argument raises
    # ValueError like every other bad input, rather than TypeError from
    # lru_cache trying to key on it.
    if not isinstance(html_string, str) or not html_string.strip():
        raise ValueError("Input must be a non-empty string.")

    return _cached_prosemirror_text(html_string)
