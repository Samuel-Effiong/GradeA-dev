# import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (  # EnrollmentStatusType,; StudentCourse,
    Course,
    School,
    Session,
)
from users.models import UserTypes

User = get_user_model()


class ClassroomBaseAPITest(APITestCase):
    def setUp(self):
        # Clear cache before each test
        cache.clear()

        # Mock delete_pattern since it's a django-redis specific method
        # and might not be available in the test cache backend.
        if not hasattr(cache, "delete_pattern"):
            cache.delete_pattern = lambda x: None

        # Create a superuser
        self.superadmin = User.objects.create_superuser(
            email="superadmin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.is_active = True
        self.superadmin.save()

        # Create teachers
        self.teacher1 = User.objects.create_user(
            email="teacher1@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
        )
        self.teacher1.user_type = UserTypes.TEACHER
        self.teacher1.is_active = True
        self.teacher1.save()

        self.teacher2 = User.objects.create_user(
            email="teacher2@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
        )
        self.teacher2.user_type = UserTypes.TEACHER
        self.teacher2.is_active = True
        self.teacher2.save()

        # Create a school
        self.school = School.objects.create(name="Central High")

    def authenticate(self, user):
        self.client.force_authenticate(user=user)


class SchoolViewSetTest(ClassroomBaseAPITest):
    def test_list_schools_superadmin_caching_and_invalidation(self):
        self.authenticate(self.superadmin)
        url = reverse("school-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First call caches the response
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 1)

            # Create a new school via API
            self.client.post(url, {"name": "New API School"})

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation effect
            cache.clear()

            # Second call should reflect the new school
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 2)

    def test_list_schools_teacher_denied(self):
        self.authenticate(self.teacher1)
        url = reverse("school-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SessionViewSetTest(ClassroomBaseAPITest):
    def test_session_cache_invalidation_on_creation(self):
        self.authenticate(self.teacher1)
        url = reverse("session-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First GET call
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 0)

            # Create a new session via API
            new_session_data = {"name": "Spring 2025"}
            create_response = self.client.post(url, new_session_data)
            self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation effect
            cache.clear()

            # Second GET call should reflect new session
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 1)

    def test_session_isolation(self):
        # Teacher 1 creates a session
        Session.objects.create(name="T1 Session", teacher=self.teacher1)

        # Teacher 2 should not see Teacher 1's session
        self.authenticate(self.teacher2)
        url = reverse("session-list")
        response = self.client.get(url)
        self.assertEqual(len(response.data["results"]), 0)

    def test_hacker_access_others_session(self):
        t1_session = Session.objects.create(name="T1 Session", teacher=self.teacher1)

        self.authenticate(self.teacher2)
        url = reverse("session-detail", kwargs={"pk": t1_session.id})

        # Try to retrieve T1's session
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CourseViewSetTest(ClassroomBaseAPITest):
    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(name="Session 1", teacher=self.teacher1)

    def test_course_cache_invalidation_on_creation(self):
        self.authenticate(self.teacher1)
        url = reverse("course-list")

        # Mock cache.delete_pattern to simulate invalidation
        with patch.object(cache, "delete_pattern") as mock_delete:
            # First GET call
            response1 = self.client.get(url)
            self.assertEqual(len(response1.data["results"]), 0)

            # Create a new course via API
            new_course_data = {"name": "Science", "session": str(self.session.id)}
            create_response = self.client.post(url, new_course_data)
            self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

            # Check if delete_pattern was called
            mock_delete.assert_called()

            # Manually clear cache to simulate invalidation
            cache.clear()

            # Second GET call should reflect the new course
            response2 = self.client.get(url)
            self.assertEqual(len(response2.data["results"]), 1)

    def test_hacker_enroll_same_student_twice(self):
        course = Course.objects.create(
            name="Math", teacher=self.teacher1, session=self.session
        )
        self.authenticate(self.teacher1)
        url = reverse("course-students", kwargs={"pk": course.id})

        email = "student@example.com"
        # First enrollment
        self.client.post(url, {"email": email})

        # Second enrollment attempt
        response = self.client.post(url, {"email": email})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already enrolled", str(response.data.get("detail", "")))

    def test_hacker_modify_others_course(self):
        t1_course = Course.objects.create(
            name="T1 Course", teacher=self.teacher1, session=self.session
        )

        self.authenticate(self.teacher2)
        url = reverse("course-detail", kwargs={"pk": t1_course.id})

        # Try to update T1's course
        response = self.client.patch(url, {"name": "Hacked"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hacker_malformed_data(self):
        self.authenticate(self.teacher1)
        url = reverse("course-list")

        # Send garbage data
        response = self.client.post(url, {"name": "", "session": "not-a-uuid"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
