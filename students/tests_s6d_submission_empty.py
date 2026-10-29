"""
Epic A S6d, FR-A-06 #6 SUBMISSION_EMPTY for empty TEXT input (08a §5, §6.1).

S6b refuses an empty FILE (zero bytes, zero pages). This covers the other
empty input the ruling names: a raw-text edit whose text is present but
empty or whitespace. It is refused 422 with the coded envelope, before the
billed extraction: nothing extracted, charged or queued.

Out of scope, per the founder's final ruling (08a §6.1): blank ANSWERS in
real input are not refused in Epic A (an all-blank paper is graded as
today), and a missing raw_input field stays a 400 validation error.

Known gap (stated in EVIDENCE): the submission row already exists when its
text is edited to nothing, and the teacher still has no free, no-AI way to
record a 0 for it. That route is the deferred follow-up in §6.1.
"""

from unittest.mock import patch

from django.urls import reverse

from assignments.exceptions import SubmissionEmptyError
from audit.enums import ErrorClass
from billing.models import CreditLedger
from students.models import BackgroundProcessingTask
from students.services import SUBMITTED_TEXT, update_submission_from_raw_text
from students.tests_s6d_rubric_gate import AI, DISPATCH, RubricGateTestCase

EMPTY_TEXTS = ("", "   ", "\n\t ")


class EmptyTextSubmissionTests(RubricGateTestCase):
    def setUp(self):
        super().setUp()
        from assignments.tests_cache_matrix_g3 import question

        self.assignment, self.submission = self.world([question()])
        self.answers_before = self.submission.answers

    def routes(self):
        return {
            "PATCH submission": (
                "patch",
                reverse("student-submission-detail", args=[self.submission.pk]),
            ),
            "update-async": (
                "post",
                reverse("student-submission-update-async", args=[self.submission.pk]),
            ),
        }

    def test_empty_text_is_submission_empty_on_both_routes(self):
        ledger = CreditLedger.objects.count()
        for text in EMPTY_TEXTS:
            for route, (method, url) in self.routes().items():
                with self.subTest(route=route, text=repr(text)), patch(AI) as ai, patch(
                    DISPATCH
                ) as dispatch:
                    response = getattr(self.client, method)(
                        url, {"raw_input": text}, format="json"
                    )

                    self.assertEqual(response.status_code, 422, response.content)
                    envelope = response.json()["error"]["field_errors"]
                    self.assertEqual(envelope["reason_code"], "SUBMISSION_EMPTY")
                    self.assertEqual(envelope["error_class"], ErrorClass.USER)
                    self.assertEqual(
                        response.json()["message"],
                        "The submitted text has no student answers to grade.",
                    )
                    ai.assert_not_called()
                    dispatch.assert_not_called()
        self.assertEqual(CreditLedger.objects.count(), ledger)
        self.assertFalse(BackgroundProcessingTask.objects.exists(), "queued")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers, self.answers_before)

    def test_a_missing_field_is_still_a_400(self):
        for route, (method, url) in self.routes().items():
            with self.subTest(route=route), patch(AI) as ai:
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                ai.assert_not_called()

    def test_the_service_refuses_before_the_billed_extraction(self):
        """The background task and the sync route share this service."""
        ledger = CreditLedger.objects.count()
        for text in EMPTY_TEXTS:
            with self.subTest(text=repr(text)), patch(AI) as ai:
                with self.assertRaises(SubmissionEmptyError) as caught:
                    update_submission_from_raw_text(self.teacher, self.submission, text)
                self.assertEqual(caught.exception.params, {"file_name": SUBMITTED_TEXT})
                ai.assert_not_called()
        self.assertEqual(CreditLedger.objects.count(), ledger)

    def test_an_all_blank_paper_is_not_refused(self):
        """No blank-answer gate in Epic A (08a §6.1): a paper whose answers
        are all BLANK is accepted for grading, as today."""
        self.submission.answers = [{"question_number": 1, "answer_html": "BLANK"}]
        self.submission.save(update_fields=["answers"])
        with patch(DISPATCH):
            response = self.client.post(
                reverse("student-submission-grade-async", args=[self.submission.pk]),
                {},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
