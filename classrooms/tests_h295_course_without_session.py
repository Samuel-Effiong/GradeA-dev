"""H-295 B1: creating a course without a session is a 400, not a 500.

Found by the H-294 audit: POST /course with the session omitted, null or ""
crashed in CourseSerializer.validate_session (`None.owner_type`). This is
the QA catalogue's "500 seen when the session id is empty" (section 3),
which H-274 (d) looked for in session creation and closed. The refusal uses
the framework's own "This field is required." text (no new wording).
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.tests_cache_matrix_g3 import make_active_user
from classrooms.models import Course, Session
from users.models import UserTypes

URL = reverse("course-list")
# H-296 rewords the framework's "required" text (owner-approved).
REQUIRED = "Enter session."


class CourseWithoutSessionTests(TestCase):
    client: APIClient

    def setUp(self):
        self.teacher = make_active_user("h295-t@grade.test", UserTypes.TEACHER)
        self.other = make_active_user("h295-o@grade.test", UserTypes.TEACHER)
        self.session = Session.objects.create(name="Mine", teacher=self.teacher)
        self.other_session = Session.objects.create(name="Theirs", teacher=self.other)
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def _post(self, **extra):
        body = {"name": "Biology", "description": "Cells", **extra}
        return self.client.post(URL, body, format="json")

    def _assert_refused(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["message"], REQUIRED)
        self.assertFalse(Course.objects.filter(name="Biology").exists())

    def test_session_omitted_is_a_400(self):
        self._assert_refused(self._post())

    def test_session_null_is_a_400(self):
        self._assert_refused(self._post(session=None))

    def test_session_empty_string_is_a_400(self):
        self._assert_refused(self._post(session=""))

    def test_own_session_still_creates_the_course(self):
        response = self._post(session=str(self.session.pk))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Course.objects.get(name="Biology").session, self.session)

    def test_another_teachers_session_is_still_refused(self):
        response = self._post(session=str(self.other_session.pk))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(
            "You do not have access to this session.", response.json()["message"]
        )
        self.assertFalse(Course.objects.filter(name="Biology").exists())

    def test_editing_a_course_without_sending_session_still_works(self):
        course = Course.objects.create(
            name="Chem", teacher=self.teacher, session=self.session
        )
        response = self.client.patch(
            reverse("course-detail", args=[course.pk]),
            {"description": "new"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
