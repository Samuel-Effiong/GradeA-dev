"""H-154: an AI grading reply that repeats or invents an evaluation.

The reply is a list of evaluations, one per question it was asked. Nothing
made the list hold each question ONCE: `_finalize_grading_result` capped
every evaluation at its own question's points and then added them all up,
while the maximum counted each question once. So a reply holding question
2 twice gave question 2's points twice, the saved score could pass the
maximum, and the saved arithmetic note said "PASS". On the short-paper
path an evaluation for a question that does not exist was added with no
cap at all.

What these tests hold, on every path a reply takes to the sum:

  * one evaluation per question is kept. Of the model's own repeats, the
    one with the LOWEST score (never inflate on a coin-flip);
  * an evaluation the system already holds for that question (graded
    against the answer key, or reused from the saved-answer store) stands
    against anything the model returns for it: the model was never asked;
  * an evaluation that matches no question is dropped;
  * the total is never above the maximum;
  * the saved note does not say PASS when anything was dropped, and says
    what was dropped; a warning is logged with numbers and counts only;
  * the saved-answer store gets each kept evaluation once, and never a
    dropped one.

The provider call is replaced; nothing here reaches a model. Evidence
enforcement is set to "log" so that the reply's shape, not its quotes, is
what each test is about.

Found by the Next-stage Checker (BE-I-04 slice C, finding 5), 2026-10-07.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.test import TestCase, override_settings

from ai_processor import grading_cache, services
from ai_processor.services import AIProcessor

LONG_PAPER = services.GRADING_QUESTIONS_PER_CHUNK + 2
STRAY = 99

SUMMARY_REPLY = {
    "overall_performance_analysis": "Solid work overall.",
    "grader_meta_analysis": "Consistent.",
    "grading_confidence": 90,
    "recommendations": [],
}

ASSIGNMENT = SimpleNamespace(
    id="h154-assignment", title="A paper", instructions="", custom_ai_prompt=""
)


def reply(payload):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = services.MAIN_MODEL
    return response


def essay(number, points=10):
    return {
        "question_number": number,
        "question_text": f"Essay question {number}?",
        "question_type": "ESSAY",
        "points": points,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": points},
            {"level": "good", "description": "Good", "points": 8},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": "A model essay.",
    }


def answer(number):
    return {"question_number": number, "answer_html": f"<p>Essay {number} answer.</p>"}


def evaluation(number, score=8):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "feedback": f"marked {score}",
        "evidence_quotes": [f"Essay {number} answer"],
    }


def is_a_batch_call(kwargs):
    return kwargs.get("response_schema") is services.GRADING_BATCH_RESPONSE_SCHEMA


def numbers_asked(kwargs):
    text = str(kwargs.get("user_prompt"))
    return [n for n in range(1, LONG_PAPER + 1) if f"Essay question {n}?" in text]


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False,
    GRADING_EVIDENCE_ENFORCEMENT=services.MODE_LOG,
)
class ReplyCase(TestCase):
    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def grade(self, numbers, evaluations):
        """One short paper, one provider call that returns `evaluations`."""
        with patch.object(AIProcessor, "execute_graded_task") as execute:
            execute.side_effect = lambda **kwargs: reply(
                {"question_evaluations": evaluations}
            )
            result = self.processor.extract_grade_with_retry(
                MagicMock(),
                [essay(n) for n in numbers],
                [answer(n) for n in numbers],
                assignment_model=ASSIGNMENT,
            )
        self.calls = execute.call_count
        return result

    def scores(self, result):
        return {
            ev["question_number"]: ev["score_awarded"]
            for ev in result["question_evaluations"]
        }

    def assertOneEvaluationPerQuestion(self, result, numbers):
        self.assertEqual(
            sorted(ev["question_number"] for ev in result["question_evaluations"]),
            sorted(numbers),
        )

    def assertNotAboveTheMaximum(self, result):
        summary = result["grading_summary"]
        self.assertLessEqual(summary["total_score"], summary["max_total_points"])
        self.assertLessEqual(summary["percentage"], 100)

    def note(self, result):
        return result["score_calculation_verification"]


class ACleanReplyIsUnchanged(ReplyCase):
    """Controls: green with or without the fix."""

    def test_three_questions_three_evaluations(self):
        result = self.grade([1, 2, 3], [evaluation(1), evaluation(2), evaluation(3)])

        self.assertOneEvaluationPerQuestion(result, [1, 2, 3])
        self.assertEqual(result["grading_summary"]["total_score"], 24)
        self.assertEqual(result["grading_summary"]["max_total_points"], 30)
        self.assertEqual(self.note(result)["verification_status"], "PASS")

    def test_the_note_of_a_clean_reply_names_nothing_dropped(self):
        result = self.grade([1, 2, 3], [evaluation(1), evaluation(2), evaluation(3)])

        self.assertNotIn("repeated_evaluations_dropped", self.note(result))
        self.assertNotIn("unmatched_evaluations_dropped", self.note(result))


class AShortPaperWhoseReplyRepeatsAQuestion(ReplyCase):
    def test_the_repeat_is_counted_once(self):
        result = self.grade(
            [1, 2, 3], [evaluation(1), evaluation(2), evaluation(2), evaluation(3)]
        )

        self.assertOneEvaluationPerQuestion(result, [1, 2, 3])
        self.assertEqual(result["grading_summary"]["total_score"], 24)
        self.assertEqual(result["grading_summary"]["max_total_points"], 30)

    def test_the_checkers_shape_one_answer_three_times(self):
        """Three times the full marks of one question, on a paper of one:
        30 out of 10 before the fix."""
        result = self.grade([1], [evaluation(1, 10)] * 3)

        self.assertOneEvaluationPerQuestion(result, [1])
        self.assertEqual(result["grading_summary"]["total_score"], 10)
        self.assertNotAboveTheMaximum(result)

    def test_of_two_different_scores_the_lowest_is_kept(self):
        for first, second in ((10, 8), (8, 10)):
            with self.subTest(order=(first, second)):
                django_cache.clear()
                result = self.grade(
                    [1, 2, 3],
                    [
                        evaluation(1),
                        evaluation(2, first),
                        evaluation(2, second),
                        evaluation(3),
                    ],
                )

                self.assertEqual(self.scores(result), {1: 8, 2: 8, 3: 8})
                kept = [
                    ev
                    for ev in result["question_evaluations"]
                    if ev["question_number"] == 2
                ]
                self.assertEqual(kept[0]["feedback"], "marked 8")

    def test_the_note_does_not_say_pass_and_names_the_question(self):
        result = self.grade(
            [1, 2, 3], [evaluation(1), evaluation(2), evaluation(2), evaluation(3)]
        )

        note = self.note(result)
        self.assertNotEqual(note["verification_status"], "PASS")
        self.assertEqual(
            note["repeated_evaluations_dropped"],
            [{"question_number": 2, "dropped": 1}],
        )
        self.assertEqual(note["unmatched_evaluations_dropped"], 0)
        self.assertEqual(note["manual_sum"], 24)
        self.assertEqual(note["individual_scores"], [8, 8, 8])

    def test_a_warning_is_logged_with_numbers_and_counts_only(self):
        with self.assertLogs("ai_processor.services", level="WARNING") as logs:
            self.grade(
                [1, 2, 3],
                [evaluation(1), evaluation(2), evaluation(2), evaluation(3)],
            )

        lines = [line for line in logs.output if "reply_corrected" in line]
        self.assertEqual(len(lines), 1, logs.output)
        self.assertIn("repeated_questions=[2]", lines[0])
        self.assertIn("repeated_dropped=1", lines[0])
        self.assertIn("unmatched_dropped=0", lines[0])
        self.assertNotIn("Essay", lines[0])
        self.assertNotIn("marked", lines[0])

    def test_one_call_was_made_no_retry(self):
        self.grade(
            [1, 2, 3], [evaluation(1), evaluation(2), evaluation(2), evaluation(3)]
        )

        self.assertEqual(self.calls, 1)


class AShortPaperWhoseReplyInventsAQuestion(ReplyCase):
    def test_the_stray_is_dropped(self):
        result = self.grade(
            [1, 2, 3],
            [evaluation(1), evaluation(2), evaluation(3), evaluation(STRAY, 50)],
        )

        self.assertOneEvaluationPerQuestion(result, [1, 2, 3])
        self.assertEqual(result["grading_summary"]["total_score"], 24)
        self.assertNotAboveTheMaximum(result)

    def test_an_evaluation_with_no_question_number_is_dropped(self):
        nameless = evaluation(1, 6)
        del nameless["question_number"]

        result = self.grade(
            [1, 2, 3], [evaluation(1), evaluation(2), evaluation(3), nameless]
        )

        self.assertEqual(len(result["question_evaluations"]), 3)
        self.assertEqual(result["grading_summary"]["total_score"], 24)

    def test_the_note_counts_it(self):
        result = self.grade(
            [1, 2, 3],
            [evaluation(1), evaluation(2), evaluation(3), evaluation(STRAY, 50)],
        )

        note = self.note(result)
        self.assertNotEqual(note["verification_status"], "PASS")
        self.assertEqual(note["unmatched_evaluations_dropped"], 1)
        self.assertEqual(note["repeated_evaluations_dropped"], [])


class ALongPaperWhoseBatchRepeatsAQuestion(ReplyCase):
    """The paper is marked in parts. A part's reply may not name a question
    of another part (that was already dropped), but could repeat its own."""

    def grade_long(self, repeated, extra_score):
        numbers = list(range(1, LONG_PAPER + 1))

        def provider(**kwargs):
            if not is_a_batch_call(kwargs):
                return reply(SUMMARY_REPLY)
            asked = numbers_asked(kwargs)
            evaluations = [evaluation(n) for n in asked]
            if repeated in asked:
                evaluations.append(evaluation(repeated, extra_score))
            return reply({"question_evaluations": evaluations})

        with patch.object(AIProcessor, "execute_graded_task") as execute:
            execute.side_effect = provider
            return self.processor.extract_grade_with_retry(
                MagicMock(),
                [essay(n) for n in numbers],
                [answer(n) for n in numbers],
                assignment_model=ASSIGNMENT,
            )

    def test_the_repeat_is_counted_once(self):
        result = self.grade_long(repeated=2, extra_score=10)

        self.assertOneEvaluationPerQuestion(result, range(1, LONG_PAPER + 1))
        self.assertEqual(result["grading_summary"]["total_score"], 8 * LONG_PAPER)
        self.assertEqual(result["grading_summary"]["max_total_points"], 10 * LONG_PAPER)

    def test_the_lowest_is_kept(self):
        result = self.grade_long(repeated=2, extra_score=0)

        self.assertEqual(self.scores(result)[2], 0)
        self.assertEqual(result["grading_summary"]["total_score"], 8 * (LONG_PAPER - 1))

    def test_the_saved_note_still_says_so_after_the_summary_call(self):
        """The long path sums twice: once on the joined parts, once more
        inside the summary step, on a list that is clean by then."""
        result = self.grade_long(repeated=2, extra_score=10)

        note = self.note(result)
        self.assertNotEqual(note["verification_status"], "PASS")
        self.assertEqual(
            note["repeated_evaluations_dropped"],
            [{"question_number": 2, "dropped": 1}],
        )


class AStoredAnswerTheModelReturnsAsWell(ReplyCase):
    """Questions 1 and 2 are answered from the saved-answer store, so only
    question 3 is sent. The reply holds an evaluation of question 1 too."""

    def grade_with_one_and_two_stored(self, returned_for_one):
        self.grade([1, 2], [evaluation(1), evaluation(2)])
        return self.grade([1, 2, 3], [evaluation(1, returned_for_one), evaluation(3)])

    def test_it_is_counted_once(self):
        result = self.grade_with_one_and_two_stored(returned_for_one=10)

        self.assertOneEvaluationPerQuestion(result, [1, 2, 3])
        self.assertEqual(result["grading_summary"]["total_score"], 24)
        self.assertNotAboveTheMaximum(result)

    def test_the_stored_one_stands_whichever_is_lower(self):
        """The model was not asked question 1. What it returns for it is
        not a second opinion on the answer, and must not raise or lower a
        grade every earlier paper with that answer was given."""
        for returned in (10, 0):
            with self.subTest(returned=returned):
                django_cache.clear()
                result = self.grade_with_one_and_two_stored(returned)

                first = [
                    ev
                    for ev in result["question_evaluations"]
                    if ev["question_number"] == 1
                ][0]
                self.assertEqual(first["score_awarded"], 8)
                self.assertTrue(first.get("from_cache"))

    def test_the_note_says_so(self):
        result = self.grade_with_one_and_two_stored(returned_for_one=10)

        note = self.note(result)
        self.assertNotEqual(note["verification_status"], "PASS")
        self.assertEqual(
            note["repeated_evaluations_dropped"],
            [{"question_number": 1, "dropped": 1}],
        )


class TheTotalIsNeverAboveTheMaximum(ReplyCase):
    """The arithmetic authority itself, called as the paths call it."""

    QUESTIONS = [essay(1), essay(2), essay(3)]

    def finalize(self, evaluations):
        return self.processor._finalize_grading_result(evaluations, self.QUESTIONS)

    def test_whatever_the_list_holds(self):
        shapes = {
            "every question twice at full marks": [evaluation(n, 10) for n in (1, 2, 3)]
            * 2,
            "one question five times": [evaluation(1, 10)] * 5
            + [evaluation(2), evaluation(3)],
            "strays only beside the three": [evaluation(n, 10) for n in (1, 2, 3)]
            + [evaluation(STRAY, 1000), evaluation("x", 7)],
            "a quoted number repeats a plain one": [
                evaluation(1, 10),
                evaluation("1", 10),
                evaluation(2, 10),
                evaluation(3, 10),
            ],
        }
        for name, evaluations in shapes.items():
            with self.subTest(shape=name):
                finalized = self.finalize(evaluations)

                self.assertLessEqual(
                    finalized["total_score"], finalized["max_total_points"]
                )
                self.assertEqual(finalized["total_score"], 30)
                self.assertEqual(len(finalized["question_evaluations"]), 3)
                self.assertLessEqual(finalized["percentage"], 100)

    def test_a_system_graded_evaluation_stands_against_a_models(self):
        by_the_key = dict(evaluation(1, 10), graded_by="deterministic")

        for evaluations in (
            [by_the_key, evaluation(1, 0)],
            [evaluation(1, 0), by_the_key],
        ):
            with self.subTest(first=evaluations[0].get("graded_by", "model")):
                finalized = self.finalize(evaluations + [evaluation(2), evaluation(3)])

                self.assertEqual(finalized["total_score"], 26)
                kept = [
                    ev
                    for ev in finalized["question_evaluations"]
                    if ev["question_number"] == 1
                ]
                self.assertEqual(kept[0].get("graded_by"), "deterministic")

    def test_the_order_of_the_questions_is_the_order_first_seen(self):
        finalized = self.finalize(
            [evaluation(3), evaluation(1, 10), evaluation(2), evaluation(1, 8)]
        )

        self.assertEqual(
            [ev["question_number"] for ev in finalized["question_evaluations"]],
            [3, 1, 2],
        )
        self.assertEqual(
            finalized["score_calculation_verification"]["individual_scores"],
            [8, 8, 8],
        )


class TheSavedAnswerStoreGetsEachKeptEvaluationOnce(ReplyCase):
    def stored(self, evaluations, numbers=(1, 2, 3)):
        with patch.object(
            grading_cache, "store_evaluation", wraps=grading_cache.store_evaluation
        ) as store:
            self.grade(list(numbers), evaluations)
        return [
            (call.args[0]["question_number"], call.args[2]["score_awarded"])
            for call in store.call_args_list
        ]

    def test_a_clean_reply_stores_three(self):
        """Control: green with or without the fix."""
        self.assertEqual(
            sorted(self.stored([evaluation(1), evaluation(2), evaluation(3)])),
            [(1, 8), (2, 8), (3, 8)],
        )

    def test_a_repeat_is_stored_once_and_it_is_the_kept_one(self):
        for first, second in ((10, 8), (8, 10)):
            with self.subTest(order=(first, second)):
                django_cache.clear()
                stored = self.stored(
                    [
                        evaluation(1),
                        evaluation(2, first),
                        evaluation(2, second),
                        evaluation(3),
                    ]
                )

                self.assertEqual(sorted(stored), [(1, 8), (2, 8), (3, 8)])

    def test_the_next_paper_with_that_answer_reuses_the_kept_score(self):
        self.grade(
            [1, 2, 3],
            [evaluation(1), evaluation(2, 8), evaluation(2, 10), evaluation(3)],
        )

        again = self.grade([1, 2, 3], [])

        self.assertEqual(self.calls, 0)
        self.assertEqual(self.scores(again), {1: 8, 2: 8, 3: 8})
        self.assertEqual(self.note(again)["verification_status"], "PASS")
