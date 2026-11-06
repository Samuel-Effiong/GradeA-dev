"""H-297: editing a course's topics must never delete its assignments.

`CourseSerializer.update` replaced `topic_names` by deleting every topic of
the course and recreating them, and `Assignment.topic` was on_delete=CASCADE,
so a course edit that resent the topic list (even unchanged) deleted every
assignment filed under a topic, with its submissions and grades. Deleting a
topic through /topics/ did the same.

After the fix:
* a name that is still in the list keeps its topic row, so its assignments
  stay filed under it;
* a removed topic is deleted, and its assignments stay, unfiled
  (Assignment.topic -> SET_NULL);
* nothing else about the course edit changes. No new wording.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment
from assignments.tests_cache_matrix_g3 import make_active_user
from classrooms.models import Course, Session, Topic
from students.models import StudentSubmission
from users.models import UserTypes


class TopicEditKeepsAssignmentsTests(TestCase):
    client: APIClient

    def setUp(self):
        self.teacher = make_active_user("h297-t@grade.test", UserTypes.TEACHER)
        self.student = make_active_user("h297-s@grade.test", UserTypes.STUDENT)
        session = Session.objects.create(name="Fall", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology", session=session, teacher=self.teacher
        )
        self.cells = Topic.objects.create(name="Cells", course=self.course)
        self.genetics = Topic.objects.create(name="Genetics", course=self.course)
        self.quiz = Assignment.objects.create(
            course=self.course, topic=self.cells, title="Cells quiz"
        )
        self.exam = Assignment.objects.create(
            course=self.course, topic=self.genetics, title="Genetics exam"
        )
        self.loose = Assignment.objects.create(course=self.course, title="No topic")
        self.submission = StudentSubmission.objects.create(
            assignment=self.quiz,
            student=self.student,
            answers={},
            score=8,
            max_points=10,
            graded_at=timezone.now(),
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def _patch(self, body):
        response = self.client.patch(
            reverse("course-detail", args=[self.course.pk]), body, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def _topic_names(self):
        return sorted(
            Topic.objects.filter(course=self.course).values_list("name", flat=True)
        )

    def _assert_all_assignments_survive(self):
        self.assertEqual(
            sorted(
                Assignment.objects.filter(course=self.course).values_list(
                    "title", flat=True
                )
            ),
            ["Cells quiz", "Genetics exam", "No topic"],
        )
        self.assertTrue(
            StudentSubmission.objects.filter(pk=self.submission.pk).exists()
        )

    def test_resending_the_same_topics_changes_nothing(self):
        self._patch({"topic_names": ["Cells", "Genetics"]})
        self._assert_all_assignments_survive()
        self.quiz.refresh_from_db()
        self.exam.refresh_from_db()
        # Same rows, not lookalikes: the assignments are still filed under them.
        self.assertEqual(self.quiz.topic_id, self.cells.pk)
        self.assertEqual(self.exam.topic_id, self.genetics.pk)
        self.assertEqual(self._topic_names(), ["Cells", "Genetics"])

    def test_adding_a_topic_keeps_existing_ones_and_their_assignments(self):
        self._patch({"topic_names": ["Cells", "Genetics", "Ecology"]})
        self._assert_all_assignments_survive()
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.topic_id, self.cells.pk)
        self.assertEqual(self._topic_names(), ["Cells", "Ecology", "Genetics"])

    def test_removing_a_topic_unfiles_its_assignments_instead_of_deleting_them(self):
        self._patch({"topic_names": ["Genetics"]})
        self._assert_all_assignments_survive()
        self.quiz.refresh_from_db()
        self.exam.refresh_from_db()
        self.assertIsNone(self.quiz.topic_id)
        self.assertEqual(self.exam.topic_id, self.genetics.pk)
        self.assertEqual(self._topic_names(), ["Genetics"])

    def test_clearing_all_topics_keeps_every_assignment(self):
        self._patch({"topic_names": []})
        self._assert_all_assignments_survive()
        self.assertEqual(self._topic_names(), [])
        self.assertFalse(
            Assignment.objects.filter(course=self.course, topic__isnull=False).exists()
        )

    def test_names_are_matched_after_trimming_spaces(self):
        self._patch({"topic_names": ["  Cells ", "Genetics"]})
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.topic_id, self.cells.pk)
        self.assertEqual(self._topic_names(), ["Cells", "Genetics"])

    def test_a_name_listed_twice_creates_one_topic(self):
        self._patch({"topic_names": ["Cells", "Ecology", "Ecology"]})
        self.assertEqual(self._topic_names(), ["Cells", "Ecology"])
        self._assert_all_assignments_survive()

    def test_a_course_edit_without_topic_names_leaves_topics_alone(self):
        self._patch({"description": "Updated"})
        self.assertEqual(self._topic_names(), ["Cells", "Genetics"])
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.topic_id, self.cells.pk)

    def test_deleting_a_topic_keeps_its_assignments(self):
        response = self.client.delete(reverse("topic-detail", args=[self.cells.pk]))
        self.assertEqual(response.status_code, 204, response.content)
        self._assert_all_assignments_survive()
        self.quiz.refresh_from_db()
        self.assertIsNone(self.quiz.topic_id)

    def test_deleting_a_course_still_deletes_its_assignments(self):
        # The fix is about topics only; a course's own deletion is unchanged.
        self.course.delete()
        self.assertFalse(Assignment.objects.filter(pk=self.quiz.pk).exists())
