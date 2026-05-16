# import csv
import io

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse

User = get_user_model()


class BulkEnrollmentTest(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Test",
            last_name="Teacher",
            user_type="TEACHER",
        )
        self.session = Session.objects.create(name="Fall 2024", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Math 101", teacher=self.teacher, session=self.session
        )
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course.pk})

    def test_bulk_add_via_tsv_string(self):
        """Test adding students via a tab-separated string (Excel paste)."""
        raw_data = (
            "First Name\tLast Name\tEmail\n"
            "John\tDoe\tjohn.doe@example.com\n"
            "Jane\tSmith\t\n"
        )
        response = self.client.post(self.url, {"raw_data": raw_data})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_processed"], 2)
        self.assertEqual(response.data["success_count"], 2)

        # Verify John Doe (with email)
        john = User.objects.get(email="john.doe@example.com")
        self.assertEqual(john.first_name, "John")
        self.assertFalse(john.is_active)  # Should be inactive (invitation flow)

        # Verify Jane Smith (without email)
        jane = User.objects.get(first_name="Jane", last_name="Smith")
        self.assertTrue(jane.is_active)  # Should be active (direct add flow)
        self.assertTrue(jane.email.endswith("@student.local"))

    def test_bulk_add_via_csv_file(self):
        """Test adding students via an uploaded CSV file."""
        csv_content = (
            "first_name,last_name,email\n"
            "Alice,Wonderland,alice@example.com\n"
            "Bob,Builder,\n"
        )
        csv_file = io.BytesIO(csv_content.encode("utf-8"))
        csv_file.name = "students.csv"

        response = self.client.post(self.url, {"file": csv_file}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 2)

        self.assertTrue(User.objects.filter(email="alice@example.com").exists())
        self.assertTrue(
            User.objects.filter(first_name="Bob", last_name="Builder").exists()
        )

    def test_bulk_add_with_duplicates(self):
        """Test handling of duplicate enrollments."""
        # Pre-enroll Alice
        alice = User.objects.create_user(
            email="alice@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Alice",
            last_name="Wonderland",
            user_type="STUDENT",
        )
        StudentCourse.objects.create(
            student=alice,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        raw_data = "first_name,last_name,email\nAlice,Wonderland,alice@example.com"
        response = self.client.post(self.url, {"raw_data": raw_data})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["status"], "skipped")
        self.assertEqual(response.data["results"][0]["error"], "Already enrolled")

    def test_bulk_add_missing_required_fields(self):
        """Test handling of missing names."""
        raw_data = "first_name,last_name,email\n,Doe,no.first@example.com"
        response = self.client.post(self.url, {"raw_data": raw_data})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["results"][0]["status"], "failed")

    def test_bulk_add_without_headers_detects_email_in_any_column(self):
        """Rows without headers should detect email by value and map remaining cells by order."""
        raw_data = (
            "john.doe@example.com,John,Doe\n"
            "Jane,jane.smith@example.com,Smith,Marie\n"
            "Michael,Brown,michael.brown@example.com\n"
        )
        response = self.client.post(self.url, {"raw_data": raw_data})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 3)
        self.assertEqual(response.data["failure_count"], 0)

        john = User.objects.get(email="john.doe@example.com")
        self.assertEqual(john.first_name, "John")
        self.assertEqual(john.last_name, "Doe")
        self.assertEqual(john.middle_name, "")

        jane = User.objects.get(email="jane.smith@example.com")
        self.assertEqual(jane.first_name, "Jane")
        self.assertEqual(jane.last_name, "Smith")
        self.assertEqual(jane.middle_name, "Marie")

        michael = User.objects.get(email="michael.brown@example.com")
        self.assertEqual(michael.first_name, "Michael")
        self.assertEqual(michael.last_name, "Brown")

    def test_bulk_add_without_headers_requires_first_and_last_name(self):
        """Rows without headers should fail when either first or last name is missing."""
        raw_data = "mary@example.com,Mary\n"
        response = self.client.post(self.url, {"raw_data": raw_data})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 0)
        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["results"][0]["status"], "failed")
        self.assertEqual(
            response.data["results"][0]["error"], "First and last names are required."
        )
