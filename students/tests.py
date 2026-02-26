from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


class StudentSubmissionGradeUpdateTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="One",
        )
        self.student = CustomUser.objects.create_user(
            email="student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="One",
        )
        self.session = Session.objects.create(name="Test Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Test Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Test Assignment",
            course=self.course,
            questions={"q1": "What is 1+1?"},
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment, student=self.student, answers={"q1": "2"}
        )
        self.url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )

    def test_teacher_can_update_grade(self):
        self.client.force_authenticate(user=self.teacher)
        data = {"score": 95.00, "feedback": {"overall": "Great job!"}}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 95.00)
        self.assertEqual(self.submission.feedback, {"overall": "Great job!"})
        self.assertTrue(self.submission.was_regraded)
        self.assertIsNotNone(self.submission.regraded_at)

    def test_student_cannot_update_grade(self):
        self.client.force_authenticate(user=self.student)
        data = {"score": 100.00}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
