"""
R1 review flags (owner decisions, 2026-11-12; plan
team/PLAN_review_flags_2026-11-12.md), a beta bug fix:

1. Grader disagreement no longer puts a submission in "Needs review". The
   second AI still runs and its result stays in feedback['second_opinion'];
   it sets no needs_review, review_severity or review_tier, and teacher
   payloads carry none of its entries in review_reasons.
2. Any question-level upload flag puts the whole submission in "Needs
   review" (flagged_for_review) on the assignment page row and the
   submission page.
3. Every flag the frontend gets says its `level`: "assignment" for the
   paper's own flag, "question" for the four question kinds.
4. A one-time data migration takes out of the queue the submissions that
   were in it only because of grader disagreement.

Run with:
    python manage.py test students.tests_review_flags
"""

import copy
import importlib
from unittest.mock import patch

from django.apps import apps as django_apps
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    OFF_TOPIC,
    READING_PROBLEM,
    SUBMISSION_MISMATCH,
    TYPE_MISMATCH,
)
from ai_processor.extraction_schemas import (
    FLAG_OFF_TOPIC,
    FLAG_READING_PROBLEM,
    FLAG_TYPE_MISMATCH,
    NOT_FOUND_IN_DOCUMENT,
)
from classrooms.models import EnrollmentStatusType, StudentCourse
from students.models import StudentSubmission
from students.services import (
    _populate_and_save_grade,
    update_submission_from_raw_text,
    upload_answers_engine,
)
from students.tests_h242_submission_flags import (
    CLEAN,
    MISMATCHED,
    TWO_QUESTIONS,
    answer,
)
from students.tests_no_grade_tell_before_release import plain
from students.tests_post_grading_submission_lock import VALID_GRADE, _classroom, _user
from users.models import UserTypes

DISAGREEMENT_TYPES = ("grader_disagreement", "second_opinion_unavailable")

#: Each question kind, and the reader's reply that raises it on Q2.
QUESTION_KIND_REPLIES = {
    OFF_TOPIC: answer(2, flag=FLAG_OFF_TOPIC),
    TYPE_MISMATCH: answer(2, flag=FLAG_TYPE_MISMATCH),
    READING_PROBLEM: answer(2, flag=FLAG_READING_PROBLEM),
    ANSWER_NOT_FOUND: answer(2, NOT_FOUND_IN_DOCUMENT),
}


def disagreement_grade(tier="critical"):
    """A grading result whose second grader disagreed on Q1."""
    grading = copy.deepcopy(VALID_GRADE)
    grading["second_opinion"] = {
        "model": "second-model",
        "agreements": [],
        "disagreements": [
            {
                "question_number": 1,
                "a": {"score_awarded": 8},
                "b": {"score_awarded": 2},
                "severity": {"tier": tier, "gap_fraction": 0.6},
            }
        ],
    }
    return grading


def unavailable_grade():
    """A grading result whose second grader was refused for credits."""
    grading = copy.deepcopy(VALID_GRADE)
    grading["second_opinion"] = {
        "skipped": "insufficient credits",
        "skipped_reason": "insufficient_credits",
        "needs_review": True,
        "review_reason": "second_opinion_unavailable",
    }
    return grading


class Base(APITestCase):
    tag = "r1"

    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.course, self.assignment = _classroom(self.tag)
        self.assignment.questions = TWO_QUESTIONS
        self.assignment.save()

    def new_student(self):
        """A fresh enrolled student: one student may upload only so often,
        and not after a grade."""
        self.students_made = getattr(self, "students_made", 0) + 1
        student = _user(f"{self.tag}-s{self.students_made}", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return student

    def upload(self, reply):
        with (
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=copy.deepcopy(reply),
            ),
            patch("students.services.send_email_task.delay"),
        ):
            submission = upload_answers_engine(
                self.assignment, "content", self.new_student()
            )
        submission.refresh_from_db()
        return submission

    def grade(self, submission, grading):
        _populate_and_save_grade(submission, grading, None)
        submission.refresh_from_db()
        return submission

    def get(self, user, url, params=None):
        cache.clear()
        self.client.force_authenticate(user=user)
        response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return plain(response.data)

    def detail(self, user, submission):
        return self.get(
            user, reverse("student-submission-detail", kwargs={"pk": submission.pk})
        )

    def list_rows(self, user, params=None):
        rows = self.get(user, reverse("student-submission-list"), params)
        return rows["results"] if isinstance(rows, dict) else rows

    def list_row(self, user, submission):
        return next(
            r for r in self.list_rows(user) if str(r["id"]) == str(submission.pk)
        )

    def assignment_row(self, submission):
        page = self.get(
            self.teacher,
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk}),
        )
        return next(
            r
            for r in page["student_submissions"]
            if str(r["submission_id"]) == str(submission.pk)
        )


class DisagreementNoLongerFlagsTest(Base):
    tag = "r1-dis"

    def test_a_disagreement_only_paper_is_not_flagged(self):
        submission = self.grade(self.upload(CLEAN), disagreement_grade())

        self.assertFalse(submission.needs_review)
        self.assertIsNone(submission.review_severity)
        self.assertIsNone(submission.review_tier)
        # SM Q3: no disagreement entry is written for a new grading.
        self.assertIsNone(submission.review_reasons)
        # The second AI's result is still stored, for measuring quality.
        [stored] = submission.feedback["second_opinion"]["disagreements"]
        self.assertEqual(stored["severity"]["tier"], "critical")

    def test_an_unavailable_second_opinion_does_not_flag(self):
        submission = self.grade(self.upload(CLEAN), unavailable_grade())

        self.assertFalse(submission.needs_review)
        self.assertIsNone(submission.review_tier)
        self.assertTrue(submission.feedback["second_opinion"]["needs_review"])

    def test_the_disagreement_does_not_raise_an_upload_flags_tier(self):
        # An off-topic answer is moderate; a critical disagreement beside
        # it no longer makes the paper critical.
        reply = dict(
            CLEAN, answers=[answer(1, html="4"), QUESTION_KIND_REPLIES[OFF_TOPIC]]
        )
        submission = self.grade(self.upload(reply), disagreement_grade("critical"))

        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "moderate")
        self.assertEqual(
            [r["type"] for r in submission.review_reasons], ["extraction_flag"]
        )

    def test_the_other_grading_reasons_still_flag(self):
        grading = copy.deepcopy(VALID_GRADE)
        grading["answers_not_found"] = [
            {
                "question_number": 2,
                "answer_status": NOT_FOUND_IN_DOCUMENT,
                "score_awarded": 0,
                "max_points": 5,
            }
        ]
        submission = self.grade(self.upload(CLEAN), grading)

        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "critical")


class TeacherPayloadsCarryNoDisagreementTest(Base):
    tag = "r1-pay"

    def setUp(self):
        super().setUp()
        # A row graded before the change: flagged by an upload flag AND a
        # disagreement, with a teacher's earlier resolution on record.
        reply = dict(
            CLEAN, answers=[answer(1, html="4"), QUESTION_KIND_REPLIES[OFF_TOPIC]]
        )
        self.submission = self.upload(reply)
        flag_reason = self.submission.review_reasons[0]
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            review_reasons=[
                flag_reason,
                {
                    "type": "grader_disagreement",
                    "question_number": 1,
                    "a_score": 8,
                    "b_score": 2,
                    "tier": "critical",
                    "gap_fraction": 0.6,
                },
                {
                    "type": "second_opinion_unavailable",
                    "detail": "insufficient credits",
                },
                {"resolved": "confirmed", "by": "x", "at": "2026-11-01T00:00:00"},
            ]
        )

    def assert_no_disagreement(self, reasons):
        self.assertIsInstance(reasons, list)
        types = [r.get("type") for r in reasons]
        for kind in DISAGREEMENT_TYPES:
            self.assertNotIn(kind, types)
        # Everything else is still sent.
        self.assertIn("extraction_flag", types)
        self.assertTrue(any(r.get("resolved") == "confirmed" for r in reasons))

    def test_the_submission_page(self):
        self.assert_no_disagreement(
            self.detail(self.teacher, self.submission)["review_reasons"]
        )

    def test_the_submission_list(self):
        self.assert_no_disagreement(
            self.list_row(self.teacher, self.submission)["review_reasons"]
        )

    def test_a_row_with_only_disagreement_sends_none(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            review_reasons=[
                {
                    "type": "grader_disagreement",
                    "question_number": 1,
                    "tier": "critical",
                }
            ]
        )
        self.assertIsNone(self.detail(self.teacher, self.submission)["review_reasons"])
        self.assertIsNone(
            self.list_row(self.teacher, self.submission)["review_reasons"]
        )

    def test_the_stored_record_is_kept(self):
        self.detail(self.teacher, self.submission)
        self.submission.refresh_from_db()
        self.assertEqual(
            [r.get("type") for r in self.submission.review_reasons][1:3],
            list(DISAGREEMENT_TYPES),
        )


class OneQuestionFlagsTheSubmissionTest(Base):
    tag = "r1-q"

    def assert_needs_review(self, submission, kind):
        self.assertTrue(submission.needs_review, kind)
        self.assertIs(self.detail(self.teacher, submission)["flagged_for_review"], True)
        self.assertIs(
            self.detail(submission.student, submission)["flagged_for_review"], True
        )
        self.assertIs(self.assignment_row(submission)["flagged_for_review"], True)

    def test_each_question_kind_at_upload(self):
        for kind, flagged in QUESTION_KIND_REPLIES.items():
            with self.subTest(kind):
                submission = self.upload(
                    dict(CLEAN, answers=[answer(1, html="4"), flagged])
                )
                self.assertEqual(
                    [f["kind"] for f in submission.extraction_flags], [kind]
                )
                self.assert_needs_review(submission, kind)

    def test_each_question_kind_on_a_typed_edit(self):
        submission = self.upload(CLEAN)
        for kind, flagged in QUESTION_KIND_REPLIES.items():
            with self.subTest(kind):
                with patch(
                    "students.services.ai_processor.extract_answer_with_retry",
                    return_value=dict(
                        copy.deepcopy(CLEAN), answers=[answer(1, html="4"), flagged]
                    ),
                ):
                    update_submission_from_raw_text(self.teacher, submission, "text")
                submission.refresh_from_db()
                self.assert_needs_review(submission, kind)

    def test_each_question_kind_after_grading_beside_a_disagreement(self):
        for kind, flagged in QUESTION_KIND_REPLIES.items():
            with self.subTest(kind):
                submission = self.upload(
                    dict(CLEAN, answers=[answer(1, html="4"), flagged])
                )
                submission = self.grade(submission, disagreement_grade())
                self.assert_needs_review(submission, kind)


class LevelOnEveryFlagTest(Base):
    tag = "r1-lvl"

    def test_the_papers_flag_is_assignment_level(self):
        submission = self.upload(MISMATCHED)
        for page in (
            self.detail(self.teacher, submission),
            self.detail(submission.student, submission),
            self.list_row(self.teacher, submission),
            self.assignment_row(submission),
        ):
            self.assertEqual(page["upload_mismatch"]["kind"], SUBMISSION_MISMATCH)
            self.assertEqual(page["upload_mismatch"]["level"], "assignment")

    def test_each_question_kind_is_question_level(self):
        for kind, flagged in QUESTION_KIND_REPLIES.items():
            with self.subTest(kind):
                submission = self.upload(
                    dict(CLEAN, answers=[answer(1, html="4"), flagged])
                )
                for user in (self.teacher, submission.student):
                    answers = self.detail(user, submission)["answers"]
                    self.assertIsNone(answers[0]["review_flag"])
                    self.assertEqual(answers[1]["review_flag"]["kind"], kind)
                    self.assertEqual(answers[1]["review_flag"]["level"], "question")

    def test_the_level_is_added_when_shown_never_stored(self):
        submission = self.upload(MISMATCHED)
        self.assertTrue(all("level" not in f for f in submission.extraction_flags))

    def test_the_schema_describes_level(self):
        from students.flag_serializers import ReviewFlagSerializer

        field = ReviewFlagSerializer().fields["level"]
        choices = field.choices  # type: ignore[attr-defined]
        self.assertEqual(sorted(choices), ["assignment", "question"])
        self.assertTrue(field.help_text)


class ReviewQueueStillWorksForUploadFlagsTest(Base):
    tag = "r1-queue"

    def test_needs_review_and_review_tier_filters(self):
        critical = self.upload(MISMATCHED)
        moderate = self.upload(
            dict(CLEAN, answers=[answer(1, html="4"), QUESTION_KIND_REPLIES[OFF_TOPIC]])
        )
        moderate = self.grade(moderate, disagreement_grade("critical"))
        self.assertEqual(moderate.review_tier, "moderate")

        flagged = {
            r["id"] for r in self.list_rows(self.teacher, {"needs_review": "true"})
        }
        self.assertEqual(flagged, {str(critical.pk), str(moderate.pk)})
        criticals = {
            r["id"]
            for r in self.list_rows(
                self.teacher, {"needs_review": "true", "review_tier": "critical"}
            )
        }
        self.assertEqual(criticals, {str(critical.pk)})


class StudentsSeeNoMoreTest(Base):
    tag = "r1-stu"

    def test_a_student_gets_no_review_queue_fields(self):
        reply = dict(
            CLEAN, answers=[answer(1, html="4"), QUESTION_KIND_REPLIES[OFF_TOPIC]]
        )
        submission = self.grade(self.upload(reply), disagreement_grade())
        row = self.list_row(submission.student, submission)
        self.assertIs(row["needs_review"], False)
        self.assertIsNone(row["review_reasons"])
        self.assertIsNone(row["review_severity"])
        self.assertIsNone(row["review_tier"])
        page = self.detail(submission.student, submission)
        for key in ("review_reasons", "second_opinion", "review_tier"):
            self.assertNotIn(key, page)

    def test_a_disagreement_only_paper_shows_the_student_nothing(self):
        submission = self.grade(self.upload(CLEAN), disagreement_grade())
        page = self.detail(submission.student, submission)
        self.assertIs(page["flagged_for_review"], False)
        self.assertIsNone(page["upload_mismatch"])


MIGRATION = "students.migrations.0037_disagreement_leaves_review_queue"


class CleanupMigrationTest(Base):
    """The 0037 data migration, run on rows as they were before the change."""

    tag = "r1-mig"

    def row(self, reasons, tier="critical", severity=0.9):
        student = self.new_student()
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[],
            score=8,
            feedback={"second_opinion": {"disagreements": [{"question_number": 1}]}},
            needs_review=True,
            review_reasons=reasons,
            review_tier=tier,
            review_severity=severity,
        )

    def forward(self):
        module = importlib.import_module(MIGRATION)
        module.leave_queue(django_apps, None)

    DISAGREE = {"type": "grader_disagreement", "question_number": 1, "tier": "critical"}
    UNAVAILABLE = {"type": "second_opinion_unavailable", "detail": "x"}

    def test_disagreement_only_rows_leave_the_queue(self):
        only = self.row([self.DISAGREE])
        unavailable = self.row([self.UNAVAILABLE], tier="moderate")
        both = self.row([self.DISAGREE, self.UNAVAILABLE])

        self.forward()

        for submission in (only, unavailable, both):
            before = list(submission.review_reasons)
            submission.refresh_from_db()
            self.assertFalse(submission.needs_review)
            self.assertIsNone(submission.review_severity)
            self.assertIsNone(submission.review_tier)
            # Grades, feedback and the record are untouched.
            self.assertEqual(float(submission.score), 8.0)
            self.assertEqual(
                submission.feedback["second_opinion"]["disagreements"][0][
                    "question_number"
                ],
                1,
            )
            self.assertEqual(submission.review_reasons, before)

    def test_rows_with_an_upload_problem_stay(self):
        kept = [
            self.row([{"type": "extraction_flag", "kind": OFF_TOPIC}, self.DISAGREE]),
            self.row(
                [{"type": "answers_unreadable", "left_out": "all"}, self.DISAGREE]
            ),
        ]
        self.forward()
        for submission in kept:
            submission.refresh_from_db()
            self.assertTrue(submission.needs_review)

    def test_rows_with_another_grading_reason_stay(self):
        # SM Q1: answer_not_found and ai_reply_corrected flag on their own.
        kept = [
            self.row(
                [{"type": "answer_not_found", "question_number": 2}, self.DISAGREE]
            ),
            self.row([{"type": "ai_reply_corrected"}, self.DISAGREE]),
        ]
        self.forward()
        for submission in kept:
            submission.refresh_from_db()
            self.assertTrue(submission.needs_review)

    def test_rows_that_stay_get_their_tier_from_what_remains(self):
        # SM Q2: the disagreement was the only critical reason; what is left
        # is one moderate upload flag, ranked as grading ranks it.
        review_state = importlib.import_module("students.review_state").review_state

        remaining = [{"type": "extraction_flag", "kind": OFF_TOPIC}]
        moderate = self.row(remaining + [self.DISAGREE])
        critical = self.row(
            [{"type": "answer_not_found", "question_number": 2}, self.DISAGREE],
            tier="moderate",
            severity=0.1,
        )
        self.forward()
        moderate.refresh_from_db()
        critical.refresh_from_db()
        self.assertEqual(moderate.review_tier, "moderate")
        self.assertAlmostEqual(moderate.review_severity, review_state(remaining)[0], 6)
        self.assertEqual(critical.review_tier, "critical")
        self.assertGreater(critical.review_severity, moderate.review_severity)

    def test_rows_off_the_queue_are_not_touched(self):
        resolved = self.row(
            [self.DISAGREE, {"resolved": "confirmed", "by": "x", "at": "y"}]
        )
        StudentSubmission.objects.filter(pk=resolved.pk).update(needs_review=False)
        self.forward()
        resolved.refresh_from_db()
        self.assertEqual(resolved.review_tier, "critical")

    def test_reverse_is_a_noop(self):
        from django.db.migrations import RunPython

        module = importlib.import_module(MIGRATION)
        [operation] = module.Migration.operations
        self.assertIsInstance(operation, RunPython)
        self.assertIs(operation.reverse_code, RunPython.noop)
        self.assertEqual(
            module.Migration.dependencies,
            [("students", "0036_studentsubmission_pending_result")],
        )


class NoPayloadCarriesTheSecondOpinionTest(Base):
    """OWNER (2026-11-12, SM Q4): teachers see nothing about the second AI
    anywhere; it stays stored in feedback for our own checks."""

    tag = "r1-so"

    def test_no_teacher_or_student_payload_carries_it(self):
        submission = self.grade(self.upload(CLEAN), disagreement_grade())
        pages = {
            "teacher detail": self.detail(self.teacher, submission),
            "teacher list": self.list_row(self.teacher, submission),
            "teacher assignment row": self.assignment_row(submission),
            "student detail": self.detail(submission.student, submission),
            "student list": self.list_row(submission.student, submission),
        }
        for name, page in pages.items():
            with self.subTest(name):
                self.assertNotIn("second_opinion", page)
                self.assertNotIn("second-model", str(page))
        submission.refresh_from_db()
        self.assertIn("second_opinion", submission.feedback)


class WholePaperUnreadableTest(Base):
    """SM Q5: the teacher's "Needs review" is true for a whole-paper-
    unreadable submission; the student's stays as today."""

    tag = "r1-unr"

    def setUp(self):
        super().setUp()
        self.submission = self.upload(CLEAN)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            needs_review=True,
            review_reasons=[{"type": "answers_unreadable", "left_out": "all"}],
            review_tier="critical",
        )

    def test_the_teacher_sees_needs_review(self):
        self.assertIs(
            self.detail(self.teacher, self.submission)["flagged_for_review"], True
        )
        self.assertIs(
            self.list_row(self.teacher, self.submission)["flagged_for_review"], True
        )
        self.assertIs(self.assignment_row(self.submission)["flagged_for_review"], True)

    def test_the_student_sees_what_they_see_today(self):
        student = self.submission.student
        self.assertIs(
            self.detail(student, self.submission)["flagged_for_review"], False
        )
        self.assertIs(
            self.list_row(student, self.submission)["flagged_for_review"], False
        )
        page = self.get(
            student, reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertIs(page["student_flagged_for_review"], False)

    def test_a_teachers_review_clears_it(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            needs_review=False
        )
        self.assertIs(
            self.detail(self.teacher, self.submission)["flagged_for_review"], False
        )


class LevelOnTeacherReviewReasonsTest(Base):
    """SM Q5 (a): the teacher's review_reasons entries about the upload
    carry their level too; the stored entries do not."""

    tag = "r1-rl"

    def test_each_entry_says_its_level(self):
        submission = self.upload(MISMATCHED)
        reasons = [
            {"type": "answers_unreadable", "left_out": "all"},
            {"type": "answer_not_found", "question_number": 2},
            {"type": "ai_reply_corrected"},
        ] + submission.review_reasons
        StudentSubmission.objects.filter(pk=submission.pk).update(
            review_reasons=reasons
        )
        sent = self.detail(self.teacher, submission)["review_reasons"]
        self.assertEqual(
            [(r["type"], r.get("kind"), r.get("level")) for r in sent],
            [
                ("answers_unreadable", None, "assignment"),
                ("answer_not_found", None, "question"),
                ("ai_reply_corrected", None, None),
                ("extraction_flag", SUBMISSION_MISMATCH, "assignment"),
                ("extraction_flag", OFF_TOPIC, "question"),
                ("extraction_flag", ANSWER_NOT_FOUND, "question"),
            ],
        )
        submission.refresh_from_db()
        self.assertTrue(all("level" not in r for r in submission.review_reasons))
