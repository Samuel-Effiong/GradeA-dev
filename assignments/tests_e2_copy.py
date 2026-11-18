"""Epic E2: single and bulk assignment copy (FR-E-01/02; plan §5)."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework.test import APIClient, APITestCase

from assignments import copy as copying
from assignments.models import Assignment, AssignmentStatus
from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent
from billing.testing.zero_credit import assert_operation_is_zero_credit
from classrooms.models import CourseCategory
from classrooms.tests_e2_fixtures import (
    copy_off,
    copy_on,
    enroll,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
)
from students.models import ExcusedWork, StudentSubmission
from tags.models import AssignmentTag, OwnerType, Tag
from tags.services import get_or_create_tag

QUESTIONS = [
    {
        "question_number": 1,
        "question_text": "2+2?",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["3", "4"],
        "rubric": [{"level": "full", "points": 10, "description": "4"}],
        "model_answer": "4",
    }
]


def _errors(response):
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(response.data)
    return found


class _Copy(APITestCase):
    def setUp(self):
        self.teacher = make_teacher()
        self.source_course = make_course(self.teacher)
        self.destination = make_course(self.teacher)
        self.source = make_assignment(
            self.source_course,
            "Fractions quiz",
            instructions="Show your work.",
            questions=QUESTIONS,
            question_count=1,
            assignment_type="OBJECTIVE",
            self_assessment="How sure are you?",
            custom_ai_prompt="Be kind.",
            due_date=timezone.now() + timedelta(days=3),
            auto_grade_on_due_date=True,
        )
        self.source.raw_input_hash = "h" * 64
        self.source.save()
        self.client.force_authenticate(self.teacher)

    def copy(self, source=None, course=None):
        return self.client.post(
            f"/api/v1/assignments/{(source or self.source).pk}/copy",
            {"course_id": str((course or self.destination).pk)},
            format="json",
        )

    def bulk(self, ids, course=None):
        return self.client.post(
            "/api/v1/assignments/bulk-copy",
            {
                "assignment_ids": [str(i) for i in ids],
                "course_id": str((course or self.destination).pk),
            },
            format="json",
        )


@copy_off
class CopyDarkTest(_Copy):
    def test_routes_answer_as_unknown_urls_while_dark(self):
        self.assertEqual(self.copy().status_code, 404)
        self.assertEqual(self.bulk([self.source.pk]).status_code, 404)
        anonymous = APIClient()
        self.assertEqual(
            anonymous.post(f"/api/v1/assignments/{self.source.pk}/copy").status_code,
            404,
        )


@copy_on
class SingleCopyTest(_Copy):
    def test_deep_independent_draft_copy(self):
        response = self.copy()
        self.assertEqual(response.status_code, 201, response.data)
        new = Assignment.objects.get(pk=response.data["id"])
        self.assertEqual(new.course, self.destination)
        for field in (
            "title",
            "instructions",
            "raw_input",
            "raw_input_hash",
            "total_points",
            "question_count",
            "assignment_type",
            "questions",
            "self_assessment",
            "custom_ai_prompt",
            "relative_weight",
        ):
            self.assertEqual(getattr(new, field), getattr(self.source, field), field)
        # Q6: a draft, no due date, auto-grade off
        self.assertEqual(new.status, AssignmentStatus.DRAFT)
        self.assertIsNone(new.due_date)
        self.assertFalse(new.auto_grade_on_due_date)
        self.assertIsNone(new.topic)
        # independent: editing the copy leaves the source alone
        assert isinstance(new.questions, list)
        new.questions[0]["question_text"] = "changed"
        new.save()
        self.source.refresh_from_db()
        self.assertEqual(self.source.questions[0]["question_text"], "2+2?")
        audit = AuditEvent.objects.get(
            action=AuditAction.ASSIGNMENT_COPY, outcome=AuditOutcome.SUCCESS
        )
        self.assertEqual(audit.target_id, new.pk)
        self.assertEqual(
            dict(audit.metadata or {})["assignment_id"], str(self.source.pk)
        )

    def test_no_student_data_is_copied(self):
        student = make_student()
        enroll(student, self.source_course)
        grade(student, self.source, 8)
        ExcusedWork.objects.create(student=make_student(), assignment=self.source)
        new = Assignment.objects.get(pk=self.copy().data["id"])
        self.assertFalse(StudentSubmission.objects.filter(assignment=new).exists())
        self.assertFalse(ExcusedWork.objects.filter(assignment=new).exists())

    def test_nothing_is_scheduled_or_sent(self):
        before = set(PeriodicTask.objects.values_list("name", flat=True))
        with patch("AutoGrader.dispatch.safe_delay") as dispatched:
            with self.captureOnCommitCallbacks(execute=True):
                new = Assignment.objects.get(pk=self.copy().data["id"])
        after = set(PeriodicTask.objects.values_list("name", flat=True))
        self.assertEqual(
            {name for name in after - before if str(new.pk) in name}, set()
        )
        # No new-assignment email, no PDF prerender: nothing is dispatched.
        dispatched.assert_not_called()

    def test_zero_credit(self):
        destination = self.destination
        assert_operation_is_zero_credit(
            self,
            "assignment.copy",
            lambda: copying.copy_assignment(
                self.source, destination, actor=self.teacher
            ),
        )

    def test_category_matched_by_name_else_uncategorized_x6(self):
        CourseCategory.objects.create(
            course=self.source_course, name="Quizzes", weight_percent=100
        )
        self.source.category = CourseCategory.objects.get(course=self.source_course)
        self.source.relative_weight = Decimal("2")
        self.source.save()
        target = CourseCategory.objects.create(
            course=self.destination, name="  quizzes ", weight_percent=100
        )
        matched = Assignment.objects.get(pk=self.copy().data["id"])
        self.assertEqual(matched.category, target)
        self.assertEqual(matched.relative_weight, Decimal("2.00"))
        elsewhere = make_course(self.teacher)
        CourseCategory.objects.create(
            course=elsewhere, name="Tests", weight_percent=100
        )
        unmatched = Assignment.objects.get(pk=self.copy(course=elsewhere).data["id"])
        self.assertIsNone(unmatched.category)

    def test_tags_follow_in_the_teachers_scope(self):
        tag, _ = get_or_create_tag(self.teacher, "UNIT", "Fractions")
        AssignmentTag.objects.create(assignment=self.source, tag=tag)
        new = Assignment.objects.get(pk=self.copy().data["id"])
        self.assertEqual(
            list(
                AssignmentTag.objects.filter(assignment=new).values_list(
                    "tag", flat=True
                )
            ),
            [tag.pk],
        )
        self.assertEqual(
            Tag.objects.filter(
                owner_type=OwnerType.TEACHER, teacher=self.teacher
            ).count(),
            1,
        )

    def test_same_course_copy_gets_a_suffix_w7(self):
        first = self.copy(course=self.source_course)
        second = self.copy(course=self.source_course)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["title"], "Fractions quiz (copy)")
        self.assertEqual(second.data["title"], "Fractions quiz (copy 2)")

    def test_another_teachers_source_or_destination_is_not_found(self):
        stranger = make_teacher()
        foreign_course = make_course(stranger)
        foreign = make_assignment(foreign_course, "Theirs")
        response = self.copy(source=foreign)
        self.assertEqual(response.status_code, 404)
        self.assertIn(copying.W8_SOURCE_UNAVAILABLE, _errors(response))
        response = self.copy(course=foreign_course)
        self.assertEqual(response.status_code, 404)
        self.assertIn(copying.W9_DESTINATION_REFUSED, _errors(response))
        self.assertFalse(
            Assignment.objects.filter(course=foreign_course)
            .exclude(pk=foreign.pk)
            .exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.ASSIGNMENT_COPY,
                outcome=AuditOutcome.FAILURE,
                reason_code="COURSE_NOT_REACHABLE",
            ).exists()
        )

    def test_students_cannot_copy(self):
        self.client.force_authenticate(make_student())
        self.assertEqual(self.copy().status_code, 403)


@copy_on
class BulkCopyTest(_Copy):
    def test_partial_success_with_reason_codes(self):
        second = make_assignment(self.source_course, "Second")
        foreign = make_assignment(make_course(make_teacher()), "Theirs")
        missing = uuid.uuid4()
        response = self.bulk([self.source.pk, foreign.pk, missing, second.pk])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["succeeded_count"], 2)
        self.assertEqual(response.data["failed_count"], 2)
        by_source = {r["source_id"]: r for r in response.data["results"]}
        self.assertTrue(by_source[str(self.source.pk)]["ok"])
        self.assertTrue(by_source[str(second.pk)]["ok"])
        for failed in (foreign.pk, missing):
            item = by_source[str(failed)]
            self.assertFalse(item["ok"])
            self.assertEqual(item["reason_code"], "ASSIGNMENT_NOT_AVAILABLE")
            self.assertEqual(item["error"], copying.W8_SOURCE_UNAVAILABLE)
        self.assertEqual(Assignment.objects.filter(course=self.destination).count(), 2)

    def test_unreachable_destination_fails_every_item_w9(self):
        response = self.bulk([self.source.pk], course=make_course(make_teacher()))
        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertEqual(item["reason_code"], "COURSE_NOT_REACHABLE")
        self.assertEqual(item["error"], copying.W9_DESTINATION_REFUSED)

    def test_limit_of_50_w10(self):
        response = self.bulk([uuid.uuid4() for _ in range(51)])
        self.assertEqual(response.status_code, 400)
        self.assertIn(copying.W10_BULK_LIMIT, _errors(response))

    def test_bulk_zero_credit(self):
        assert_operation_is_zero_credit(
            self,
            "assignment.bulk_copy",
            lambda: copying.bulk_copy(
                [self.source.pk], self.destination.pk, actor=self.teacher
            ),
        )
