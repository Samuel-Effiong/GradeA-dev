from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from classrooms.models import Course, Session
from classrooms.serializers import CourseSerializer

User = get_user_model()


class CourseSerializerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type="TEACHER",  # pragma: allowlist secret
        )
        self.session = Session.objects.create(name="Fall 2024", teacher=self.user)
        self.course = Course.objects.create(
            name="Math 101", session=self.session, teacher=self.user
        )

        factory = APIRequestFactory()
        request = factory.get("/")
        request.user = self.user
        self.context = {"request": Request(request)}

    def test_course_serializer_output_contains_categories(self):
        """Verify that categories is present in serialized data even if empty."""
        serializer = CourseSerializer(instance=self.course, context=self.context)
        self.assertIn("categories", serializer.data)
        self.assertEqual(serializer.data["categories"], [])

    def test_course_serializer_accepts_categories_on_create(self):
        """Verify that providing categories during creation doesn't cause errors."""
        data = {
            "name": "Physics 101",
            "session": str(self.session.id),
            "categories": ["Science", "Physics"],
        }
        serializer = CourseSerializer(data=data, context=self.context)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        self.assertEqual(course.name, "Physics 101")
        # Ensure it's not saved to model (since it doesn't exist)
        self.assertFalse(hasattr(course, "categories"))

    def test_course_serializer_accepts_categories_on_update(self):
        """Verify that providing categories during update doesn't cause errors."""
        data = {"name": "Advanced Math", "categories": ["Advanced"]}
        serializer = CourseSerializer(
            instance=self.course, data=data, partial=True, context=self.context
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        course = serializer.save()
        self.assertEqual(course.name, "Advanced Math")
