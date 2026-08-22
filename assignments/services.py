import base64
import logging
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import bleach
import fitz
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape as escape_html
from django.utils.html import strip_tags
from lxml import html as lxml_html
from PIL import Image
from rest_framework.exceptions import ParseError

from ai_processor.services import ai_processor, pdf_service
from ai_processor.tools import (
    ImageCompressionError,
    compress_image_for_upload,
    encode_image,
)
from assignments.prosemirror_converter import (
    html_to_prosemirror_json,
    html_to_prosemirror_text,
    strip_control_chars,
    strip_raw_text_elements,
)
from students.task_tracking import (
    ensure_task_not_cancelled,
    lock_processing_task_for_final_save,
)

logger = logging.getLogger(__name__)

# from docutils.transforms.universal import Validate

# from ai_processor.services import ai_processor

# from assignments.models import Assignment

# AI-generated content is untrusted input. It's only ever supposed to use
# plain formatting markup (see the "HTML Must Be Clean" rules in
# ai_processor/ASSIGNMENT_GENERATION_PROMPT_6.txt) - that's an instruction
# to the model, not an enforced boundary, so every AI-supplied HTML field
# is run through this allowlist before it's stored or rendered. No links,
# images, scripts, or style/event attributes are permitted: those aren't
# needed for assignment text and are the tags/attributes that make markup
# executable.
AI_HTML_ALLOWED_TAGS = [
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "sub",
    "sup",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "code",
    "pre",
    "span",
    "br",
    "hr",
    "blockquote",
]


def _allow_math_block_class(tag, name, value):
    return name == "class" and value == "math-block"


AI_HTML_ALLOWED_ATTRIBUTES: Dict[str, Any] = {
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
    "span": _allow_math_block_class,
}


def _none_default(value, default):
    """
    Coalesce to `default` on None only - never on another falsy value like 0,
    False, or "".

    `dict.get(key, default)` only substitutes when `key` is absent; it passes
    an explicit `None` straight through. That distinction matters here because
    several of the fields read by format_assignment_standard_html come from
    columns that are null=True (Assignment.title/.instructions/.total_points/
    .questions) or from AI extraction, which can emit an explicit `null` for
    an optional field - so `None` is exactly as live as a missing key. Using
    `value or default` instead would be wrong in the other direction: it would
    replace a legitimate `0` (e.g. a 0-point rubric level) with the default.
    """
    return default if value is None else value


def _list_or_empty(value, field_name):
    """
    `_none_default` to [], then coerce a non-list survivor (e.g. AI extraction
    returning a string where a list was expected) to [] as well, logging
    once so the malformed shape is visible without failing the document.
    """
    value = _none_default(value, [])
    if not isinstance(value, list):
        logger.warning(
            "format_assignment_standard_html: '%s' was %r, not a list; "
            "treating as empty.",
            field_name,
            type(value).__name__,
        )
        return []
    return value


# Matches a letter option-marker the AI may already have baked into the option
# text ("A) ...", "A. ...", "(A) ...") so it can be stripped before we prepend
# our own generated letter - otherwise the rendered option shows the letter
# twice ("A. A) ...").
_OPTION_LETTER_PREFIX_RE = re.compile(r"^\s*(?:\([A-Za-z]\)|[A-Za-z][.)])\s*")


def _strip_leading_option_letter(text):
    """
    Strip any pre-existing "A)"/"A."/"(A)" marker(s) from AI-supplied option
    text. Loops rather than a single substitution: option text has been seen
    with the marker baked in more than once (e.g. after an edit round-trips
    the assignment through AI re-extraction on top of already-rendered
    content), and leaving even one copy behind still doubles up against the
    letter this module generates and prepends itself.
    """
    if not isinstance(text, str):
        return text
    while True:
        stripped = _OPTION_LETTER_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            return stripped
        text = stripped


def _strip_html_from_title(value):
    """
    Reduce `title` to plain text.

    AI extraction wraps the title in heading/paragraph tags meant for the
    rich editor/PDF body (format_assignment_standard_html re-wraps a plain
    title in its own <h1> for that rendering). `title` itself is read
    verbatim in plain-text contexts - notification emails, PDF
    headers/filenames, list views - so raw markup must never reach it,
    regardless of which write path (DRF serializer, AI extraction task,
    admin, shell) produced it.
    """
    if not isinstance(value, str) or not value:
        return value
    return re.sub(r"\s+", " ", strip_tags(value)).strip()


def _option_letter(index: int) -> str:
    """A, B, C, ... Z, then falls back to a 1-based number past 26 options."""
    return chr(65 + index) if index < 26 else str(index + 1)


def _render_lettered_option_html(letter: str, sanitized_option_html: str) -> str:
    """
    Merge a "<strong>A. </strong>" marker into the FRONT of the option's own
    leading paragraph, rather than wrapping the option in a second, outer
    <p>.

    The AI sometimes returns a bare-text option ("A) $x=5$") and sometimes a
    fully <p>-wrapped one ("<p>Evaporation</p>", confirmed against a live
    generation call) - both need the same visible result. Wrapping either
    form in an outer `<p><strong>A. </strong>{option}</p>` is fine for the
    bare-text case, but for the pre-wrapped case it nests a <p> inside a
    <p>, which is invalid HTML: the parser that turns this into ProseMirror
    JSON auto-closes the outer <p> as soon as it meets the inner one,
    splitting the letter and the option's actual text into two separate,
    disconnected paragraphs - the option renders with no visible text next
    to its own letter.
    """
    if not sanitized_option_html.strip():
        return f"<p><strong>{letter}. </strong></p>"

    fragment = lxml_html.fromstring(f"<div>{sanitized_option_html}</div>")
    marker = lxml_html.fromstring(f"<strong>{letter}. </strong>")
    children = list(fragment)

    if children and children[0].tag == "p":
        target = children[0]
        marker.tail = target.text or ""
        target.text = None
        target.insert(0, marker)
    else:
        # No wrapping <p> at all (bare text and/or inline tags only) - build
        # one, with the marker leading whatever text/elements came first.
        target = lxml_html.fromstring("<p></p>")
        marker.tail = fragment.text or ""
        target.append(marker)
        for child in children:
            target.append(child)
        fragment = lxml_html.fromstring("<div></div>")
        fragment.append(target)
        children = [target]

    return "".join(lxml_html.tostring(child, encoding="unicode") for child in children)


def _parse_due_date(due_date):
    """
    Render `due_date` as "Month DD, YYYY", or None if absent/unparsable.

    A malformed due_date (wrong type, or a string fromisoformat rejects) is
    logged and treated as absent rather than raising - assignment generation
    sits downstream of a billed AI call, and a due-date typo should not fail
    the entire document.
    """
    if not due_date:
        return None
    try:
        return datetime.fromisoformat(str(due_date).replace("Z", "+00:00")).strftime(
            "%B %d, %Y"
        )
    except (ValueError, TypeError, OverflowError):
        logger.warning(
            "format_assignment_standard_html: unparsable due_date %r; omitting.",
            due_date,
        )
        return None


class PDFService:
    """
    Service class for extracting structured data from assignment PDFs
    """

    def __init__(self, uploaded_file: UploadedFile) -> None:
        self.uploaded_file = uploaded_file
        self.extracted_data = {
            "title": "",
            "questions": [],
            "page_count": 0,
        }

    def extract(self) -> dict:
        """
        Extract data from the uploaded pdf
        """

        if self.uploaded_file.content_type != "application/pdf":
            raise ValueError("Unsupported file format. Only PDF is supported.")
        else:
            self.__process_pdf()
        return self.extracted_data

    def __process_pdf(self):
        """
        Process the PDF using fitz (PyMuPDF) to extract data from the UploadedFile object.
        """
        try:
            pdf_bytes = self.uploaded_file.read()
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")

            self.extracted_data["page_count"] = pdf_document.page_count

            full_text = ""

            for page_number in range(pdf_document.page_count):
                page = pdf_document.load_page(page_number)
                full_text += page.get_text().strip()

            # Use the filename for the title
            self.extracted_data["title"] = Path(self.uploaded_file.name).stem
            self.extracted_data["questions"] = full_text

            pdf_document.close()
        except Exception as e:
            raise ValueError(f"Something went wrong: {str(e)}") from e


class AssignmentProcessingService:
    IMAGE_FORMATS = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    PDF_FORMAT = "application/pdf"

    @classmethod
    def prepare_ai_content(cls, uploaded_file, prompt_text: str):
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]

        if uploaded_file.content_type in cls.IMAGE_FORMATS:
            try:
                image = Image.open(BytesIO(uploaded_file.read()))
                compressed_bytes = compress_image_for_upload(image)
            except ImageCompressionError as exc:
                raise ParseError(str(exc)) from exc
            base64_data = encode_image(image_byte=compressed_bytes)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"},
                    "bytes": base64_data,
                }
            )
        elif uploaded_file.content_type == cls.PDF_FORMAT:
            pdf_service.set_uploaded_file(uploaded_file)
            try:
                images = pdf_service.extract()
            except (ValueError, ImageCompressionError) as exc:
                raise ParseError(str(exc)) from exc

            for image in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                        "bytes": image,
                    }
                )
        else:
            raise ParseError(
                f"Unsupported format: {uploaded_file.name}. "
                "Only images (JPEG, PNG, GIF, WebP) and PDFs are allowed."
            )

        return content

    @classmethod
    def clean_xml_text(cls, value):
        return strip_control_chars(value)

    @classmethod
    def sanitize_ai_html(cls, value):
        """
        Strip AI-generated HTML down to a plain-formatting allowlist.

        The AI is only instructed (not enforced) to emit safe markup, so
        this is the actual security boundary: scripts, event handlers,
        links, images, and inline style/class are dropped rather than
        trusted.
        """
        if not isinstance(value, str) or not value:
            return value

        cleaned = bleach.clean(
            # Raw-text elements are dropped whole first: bleach removes the
            # <script>/<style> tags but keeps their source, which would
            # otherwise surface as visible prose in the rendered assignment.
            strip_raw_text_elements(cls.clean_xml_text(value)),
            tags=AI_HTML_ALLOWED_TAGS,
            attributes=AI_HTML_ALLOWED_ATTRIBUTES,
            protocols=[],
            strip=True,
        )
        return cleaned

    @classmethod
    def sanitize_ai_image_url(cls, value):
        """
        Only allow absolute http(s) image URLs through - anything else
        (javascript:, data:, or a malformed value that could break out of
        the surrounding HTML attribute) is dropped.
        """
        if not isinstance(value, str) or not value.strip():
            return ""

        candidate = value.strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""

        return candidate

    @classmethod
    def build_async_upload_payload(cls, uploaded_file: UploadedFile) -> dict:
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)

        return {
            "name": uploaded_file.name,
            "content_type": uploaded_file.content_type,
            "content_b64": base64.b64encode(file_bytes).decode("utf-8"),
        }

    @classmethod
    def rebuild_uploaded_file(cls, payload: dict) -> SimpleUploadedFile:
        file = SimpleUploadedFile(
            name=payload["name"],
            content=base64.b64decode(payload["content_b64"]),
            content_type=payload["content_type"],
        )

        return file

    @classmethod
    def html_to_prosemirror_json(cls, html_string: str) -> dict:
        """
        Convert an HTML string into a ProseMirror document (as a dict).

        Thin delegate to assignments.prosemirror_converter, which owns the
        schema. Prefer html_to_prosemirror_text() when the result is headed
        for a raw_input column - see that method for why.

        Raises:
            ValueError: input is not a non-empty string.
            prosemirror_converter.ProseMirrorConversionError: conversion
                failed. Subclasses RuntimeError, so existing handlers keep
                working.
        """
        return html_to_prosemirror_json(html_string)

    @classmethod
    def html_to_prosemirror_text(cls, html_string: str) -> str:
        """
        Convert an HTML string into a ProseMirror document serialised as JSON.

        This is the correct entry point for anything persisting to a
        `raw_input` column. Both columns are TextFields: assigning the dict
        form lets Django coerce it with str(), which stores a Python repr
        ("{'type': 'doc', ...}") that no JSON parser can read back.
        """
        return html_to_prosemirror_text(html_string)

    @classmethod
    def format_assignment_standard_html(
        cls,
        data: dict,
        include_rubric: bool = True,
        include_document_header: bool = True,
    ) -> str:
        """
        Converts structured assignment data into a globally recognized academic format.
        Preserves ALL HTML and displays questions, rubrics, and model answers professionally.

        Args:
            data: Structured assignment dict with title, instructions, questions, etc.
            include_rubric: When False, rubric tables and model answers are omitted.
                            Use False when generating the student-facing version.
            include_document_header: When False, the title/instructions/meta blocks
                            are omitted and only the questions are rendered. Use False
                            when the caller already renders its own title/instructions
                            (e.g. the PDF download endpoint), so they aren't duplicated.

        Every field read here can legitimately be None, not just absent: the
        Assignment columns this data is built from (title, instructions,
        total_points, questions) are all null=True, and per-question fields
        come from AI extraction, which can emit an explicit `null`. A bare
        `dict.get(key, default)` only substitutes on a *missing* key, so an
        explicit None previously either rendered the literal text "None"
        into a document a student or teacher sees, or crashed the whole
        assignment (e.g. `None.upper()`) - taking down a page that usually
        sits downstream of a billed AI call. Every read below goes through
        `_none_default` instead, and malformed per-item data (a non-dict
        question/rubric entry, a non-list options/rubric, an unparsable
        due_date) is logged and skipped rather than allowed to fail the
        entire document.
        """

        questions = _none_default(data.get("questions"), [])
        if not isinstance(questions, list):
            logger.warning(
                "format_assignment_standard_html: 'questions' was %r, not a "
                "list; treating as empty.",
                type(questions).__name__,
            )
            questions = []

        # Title is always reduced to plain text and re-wrapped in a real <h1>
        # here, rather than trusting whatever markup AI extraction put in
        # `data["title"]` - this is the single choke point every caller
        # (fresh AI drafts, saved Assignment rows alike) renders through, so
        # the title always displays as prominent, first-in-document heading
        # regardless of how it arrived.
        title_plain = _strip_html_from_title(_none_default(data.get("title"), ""))
        title_html = f"<h1>{escape_html(title_plain)}</h1>" if title_plain else ""
        instructions_html = cls.sanitize_ai_html(
            _none_default(data.get("instructions"), "")
        )
        due_date = _parse_due_date(data.get("due_date"))
        total_points = escape_html(str(_none_default(data.get("total_points"), 0)))

        html_output = []

        if include_document_header:
            # Title
            html_output.append(
                f"""
            <div style="text-align:center; margin-bottom:25px;">
                {title_html}
            </div>
            """
            )

            # Instructions
            html_output.append(
                f"""
            <div style="margin-bottom:20px;">
                {instructions_html}
            </div>
            """
            )

            # Meta
            html_output.append(
                f"""
            <div style="margin-bottom:30px;">
                <p><strong>Total Marks:</strong> {total_points}</p>
                {"<p><strong>Due Date:</strong> " + due_date + "</p>" if due_date else ""}
            </div>
            """
            )

        # Questions Header
        html_output.append(
            """
        <h2>Assignment Questions</h2>
        <hr>
        """
        )

        # Questions
        for q in questions:
            if not isinstance(q, dict):
                logger.warning(
                    "format_assignment_standard_html: skipping non-dict "
                    "question entry %r.",
                    q,
                )
                continue

            q_no = escape_html(str(_none_default(q.get("question_number"), "")))
            q_points = escape_html(str(_none_default(q.get("points"), "")))
            q_text = cls.sanitize_ai_html(_none_default(q.get("question_text"), ""))
            q_type = str(_none_default(q.get("question_type"), "")).upper()
            options = _list_or_empty(q.get("options"), "options")
            rubric = _list_or_empty(q.get("rubric"), "rubric")
            model_answer = cls.sanitize_ai_html(
                _none_default(q.get("model_answer"), "")
            )
            image_url = cls.sanitize_ai_image_url(q.get("question_image", ""))

            html_output.append(
                f"""
            <div style="margin-bottom:40px;">
                <p><strong>Question {q_no} ({q_points} marks)</strong></p>
                {q_text}
            """
            )

            # Question Image
            if image_url:
                # Built as its own statement, not inlined into the f-string
                # below: the double-quotes around the HTML attributes are
                # deliberate markup, not Python repr formatting, so B907's
                # !r suggestion doesn't apply — but a `# noqa` placed
                # inside a multi-line f-string literal isn't a comment at
                # all, it becomes literal string content and would leak
                # into the rendered HTML. Isolating the tag onto its own
                # line gives the suppression somewhere real to attach.
                img_tag = (
                    f'<img src="{escape_html(image_url)}" '  # noqa: B907
                    f'style="max-width:100%; height:auto;" '
                    f'alt="Question {q_no} image">'
                )
                html_output.append(
                    f"""
                <div style="margin-top:12px; margin-bottom:12px; text-align:center;">
                    {img_tag}
                </div>
                """
                )

            # Objective Options
            #
            # Rendered as lettered paragraphs ("A.", "B.", ...), not a
            # <ul>/<li> list. The bullet-list form is schema-valid and
            # survives conversion fine, but a lettered MCQ reads as answer
            # choices; bullets read as an unordered list of facts. The
            # letter is generated here from the option's position rather
            # than trusted from the AI's own text, because the AI already
            # bakes a marker into that text ("A) ...", "(A) ...", "A. ...")
            # per the extraction/generation prompts - using both would show
            # the letter twice ("A. A) ..."), so the AI's marker is stripped
            # first via _strip_leading_option_letter.
            #
            # The separating space is baked into the <strong> tag's own text
            # ("A. ", not "A." followed by a bare space) rather than left as
            # its own text node. When the option text itself starts with
            # bold content (the AI does emit fully-bolded options), a space
            # sitting *between* two <strong> runs is whitespace ProseMirror's
            # DOMParser treats as insignificant and drops on conversion - the
            # two runs merge into one and the letter and option text collide
            # into "A.Bold option" with no gap. Keeping the space inside the
            # first run's own text survives that merge either way.
            #
            # The marker is merged into the option's own leading paragraph
            # via _render_lettered_option_html rather than wrapped around it
            # - see that function's docstring for why a naive outer <p> is
            # broken for AI options that already arrive <p>-wrapped.
            if q_type == "OBJECTIVE" and options:
                for i, opt in enumerate(options):
                    letter = _option_letter(i)
                    opt_text = _strip_leading_option_letter(_none_default(opt, ""))
                    opt_html = cls.sanitize_ai_html(opt_text)
                    html_output.append(_render_lettered_option_html(letter, opt_html))

            # Rubric (for essay & short answer) — hidden from students
            if include_rubric and rubric:
                html_output.append(
                    """
                <div style="margin-top:15px;">
                    <p><strong>Marking Guide / Rubric:</strong></p>
                    <table border="1" cellpadding="6" cellspacing="0" width="100%" style="border-collapse:collapse;">
                        <thead>
                            <tr>
                                <th align="left">Performance Level</th>
                                <th align="center">Marks</th>
                                <th align="left">Criteria</th>
                            </tr>
                        </thead>
                        <tbody>
                """
                )

                for r in rubric:
                    if not isinstance(r, dict):
                        logger.warning(
                            "format_assignment_standard_html: skipping "
                            "non-dict rubric entry %r.",
                            r,
                        )
                        continue

                    level = cls.sanitize_ai_html(
                        str(_none_default(r.get("level"), "")).title()
                    )
                    points = escape_html(str(_none_default(r.get("points"), "")))
                    desc = cls.sanitize_ai_html(_none_default(r.get("description"), ""))

                    html_output.append(
                        f"""
                        <tr>
                            <td>{level}</td>
                            <td align="center">{points}</td>
                            <td>{desc}</td>
                        </tr>
                    """
                    )

                html_output.append(
                    """
                        </tbody>
                    </table>
                </div>
                """
                )

            # Model Answer — hidden from students
            if include_rubric and model_answer:
                html_output.append(
                    f"""
                <div style="margin-top:15px;">
                    <p><strong>Model Answer / Expected Response:</strong></p>
                    <div style="padding-left:15px;">
                        {model_answer}
                    </div>
                </div>
                """
                )

            html_output.append("</div>")

        return "\n".join(html_output)

    @classmethod
    def extract_assignment_data(
        cls,
        user,
        content,
        *,
        assignment=None,
        course=None,
        topic=None,
        raw_input=None,
        keep_existing_title=False,
        generate_raw_input=False,
        upload=False,
        processing_task_id=None,
    ) -> dict:

        print("Extracting assignment content")

        # assignment = Assignment.objects.get(id=assignment_id)

        ensure_task_not_cancelled(processing_task_id)
        extraction_started_at = timezone.now()
        assignment_questions = ai_processor.extract_assignment_with_retry(
            user,
            content,
            max_retries=3,
            upload=upload,
            processing_task_id=processing_task_id,
        )
        extraction_completed_at = timezone.now()

        if keep_existing_title and assignment and assignment.title:
            assignment_questions["title"] = assignment.title

        assignment_questions["ai_generated"] = False
        assignment_questions["ai_raw_payload"] = {
            "title": (assignment_questions["title"]),
            "instructions": assignment_questions["instructions"],
            "questions": assignment_questions["questions"],
        }
        assignment_questions["extraction_started_at"] = extraction_started_at
        assignment_questions["extraction_completed_at"] = extraction_completed_at

        resolved_course = course or (assignment.course if assignment else None)
        resolved_topic = (
            topic if topic is not None else (assignment.topic if assignment else None)
        )

        if resolved_course is not None:
            assignment_questions["course"] = (
                resolved_course.id
                if hasattr(resolved_course, "id")
                else resolved_course
            )

        if resolved_topic is not None:
            assignment_questions["topic"] = (
                resolved_topic.id if hasattr(resolved_topic, "id") else resolved_topic
            )

        if raw_input is not None:
            assignment_questions["raw_input"] = raw_input

        if generate_raw_input:
            ensure_task_not_cancelled(processing_task_id)
            assignment_html = cls.format_assignment_standard_html(assignment_questions)
            assignment_questions["raw_input"] = cls.html_to_prosemirror_text(
                assignment_html
            )

        return assignment_questions

    @classmethod
    def update_assignment_from_extraction(
        cls,
        user,
        assignment,
        content,
        *,
        topic=None,
        raw_input=None,
        keep_existing_title=False,
        upload=False,
        processing_task_id=None,
    ):
        from .serializers import AssignmentSerializer

        assignment_data = cls.extract_assignment_data(
            user,
            content,
            assignment=assignment,
            topic=topic,
            raw_input=raw_input,
            keep_existing_title=keep_existing_title,
            upload=upload,
            processing_task_id=processing_task_id,
        )

        with transaction.atomic():
            lock_processing_task_for_final_save(processing_task_id)
            serializer = AssignmentSerializer(
                assignment, data=assignment_data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

        if assignment.submissions.exists():
            from students.services import notify_students_of_assignment_edit

            notify_students_of_assignment_edit(assignment)

        return assignment

    @classmethod
    def extract_assignment(cls, user, assignment, content):
        from .serializers import AssignmentListSerializer

        updated_assignment = cls.update_assignment_from_extraction(
            user, assignment, content, keep_existing_title=True
        )
        return AssignmentListSerializer(updated_assignment)
