"""
Attribution of teacher-uploaded ("proxy") submissions to enrolled students.

The extractor reads a student name off the uploaded file, and
students.services._match_enrolled_student turns it into a student row.
Before the section 7 audit that lookup was `first_name__icontains /
last_name__icontains ... .first()`: an ambiguous name ("Sam" against
Samuel and Samantha; a single-token name against everyone whose first name
contained it) silently picked an arbitrary student, and one student's work
and grade landed on another's record with no error anywhere. A match is
now accepted only when it is unique.

Run with:
    python manage.py test students.tests_proxy_upload_attribution
"""

from unittest.mock import patch

from django.test import TestCase

from assignments.models import Assignment
from AutoGrader.error_messages import (
    describe_background_task_error,
    describe_user_error,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.exceptions import (
    CannotAssociateStudentError,
    StudentNameUnmatchedError,
    StudentNotOnRosterError,
    SubmissionLimitReachedError,
)
from students.models import StudentSubmission
from students.services import _match_enrolled_student, upload_answers_engine
from users.models import CustomUser, UserTypes


class MatchEnrolledStudentTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="proxy-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def _enrol(self, first_name, last_name, status=EnrollmentStatusType.ENROLLED):
        student = CustomUser.objects.create_user(
            email=f"{first_name}-{last_name}-{status}@example.com".lower(),
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name=first_name,
            last_name=last_name,
        )
        StudentCourse.objects.create(
            student=student, course=self.course, enrollment_status=status
        )
        return student

    def test_exact_match_wins_even_when_substring_would_be_ambiguous(self):
        sam = self._enrol("Sam", "Effiong")
        self._enrol("Samuel", "Effiong")

        self.assertEqual(_match_enrolled_student(self.course, "Sam Effiong"), sam)

    def test_case_and_whitespace_are_ignored(self):
        samuel = self._enrol("Samuel", "Effiong")

        self.assertEqual(
            _match_enrolled_student(self.course, "  samuel   EFFIONG "), samuel
        )

    def test_ambiguous_substring_match_is_refused_not_guessed(self):
        self._enrol("Samuel", "Effiong")
        self._enrol("Samantha", "Effiong")

        with self.assertRaises(CannotAssociateStudentError) as ctx:
            _match_enrolled_student(self.course, "Sam Effiong")
        self.assertIn("more than one", str(ctx.exception))

    def test_single_token_name_matching_two_students_is_refused(self):
        # A single token leaves last_name empty, and `icontains ""` matches
        # every row - previously this picked whichever Samuel came first.
        self._enrol("Samuel", "Adams")
        self._enrol("Samuel", "Brown")

        with self.assertRaises(CannotAssociateStudentError) as ctx:
            _match_enrolled_student(self.course, "Samuel")
        self.assertIn("more than one", str(ctx.exception))

    def test_single_token_name_matching_one_student_is_accepted(self):
        samuel = self._enrol("Samuel", "Adams")
        self._enrol("Bola", "Brown")

        self.assertEqual(_match_enrolled_student(self.course, "Samuel"), samuel)

    def test_student_who_is_not_enrolled_is_not_matched(self):
        self._enrol("Samuel", "Effiong", status=EnrollmentStatusType.PENDING)

        # FR-A-06 S6c: the teacher's own pending student is named as not on
        # the roster (STUDENT_NOT_ON_ROSTER), still refused, never matched.
        with self.assertRaises(StudentNotOnRosterError) as ctx:
            _match_enrolled_student(self.course, "Samuel Effiong")
        self.assertIsInstance(ctx.exception, CannotAssociateStudentError)
        self.assertIn("isn't enrolled in this course", str(ctx.exception))

    def test_without_a_file_name_the_not_on_roster_sentence_starts_capitalised(self):
        # v2's S6c N1: the #2 template opens with the file name. The stand-in
        # used when none is known starts the sentence capitalised...
        self._enrol("Samuel", "Effiong", status=EnrollmentStatusType.PENDING)

        with self.assertRaises(StudentNotOnRosterError) as ctx:
            _match_enrolled_student(self.course, "Samuel Effiong")
        self.assertTrue(
            str(ctx.exception).startswith("The paper belongs to Samuel Effiong"),
            str(ctx.exception),
        )
        self.assertEqual(ctx.exception.params["file_name"], "the paper")

    def test_a_real_file_name_is_never_recased(self):
        # ...but a real file name is shown exactly as given, even where it
        # opens the sentence, and mid-sentence the stand-in stays lower case.
        self._enrol("Samuel", "Effiong", status=EnrollmentStatusType.PENDING)

        with self.assertRaises(StudentNotOnRosterError) as ctx:
            _match_enrolled_student(self.course, "Samuel Effiong", file_name="scan.png")
        self.assertTrue(str(ctx.exception).startswith("scan.png belongs to"))

        with self.assertRaises(StudentNameUnmatchedError) as ctx2:
            _match_enrolled_student(self.course, "")
        self.assertTrue(
            str(ctx2.exception).startswith("We couldn't match the paper to"),
            str(ctx2.exception),
        )

    def test_missing_name_is_reported(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(StudentNameUnmatchedError) as ctx:
                    _match_enrolled_student(self.course, value)
                self.assertIn("no name was found", str(ctx.exception))

    def test_ambiguous_proxy_upload_creates_no_submission_for_anyone(self):
        # End to end through upload_answers_engine: the load-bearing outcome
        # is that NOBODY gets a submission they didn't make.
        self._enrol("Samuel", "Effiong")
        self._enrol("Samantha", "Effiong")

        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_answer_with_retry.return_value = {
                "student_name": "Sam Effiong",
                "answers": [{"question_number": 1, "answer_html": "x"}],
            }
            with self.assertRaises(CannotAssociateStudentError):
                upload_answers_engine(
                    self.assignment, "ignored", self.teacher, is_proxy_upload=True
                )

        self.assertFalse(StudentSubmission.objects.exists())

    def test_unique_proxy_upload_lands_on_the_right_student(self):
        samuel = self._enrol("Samuel", "Effiong")
        self._enrol("Samantha", "Effiong")

        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_answer_with_retry.return_value = {
                "student_name": "Samuel Effiong",
                "answers": [{"question_number": 1, "answer_html": "x"}],
            }
            submission = upload_answers_engine(
                self.assignment, "ignored", self.teacher, is_proxy_upload=True
            )

        self.assertEqual(submission.student, samuel)
        # A teacher upload is not one of the student's own attempts.
        self.assertEqual(submission.attempt_count, 0)


class SubmissionLimitReachedMessageTest(TestCase):
    """The attempt-limit error is user-authored and must reach the user
    verbatim, not collapse into the generic "we couldn't process" text."""

    def test_limit_error_passes_through_both_describers(self):
        exc = SubmissionLimitReachedError("You have reached the maximum of 3")
        self.assertEqual(describe_user_error(exc, "fallback"), str(exc))
        self.assertEqual(describe_background_task_error(exc, "fallback"), str(exc))

    def test_limit_error_is_still_a_value_error_for_existing_handlers(self):
        self.assertIsInstance(SubmissionLimitReachedError("x"), ValueError)
