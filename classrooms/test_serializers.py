from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from classrooms.models import Course, Session, Topic
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

    def test_course_serializer_contains_topics(self):
        """Verify that topics are correctly serialized within CourseSerializer."""
        # Create topics for the course
        Topic.objects.create(name="Algebra", course=self.course)
        Topic.objects.create(name="Geometry", course=self.course)

        serializer = CourseSerializer(instance=self.course, context=self.context)
        self.assertIn("topics", serializer.data)
        self.assertEqual(len(serializer.data["topics"]), 2)
        topic_names = [t["name"] for t in serializer.data["topics"]]
        self.assertIn("Algebra", topic_names)
        self.assertIn("Geometry", topic_names)
