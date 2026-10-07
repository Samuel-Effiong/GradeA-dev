"""H-165: stored answers the answer document cannot print.

`StudentSubmission.answers` is a JSON column and takes any JSON. The
answer document builder, `student_submission_to_html`, walked it as a
list of objects and raised on anything else that was not empty: an
object, a string, a number, a list holding a string. Beta's two writers
could not store such a value, but only because the builder raised inside
them before they saved; the live service's edit-by-text path saves first.
For such a row a student's read of the unreleased paper answered 500 on
every read (H-130 rebuilds per read), anyone's read answered 500 when the
stored document was empty, and grading failed at its save.

The rule (Senior Manager, 2026-10-07):

- the builder never raises on the shape of `answers`. What cannot be
  printed is left out and ONE fixed line of ours says so, the same on
  every path; it says nothing of grading. An ordinary row's document is
  byte for byte what it was;
- both writers refuse a value that is not a list of objects, themselves,
  now that the builder no longer does it for them;
- a paper with NOTHING printable is refused for grading before any paid
  call and put in the teacher's review queue; the way round is readable
  answers (a new upload, or the edit by text). A paper with SOME left out
  is graded and flagged;
- one log line, ids and the kind of value only, where a document or a
  grade is stored for such a row. Not in the builder: it runs on every
  read of an unreleased paper by its student.
"""

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status

from billing.refunds import record_billing_task_id
from classrooms.tests_final_grade_zero_score import grading_result
from students import exceptions, services
from students.models import StudentSubmission
from students.services import student_submission_to_html, upload_answers_engine
from students.tests_answer_document_before_release import EXTRACTED, AnswerDocumentBase

#: The two fixed lines, written here as the contract. Neither is part of
#: the other (they differ in their first word and its capital).
NOTHING = "This submission's answers could not be displayed."
SOME = "Some of this submission's answers could not be displayed."
#: What the grade route tells the teacher when nothing can be printed.
REFUSAL = (
    "This submission's answers could not be read. Upload the paper again "
    "or re-enter its answers, then grade it."
)
#: A value that would be recognised if any of it were logged or shown.
TELLTALE = "the-answer-text-xyz"
ONE_ANSWER = EXTRACTED["answers"][0]
#: Shapes from which nothing can be printed. Each is true in Python's
#: sense: the builder does not skip it as empty.
NOTHING_PRINTABLE = {
    "an object": {"q1": TELLTALE},
    "a string": TELLTALE,
    "a number": 7,
    "true": True,
    "a list of strings": [TELLTALE, "4"],
    "a list of lists": [[TELLTALE]],
}
#: One entry that can be printed and one that cannot.
MIXED = [ONE_ANSWER, TELLTALE]
EMPTY_VALUES = {"an empty list": [], "an empty object": {}, "an empty string": ""}
GRADER = "students.services.ai_processor"
EXTRACTOR = "students.services.ai_processor.extract_answer_with_retry"
#: Names this row adds, looked up when a test runs: before the change
#: each test that needs one fails by itself, and the module still loads.
DECIDER = "printable_answers"
UNREADABLE_ERROR = "SubmissionAnswersUnreadableError"


def fixed_row(answers):
    """A row the builder can print with nothing from a database or a clock."""
    return SimpleNamespace(
        answers=answers,
        graded_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        score=Decimal("7.00"),
        submission_date=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        student=SimpleNamespace(get_full_name=lambda: "Adaeze Okonkwo"),
        assignment=SimpleNamespace(
            title="<p>Essay</p>",
            due_date=datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc),
        ),
    )


#: Checksums of documents, not secrets: sha256 of what the builder gave
#: for `fixed_row` on 220f9cd6, before this row's change. Written in two
#: halves so that each line can carry the secrets scanner's allowlist mark.
ORDINARY_GRADED_SHA256 = (
    "5777fd71d62792d120af9c3229ac4bbb"  # pragma: allowlist secret
    "16d7efe6c6867afeb1874cd26e32fc46"  # pragma: allowlist secret
)
ORDINARY_UNGRADED_SHA256 = (
    "9cd077e818f1395e0e4524cf5fde9f14"  # pragma: allowlist secret
    "fac465ef3fcc99941b7c89a9fa09d118"  # pragma: allowlist secret
)
EMPTY_GRADED_SHA256 = (
    "c5e144977b33af34e3a3ba807941a63b"  # pragma: allowlist secret
    "8e63a078a0a0588371299af32e54b233"  # pragma: allowlist secret
)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class AnOrdinaryDocumentIsWhatItWas(SimpleTestCase):
    """Controls. The checksums were taken by calling the builder on these
    rows BEFORE this row's change (2026-10-07, on 220f9cd6)."""

    ORDINARY = [
        dict(ONE_ANSWER),
        {
            "question_number": 2,
            "question_text": "<p>Where is it set?</p>",
            "answer_html": "",
        },
    ]

    def test_a_list_of_objects_byte_for_byte(self):
        graded = student_submission_to_html(fixed_row(self.ORDINARY))
        ungraded = student_submission_to_html(
            fixed_row(self.ORDINARY), show_grade=False
        )

        self.assertIn("It is Scout.", graded)
        self.assertEqual(sha256(graded), ORDINARY_GRADED_SHA256)
        self.assertEqual(sha256(ungraded), ORDINARY_UNGRADED_SHA256)

    def test_an_empty_value_byte_for_byte(self):
        for name, value in EMPTY_VALUES.items():
            with self.subTest(answers=name):
                self.assertEqual(
                    sha256(student_submission_to_html(fixed_row(value))),
                    EMPTY_GRADED_SHA256,
                )

    def test_neither_line_is_part_of_the_other(self):
        self.assertNotIn(NOTHING, SOME)
        self.assertNotIn(SOME, NOTHING)


class TheBuilderNeverRaisesOnTheShape(SimpleTestCase):
    def test_nothing_printable_says_so_in_one_fixed_line(self):
        for name, value in NOTHING_PRINTABLE.items():
            with self.subTest(answers=name):
                self.assertTrue(value)

                html = student_submission_to_html(fixed_row(value))

                self.assertIn(NOTHING, html)
                self.assertNotIn(SOME, html)
                self.assertNotIn(TELLTALE, html)
                self.assertIn("Adaeze Okonkwo", html)

    def test_some_left_out_prints_the_rest_and_says_so(self):
        html = student_submission_to_html(fixed_row(MIXED))

        self.assertIn("It is Scout.", html)
        self.assertIn(SOME, html)
        self.assertNotIn(NOTHING, html)
        self.assertNotIn(TELLTALE, html)

    def test_the_form_a_student_reads_before_release_has_the_same_line(self):
        graded = student_submission_to_html(fixed_row({"q1": TELLTALE}))
        ungraded = student_submission_to_html(
            fixed_row({"q1": TELLTALE}), show_grade=False
        )

        self.assertIn(NOTHING, ungraded)
        # The line is the same text in both; the two forms differ only in
        # the header, as they do for an ordinary row.
        self.assertNotEqual(graded, ungraded)
        self.assertEqual(graded.count(NOTHING), 1)
        self.assertEqual(ungraded.count(NOTHING), 1)

    def test_what_can_be_printed_is_decided_in_one_place(self):
        decide = getattr(services, DECIDER)

        self.assertEqual(decide([ONE_ANSWER]), ([ONE_ANSWER], 0))
        self.assertEqual(decide(MIXED), ([ONE_ANSWER], 1))
        for name, value in NOTHING_PRINTABLE.items():
            with self.subTest(answers=name):
                self.assertEqual(decide(value), ([], "all"))
        for name, value in {**EMPTY_VALUES, "zero": 0, "false": False}.items():
            with self.subTest(answers=name):
                self.assertEqual(decide(value), ([], 0))
        self.assertEqual(decide(None), ([], 0))


class AffectedRowCase(AnswerDocumentBase):
    """The base's upload-made row, with its answers then set to a value no
    writer of today's code would store."""

    answers: object = {"q1": TELLTALE}

    def setUp(self):
        super().setUp()
        self.ordinary_document = self.stored()
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            answers=self.answers
        )
        self.submission.refresh_from_db()
        cache.clear()

    def row(self):
        return StudentSubmission.objects.get(pk=self.submission.pk)

    def refused(self):
        """grade_engine on the row, the grader replaced; returns the error
        and the replaced grader."""
        unreadable = getattr(exceptions, UNREADABLE_ERROR)
        with patch(GRADER) as grader:
            grader.extract_grade_with_retry.return_value = grading_result(7, 10)
            with self.assertRaises(unreadable) as raised:
                services.grade_engine(self.teacher, self.row())
        return raised.exception, grader


class AnAffectedRowIsReadNotRefused(AffectedRowCase):
    def test_the_fixture_is_what_it_says(self):
        """Control: the row holds the odd value and a stored document."""
        self.assertEqual(self.row().answers, {"q1": TELLTALE})
        self.assertIn("It is Scout.", str(self.ordinary_document))

    def test_the_student_reads_the_unreleased_paper(self):
        document = str(self.on_the_submission(self.student))

        self.assertIn(NOTHING, document)
        self.assertNotIn(TELLTALE, document)

    def test_the_student_reads_it_on_the_assignment(self):
        document = str(self.on_the_assignment(self.student))

        self.assertIn(NOTHING, document)
        self.assertNotIn(TELLTALE, document)

    def test_a_read_that_rebuilds_an_empty_stored_document(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(raw_input="")

        with self.assertLogs("students.views", level="WARNING") as logs:
            document = str(self.on_the_submission(self.teacher))
        logged = "\n".join(logs.output)

        self.assertIn(NOTHING, document)
        self.assertIn(NOTHING, str(self.stored()))
        self.assertIn(str(self.submission.pk), logged)
        self.assertIn("answers=dict", logged)
        self.assertIn("left_out=all", logged)
        self.assertNotIn(TELLTALE, logged)

    def test_a_student_is_not_told_more_than_the_line(self):
        """Nothing of grading, review or an error in what the student is
        sent for such a paper."""
        self.client.force_authenticate(self.student)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        sent = str(response.data)
        self.assertIn(NOTHING, sent)
        for word in ("answers_unreadable", "review_reasons", "Traceback", TELLTALE):
            self.assertNotIn(word, sent)


class APaperWithNothingPrintableIsNotGraded(AffectedRowCase):
    def test_no_paid_call_is_made(self):
        _, grader = self.refused()

        grader.extract_grade_with_retry.assert_not_called()

    def test_the_teacher_is_told_what_to_do(self):
        error, _ = self.refused()

        self.assertEqual(str(error), REFUSAL)
        from AutoGrader.error_messages import is_user_facing_error

        self.assertTrue(is_user_facing_error(error))

    def test_the_paper_is_put_in_the_review_queue(self):
        before = self.row()
        self.assertFalse(before.needs_review)

        self.refused()

        after = self.row()
        self.assertTrue(after.needs_review)
        self.assertEqual(
            after.review_reasons, [{"type": "answers_unreadable", "left_out": "all"}]
        )
        self.assertEqual(after.review_tier, "critical")
        self.assertIsNotNone(after.review_severity)

    def test_nothing_of_a_grade_is_written_and_no_claim_is_left(self):
        before = self.row()

        self.refused()

        after = self.row()
        self.assertIsNone(after.graded_at)
        self.assertEqual(after.grading_state, before.grading_state)
        self.assertEqual(after.score, before.score)
        self.assertEqual(after.feedback, before.feedback)
        self.assertEqual(after.raw_input, before.raw_input)

    def test_refused_twice_the_reason_stands_once(self):
        self.refused()
        self.refused()

        self.assertEqual(
            self.row().review_reasons,
            [{"type": "answers_unreadable", "left_out": "all"}],
        )

    def test_every_such_shape_is_refused(self):
        for name, value in NOTHING_PRINTABLE.items():
            with self.subTest(answers=name):
                StudentSubmission.objects.filter(pk=self.submission.pk).update(
                    answers=value
                )
                _, grader = self.refused()
                grader.extract_grade_with_retry.assert_not_called()

    def test_the_grade_route_answers_400_with_that_sentence(self):
        self.client.force_authenticate(self.teacher)
        with patch(GRADER) as grader:
            grader.extract_grade_with_retry.return_value = grading_result(7, 10)
            response = self.client.post(
                reverse("student-submission-grade", kwargs={"pk": self.submission.pk})
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": REFUSAL})
        grader.extract_grade_with_retry.assert_not_called()


class TheWayRoundIsReadableAnswers(AffectedRowCase):
    """The refusal's sentence promises two cures. Each replaces the stored
    value with a list of objects, and grading then goes through. The first
    two are controls: both writers replaced the value before this row as
    well. They are here because the sentence now sends a teacher to them."""

    def graded(self):
        with (
            patch(GRADER) as grader,
            patch("students.services._formatted_grade_task"),
            patch("students.services.student_summary_async"),
        ):
            grader.extract_grade_with_retry.return_value = grading_result(7, 10)
            services.grade_engine(self.teacher, self.row())
        grader.extract_grade_with_retry.assert_called_once()
        return self.row()

    def assert_cured(self):
        row = self.row()
        self.assertEqual(row.answers, EXTRACTED["answers"])
        self.assertNotIn(NOTHING, str(row.raw_input))

        row = self.graded()
        self.assertIsNotNone(row.graded_at)
        self.assertEqual(row.score, Decimal("7"))
        self.assertFalse(row.needs_review)
        self.assertIsNone(row.review_reasons)

    def test_a_new_upload(self):
        with (
            patch(EXTRACTOR, return_value=dict(EXTRACTED)),
            patch("students.services.send_email_task.delay"),
        ):
            upload_answers_engine(
                self.assignment, [{"type": "text", "text": "an answer"}], self.student
            )

        self.assert_cured()

    def test_the_edit_by_text(self):
        with patch(EXTRACTOR, return_value=dict(EXTRACTED)):
            services.update_submission_from_raw_text(
                self.student, self.row(), "It is Scout."
            )

        self.assert_cured()

    def test_a_refusal_before_the_cure_does_not_stay_on_the_paper(self):
        self.refused()
        self.assertTrue(self.row().needs_review)
        with patch(EXTRACTOR, return_value=dict(EXTRACTED)):
            services.update_submission_from_raw_text(
                self.student, self.row(), "It is Scout."
            )

        self.assert_cured()


class APaperWithSomeLeftOutIsGradedAndFlagged(AffectedRowCase):
    answers = MIXED

    def test_the_grade_is_saved_and_the_paper_flagged(self):
        with self.assertLogs("students.services", level="WARNING") as logs:
            self.grade_by_ai(self.submission, 7)
        logged = "\n".join(logs.output)

        row = self.row()
        self.assertEqual(row.score, Decimal("7"))
        self.assertIsNotNone(row.graded_at)
        self.assertTrue(row.needs_review)
        self.assertEqual(
            row.review_reasons, [{"type": "answers_unreadable", "left_out": 1}]
        )
        self.assertEqual(row.review_tier, "critical")
        self.assertIn(SOME, str(row.raw_input))
        self.assertIn("It is Scout.", str(row.raw_input))
        self.assertIn(str(self.submission.pk), logged)
        self.assertIn("answers=list", logged)
        self.assertIn("left_out=1", logged)
        self.assertNotIn(TELLTALE, logged)

    def test_it_is_not_refused(self):
        with (
            patch(GRADER) as grader,
            patch("students.services._formatted_grade_task"),
            patch("students.services.student_summary_async"),
        ):
            grader.extract_grade_with_retry.return_value = grading_result(7, 10)
            services.grade_engine(self.teacher, self.row())

        grader.extract_grade_with_retry.assert_called_once()
        self.assertIsNotNone(self.row().graded_at)

    def test_the_reason_stands_beside_the_others(self):
        result = grading_result(7, 10)
        result["answers_not_found"] = [
            {
                "question_number": 2,
                "answer_status": "NOT_FOUND_IN_DOCUMENT",
                "score_awarded": 0,
                "max_points": 5,
            }
        ]
        self.submission.refresh_from_db()

        services._populate_and_save_grade(self.submission, result, None)

        self.assertEqual(
            [reason["type"] for reason in self.row().review_reasons],
            ["answer_not_found", "answers_unreadable"],
        )


class AnOrdinaryPaperIsNotFlagged(AnswerDocumentBase):
    def test_grading_leaves_no_such_reason(self):
        """Control: green with or without the change."""
        self.grade_by_ai(self.submission, 7)

        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertEqual(row.answers, EXTRACTED["answers"])
        self.assertFalse(row.needs_review)
        self.assertIsNone(row.review_reasons)
        self.assertNotIn(NOTHING, str(row.raw_input))
        self.assertNotIn(SOME, str(row.raw_input))


class TheWritersRefuseWhatTheBuilderRefusedForThem(AnswerDocumentBase):
    """Until this row a list holding an entry that is not an object was
    kept out of the database by the builder raising inside each writer.
    Each writer now refuses it itself, with the error it already gives
    for a value that is not a list."""

    NOT_A_LIST_OF_OBJECTS = {
        "a list holding a string": [ONE_ANSWER, TELLTALE],
        "a list of strings": [TELLTALE],
        "a list holding a list": [[ONE_ANSWER]],
    }
    ALREADY_REFUSED = {"an object": {"q1": TELLTALE}, "a string": TELLTALE}

    def upload(self, answers):
        with (
            patch(EXTRACTOR, return_value={"answers": answers}),
            patch("students.services.send_email_task.delay"),
        ):
            upload_answers_engine(
                self.assignment, [{"type": "text", "text": "an answer"}], self.student
            )

    def edit(self, answers):
        with patch(EXTRACTOR, return_value={"answers": answers}):
            services.update_submission_from_raw_text(
                self.student, self.submission, "some text"
            )

    def stored_answers(self):
        return StudentSubmission.objects.get(pk=self.submission.pk).answers

    def assert_refused(self, write, shapes):
        for name, value in shapes.items():
            with self.subTest(answers=name):
                with self.assertRaises(ValueError) as raised:
                    write(value)
                self.assertIn("no usable `answers` list", str(raised.exception))
                self.assertEqual(self.stored_answers(), EXTRACTED["answers"])

    def test_the_upload_refuses_a_list_that_is_not_all_objects(self):
        self.assert_refused(self.upload, self.NOT_A_LIST_OF_OBJECTS)

    def test_the_edit_refuses_a_list_that_is_not_all_objects(self):
        self.assert_refused(self.edit, self.NOT_A_LIST_OF_OBJECTS)

    @patch("billing.services.SubscriptionService.refund_credits")
    def test_the_edits_refusal_refunds_the_charge(self, refund):
        """The refusal is raised inside the edit's refund scope, as the
        one for a value that is not a list already is."""

        def charge_then_return(*args, **kwargs):
            record_billing_task_id("h165-edit-charge")
            return {"answers": [ONE_ANSWER, TELLTALE]}

        with patch(EXTRACTOR, side_effect=charge_then_return):
            with self.assertRaises(ValueError):
                services.update_submission_from_raw_text(
                    self.student, self.submission, "some text"
                )

        refund.assert_called_once()
        self.assertEqual(refund.call_args.args[0], "h165-edit-charge")
        self.assertEqual(self.stored_answers(), EXTRACTED["answers"])

    def test_what_was_refused_is_still_refused(self):
        """Control."""
        self.assert_refused(self.upload, self.ALREADY_REFUSED)
        self.assert_refused(self.edit, self.ALREADY_REFUSED)

    def test_what_was_accepted_is_still_accepted(self):
        """Control: a list of objects, by either writer; and an empty list."""
        other = [dict(ONE_ANSWER, answer_html="<p>It is Jem.</p>")]

        self.upload(other)
        self.assertEqual(self.stored_answers(), other)
        self.edit(EXTRACTED["answers"])
        self.assertEqual(self.stored_answers(), EXTRACTED["answers"])
        self.upload([])
        self.assertEqual(self.stored_answers(), [])
