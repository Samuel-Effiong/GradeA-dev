"""H-154: a grade made from a corrected AI reply goes to the teacher.

When the AI's grading reply repeated a question or held an evaluation for
a question that does not exist, the arithmetic authority keeps one
evaluation per question and drops the rest
(`ai_processor.tests_reply_repeated_evaluation`). The grade that results
is the cautious one, but it was made from a reply that was wrong in
shape, so the paper is put in the teacher's review queue: a third source
of review beside "answer not found" and "the second grader disagreed".

No model change: the reason is one more entry in `review_reasons`, with
the question numbers and counts and nothing of the answers.
"""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session
from classrooms.services import enroll_student_by_email
from students.feedback_projection import grading_result_for_formatter
from students.models import StudentSubmission
from students.services import _populate_and_save_grade
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def grading_result(note=None, **extra):
    verification = {
        "individual_scores": [8, 8, 8],
        "manual_sum": 24,
        "verification_status": "PASS",
        "calculation_notes": "8 + 8 + 8 = 24.",
    }
    verification.update(note or {})
    result = {
        "grading_summary": {
            "total_score": 24,
            "max_total_points": 30,
            "percentage": 80.0,
        },
        "grading_confidence": 90,
        "score_calculation_verification": verification,
    }
    result.update(extra)
    return result


REPEATED = {
    "verification_status": "CORRECTED",
    "repeated_evaluations_dropped": [{"question_number": 2, "dropped": 1}],
    "unmatched_evaluations_dropped": 0,
    "correction_note": "1 repeated evaluation(s) were left out of the sum.",
}
STRAY = {
    "verification_status": "CORRECTED",
    "repeated_evaluations_dropped": [],
    "unmatched_evaluations_dropped": 2,
    "correction_note": "2 evaluation(s) matching no question were left out.",
}


@override_settings(CACHES=LOCMEM)
class SavedGradeCase(TestCase):
    def setUp(self):
        cache.clear()
        teacher = User.objects.create_user(email="h154-t@x.test")
        teacher.user_type = UserTypes.TEACHER
        teacher.is_active = True
        teacher.save()
        course = Course.objects.create(
            name="English 7",
            teacher=teacher,
            session=Session.objects.create(name="Term", teacher=teacher),
        )
        student, _ = enroll_student_by_email(course=course, email="h154-s@x.test")
        assignment = Assignment.objects.create(
            title="Essay",
            course=course,
            teacher=teacher,
            status=AssignmentStatus.PUBLISHED,
            total_points=30,
        )
        self.teacher, self.student = teacher, student
        self.submission = StudentSubmission.objects.create(
            student=student, assignment=assignment, answers={}
        )

    def save(self, grading):
        _populate_and_save_grade(self.submission, grading, None)
        self.submission.refresh_from_db()
        return self.submission


class ACorrectedReplyIsPutInTheReviewQueue(SavedGradeCase):
    def test_a_clean_reply_is_not_flagged(self):
        """Control: green with or without the fix."""
        saved = self.save(grading_result())

        self.assertFalse(saved.needs_review)
        self.assertIsNone(saved.review_reasons)
        self.assertIsNone(saved.review_tier)

    def test_a_dropped_repeat_flags_the_paper(self):
        saved = self.save(grading_result(REPEATED))

        self.assertTrue(saved.needs_review)
        self.assertEqual(
            saved.review_reasons,
            [
                {
                    "type": "ai_reply_corrected",
                    "repeated_questions": [2],
                    "repeated_dropped": 1,
                    "unmatched_dropped": 0,
                }
            ],
        )
        self.assertEqual(saved.review_tier, "moderate")
        self.assertIsNotNone(saved.review_severity)

    def test_a_dropped_stray_flags_the_paper(self):
        saved = self.save(grading_result(STRAY))

        self.assertTrue(saved.needs_review)
        self.assertEqual(
            saved.review_reasons,
            [
                {
                    "type": "ai_reply_corrected",
                    "repeated_questions": [],
                    "repeated_dropped": 0,
                    "unmatched_dropped": 2,
                }
            ],
        )

    def test_it_is_one_reason_beside_the_others_not_instead_of_them(self):
        saved = self.save(
            grading_result(
                REPEATED,
                answers_not_found=[
                    {
                        "question_number": 3,
                        "answer_status": "NOT_FOUND_IN_DOCUMENT",
                        "score_awarded": 0,
                        "max_points": 10,
                    }
                ],
            )
        )

        self.assertEqual(
            [reason["type"] for reason in saved.review_reasons],
            ["answer_not_found", "ai_reply_corrected"],
        )
        self.assertEqual(saved.review_tier, "critical")

    def test_a_regrade_from_a_clean_reply_clears_the_flag(self):
        self.save(grading_result(REPEATED))

        saved = self.save(grading_result())

        self.assertFalse(saved.needs_review)
        self.assertIsNone(saved.review_reasons)

    def test_the_score_saved_is_the_corrected_one(self):
        """Control: the save writes the summary it is given."""
        saved = self.save(grading_result(REPEATED))

        self.assertEqual(float(saved.score), 24.0)
        self.assertEqual(saved.max_points, 30)

    def test_a_note_that_is_not_a_dict_is_not_a_reason(self):
        """Control: a result from any other source, with no note or a
        malformed one, saves as before."""
        for note in (None, "PASS", ["PASS"]):
            with self.subTest(note=note):
                result = grading_result()
                result["score_calculation_verification"] = note

                saved = self.save(result)

                self.assertFalse(saved.needs_review)


class TheFormatterIsNotToldOfTheCorrection(TestCase):
    """The feedback formatter words the result for the STUDENT, and it can
    restate whatever it is sent. That a reply was corrected is for the
    teacher's review queue: the formatter gets the arithmetic and nothing
    of the correction. No database."""

    def test_a_clean_note_is_sent_whole(self):
        """Control: green with or without the fix."""
        result = grading_result()

        self.assertEqual(
            grading_result_for_formatter(result)["score_calculation_verification"],
            result["score_calculation_verification"],
        )

    def test_a_corrected_note_is_sent_as_arithmetic_only(self):
        for name, note in (("repeat", REPEATED), ("stray", STRAY)):
            with self.subTest(dropped=name):
                sent = grading_result_for_formatter(grading_result(note))

                self.assertEqual(
                    sent["score_calculation_verification"],
                    {
                        "individual_scores": [8, 8, 8],
                        "manual_sum": 24,
                        "calculation_notes": "8 + 8 + 8 = 24.",
                    },
                )
                self.assertNotIn("CORRECTED", str(sent))
                self.assertNotIn("left out", str(sent))

    def test_the_saved_result_is_not_changed_by_it(self):
        result = grading_result(REPEATED)

        grading_result_for_formatter(result)

        self.assertEqual(
            result["score_calculation_verification"]["verification_status"],
            "CORRECTED",
        )
        self.assertIn("correction_note", result["score_calculation_verification"])

    def test_the_rest_of_the_result_is_sent_as_before(self):
        """Control: the second opinion is still left out, the rest kept."""
        sent = grading_result_for_formatter(
            grading_result(REPEATED, second_opinion={"model": "b"}, recommendations=[])
        )

        self.assertNotIn("second_opinion", sent)
        self.assertEqual(sent["grading_summary"]["total_score"], 24)
        self.assertEqual(sent["recommendations"], [])


#: What a corrected reply leaves in the saved row: the note's status, its
#: field names, its sentence, and the review reason's type.
OF_THE_CORRECTION = (
    "CORRECTED",
    "correction_note",
    "left out of the sum",
    "repeated_evaluations_dropped",
    "unmatched_evaluations_dropped",
    "ai_reply_corrected",
)


class NoStudentFacingAnswerHoldsTheCorrection(SavedGradeCase):
    """The note and the review reason are for the teacher. A student whose
    grade is RELEASED reads the saved result through the student
    projection and the review fields blanked (H-127): this holds that
    neither lets anything of the correction through."""

    def setUp(self):
        super().setUp()
        self.save(
            grading_result(
                REPEATED,
                question_evaluations=[
                    {"question_number": n, "score_awarded": 8, "max_points": 10}
                    for n in (1, 2, 3)
                ],
            )
        )
        self.submission.is_published = True
        self.submission.save()
        cache.clear()

    def read(self, user, name, **kwargs):
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(reverse(name, kwargs=kwargs))
        self.assertEqual(response.status_code, 200, response.data)
        return json.dumps(response.data, default=str)

    def test_the_saved_row_really_holds_it(self):
        """Control: without this the two tests below could pass on a row
        that has nothing to hide."""
        self.submission.refresh_from_db()
        saved = self.submission.feedback or {}
        reasons = self.submission.review_reasons or []
        self.assertEqual(
            saved["score_calculation_verification"]["verification_status"],
            "CORRECTED",
        )
        self.assertEqual([reason["type"] for reason in reasons], ["ai_reply_corrected"])
        self.assertIn(
            "ai_reply_corrected",
            self.read(self.teacher, "student-submission-detail", pk=self.submission.pk),
        )

    def test_the_students_own_submission(self):
        answer = self.read(
            self.student, "student-submission-detail", pk=self.submission.pk
        )

        self.assertIn('"total_score": 24', answer)
        for word in OF_THE_CORRECTION:
            self.assertNotIn(word, answer)

    def test_the_students_list_of_submissions(self):
        answer = self.read(self.student, "student-submission-list")

        self.assertIn(str(self.submission.pk), answer)
        for word in OF_THE_CORRECTION:
            self.assertNotIn(word, answer)
