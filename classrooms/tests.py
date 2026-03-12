from datetime import date, timedelta

from django.contrib.auth import get_user_model

# from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Course, School, Session

User = get_user_model()


class SchoolModelTest(TestCase):
    """
    Tests for the School model.
    """

    def setUp(self):
        self.school_data = {
            "name": "Greenwood High",
            "address": "123 Education Lane",
            "phone": "+1234567890",
            "website": "https://greenwood.edu",
        }
        self.school = School.objects.create(**self.school_data)

    def test_school_creation(self):
        """Test that a School instance is created correctly."""
        self.assertEqual(self.school.name, self.school_data["name"])
        self.assertEqual(self.school.address, self.school_data["address"])
        self.assertEqual(self.school.phone, self.school_data["phone"])
        self.assertEqual(self.school.website, self.school_data["website"])
        self.assertIsNotNone(self.school.id)

    def test_school_str_representation(self):
        """Test the __str__ method of the School model."""
        self.assertEqual(str(self.school), self.school.name)

    def test_school_name_uniqueness(self):
        """Test that school names must be unique."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                School.objects.create(name="Greenwood High")

    def test_school_optional_fields(self):
        """Test that optional fields can be blank/null."""
        school = School.objects.create(name="Minimal School")
        self.assertIsNone(school.address)
        self.assertIsNone(school.phone)
        self.assertIsNone(school.website)


class SessionModelTest(TestCase):
    """
    Tests for the Session model.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Test",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Fall 2024", teacher=self.teacher)

    def test_session_creation(self):
        """Test that a Session instance is created correctly."""
        self.assertEqual(self.session.name, "Fall 2024")
        self.assertEqual(self.session.teacher, self.teacher)
        self.assertIsNotNone(self.session.id)

    def test_session_name_uniqueness_per_teacher(self):
        """Test the unique constraint: name per teacher."""
        # Same teacher, same name -> should fail
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Session.objects.create(name="Fall 2024", teacher=self.teacher)

        # Different teacher, same name -> should pass
        teacher2 = User.objects.create_user(
            email="teacher2@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Test2",
            last_name="Teacher2",
        )
        Session.objects.create(name="Fall 2024", teacher=teacher2)

    def test_session_ordering(self):
        """Test that sessions are ordered by created_at descending."""
        Session.objects.all().delete()
        today = date.today()
        yesterday = today - timedelta(days=1)

        s1 = Session.objects.create(name="Old Session", teacher=self.teacher)
        # Manually updating created_at because auto_now_add makes it uneditable during creation
        Session.objects.filter(id=s1.id).update(created_at=yesterday)

        s2 = Session.objects.create(name="New Session", teacher=self.teacher)
        Session.objects.filter(id=s2.id).update(created_at=today)

        sessions = Session.objects.all()
        self.assertEqual(sessions[0].name, "New Session")
        self.assertEqual(sessions[1].name, "Old Session")


class CourseModelTest(TestCase):
    """
    Tests for the Course model.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="course_teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Course",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Spring 2025", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Mathematics 101",
            teacher=self.teacher,
            session=self.session,
            description="Basic Math",
        )

    def test_course_creation(self):
        """Test that a Course instance is created correctly."""
        self.assertEqual(self.course.name, "Mathematics 101")
        self.assertEqual(self.course.teacher, self.teacher)
        self.assertEqual(self.course.session, self.session)
        self.assertTrue(self.course.is_active)

    def test_course_str_representation(self):
        """Test the __str__ method of the Course model."""
        expected_str = f"{self.session.name} - {self.course.name}"
        self.assertEqual(str(self.course), expected_str)

    def test_course_uniqueness_per_session(self):
        """Test unique constraint: name, teacher, session."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Course.objects.create(
                    name="Mathematics 101", teacher=self.teacher, session=self.session
                )

    def test_course_null_teacher_or_session(self):
        """Test that teacher and session can be null as per model definition."""
        course = Course.objects.create(name="Generic Course")
        self.assertIsNone(course.teacher)
        self.assertIsNone(course.session)

    def test_course_ordering(self):
        """Test that courses are ordered by name."""
        Course.objects.all().delete()
        Course.objects.create(name="B Course")
        Course.objects.create(name="A Course")
        courses = Course.objects.all()
        self.assertEqual(courses[0].name, "A Course")
        self.assertEqual(courses[1].name, "B Course")
