"""
H-253: the reader's flags live inside each answer, not in a list beside
them (the owner's ruling, 2026-10-22).

  * Each entry of `answers` carries `review_flag`: {kind, message, detail}
    for that question, or null when there is nothing to check.
  * The one flag about the whole paper ("this upload does not appear to be
    for this assignment") has no question, so it is its own field on the
    submission, `upload_mismatch` (null when the paper is for this
    assignment). It is also in `review_reasons`, as before.
  * `submission_flags` (and the student page's `student_submission_flags`)
    is gone. `review_reasons` and `flagged_for_review` stay.
  * The answer document no longer says "No answer submitted" / "Skipped"
    for an answer that was not found or not read, and names each flag.
  * The flags are added to the answers when they are sent, from the
    paper's stored flags (`extraction_flags`); the stored answers are not
    changed, so a paper read since H-242 shows them with no rewrite.

Run with:
    python manage.py test students.tests_h253_flags_inside_answers
"""

import copy
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    OFF_TOPIC,
    SUBMISSION_MISMATCH,
    answer_review_flag,
    answers_with_flags,
    upload_mismatch_flag,
)
from ai_processor.extraction_schemas import (
    BLANK,
    FLAG_OFF_TOPIC,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from students.models import StudentSubmission
from students.services import (
    student_submission_to_html,
    update_submission_from_raw_text,
    upload_answers_engine,
)
from students.tests_h242_submission_flags import (
    CLEAN,
    ESSAY,
    MISMATCHED,
    TWO_QUESTIONS,
    answer,
)
from students.tests_no_grade_tell_before_release import plain
from students.tests_post_grading_submission_lock import _classroom

OFF_TOPIC_FLAG = {
    "kind": OFF_TOPIC,
    "message": "Question 1: the answer does not appear to be about this question.",
    "detail": "About Macbeth.",
}
NOT_FOUND_FLAG = {
    "kind": ANSWER_NOT_FOUND,
    "message": "Question 2: no answer to this question could be found in the upload.",
    "detail": "Only the essay is on the page.",
}
MISMATCH = {
    "kind": SUBMISSION_MISMATCH,
    "message": "This upload does not appear to be for this assignment.",
    "detail": ESSAY,
}


class TheRulesTest(SimpleTestCase):
    def test_flags_are_matched_to_answers_by_number(self):
        flags = [
            dict(MISMATCH, question_number=None),
            dict(OFF_TOPIC_FLAG, question_number="Q1 "),
        ]
        shown = answers_with_flags(
            [dict(answer(1), question_number="q1"), answer(2), "odd"], flags
        )
        self.assertEqual(shown[0]["review_flag"], OFF_TOPIC_FLAG)
        self.assertIsNone(shown[1]["review_flag"])
        self.assertEqual(shown[2], "odd")
        # A paper read before H-242 has no flags: every answer says none.
        self.assertIsNone(answers_with_flags([answer(1)], None)[0]["review_flag"])
        self.assertEqual(answers_with_flags("not a list", flags), "not a list")

    def test_one_answers_flag(self):
        self.assertEqual(
            answer_review_flag(
                answer(
                    1,
                    html="<p>Macbeth...</p>",
                    flag=FLAG_OFF_TOPIC,
                    detail="About Macbeth.",
                )
            ),
            OFF_TOPIC_FLAG,
        )
        self.assertIsNone(answer_review_flag(answer(1)))
        self.assertIsNone(answer_review_flag(answer(1, BLANK)))
        self.assertIsNone(answer_review_flag("not an answer"))

    def test_the_paper_flag(self):
        self.assertEqual(
            upload_mismatch_flag(MISMATCHED["answers"], matches=False, detail=ESSAY),
            MISMATCH,
        )
        self.assertIsNone(upload_mismatch_flag(CLEAN["answers"], matches=True))
        # One answer on topic and it is this assignment's paper (H-242).
        self.assertIsNone(
            upload_mismatch_flag([answer(1, html="4")], matches=False, detail=ESSAY)
        )


class StoredWithTheAnswersTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.course, self.assignment = _classroom("h253")
        self.assignment.questions = TWO_QUESTIONS
        self.assignment.save()

    def _upload(self, reply):
        with (
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=copy.deepcopy(reply),
            ),
            patch("students.services.send_email_task.delay"),
        ):
            submission = upload_answers_engine(self.assignment, "content", self.student)
        submission.refresh_from_db()
        return submission

    def shown(self, submission):
        return answers_with_flags(submission.answers, submission.extraction_flags)

    def test_each_answer_carries_its_own_flag(self):
        submission = self._upload(MISMATCHED)
        self.assertEqual(
            [entry["review_flag"] for entry in self.shown(submission)],
            [OFF_TOPIC_FLAG, NOT_FOUND_FLAG],
        )
        # The answer itself is untouched.
        self.assertEqual(submission.answers[0]["answer_html"], "<p>Macbeth...</p>")

    def test_the_stored_answers_are_what_the_reader_returned(self):
        submission = self._upload(MISMATCHED)
        self.assertEqual(submission.answers, MISMATCHED["answers"])

    def test_a_clean_answer_says_so(self):
        submission = self._upload(CLEAN)
        self.assertEqual(
            [entry["review_flag"] for entry in self.shown(submission)], [None, None]
        )

    def test_a_typed_edit_writes_them_the_same_way(self):
        submission = self._upload(CLEAN)
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            return_value=copy.deepcopy(MISMATCHED),
        ):
            update_submission_from_raw_text(self.teacher, submission, "edited text")
        submission.refresh_from_db()
        self.assertEqual(self.shown(submission)[1]["review_flag"], NOT_FOUND_FLAG)

    def test_the_review_queue_is_unchanged(self):
        submission = self._upload(MISMATCHED)
        self.assertTrue(submission.needs_review)
        self.assertEqual(
            [reason["kind"] for reason in submission.review_reasons],
            [SUBMISSION_MISMATCH, OFF_TOPIC, ANSWER_NOT_FOUND],
        )

    # -- the answer document ---------------------------------------------

    def test_the_document_names_each_flag_under_its_answer(self):
        html = student_submission_to_html(self._upload(MISMATCHED))
        self.assertIn(
            "<strong>Flagged for review:</strong> " + OFF_TOPIC_FLAG["message"], html
        )
        self.assertIn(OFF_TOPIC_FLAG["detail"], html)
        self.assertIn(NOT_FOUND_FLAG["message"], html)
        self.assertNotIn("No answer submitted.", html)
        self.assertNotIn("Skipped", html)

    def test_the_document_says_why_an_answer_is_empty(self):
        submission = self._upload(CLEAN)
        cases = (
            (
                NOT_FOUND_IN_DOCUMENT,
                "No answer could be found for this question in the upload.",
                "Not found in the upload",
            ),
            (
                ILLEGIBLE,
                "This answer could not be read from the upload.",
                "Could not be read",
            ),
            (BLANK, "Left blank.", "Left blank"),
            (None, "No answer submitted.", "Skipped"),
        )
        for answer_status, empty, word in cases:
            with self.subTest(answer_status=answer_status):
                entry = {
                    "question_number": 1,
                    "answer_html": "",
                    "answer_status": answer_status,
                }
                submission.answers = [entry]
                html = student_submission_to_html(submission)
                self.assertIn(f"<em>{empty}</em>", html)
                self.assertIn(f"<strong>Status:</strong> {word}", html)

    def test_the_stored_document_is_written_after_the_flags(self):
        submission = self._upload(MISMATCHED)
        self.assertIn("Flagged for review:", submission.raw_input)


class ThePagesTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.course, self.assignment = _classroom("h253p")
        self.assignment.questions = TWO_QUESTIONS
        self.assignment.save()
        with (
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=copy.deepcopy(MISMATCHED),
            ),
            patch("students.services.send_email_task.delay"),
        ):
            self.submission = upload_answers_engine(
                self.assignment, "content", self.student
            )

    def _get(self, user, url):
        cache.clear()
        self.client.force_authenticate(user=user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return plain(response.data)

    def _detail(self, user):
        return self._get(
            user,
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk}),
        )

    def _list(self, user):
        rows = self._get(user, reverse("student-submission-list"))
        rows = rows["results"] if isinstance(rows, dict) else rows
        return next(r for r in rows if str(r["id"]) == str(self.submission.pk))

    def _assignment(self, user):
        return self._get(
            user, reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )

    def _row(self):
        rows = self._assignment(self.teacher)["student_submissions"]
        return next(
            r for r in rows if str(r["submission_id"]) == str(self.submission.pk)
        )

    def assert_page(
        self, page, mismatch_key="upload_mismatch", flagged_key="flagged_for_review"
    ):
        self.assertEqual(page[mismatch_key], MISMATCH)
        self.assertIs(page[flagged_key], True)
        self.assertNotIn("submission_flags", page)
        self.assertNotIn("student_submission_flags", page)

    def test_the_submission_and_the_list_for_both(self):
        for user in (self.teacher, self.student):
            for page in (self._detail(user), self._list(user)):
                with self.subTest(user=user.user_type):
                    self.assert_page(page)

    def test_the_teacher_reads_each_flag_in_its_answer(self):
        page = self._detail(self.teacher)
        self.assertEqual(
            [entry["review_flag"] for entry in page["answers"]],
            [OFF_TOPIC_FLAG, NOT_FOUND_FLAG],
        )

    def test_the_student_reads_each_flag_under_its_answer_in_the_document(self):
        # A student is never sent `answers` (it holds the reader's notes):
        # their page is the answer document, which prints each flag.
        page = self._detail(self.student)
        self.assertNotIn("answers", page)
        for flag in (OFF_TOPIC_FLAG, NOT_FOUND_FLAG):
            self.assertIn(flag["message"], page["raw_input"])

    def test_the_assignments_student_rows(self):
        self.assert_page(self._row())

    def test_the_students_assignment_page(self):
        self.assert_page(
            self._assignment(self.student),
            mismatch_key="student_upload_mismatch",
            flagged_key="student_flagged_for_review",
        )

    def test_a_paper_for_this_assignment_has_no_mismatch(self):
        flags = [
            flag
            for flag in self.submission.extraction_flags
            if flag["kind"] != SUBMISSION_MISMATCH
        ]
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            extraction_flags=flags
        )
        self.assertIsNone(self._detail(self.student)["upload_mismatch"])
        self.assertIsNone(self._row()["upload_mismatch"])
        self.assertIsNone(self._assignment(self.student)["student_upload_mismatch"])

    def test_the_student_still_never_gets_the_review_queue(self):
        page = self._list(self.student)
        self.assertIs(page["needs_review"], False)
        self.assertIsNone(page["review_reasons"])
