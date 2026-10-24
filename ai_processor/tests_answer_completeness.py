"""
Tests for ai_processor/answer_completeness.py.

The module's whole purpose is that a lost student answer can never again
be scored 0 in silence, so these tests are weighted toward the two
properties that actually deliver that:

  1. every question is accounted for exactly once, whatever the model sent;
  2. repair never destroys a real transcription, and never invents one.

Run with:
    python manage.py test ai_processor.tests_answer_completeness
"""

from django.test import SimpleTestCase

from ai_processor.answer_completeness import (
    MODE_LOG,
    MODE_OFF,
    MODE_STRICT,
    AnswerExtractionCompletenessError,
    check_answer_completeness,
    enforce_answer_completeness,
    infer_answer_status,
)
from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)


def question(number, text="Q?"):
    return {"question_number": number, "question_text": text, "points": 10}


def answer(number, html="an answer", status=ANSWERED, **extra):
    entry = {
        "question_number": number,
        "question_text": "Q?",
        "source_page": 1,
        "transcription_notes": "",
        "answer_html": html,
        "answer_status": status,
        "confidence": 90,
    }
    entry.update(extra)
    return entry


QUESTIONS = [question(1), question(2), question(3)]


def statuses(repaired):
    return [e["answer_status"] for e in repaired]


def numbers(repaired):
    return [e["question_number"] for e in repaired]


class HappyPathTest(SimpleTestCase):
    def test_complete_payload_produces_no_violations(self):
        answers = [answer(1), answer(2), answer(3)]
        repaired, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(violations, [])
        self.assertEqual(numbers(repaired), [1, 2, 3])
        self.assertEqual(statuses(repaired), [ANSWERED] * 3)

    def test_genuinely_blank_answers_are_not_violations(self):
        # A student skipping a question is normal and must stay quiet.
        answers = [answer(1), answer(2, html="", status=BLANK), answer(3)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(violations, [])

    def test_answers_are_returned_in_question_order(self):
        answers = [answer(3), answer(1), answer(2)]
        repaired, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(violations, [])
        self.assertEqual(numbers(repaired), [1, 2, 3])

    def test_string_question_numbers_match_integer_questions(self):
        # THE REGRESSION GUARD. "3" vs 3 must not read as a missing answer:
        # that would flag a correctly extracted submission for review and
        # bury the real misses in noise.
        answers = [answer("1"), answer("2"), answer("3")]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(violations, [])

    def test_non_numeric_question_numbers_are_supported(self):
        qs = [question("2a"), question("2b")]
        _, violations = check_answer_completeness([answer("2a"), answer("2b")], qs)
        self.assertEqual(violations, [])


class MissingAnswerTest(SimpleTestCase):
    """The silent-zero case this module exists for."""

    def test_missing_answer_is_a_violation(self):
        repaired, violations = check_answer_completeness(
            [answer(1), answer(3)], QUESTIONS
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("2", violations[0])

    def test_missing_answer_is_backfilled_as_not_found_not_blank(self):
        # The distinction is the entire point: BLANK would say "the student
        # skipped it", which we have no evidence for.
        repaired, _ = check_answer_completeness([answer(1), answer(3)], QUESTIONS)
        self.assertEqual(
            statuses(repaired), [ANSWERED, NOT_FOUND_IN_DOCUMENT, ANSWERED]
        )

    def test_backfilled_entry_never_invents_an_answer(self):
        repaired, _ = check_answer_completeness([answer(1), answer(3)], QUESTIONS)
        self.assertEqual(repaired[1]["answer_html"], "")

    def test_backfilled_entry_carries_zero_confidence(self):
        repaired, _ = check_answer_completeness([answer(1), answer(3)], QUESTIONS)
        self.assertEqual(repaired[1]["confidence"], 0.0)

    def test_backfilled_entry_explains_itself(self):
        # A teacher opening the review has to understand why it is there.
        repaired, _ = check_answer_completeness([answer(1), answer(3)], QUESTIONS)
        self.assertIn("no entry", repaired[1]["transcription_notes"].lower())

    def test_entirely_empty_answers_list_flags_every_question(self):
        repaired, violations = check_answer_completeness([], QUESTIONS)
        self.assertEqual(len(violations), 3)
        self.assertEqual(statuses(repaired), [NOT_FOUND_IN_DOCUMENT] * 3)

    def test_every_question_is_present_exactly_once_after_repair(self):
        # The invariant grading depends on.
        for payload in ([], [answer(2)], [answer(1), answer(1)], [answer(9)]):
            with self.subTest(payload=payload):
                repaired, _ = check_answer_completeness(payload, QUESTIONS)
                self.assertEqual(numbers(repaired), [1, 2, 3])


class DuplicateAnswerTest(SimpleTestCase):
    def test_duplicate_question_numbers_are_a_violation(self):
        _, violations = check_answer_completeness(
            [answer(1), answer(1), answer(2), answer(3)], QUESTIONS
        )
        self.assertTrue(any("2 answer entries" in v for v in violations))

    def test_duplicate_repair_keeps_the_real_transcription(self):
        # Never let an empty duplicate shadow a real answer.
        answers = [
            answer(1, html="", status=BLANK),
            answer(1, html="the real work"),
            answer(2),
            answer(3),
        ]
        repaired, _ = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(repaired[0]["answer_html"], "the real work")

    def test_duplicate_repair_is_order_independent(self):
        for order in (
            [("", BLANK), ("real", ANSWERED)],
            [("real", ANSWERED), ("", BLANK)],
        ):
            with self.subTest(order=order):
                answers = [answer(1, html=h, status=s) for h, s in order]
                answers += [answer(2), answer(3)]
                repaired, _ = check_answer_completeness(answers, QUESTIONS)
                self.assertEqual(repaired[0]["answer_html"], "real")

    def test_two_distinct_transcriptions_are_reported_as_distinct(self):
        answers = [
            answer(1, html="alpha"),
            answer(1, html="beta"),
            answer(2),
            answer(3),
        ]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertTrue(any("2 distinct" in v for v in violations))


class NumberingDriftTest(SimpleTestCase):
    def test_answer_outside_the_assignment_is_a_violation(self):
        answers = [answer(1), answer(2), answer(3), answer(99)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertTrue(any("99" in v and "drift" in v for v in violations))

    def test_extra_answer_is_dropped_from_the_repaired_payload(self):
        answers = [answer(1), answer(2), answer(3), answer(99)]
        repaired, _ = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(numbers(repaired), [1, 2, 3])

    def test_duplicate_question_in_the_assignment_is_reported(self):
        malformed = [question(1), question(1), question(2)]
        _, violations = check_answer_completeness([answer(1), answer(2)], malformed)
        self.assertTrue(any("more than one question" in v for v in violations))


class SelfContradictionTest(SimpleTestCase):
    """Cross-field rules a JSON schema cannot express."""

    def test_answered_but_empty_is_a_violation(self):
        answers = [answer(1, html="", status=ANSWERED), answer(2), answer(3)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertTrue(any("marked ANSWERED" in v for v in violations))

    def test_answered_but_empty_repairs_toward_not_found(self):
        # Trusting the empty transcription over the label, and flagging it.
        answers = [answer(1, html="", status=ANSWERED), answer(2), answer(3)]
        repaired, _ = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(repaired[0]["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_blank_but_populated_is_a_violation(self):
        answers = [answer(1, html="real work", status=BLANK), answer(2), answer(3)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertTrue(any("marked BLANK" in v for v in violations))

    def test_blank_but_populated_repairs_toward_answered(self):
        # THE MOST IMPORTANT REPAIR: never discard work the model actually
        # transcribed just because it mislabelled it.
        answers = [answer(1, html="real work", status=BLANK), answer(2), answer(3)]
        repaired, _ = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(repaired[0]["answer_status"], ANSWERED)
        self.assertEqual(repaired[0]["answer_html"], "real work")

    def test_whitespace_only_answer_counts_as_empty(self):
        answers = [answer(1, html="   \n  ", status=ANSWERED), answer(2), answer(3)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertTrue(any("marked ANSWERED" in v for v in violations))

    def test_illegible_may_carry_a_partial_transcription(self):
        # ILLEGIBLE is deliberately NOT in EMPTY_ANSWER_STATUSES: a
        # best-guess transcription of bad handwriting is exactly what the
        # prompt asks for, and must not be treated as a contradiction.
        answers = [answer(1, html="best guess", status=ILLEGIBLE), answer(2), answer(3)]
        _, violations = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(violations, [])


class MalformedInputTest(SimpleTestCase):
    """Adversarial shapes. Nothing here may raise."""

    def test_answers_not_a_list_is_a_violation_not_a_crash(self):
        repaired, violations = check_answer_completeness({"a": 1}, QUESTIONS)
        self.assertTrue(any("must be a list" in v for v in violations))
        self.assertEqual(numbers(repaired), [1, 2, 3])

    def test_none_answers_is_handled(self):
        repaired, violations = check_answer_completeness(None, QUESTIONS)
        self.assertTrue(violations)
        self.assertEqual(statuses(repaired), [NOT_FOUND_IN_DOCUMENT] * 3)

    def test_non_dict_answer_entries_are_reported_and_skipped(self):
        repaired, violations = check_answer_completeness(
            ["garbage", None, 42, answer(1), answer(2), answer(3)], QUESTIONS
        )
        self.assertEqual(sum("not an object" in v for v in violations), 3)
        self.assertEqual(numbers(repaired), [1, 2, 3])

    def test_non_dict_questions_are_skipped(self):
        repaired, _ = check_answer_completeness(
            [answer(1)], [question(1), "junk", None]
        )
        self.assertEqual(numbers(repaired), [1])

    def test_empty_questions_list_produces_empty_repair(self):
        repaired, violations = check_answer_completeness([], [])
        self.assertEqual(repaired, [])
        self.assertEqual(violations, [])

    def test_unrecognised_status_is_re_derived_from_the_transcription(self):
        answers = [
            answer(1, html="work", status="WEIRD"),
            answer(2, html="", status="WEIRD"),
            answer(3),
        ]
        repaired, _ = check_answer_completeness(answers, QUESTIONS)
        self.assertEqual(repaired[0]["answer_status"], ANSWERED)
        self.assertEqual(repaired[1]["answer_status"], BLANK)

    def test_missing_status_key_is_inferred(self):
        # The schema-disabled path.
        bare = {"question_number": 1, "answer_html": "work"}
        repaired, violations = check_answer_completeness([bare], [question(1)])
        self.assertEqual(repaired[0]["answer_status"], ANSWERED)
        self.assertEqual(violations, [])

    def test_missing_answer_html_key_is_treated_as_empty(self):
        bare = {"question_number": 1, "answer_status": ANSWERED}
        _, violations = check_answer_completeness([bare], [question(1)])
        self.assertTrue(any("marked ANSWERED" in v for v in violations))

    def test_input_payload_is_not_mutated(self):
        original = answer(1, html="", status=ANSWERED)
        snapshot = dict(original)
        check_answer_completeness([original], [question(1)])
        self.assertEqual(original, snapshot)


class InferStatusTest(SimpleTestCase):
    def test_populated_answer_infers_answered(self):
        self.assertEqual(infer_answer_status({"answer_html": "x"}), ANSWERED)

    def test_empty_answer_infers_blank_never_not_found(self):
        # Lossy on purpose — see the docstring. Guessing NOT_FOUND here
        # would flag every genuinely skipped question in the class.
        self.assertEqual(infer_answer_status({"answer_html": ""}), BLANK)
        self.assertEqual(infer_answer_status({}), BLANK)

    def test_whitespace_only_infers_blank(self):
        self.assertEqual(infer_answer_status({"answer_html": "  \t "}), BLANK)


class EnforceModeTest(SimpleTestCase):
    def test_strict_raises_on_violation(self):
        with self.assertRaises(AnswerExtractionCompletenessError) as ctx:
            enforce_answer_completeness([answer(1)], QUESTIONS, mode=MODE_STRICT)
        self.assertIn("2", str(ctx.exception))

    def test_strict_error_is_a_value_error_so_retry_handlers_catch_it(self):
        # extract_answer_with_retry catches broad Exception; grading's
        # completeness error is likewise a ValueError subclass.
        self.assertTrue(issubclass(AnswerExtractionCompletenessError, ValueError))

    def test_strict_returns_repaired_payload_when_clean(self):
        result = enforce_answer_completeness(
            [answer(1), answer(2), answer(3)], QUESTIONS, mode=MODE_STRICT
        )
        self.assertEqual(numbers(result), [1, 2, 3])

    def test_log_mode_repairs_instead_of_raising(self):
        result = enforce_answer_completeness([answer(1)], QUESTIONS, mode=MODE_LOG)
        self.assertEqual(numbers(result), [1, 2, 3])
        self.assertEqual(
            statuses(result), [ANSWERED, NOT_FOUND_IN_DOCUMENT, NOT_FOUND_IN_DOCUMENT]
        )

    def test_log_mode_logs_the_violations(self):
        with self.assertLogs("ai_processor.answer_completeness", "WARNING") as logs:
            enforce_answer_completeness([answer(1)], QUESTIONS, mode=MODE_LOG)
        self.assertIn("Repaired", logs.output[0])

    def test_off_mode_returns_the_payload_untouched(self):
        payload = [answer(1)]
        self.assertIs(
            enforce_answer_completeness(payload, QUESTIONS, mode=MODE_OFF), payload
        )

    def test_custom_key_fn_is_honoured(self):
        # The pipeline passes AIProcessor._question_number_key.
        result = enforce_answer_completeness(
            [answer(" 1 "), answer(2), answer(3)],
            QUESTIONS,
            mode=MODE_STRICT,
            key_fn=lambda v: int(str(v).strip()),
        )
        self.assertEqual(len(result), 3)


class RepairInvariantTest(SimpleTestCase):
    """
    Properties that must hold for EVERY input, because they are what make
    the final-attempt degrade safe to ship.
    """

    PAYLOADS = [
        [],
        None,
        "not a list",
        [answer(1), answer(2), answer(3)],
        [answer(1)],
        [answer(1), answer(1)],
        [answer(99)],
        [answer(1, html="", status=ANSWERED)],
        [answer(1, html="x", status=BLANK)],
        ["junk", answer(2)],
        [{"question_number": 1}],
    ]

    def test_repair_always_accounts_for_every_question_exactly_once(self):
        for payload in self.PAYLOADS:
            with self.subTest(payload=payload):
                repaired, _ = check_answer_completeness(payload, QUESTIONS)
                self.assertEqual(numbers(repaired), [1, 2, 3])

    def test_repair_never_invents_answer_text(self):
        # Every non-empty transcription in the output must have been
        # present in the input. Repair may relabel; it may never author.
        for payload in self.PAYLOADS:
            with self.subTest(payload=payload):
                repaired, _ = check_answer_completeness(payload, QUESTIONS)
                source = {
                    str(e.get("answer_html") or "").strip()
                    for e in (payload if isinstance(payload, list) else [])
                    if isinstance(e, dict)
                }
                for entry in repaired:
                    html = (entry.get("answer_html") or "").strip()
                    if html:
                        self.assertIn(html, source)

    def test_repair_never_loses_a_real_transcription(self):
        for payload in self.PAYLOADS:
            with self.subTest(payload=payload):
                if not isinstance(payload, list):
                    continue
                expected = {
                    str(e.get("answer_html") or "").strip()
                    for e in payload
                    if isinstance(e, dict) and str(e.get("answer_html") or "").strip()
                    # Entries numbered outside the assignment are dropped
                    # by design (numbering drift is already a violation).
                    and str(e.get("question_number")) in {"1", "2", "3"}
                }
                repaired, _ = check_answer_completeness(payload, QUESTIONS)
                survived = {
                    (e.get("answer_html") or "").strip()
                    for e in repaired
                    if (e.get("answer_html") or "").strip()
                }
                self.assertTrue(expected <= survived)

    def test_repair_output_always_has_a_valid_status(self):
        from ai_processor.extraction_schemas import ANSWER_STATUSES

        for payload in self.PAYLOADS:
            with self.subTest(payload=payload):
                repaired, _ = check_answer_completeness(payload, QUESTIONS)
                for entry in repaired:
                    self.assertIn(entry["answer_status"], ANSWER_STATUSES)

    def test_repair_is_idempotent(self):
        # Running the checker on its own output must be a no-op, or the
        # final-attempt path could oscillate.
        for payload in self.PAYLOADS:
            with self.subTest(payload=payload):
                once, _ = check_answer_completeness(payload, QUESTIONS)
                twice, violations = check_answer_completeness(once, QUESTIONS)
                self.assertEqual(once, twice)
                self.assertEqual(violations, [])
