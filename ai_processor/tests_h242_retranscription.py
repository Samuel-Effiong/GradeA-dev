"""
H-242: the second reading of answers the re-check found writing for
(_retranscribe_found_answers).

Seen live: on a paper of numbered answers for another subject, the first
reading said in its own notes that the student wrote under questions 2-4,
still returned them as not found, and the re-check found the writing. The
re-check's fragment is never the answer, so the teacher saw an empty box.
The second reading transcribes those questions in full.

Its properties, as for the re-check:
  * it costs nothing unless the re-check found writing;
  * it can never make things worse: a failure, or a reply that still finds
    nothing, leaves the re-check's not-found entry as it was, except that
    since H-253 the re-check's quote becomes its text as a last resort
    (ai_processor/tests_h253_answers_never_empty.py);
  * a recovered answer is never silently trusted: it stays flagged.

Run with:
    python manage.py test ai_processor.tests_h242_retranscription
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    READING_PROBLEM,
    TIER_OF,
    submission_flags,
)
from ai_processor.extraction_schemas import (
    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
    ANSWERED,
    BLANK,
    FLAG_NONE,
    FLAG_OFF_TOPIC,
    FLAG_READING_PROBLEM,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import (
    ANSWERS_EXTRACTION_PROMPT,
    PARTIAL_ANSWER_NOTE,
    RECOVERED_ANSWER_NOTE,
    AIFeatureNotAvailableError,
    AIProcessor,
    InsufficientCreditsError,
    _html_text,
)
from students.exceptions import TaskCancelledError

TARGET = "ai_processor.services.AIProcessor.execute_graded_task"
ASSIGNMENT = "<p>Q1. 7 x 8</p><p>Q2. Solve 2x + 3 = 11</p>"


def image(url="data:image/jpeg;base64,PAGE"):
    return {"type": "image_url", "image_url": {"url": url}}


def found(number, fragment="The main theme of Romeo and Juliet"):
    """A not-found entry the re-check found writing for."""
    return {
        "question_number": number,
        "answer_html": "",
        "answer_status": NOT_FOUND_IN_DOCUMENT,
        "answer_flag": FLAG_NONE,
        "answer_flag_detail": "",
        "transcription_notes": "student wrote a paragraph | RE-READ FOUND WRITING",
        "blank_verification": {
            "content_found": True,
            "observed": "a paragraph",
            "verbatim_fragment": fragment,
            "page": 1,
        },
    }


def answered(number, html="<p>56</p>", flag=FLAG_NONE, detail=""):
    return {
        "question_number": number,
        "answer_html": html,
        "answer_status": ANSWERED,
        "answer_flag": flag,
        "answer_flag_detail": detail,
        "transcription_notes": "clear",
    }


def reply(answers):
    fake = MagicMock()
    fake.choices = [MagicMock()]
    fake.choices[0].message.content = json.dumps(
        {
            "answers": answers,
            "submission_matches_assignment": True,
            "mismatch_detail": "",
        }
    )
    fake.usage.total_tokens = 10
    fake.model = "reader-model"
    return fake


class RetranscriptionTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def run_step(self, answers, returned=None, side_effect=None, content=None):
        kwargs = (
            {"side_effect": side_effect}
            if side_effect is not None
            else {"return_value": reply(returned or [])}
        )
        with patch(TARGET, **kwargs) as mocked:
            result = self.processor._retranscribe_found_answers(
                None,
                content if content is not None else [image()],
                ASSIGNMENT,
                answers,
            )
        return result, mocked

    def assert_last_resort(self, result, entry):
        """Nothing recovered: the entry as it was, but with the re-check's
        quote as its text (H-253)."""
        fragment = entry["blank_verification"]["verbatim_fragment"]
        self.assertEqual(
            result,
            [
                dict(
                    entry,
                    answer_html=f"<p>{fragment}</p>",
                    answer_status=ANSWERED,
                    partial_answer=True,
                    answer_flag=FLAG_READING_PROBLEM,
                    answer_flag_detail=PARTIAL_ANSWER_NOTE,
                )
            ],
        )

    # ── cost: only when the re-check found writing ─────────────────────
    def test_no_call_when_nothing_was_found_by_the_re_check(self):
        answers = [answered(1)]
        result, mocked = self.run_step(answers)
        mocked.assert_not_called()
        self.assertEqual(result, answers)

    def test_no_call_for_a_not_found_the_re_check_did_not_find_writing_for(self):
        plain = found(2)
        del plain["blank_verification"]
        confirmed = found(3)
        confirmed["blank_verification"]["content_found"] = False
        result, mocked = self.run_step([plain, confirmed])
        mocked.assert_not_called()
        self.assertEqual(result, [plain, confirmed])

    def test_no_call_for_a_blank_even_with_evidence(self):
        # The re-check only ever leaves NOT_FOUND behind it; a BLANK with
        # evidence is not its output and is not re-read.
        entry = found(2)
        entry["answer_status"] = BLANK
        _, mocked = self.run_step([entry])
        mocked.assert_not_called()

    def test_no_call_without_page_images(self):
        _, mocked = self.run_step([found(2)], content=[{"type": "text", "text": "x"}])
        mocked.assert_not_called()

    def test_exactly_one_call_for_several_questions(self):
        _, mocked = self.run_step([found(2), found(3), found(4)])
        self.assertEqual(mocked.call_count, 1)

    # ── what is sent ───────────────────────────────────────────────────
    def test_the_call_uses_the_reading_prompt_schema_and_every_page(self):
        pages = [image(f"data:image/jpeg;base64,P{n}") for n in range(3)]
        _, mocked = self.run_step([answered(1), found(2), found(4)], content=pages)
        kwargs = mocked.call_args.kwargs
        messages = kwargs["messages"]
        self.assertEqual(
            messages[0], {"role": "system", "content": ANSWERS_EXTRACTION_PROMPT}
        )
        self.assertEqual(messages[1], {"role": "user", "content": ASSIGNMENT})
        sent = messages[2]["content"]
        self.assertEqual(sum(1 for p in sent if p.get("type") == "image_url"), 3)
        note = sent[0]["text"]
        self.assertIn("[2, 4]", note)
        self.assertNotIn("1,", note)
        self.assertEqual(kwargs["response_schema"], ANSWER_EXTRACTION_RESPONSE_SCHEMA)
        self.assertEqual(kwargs["task_type"], "extract_answer")
        self.assertEqual(kwargs["prompt_version"], ANSWERS_EXTRACTION_PROMPT.version)

    # ── a recovered answer ─────────────────────────────────────────────
    def test_a_recovered_answer_replaces_the_empty_one(self):
        text = "<p>The main theme of Romeo and Juliet is love and hate.</p>"
        result, _ = self.run_step([answered(1), found(2)], [answered(2, text)])
        self.assertEqual(result[0], answered(1))
        self.assertEqual(result[1]["answer_html"], text)
        self.assertEqual(result[1]["answer_status"], ANSWERED)
        self.assertIs(result[1]["retranscribed"], True)

    def test_the_re_check_evidence_is_kept(self):
        entry = found(2)
        result, _ = self.run_step([entry], [answered(2, "<p>words</p>")])
        self.assertEqual(result[0]["blank_verification"], entry["blank_verification"])

    def test_the_assignment_label_is_kept_not_the_reply_spelling(self):
        result, _ = self.run_step([found("Q2")], [answered("2", "<p>words</p>")])
        self.assertEqual(result[0]["question_number"], "Q2")
        self.assertEqual(result[0]["answer_html"], "<p>words</p>")

    def test_an_unflagged_recovery_is_flagged_as_a_reading_problem(self):
        result, _ = self.run_step([found(2)], [answered(2, "<p>words</p>")])
        self.assertEqual(result[0]["answer_flag"], FLAG_READING_PROBLEM)
        self.assertEqual(result[0]["answer_flag_detail"], RECOVERED_ANSWER_NOTE)

    def test_a_recovery_keeps_its_own_flag_and_adds_the_note(self):
        returned = answered(
            2, "<p>a poem</p>", FLAG_OFF_TOPIC, "  An English answer,\n not maths. "
        )
        result, _ = self.run_step([found(2)], [returned])
        self.assertEqual(result[0]["answer_flag"], FLAG_OFF_TOPIC)
        self.assertEqual(
            result[0]["answer_flag_detail"],
            "An English answer, not maths. " + RECOVERED_ANSWER_NOTE,
        )

    def test_an_illegible_recovery_counts(self):
        returned = answered(2, "<p>x = [illegible]</p>")
        returned["answer_status"] = ILLEGIBLE
        result, _ = self.run_step([found(2)], [returned])
        self.assertEqual(result[0]["answer_status"], ILLEGIBLE)
        self.assertEqual(result[0]["answer_html"], "<p>x = [illegible]</p>")

    def test_a_recovered_paper_stays_in_review_but_no_longer_says_not_found(self):
        result, _ = self.run_step(
            [answered(1), found(2)], [answered(2, "<p>words</p>")]
        )
        flags = submission_flags(result)
        self.assertEqual([f["kind"] for f in flags], [READING_PROBLEM])
        self.assertEqual(TIER_OF[flags[0]["kind"]], "critical")
        self.assertIn(RECOVERED_ANSWER_NOTE, flags[0]["detail"])
        self.assertNotIn(ANSWER_NOT_FOUND, [f["kind"] for f in flags])

    def test_a_recovered_answer_is_credited_to_the_second_readings_reply(self):
        # The reading label votes over the answers as saved: a recovered
        # answer has the second reading's reply behind it.
        from ai_processor.reading_run import ReadingRun

        run = ReadingRun()
        with patch(TARGET, return_value=reply([answered(2, "<p>words</p>")])):
            result = self.processor._retranscribe_found_answers(
                None, [image()], ASSIGNMENT, [answered(1), found(2)], reading_run=run
            )
        self.assertTrue(run.reading.has_kept_reply())
        self.assertEqual(run.votes([result[1]]), {None: 1})
        self.assertEqual(run.votes([result[0]]), {})

    def test_nothing_recovered_credits_no_reply(self):
        from ai_processor.reading_run import ReadingRun

        run = ReadingRun()
        with patch(TARGET, return_value=reply([])):
            self.processor._retranscribe_found_answers(
                None, [image()], ASSIGNMENT, [found(2)], reading_run=run
            )
        self.assertFalse(run.reading.has_kept_reply())

    # ── a reply that recovers nothing changes nothing but the quote ────
    def test_still_not_found_keeps_the_original_entry(self):
        entry = found(2)
        still = dict(entry, blank_verification=None)
        result, _ = self.run_step([entry], [still])
        self.assert_last_resort(result, entry)

    def test_an_answer_with_only_empty_markup_is_not_a_recovery(self):
        entry = found(2)
        for html in ("", "<p></p>", "<p>&nbsp;</p>", "  <br/> ", None):
            with self.subTest(html=html):
                result, _ = self.run_step([entry], [answered(2, html)])
                self.assert_last_resort(result, entry)

    def test_a_blank_reply_is_not_a_recovery(self):
        entry = found(2)
        returned = answered(2, "<p>words</p>")
        returned["answer_status"] = BLANK
        result, _ = self.run_step([entry], [returned])
        self.assert_last_resort(result, entry)

    def test_an_answer_the_step_did_not_ask_for_is_ignored(self):
        # The reply re-reads question 1 too; the first reading's answer to
        # it stands, untouched.
        first = answered(1, "<p>56</p>")
        result, _ = self.run_step(
            [first, found(2)],
            [answered(1, "<p>54</p>"), answered(2, "<p>words</p>")],
        )
        self.assertEqual(result[0], first)
        self.assertEqual(result[1]["answer_html"], "<p>words</p>")

    def test_an_already_answered_question_is_never_replaced(self):
        # Even when its number is asked about by another target's label.
        first = answered(2, "<p>first</p>")
        result, _ = self.run_step([first, found("2")], [answered(2, "<p>second</p>")])
        self.assertEqual(result[0], first)

    def test_the_first_of_two_replies_for_a_question_wins(self):
        result, _ = self.run_step(
            [found(2)], [answered(2, "<p>one</p>"), answered(2, "<p>two</p>")]
        )
        self.assertEqual(result[0]["answer_html"], "<p>one</p>")

    def test_a_malformed_reply_changes_nothing(self):
        for payload in ({"answers": "nope"}, [], {"answers": [1, None]}, {}):
            with self.subTest(payload=payload):
                fake = reply([])
                fake.choices[0].message.content = json.dumps(payload)
                entry = found(2)
                result, _ = self.run_step([entry], side_effect=[fake])
                self.assert_last_resort(result, entry)

    def test_non_list_answers_are_returned_as_they_are(self):
        result, mocked = self.run_step(None)
        self.assertIsNone(result)
        mocked.assert_not_called()

    # ── failures are never fatal ───────────────────────────────────────
    def test_a_provider_failure_changes_nothing(self):
        entry = found(2)
        result, _ = self.run_step([entry], side_effect=RuntimeError("boom"))
        self.assert_last_resort(result, entry)

    def test_broken_json_changes_nothing(self):
        fake = reply([])
        fake.choices[0].message.content = "{not json"
        entry = found(2)
        result, _ = self.run_step([entry], side_effect=[fake])
        self.assert_last_resort(result, entry)

    def test_no_credits_or_no_feature_changes_nothing(self):
        for error in (
            InsufficientCreditsError("no credits"),
            AIFeatureNotAvailableError("off"),
        ):
            with self.subTest(error=type(error).__name__):
                entry = found(2)
                result, _ = self.run_step([entry], side_effect=error)
                self.assert_last_resort(result, entry)

    def test_a_cancellation_is_not_swallowed(self):
        with self.assertRaises(TaskCancelledError):
            self.run_step([found(2)], side_effect=TaskCancelledError("stop"))


class RecheckWordingTest(SimpleTestCase):
    """Seen live: the first reading put all four numbered English answers
    under question 1 and returned 2-4 as not found, and the re-check, asked
    for writing "responding to" each question, said an English paragraph
    under number 2 was nothing, so the second reading never ran."""

    def test_writing_in_a_questions_place_counts_whatever_it_is_about(self):
        from ai_processor.services import BLANK_VERIFICATION_INSTRUCTION

        text = " ".join(str(BLANK_VERIFICATION_INSTRUCTION).split())
        self.assertNotIn("writing responding to it", text)
        for phrase in (
            "ANY student writing in that question's place",
            "under its number, or where its answer would go",
            "WHATEVER it is about",
            "writing for another subject, another assignment or another "
            "question still counts",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class HtmlTextTest(SimpleTestCase):
    def test_visible_text(self):
        self.assertEqual(_html_text("<p>a</p>\n<p> b&nbsp;c </p>"), "a b c")
        self.assertEqual(_html_text("<p></p>"), "")
        self.assertEqual(_html_text(None), "")
        self.assertEqual(_html_text(5), "")


class RetranscriptionInTheReadingTest(SimpleTestCase):
    """The order inside extract_answer_with_retry: reading, re-check, second
    reading, and the recovered text is what the reading returns."""

    def test_reading_then_re_check_then_second_reading(self):
        processor = AIProcessor()
        first = {
            "answers": [
                answered(1),
                {
                    "question_number": 2,
                    "answer_html": "",
                    "answer_status": NOT_FOUND_IN_DOCUMENT,
                    "answer_flag": FLAG_NONE,
                    "answer_flag_detail": "",
                    "transcription_notes": "student wrote a paragraph under 2",
                },
            ],
            "submission_matches_assignment": True,
            "mismatch_detail": "",
        }
        recheck = MagicMock()
        recheck.choices = [MagicMock()]
        recheck.choices[0].message.content = json.dumps(
            {
                "findings": [
                    {
                        "question_number": 2,
                        "observed": "a paragraph",
                        "page": 1,
                        "verbatim_fragment": "The main theme",
                        "content_found": True,
                    }
                ]
            }
        )
        second = reply([answered(2, "<p>The main theme is love.</p>")])
        with patch.object(
            AIProcessor, "extract_answer_image", return_value=first
        ), patch(TARGET, side_effect=[recheck, second]) as mocked:
            result = processor.extract_answer_with_retry(None, [image()], ASSIGNMENT)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            mocked.call_args_list[1].kwargs["messages"][0]["content"],
            ANSWERS_EXTRACTION_PROMPT,
        )
        q2 = result["answers"][1]
        self.assertEqual(q2["answer_html"], "<p>The main theme is love.</p>")
        self.assertEqual(q2["answer_flag"], FLAG_READING_PROBLEM)
        self.assertEqual(
            q2["blank_verification"]["verbatim_fragment"], "The main theme"
        )

    def test_a_confirmed_blank_costs_no_second_reading(self):
        processor = AIProcessor()
        first = {
            "answers": [
                answered(1),
                {
                    "question_number": 2,
                    "answer_html": "",
                    "answer_status": BLANK,
                    "transcription_notes": "nothing",
                },
            ]
        }
        recheck = MagicMock()
        recheck.choices = [MagicMock()]
        recheck.choices[0].message.content = json.dumps(
            {
                "findings": [
                    {
                        "question_number": 2,
                        "observed": "empty space",
                        "page": None,
                        "verbatim_fragment": None,
                        "content_found": False,
                    }
                ]
            }
        )
        with patch.object(
            AIProcessor, "extract_answer_image", return_value=first
        ), patch(TARGET, side_effect=[recheck]) as mocked:
            result = processor.extract_answer_with_retry(None, [image()], ASSIGNMENT)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["answers"][1]["answer_status"], BLANK)
