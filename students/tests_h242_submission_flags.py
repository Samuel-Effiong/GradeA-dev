"""
H-242: flag an upload that does not match its assignment, at answer
extraction, and show the flag to the teacher and the student.

A live probe (4 billed extraction runs on a maths assignment) found the
reader already noticed an unrelated essay, English answers on a maths
paper, answers in the wrong form and an off-topic answer, but had no field
to say so, its overall comment was discarded, off-topic work was dropped
as "not found", nothing was flagged at upload, and a not-found objective
answer told the student "No answer was submitted".

Run with:
    python manage.py test students.tests_h242_submission_flags
"""

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor import services as ai_services
from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    OFF_TOPIC,
    READING_PROBLEM,
    SUBMISSION_MISMATCH,
    TYPE_MISMATCH,
    flags_from_extraction,
    merge_answer_flags,
    paper_verdict,
    submission_flags,
)
from ai_processor.extraction_schemas import (
    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
    ANSWERED,
    BLANK,
    EXTRACTED_ANSWER_SCHEMA,
    FLAG_NONE,
    FLAG_OFF_TOPIC,
    FLAG_READING_PROBLEM,
    FLAG_TYPE_MISMATCH,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.objective_grading import NOT_ATTEMPTED, build_objective_evaluation
from ai_processor.services import ANSWERS_EXTRACTION_PROMPT, _merge_chunk_answer
from students.models import StudentSubmission
from students.services import (
    _populate_and_save_grade,
    update_submission_from_raw_text,
    upload_answers_engine,
)
from students.tests_no_grade_tell_before_release import plain
from students.tests_post_grading_submission_lock import VALID_GRADE, _classroom


def answer(number, status=ANSWERED, html="x", flag=FLAG_NONE, detail="", notes=""):
    return {
        "question_number": number,
        "answer_html": html if status in (ANSWERED, ILLEGIBLE) else "",
        "answer_status": status,
        "answer_flag": flag,
        "answer_flag_detail": detail,
        "transcription_notes": notes,
    }


ESSAY = "An English essay on Macbeth; nothing in it answers these maths questions."

#: The reader's reply for an upload that is not for this assignment.
MISMATCHED = {
    "answers": [
        answer(
            1, html="<p>Macbeth...</p>", flag=FLAG_OFF_TOPIC, detail="About Macbeth."
        ),
        answer(2, NOT_FOUND_IN_DOCUMENT, notes="Only the essay is on the page."),
    ],
    "extraction_confidence": 90,
    "submission_matches_assignment": False,
    "mismatch_detail": ESSAY,
}

CLEAN = {
    "answers": [answer(1, html="4"), answer(2, html="20")],
    "extraction_confidence": 95,
    "submission_matches_assignment": True,
    "mismatch_detail": "",
}

TWO_QUESTIONS = [
    {"question_number": 1, "question_text": "Q1?", "points": 5, "model_answer": "4"},
    {"question_number": 2, "question_text": "Q2?", "points": 5, "model_answer": "20"},
]


def kinds(flags):
    return [(flag["kind"], flag["question_number"]) for flag in flags]


class FlagRulesTest(SimpleTestCase):
    def test_a_paper_for_another_assignment_is_flagged_first_with_the_readers_words(
        self,
    ):
        flags = flags_from_extraction(MISMATCHED)
        self.assertEqual(
            kinds(flags),
            [(SUBMISSION_MISMATCH, None), (OFF_TOPIC, 1), (ANSWER_NOT_FOUND, 2)],
        )
        self.assertEqual(
            flags[0]["message"],
            "This upload does not appear to be for this assignment.",
        )
        self.assertEqual(flags[0]["detail"], ESSAY)
        self.assertEqual(flags[1]["detail"], "About Macbeth.")

    def test_each_kind_names_its_question(self):
        flags = submission_flags(
            [
                answer(1, flag=FLAG_OFF_TOPIC),
                answer(2, flag=FLAG_TYPE_MISMATCH, detail="A paragraph, not A-D."),
                answer(3, flag=FLAG_READING_PROBLEM, detail="Two words smudged."),
                answer(4, ILLEGIBLE, notes="Faint pencil."),
                answer(5, NOT_FOUND_IN_DOCUMENT, notes="Page 2 missing."),
            ]
        )
        self.assertEqual(
            kinds(flags),
            [
                (OFF_TOPIC, 1),
                (TYPE_MISMATCH, 2),
                (READING_PROBLEM, 3),
                (READING_PROBLEM, 4),
                (ANSWER_NOT_FOUND, 5),
            ],
        )
        self.assertEqual(
            [flag["message"] for flag in flags],
            [
                "Question 1: the answer does not appear to be about this question.",
                "Question 2: the answer is not in the form this question asks for.",
                "Question 3: part or all of this answer could not be read clearly "
                "from the upload.",
                "Question 4: part or all of this answer could not be read clearly "
                "from the upload.",
                "Question 5: no answer to this question could be found in the upload.",
            ],
        )
        # An unread or lost answer falls back to what the reader saw.
        self.assertEqual(flags[3]["detail"], "Faint pencil.")
        self.assertEqual(flags[4]["detail"], "Page 2 missing.")

    def test_the_readers_note_is_not_borrowed_for_an_answer_that_was_read(self):
        flags = submission_flags([answer(1, flag=FLAG_OFF_TOPIC, notes="Line 1")])
        self.assertEqual(flags[0]["detail"], "")

    def test_a_status_says_more_than_a_flag(self):
        # A lost answer is a lost answer, whatever flag came with it.
        flags = submission_flags(
            [
                answer(1, NOT_FOUND_IN_DOCUMENT, flag=FLAG_OFF_TOPIC),
                answer(2, ILLEGIBLE, flag=FLAG_OFF_TOPIC),
            ]
        )
        self.assertEqual(kinds(flags), [(ANSWER_NOT_FOUND, 1), (READING_PROBLEM, 2)])

    def test_a_blank_or_a_clean_answer_raises_nothing(self):
        self.assertEqual(
            submission_flags([answer(1, BLANK), answer(2), answer(3, flag="BOGUS")]),
            [],
        )
        self.assertEqual(flags_from_extraction(CLEAN), [])

    def test_an_older_reply_with_no_verdict_or_flags_raises_only_its_statuses(self):
        old = {
            "answers": [
                {"question_number": 1, "answer_html": "x", "answer_status": ANSWERED},
                {"question_number": 2, "answer_html": "", "answer_status": BLANK},
            ]
        }
        self.assertEqual(flags_from_extraction(old), [])
        self.assertEqual(flags_from_extraction(None), [])
        self.assertEqual(submission_flags("not a list"), [])

    def test_whitespace_in_a_detail_is_tidied(self):
        flags = submission_flags([answer(1, flag=FLAG_OFF_TOPIC, detail="  a \n b ")])
        self.assertEqual(flags[0]["detail"], "a b")

    # -- one paper read in parts --------------------------------------------

    def test_a_paper_is_a_mismatch_only_when_every_part_says_so(self):
        no = {"submission_matches_assignment": False, "mismatch_detail": "Essay."}
        yes = {"submission_matches_assignment": True, "mismatch_detail": ""}
        self.assertEqual(
            paper_verdict([no, dict(no, mismatch_detail="More.")]),
            (False, "Essay. | More."),
        )
        self.assertEqual(paper_verdict([no, yes]), (True, ""))
        self.assertEqual(paper_verdict([no, {}]), (True, ""))
        self.assertEqual(paper_verdict([]), (True, ""))

    def test_a_flag_on_either_part_of_a_joined_answer_stands(self):
        flagged = answer(1, flag=FLAG_OFF_TOPIC, detail="Second half is a recipe.")
        self.assertEqual(
            merge_answer_flags(answer(1), flagged),
            (FLAG_OFF_TOPIC, "Second half is a recipe."),
        )
        self.assertEqual(
            merge_answer_flags(
                flagged, answer(1, flag=FLAG_READING_PROBLEM, detail="Blur.")
            ),
            (FLAG_OFF_TOPIC, "Second half is a recipe. | Blur."),
        )
        self.assertEqual(merge_answer_flags(answer(1), answer(1)), (FLAG_NONE, ""))

    def test_the_chunk_merge_keeps_a_flag_from_the_later_part(self):
        first = dict(answer(1, html="2x = 8"), source_page=1)
        second = dict(
            answer(1, html="jollof rice", flag=FLAG_OFF_TOPIC, detail="A recipe."),
            source_page=4,
        )
        merged, _ = _merge_chunk_answer(first, (0, 3), second, (1, 3))
        self.assertEqual(merged["answer_html"], "2x = 8\njollof rice")
        self.assertEqual(merged["answer_flag"], FLAG_OFF_TOPIC)
        self.assertEqual(merged["answer_flag_detail"], "A recipe.")


class ReaderContractTest(SimpleTestCase):
    def test_the_reader_must_answer_every_new_field(self):
        props = EXTRACTED_ANSWER_SCHEMA["properties"]
        self.assertEqual(
            props["answer_flag"]["enum"],
            [FLAG_NONE, FLAG_OFF_TOPIC, FLAG_TYPE_MISMATCH, FLAG_READING_PROBLEM],
        )
        for key in ("answer_flag", "answer_flag_detail"):
            self.assertIn(key, EXTRACTED_ANSWER_SCHEMA["required"])
        order = list(props)
        self.assertLess(order.index("answer_status"), order.index("answer_flag"))
        top = ANSWER_EXTRACTION_RESPONSE_SCHEMA["schema"]
        for key in ("submission_matches_assignment", "mismatch_detail"):
            self.assertIn(key, top["required"])
            self.assertIn(key, top["properties"])

    def test_the_prompt_says_to_transcribe_off_topic_work_and_flag_it(self):
        prompt = str(ANSWERS_EXTRACTION_PROMPT)
        for phrase in (
            "Off-topic writing is still the student's work: transcribe it.",
            "`submission_matches_assignment`",
            "`answer_flag_detail`",
            "transcribe the\nwhole piece under the FIRST question",
        ):
            self.assertIn(phrase, prompt)
        # The field the old prompt asked for and the schema refused.
        self.assertNotIn('"notes": "WARNING', prompt)


class ChunkedPaperTest(SimpleTestCase):
    """A long upload read in parts carries the paper's verdict."""

    def _read(self, replies):
        processor = ai_services.AIProcessor.__new__(ai_services.AIProcessor)
        size = ai_services.ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        pages = [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{n}"}}
            for n in range(size * len(replies))
        ]
        queue = [json.dumps(reply) for reply in replies]

        def reply(**kwargs):
            content = queue.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=None,
                model="fake",
            )

        with patch.object(processor, "execute_graded_task", side_effect=reply):
            return processor._extract_answers_chunked(
                user=None,
                image_contents=pages,
                assignment=json.dumps(TWO_QUESTIONS),
            )

    def _part(self, matches, detail, *answers):
        return {
            "student_name": "",
            "student_name_raw": None,
            "student_id": "",
            "answers": list(answers),
            "extraction_confidence": 90,
            "submission_matches_assignment": matches,
            "mismatch_detail": detail,
            "feedback": "",
        }

    def test_every_part_a_mismatch_makes_the_paper_one(self):
        result = self._read(
            [
                self._part(False, "Essay p1.", answer(1, flag=FLAG_OFF_TOPIC)),
                self._part(False, "Essay p2.", answer(2, NOT_FOUND_IN_DOCUMENT)),
            ]
        )
        self.assertIs(result["submission_matches_assignment"], False)
        self.assertEqual(result["mismatch_detail"], "Essay p1. | Essay p2.")
        self.assertEqual(
            kinds(flags_from_extraction(result))[0], (SUBMISSION_MISMATCH, None)
        )

    def test_one_part_matching_keeps_the_paper(self):
        result = self._read(
            [
                self._part(True, "", answer(1)),
                self._part(
                    False, "Blank continuation.", answer(2, NOT_FOUND_IN_DOCUMENT)
                ),
            ]
        )
        self.assertIs(result["submission_matches_assignment"], True)
        self.assertEqual(result["mismatch_detail"], "")


class ObjectiveWordingTest(SimpleTestCase):
    QUESTION = {
        "question_number": 1,
        "question_text": "7 x 8?",
        "points": 2,
        "options": ["A. 54", "B. 56"],
        "model_answer": "B. 56",
    }

    def test_an_answer_not_found_does_not_tell_the_student_they_skipped_it(self):
        for status_, word in ((NOT_FOUND_IN_DOCUMENT, "found"), (ILLEGIBLE, "read")):
            with self.subTest(status=status_):
                evaluation = build_objective_evaluation(
                    self.QUESTION, "", NOT_ATTEMPTED, answer_status=status_
                )
                feedback = evaluation["feedback_for_student"]
                self.assertNotIn("not attempted", feedback)
                self.assertNotIn(
                    "No answer was submitted", evaluation["evaluation_rationale"]
                )
                self.assertIn(f"could not be {word}", feedback)
                self.assertIn("flagged for your teacher", feedback)
                # Nothing is given away while the teacher checks the paper.
                self.assertNotIn("B. 56", feedback)
                self.assertEqual(evaluation["score_awarded"], 0)
                self.assertEqual(evaluation["level_achieved"], "not_attempted")

    def test_a_real_blank_is_still_not_attempted(self):
        for status_ in (BLANK, None):
            evaluation = build_objective_evaluation(
                self.QUESTION, "", NOT_ATTEMPTED, answer_status=status_
            )
            self.assertIn("not attempted", evaluation["feedback_for_student"])

    def _graded(self, answers):
        processor = ai_services.AIProcessor.__new__(ai_services.AIProcessor)
        evaluations, _, _ = processor._partition_deterministic(
            [dict(self.QUESTION, question_type="OBJECTIVE")], answers
        )
        return evaluations[0]["feedback_for_student"]

    def test_grading_hands_the_readers_status_to_the_wording(self):
        lost = self._graded([answer(1, NOT_FOUND_IN_DOCUMENT)])
        self.assertIn("could not be found", lost)
        # No entry at all is an answer not found, not a skipped question.
        self.assertIn("could not be found", self._graded([]))
        self.assertIn("not attempted", self._graded([answer(1, BLANK)]))


class UploadFlagsTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.course, self.assignment = _classroom("h242")
        self.assignment.questions = TWO_QUESTIONS
        self.assignment.save()

    def _upload(self, reply, *, user=None):
        with (
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=copy.deepcopy(reply),
            ),
            patch("students.services.send_email_task.delay"),
        ):
            return upload_answers_engine(
                self.assignment, "content", user or self.student
            )

    def test_a_mismatched_upload_is_flagged_and_queued_at_upload(self):
        submission = self._upload(MISMATCHED)
        submission.refresh_from_db()
        self.assertEqual(
            kinds(submission.extraction_flags),
            [(SUBMISSION_MISMATCH, None), (OFF_TOPIC, 1), (ANSWER_NOT_FOUND, 2)],
        )
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "critical")
        self.assertEqual(
            [reason["kind"] for reason in submission.review_reasons],
            [SUBMISSION_MISMATCH, OFF_TOPIC, ANSWER_NOT_FOUND],
        )
        self.assertTrue(
            all(r["type"] == "extraction_flag" for r in submission.review_reasons)
        )
        # The student's off-topic writing is kept.
        self.assertEqual(submission.answers[0]["answer_html"], "<p>Macbeth...</p>")

    def test_an_off_topic_answer_alone_is_moderate(self):
        reply = dict(
            CLEAN, answers=[answer(1, html="4"), answer(2, flag=FLAG_OFF_TOPIC)]
        )
        submission = self._upload(reply)
        submission.refresh_from_db()
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "moderate")

    def test_a_clean_reupload_takes_the_paper_off_the_queue(self):
        self._upload(MISMATCHED)
        submission = self._upload(CLEAN)
        submission.refresh_from_db()
        self.assertEqual(submission.extraction_flags, [])
        self.assertFalse(submission.needs_review)
        self.assertIsNone(submission.review_reasons)
        self.assertIsNone(submission.review_tier)
        self.assertIsNone(submission.review_severity)

    def test_a_typed_edit_is_read_and_flagged_the_same_way(self):
        submission = self._upload(CLEAN)
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            return_value=copy.deepcopy(MISMATCHED),
        ):
            update_submission_from_raw_text(self.teacher, submission, "edited text")
        submission.refresh_from_db()
        self.assertEqual(
            kinds(submission.extraction_flags)[0], (SUBMISSION_MISMATCH, None)
        )
        self.assertTrue(submission.needs_review)

    def test_grading_keeps_the_readers_flags_and_does_not_count_one_twice(self):
        submission = self._upload(MISMATCHED)
        grading = copy.deepcopy(VALID_GRADE)
        grading["answers_not_found"] = [
            {
                "question_number": 2,
                "answer_status": NOT_FOUND_IN_DOCUMENT,
                "score_awarded": 0,
                "max_points": 5,
            }
        ]
        _populate_and_save_grade(submission, grading, None)
        submission.refresh_from_db()
        reasons = submission.review_reasons
        self.assertEqual(
            [(r["type"], r.get("kind"), r["question_number"]) for r in reasons],
            [
                ("answer_not_found", None, 2),
                ("extraction_flag", SUBMISSION_MISMATCH, None),
                ("extraction_flag", OFF_TOPIC, 1),
            ],
        )
        # The lost answer carries the reader's message in its one entry.
        self.assertEqual(
            reasons[0]["message"],
            "Question 2: no answer to this question could be found in the upload.",
        )
        self.assertEqual(reasons[0]["detail"], "Only the essay is on the page.")
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "critical")

    def test_grading_alone_raises_an_off_topic_flag(self):
        reply = dict(
            CLEAN, answers=[answer(1, html="4"), answer(2, flag=FLAG_OFF_TOPIC)]
        )
        submission = self._upload(reply)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            needs_review=False, review_reasons=None
        )
        submission.refresh_from_db()
        _populate_and_save_grade(submission, copy.deepcopy(VALID_GRADE), None)
        submission.refresh_from_db()
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_reasons[0]["kind"], OFF_TOPIC)
        self.assertEqual(submission.review_tier, "moderate")


class FlagsOnEveryPageTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.course, self.assignment = _classroom("h242p")
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

    def _assignment(self, user):
        return self._get(
            user, reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )

    def _list(self, user):
        rows = self._get(user, reverse("student-submission-list"))
        rows = rows["results"] if isinstance(rows, dict) else rows
        return next(r for r in rows if str(r["id"]) == str(self.submission.pk))

    def assert_flagged(
        self, page, flags_key="submission_flags", flag_key="flagged_for_review"
    ):
        self.assertIs(page[flag_key], True)
        self.assertEqual(
            kinds(page[flags_key]),
            [(SUBMISSION_MISMATCH, None), (OFF_TOPIC, 1), (ANSWER_NOT_FOUND, 2)],
        )
        self.assertEqual(page[flags_key][0]["detail"], ESSAY)

    def test_the_teacher_sees_the_flags_on_the_submission_and_the_list(self):
        self.assert_flagged(self._detail(self.teacher))
        self.assert_flagged(self._list(self.teacher))

    def test_the_teacher_sees_the_flag_on_the_assignments_student_rows(self):
        rows = self._assignment(self.teacher)["student_submissions"]
        row = next(
            r for r in rows if str(r["submission_id"]) == str(self.submission.pk)
        )
        self.assert_flagged(row)

    def test_the_student_sees_the_flags_before_any_grade(self):
        self.assert_flagged(self._detail(self.student))
        self.assert_flagged(self._list(self.student))
        self.assert_flagged(
            self._assignment(self.student),
            flags_key="student_submission_flags",
            flag_key="student_flagged_for_review",
        )

    def test_the_student_still_never_gets_the_review_queue_itself(self):
        # H-127 stands: needs_review and its reasons stay the teacher's.
        page = self._list(self.student)
        self.assertIs(page["needs_review"], False)
        self.assertIsNone(page["review_reasons"])
        self.assertNotIn("review_reasons", self._detail(self.student))

    def test_a_teachers_review_clears_needs_review_for_both(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse(
                "student-submission-mark-reviewed", kwargs={"pk": self.submission.pk}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for user in (self.teacher, self.student):
            page = self._detail(user)
            self.assertIs(page["flagged_for_review"], False)
            # What the reader noticed stays on record.
            self.assertEqual(len(page["submission_flags"]), 3)

    def test_a_paper_with_no_flags_shows_none(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            extraction_flags=None
        )
        page = self._detail(self.student)
        self.assertIs(page["flagged_for_review"], False)
        self.assertEqual(page["submission_flags"], [])
