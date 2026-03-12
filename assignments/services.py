import base64
import json
import re
import string
from datetime import datetime
from pathlib import Path

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.utils import timezone
from lxml import html
from prosemirror.model import DOMParser, Schema
from prosemirror.schema.basic import schema as basic_schema
from prosemirror.schema.list import add_list_nodes
from rest_framework.exceptions import ParseError

from ai_processor.services import ai_processor, pdf_service
from ai_processor.tools import encode_image

from .serializers import AssignmentListSerializer, AssignmentSerializer

# from docutils.transforms.universal import Validate

# from ai_processor.services import ai_processor

# from assignments.models import Assignment

INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


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
        content = [{"type": "text", "text": prompt_text}]

        if uploaded_file.content_type in cls.IMAGE_FORMATS:
            base64_data = encode_image(uploaded_file)
            content.append(
                {
                    "type": "image_url",
                    "image_url": f"data:{uploaded_file.content_type};base64,{base64_data}",
                    "bytes": base64_data,
                }
            )
        elif uploaded_file.content_type == cls.PDF_FORMAT:
            pdf_service.set_uploaded_file(uploaded_file)
            images = pdf_service.extract()

            for image in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": f"data:image/PNG;base64,{image}",
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
        if not isinstance(value, str):
            return value
        return INVALID_XML_CHARS.sub("", value)

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
        Convert an HTML string into ProseMirror-compatible JSON using prosemirror-py.

        Args:
            html_string (str): Raw HTML content.

        Returns:
            dict: ProseMirror JSON document.

        Raises:
            ValueError: If input is empty or invalid.
            RuntimeError: If HTML parsing or ProseMirror conversion fails.
        """

        if not isinstance(html_string, str) or not html_string.strip():
            raise ValueError("Input must be a non-empty string.")

        try:
            # 1. Start with base nodes and marks
            nodes_spec = add_list_nodes(
                basic_schema.spec["nodes"], "paragraph block*", "block"
            )
            marks_spec = basic_schema.spec["marks"]

            # 2. Update Paragraph AND Headings to preserve alignment/styles
            # We loop through headings 1-6 to apply the same CSS-preservation logic
            updated_nodes = {}

            # Paragraph Override
            updated_nodes["paragraph"] = {
                "content": "inline*",
                "group": "block",
                "attrs": {"textAlign": {"default": "left"}, "style": {"default": None}},
                "parseDOM": [
                    {
                        "tag": "p",
                        "getAttrs": lambda dom: {
                            "textAlign": (
                                next(
                                    (
                                        s.split(":")[1].strip()
                                        for s in dom.get("style", "").split(";")
                                        if "text-align" in s.lower()
                                    ),
                                    "left",
                                )
                            ),
                            "style": dom.get("style"),
                        },
                    }
                ],
                "toDOM": lambda node: [
                    "p",
                    {
                        "style": node.attrs.get("style")
                        or f"text-align: {node.attrs['textAlign']}"
                    },
                    0,
                ],
            }

            # Heading Override (h1 through h6)
            updated_nodes["heading"] = {
                "attrs": {
                    "level": {"default": 1},
                    "textAlign": {"default": "left"},
                    "style": {"default": None},
                },
                "content": "inline*",
                "group": "block",
                "defining": True,
                "parseDOM": [
                    {
                        "tag": f"h{i}",
                        "attrs": {"level": i},
                        "getAttrs": lambda dom: {
                            "textAlign": (
                                next(
                                    (
                                        s.split(":")[1].strip()
                                        for s in dom.get("style", "").split(";")
                                        if "text-align" in s.lower()
                                    ),
                                    "left",
                                )
                            ),
                            "style": dom.get("style"),
                        },
                    }
                    for i in range(1, 7)
                ],
                "toDOM": lambda node: [
                    f"h{node.attrs['level']}",
                    {"style": node.attrs.get("style")},
                    0,
                ],
            }

            # Apply updates to the nodes spec
            nodes_spec.update(updated_nodes)

            nodes_spec.update(
                {
                    "table": {
                        "content": "table_row*",
                        "tableRole": "table",
                        "group": "block",
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
                        "attrs": {"style": {"default": None}},
                        "tableRole": "cell",
                        "parseDOM": [
                            {
                                "tag": "td",
                                "getAttrs": lambda dom: {"style": dom.get("style")},
                            }
                        ],
                        "toDOM": lambda node: ["td", {"style": node.attrs["style"]}, 0],
                    },
                    "table_header": {
                        "content": "block+",
                        "attrs": {"style": {"default": None}},
                        "tableRole": "header_cell",
                        "parseDOM": [
                            {
                                "tag": "th",
                                "getAttrs": lambda dom: {"style": dom.get("style")},
                            }
                        ],
                        "toDOM": lambda node: ["th", {"style": node.attrs["style"]}, 0],
                    },
                }
            )

            # 3. Update Marks (Links and Spans)
            # We add 'textStyle' as a catch-all for spans and preserve style on links
            marks_spec.update(
                {
                    "link": {
                        "attrs": {
                            "href": {},
                            "title": {"default": None},
                            "style": {"default": None},
                            "class": {"default": None},
                        },
                        "inclusive": False,
                        "parseDOM": [
                            {
                                "tag": "a[href]",
                                "getAttrs": lambda dom: {
                                    "href": dom.get("href"),
                                    "title": dom.get("title"),
                                    "style": dom.get("style"),
                                    "class": dom.get("class"),
                                },
                            }
                        ],
                        "toDOM": lambda node: [
                            "a",
                            {
                                "href": node.attrs["href"],
                                "title": node.attrs["title"],
                                "style": node.attrs["style"],
                                "class": node.attrs["class"],
                            },
                            0,
                        ],
                    }
                }
            )

            # Add the 'textStyle' mark to catch general <span> CSS (colors, font-size, etc.)
            marks_spec.update(
                {
                    "textStyle": {
                        "attrs": {"style": {"default": None}},
                        "parseDOM": [
                            {
                                "tag": "span",
                                "getAttrs": lambda dom: {"style": dom.get("style")},
                            }
                        ],
                        "toDOM": lambda mark: [
                            "span",
                            {"style": mark.attrs["style"]},
                            0,
                        ],
                    }
                }
            )

            # 4. Construct the Schema using our CUSTOMIZED specs
            my_schema = Schema({"nodes": nodes_spec, "marks": marks_spec})

            # 5. Parse the HTML
            safe_html = cls.clean_xml_text(html_string)
            dom = html.fromstring(f"<div>{safe_html}</div>")
            doc = DOMParser.from_schema(my_schema).parse(dom)

            return doc.to_json()
        except Exception as e:
            raise RuntimeError(
                f"Failed to convert HTML to ProseMirror JSON: {str(e)}"
            ) from e

    @classmethod
    def format_assignment_standard_html(cls, data: dict) -> str:
        """
        Converts structured assignment data into a globally recognized academic format.
        Preserves ALL HTML and displays questions, rubrics, and model answers professionally.
        """

        title_html = cls.clean_xml_text(data.get("title", ""))
        instructions_html = cls.clean_xml_text(data.get("instructions", ""))
        due_date = data.get("due_date")
        total_points = data.get("total_points", 0)
        questions = data.get("questions", [])

        if due_date:
            due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00")).strftime(
                "%B %d, %Y"
            )

        html_output = []

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
            q_no = q.get("question_number")
            q_points = q.get("points")
            q_text = cls.clean_xml_text(q.get("question_text", ""))
            q_type = q.get("question_type", "").upper()
            options = q.get("options", [])
            rubric = q.get("rubric", [])
            model_answer = cls.clean_xml_text(q.get("model_answer", ""))
            image_url = q.get("question_image", "")

            html_output.append(
                f"""
            <div style="margin-bottom:40px;">
                <p><strong>Question {q_no} ({q_points} marks)</strong></p>
                {q_text}
            """
            )

            # Question Image
            if image_url:
                html_output.append(
                    f"""
                <div style="margin-top:12px; margin-bottom:12px; text-align:center;">
                    <img src={image_url!r} style="max-width:100%; height:auto;" alt="Question {q_no} image">
                </div>
                """
                )

            # Objective Options
            if q_type == "OBJECTIVE" and options:
                html_output.append(
                    """
                <div style="margin-top:12px; padding-left:25px;">
                """
                )

                for idx, opt in enumerate(options):
                    letter = string.ascii_uppercase[idx]
                    html_output.append(
                        f"""
                        <p><strong>{letter}.</strong> {opt}</p>
                    """
                    )

                html_output.append("</div>")

            # Rubric (for essay & short answer)
            if rubric:
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
                    level = r.get("level", "").title()
                    points = r.get("points", "")
                    desc = r.get("description", "")

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

            # Model Answer
            if model_answer:
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
    ) -> dict:

        print("Extracting assignment content")

        # assignment = Assignment.objects.get(id=assignment_id)

        extraction_started_at = timezone.now()
        assignment_questions = ai_processor.extract_assignment_with_retry(
            user, content, max_retries=3, upload=upload
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
            assignment_html = cls.format_assignment_standard_html(assignment_questions)
            raw_input = cls.html_to_prosemirror_json(assignment_html)
            assignment_questions["raw_input"] = json.dumps(raw_input)

        return assignment_questions

        # serializer = AssignmentSerializer(
        #     assignment, data=assignment_questions, partial=True
        # )
        # serializer.is_valid(raise_exception=True)
        # serializer.save()
        #
        # serializer = AssignmentListSerializer(assignment)
        #
        # print("Assignment saved successfully")
        #
        # return serializer

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
    ):
        assignment_data = cls.extract_assignment_data(
            user,
            content,
            assignment=assignment,
            topic=topic,
            raw_input=raw_input,
            keep_existing_title=keep_existing_title,
            upload=upload,
        )

        serializer = AssignmentSerializer(
            assignment, data=assignment_data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return assignment

    @classmethod
    def extract_assignment(cls, user, assignment, content):
        updated_assignment = cls.update_assignment_from_extraction(
            user, assignment, content, keep_existing_title=True
        )
        return AssignmentListSerializer(updated_assignment)
