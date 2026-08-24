"""Tests for assignments/pdf_cache.py and its wiring into download_pdf.

The renderer is mocked throughout: what's under test is the caching
decision (hit/miss/key separation/failure tolerance), not PDF rendering
itself, which assignments/tests_pdf_renderer.py covers against real
Chromium.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments import pdf_cache
from assignments.models import Assignment, AssignmentStatus
from assignments.tests_download_pdf import objective_question
from assignments.tests_rigor import RigorFixtureMixin
from classrooms.models import EnrollmentStatusType, StudentCourse
from users.models import CustomUser, UserTypes


class BuildCacheKeyTest(RigorFixtureMixin, APITestCase):
    def setUp(self):
        self.course = self.make_course(suffix="-cachekey")
        self.assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question()],
        )

    def test_key_includes_id_view_type_and_timestamp(self):
        key = pdf_cache.build_cache_key(self.assignment, "teacher")
        self.assertIn(str(self.assignment.id), key)
        self.assertIn("teacher", key)
        self.assertIn(self.assignment.updated_at.isoformat(), key)

    def test_student_and_teacher_views_get_different_keys(self):
        """
        The teacher's PDF embeds rubrics the student's must never show -
        sharing one cache entry between them would leak answers.
        """
        self.assertNotEqual(
            pdf_cache.build_cache_key(self.assignment, "teacher"),
            pdf_cache.build_cache_key(self.assignment, "student"),
        )

    def test_key_changes_when_the_assignment_is_edited(self):
        before = pdf_cache.build_cache_key(self.assignment, "student")
        self.assignment.title = "Edited"
        self.assignment.save()
        self.assignment.refresh_from_db()

        self.assertNotEqual(
            before, pdf_cache.build_cache_key(self.assignment, "student")
        )

    def test_unsaved_assignment_gets_a_never_matching_key(self):
        unsaved = Assignment(title="Unsaved", course=self.course)
        self.assertIn("unsaved", pdf_cache.build_cache_key(unsaved, "student"))

    def test_key_is_namespaced_for_the_existing_wildcard_invalidation(self):
        # assignments/signals.py clear_assignment_cache deletes
        # "assignments:*" on every Assignment save/delete.
        self.assertTrue(
            pdf_cache.build_cache_key(self.assignment, "student").startswith(
                "assignments:"
            )
        )


class PdfCacheReadWriteTest(RigorFixtureMixin, APITestCase):
    def setUp(self):
        cache.clear()
        self.course = self.make_course(suffix="-cacherw")
        self.assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question()],
        )

    def tearDown(self):
        cache.clear()

    def test_store_then_get_round_trips_the_bytes(self):
        pdf_cache.store_pdf(self.assignment, "student", b"%PDF-abc")
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"%PDF-abc"
        )

    def test_get_returns_none_on_a_miss(self):
        self.assertIsNone(pdf_cache.get_cached_pdf(self.assignment, "student"))

    @override_settings(ASSIGNMENT_PDF_CACHE_ENABLED=False)
    def test_disabled_cache_never_stores_or_returns(self):
        pdf_cache.store_pdf(self.assignment, "student", b"%PDF-abc")
        self.assertIsNone(pdf_cache.get_cached_pdf(self.assignment, "student"))

    @override_settings(ASSIGNMENT_PDF_CACHE_MAX_BYTES=100)
    def test_oversized_pdf_is_not_cached(self):
        """
        An image-heavy assignment can render to megabytes; caching those
        unbounded would let a few of them hold a large share of Redis for
        a full TTL and evict everything else. Oversized renders are still
        served - they just don't get stored.
        """
        pdf_cache.store_pdf(self.assignment, "student", b"x" * 101)
        self.assertIsNone(pdf_cache.get_cached_pdf(self.assignment, "student"))

    @override_settings(ASSIGNMENT_PDF_CACHE_MAX_BYTES=100)
    def test_pdf_at_exactly_the_cap_is_still_cached(self):
        pdf_cache.store_pdf(self.assignment, "student", b"x" * 100)
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"x" * 100
        )

    @override_settings(ASSIGNMENT_PDF_CACHE_MAX_BYTES=0)
    def test_zero_disables_the_size_cap(self):
        pdf_cache.store_pdf(self.assignment, "student", b"x" * 10_000)
        self.assertEqual(
            pdf_cache.get_cached_pdf(self.assignment, "student"), b"x" * 10_000
        )

    def test_read_failure_degrades_to_a_miss_rather_than_raising(self):
        with patch.object(cache, "get", side_effect=RuntimeError("redis down")):
            self.assertIsNone(pdf_cache.get_cached_pdf(self.assignment, "student"))

    def test_write_failure_is_swallowed_rather_than_raising(self):
        with patch.object(cache, "set", side_effect=RuntimeError("redis down")):
            pdf_cache.store_pdf(
                self.assignment, "student", b"%PDF-abc"
            )  # must not raise


class DownloadPdfCachingTest(RigorFixtureMixin, APITestCase):
    """The cache as the download endpoint actually uses it."""

    def setUp(self):
        cache.clear()
        self.course = self.make_course(suffix="-dlcache")
        self.teacher = self.course.teacher
        self.student = CustomUser.objects.create_user(
            email="pdf-cache-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Cache",
            last_name="Student",
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="Cached Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[objective_question()],
        )
        self.url = reverse("assignment-download-pdf", kwargs={"pk": self.assignment.id})

    def tearDown(self):
        cache.clear()

    @patch("assignments.views.render_html_to_pdf")
    def test_second_download_is_served_from_cache_without_rendering(self, mock_render):
        mock_render.return_value = b"%PDF-rendered"
        self.client.force_authenticate(user=self.teacher)

        first = self.client.get(self.url, {"view": "teacher"})
        second = self.client.get(self.url, {"view": "teacher"})

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        mock_render.assert_called_once()
        self.assertEqual(b"".join(second.streaming_content), b"%PDF-rendered")

    @patch("assignments.views.render_html_to_pdf")
    def test_editing_the_assignment_forces_a_fresh_render(self, mock_render):
        mock_render.return_value = b"%PDF-rendered"
        self.client.force_authenticate(user=self.teacher)

        self.client.get(self.url, {"view": "teacher"})
        self.assignment.title = "Edited Title"
        self.assignment.save()
        self.client.get(self.url, {"view": "teacher"})

        self.assertEqual(mock_render.call_count, 2)

    @patch("assignments.views.render_html_to_pdf")
    def test_student_and_teacher_views_are_cached_independently(self, mock_render):
        mock_render.return_value = b"%PDF-rendered"

        self.client.force_authenticate(user=self.teacher)
        self.client.get(self.url, {"view": "teacher"})
        self.client.force_authenticate(user=self.student)
        self.client.get(self.url, {"view": "student"})

        # Two distinct documents (the teacher's includes rubrics), so two
        # renders - neither may be served from the other's entry.
        self.assertEqual(mock_render.call_count, 2)

    @patch("assignments.views.render_html_to_pdf")
    def test_a_cache_hit_still_enforces_permissions(self, mock_render):
        """
        The cache lookup sits after the permission checks, so warming an
        entry as the teacher must not let a student pull the teacher view
        (which contains rubrics/answers) out of the cache.
        """
        mock_render.return_value = b"%PDF-rendered"
        self.client.force_authenticate(user=self.teacher)
        self.client.get(self.url, {"view": "teacher"})

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url, {"view": "teacher"})

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    @patch("assignments.views.render_html_to_pdf")
    def test_cache_backend_failure_still_serves_a_pdf(self, mock_render):
        mock_render.return_value = b"%PDF-rendered"
        self.client.force_authenticate(user=self.teacher)

        with patch.object(
            cache, "get", side_effect=RuntimeError("redis down")
        ), patch.object(cache, "set", side_effect=RuntimeError("redis down")):
            response = self.client.get(self.url, {"view": "teacher"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-rendered")

    @patch("assignments.views.render_html_to_pdf")
    def test_a_failed_render_is_never_cached(self, mock_render):
        mock_render.side_effect = RuntimeError("boom")
        self.client.force_authenticate(user=self.teacher)

        failed = self.client.get(self.url, {"view": "teacher"})
        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # The next attempt must retry the render, not serve a cached failure.
        mock_render.side_effect = None
        mock_render.return_value = b"%PDF-rendered"
        retried = self.client.get(self.url, {"view": "teacher"})

        self.assertEqual(retried.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_render.call_count, 2)
