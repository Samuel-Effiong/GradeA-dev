"""
H-253: a student's writing never ends up as an empty answer.

Seen on beta (2026-10-16): a Grade 4 biomes lab report uploaded to a
calculus assignment. The first reading flagged every answer OFF_TOPIC but
also marked it NOT_FOUND_IN_DOCUMENT with no text, against its own prompt.
The re-check found writing under all five questions ("A biome is when
animals live somewhere."), the second reading recovered none of it, and
the teacher was told "no answer to this question could be found".

So, in _retranscribe_found_answers:
  * an answer the first reading flagged off topic or in the wrong form is
    writing it saw, so a NOT_FOUND beside that flag is re-read like one the
    re-check found writing for;
  * the second reading is shown the re-check's quotes and told those
    questions are not to come back not found or blank;
  * when it still recovers nothing, by any route (a reply with nothing, a
    failure, no credits), the re-check's quote becomes the answer, as a
    last resort the owner approved, flagged and marked as only part of it.

Builds on H-242's fixtures (ai_processor/tests_h242_retranscription.py).

Run with:
    python manage.py test ai_processor.tests_h253_answers_never_empty
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    OFF_TOPIC,
    READING_PROBLEM,
    submission_flags,
)
from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    FLAG_NONE,
    FLAG_OFF_TOPIC,
    FLAG_READING_PROBLEM,
    FLAG_TYPE_MISMATCH,
)
from ai_processor.services import (
    PARTIAL_ANSWER_NOTE,
    RECOVERED_ANSWER_NOTE,
    AIFeatureNotAvailableError,
    AIProcessor,
    InsufficientCreditsError,
)
from ai_processor.tests_h242_retranscription import (
    ASSIGNMENT,
    TARGET,
    answered,
    found,
    image,
    reply,
)

BIOMES = "The uploaded document is a 4th grade science biomes lab report."


def contradicted(number, flag=FLAG_OFF_TOPIC, content_found=False, fragment=None):
    """The beta paper's shape: flagged as writing, returned as not found."""
    entry = found(number, fragment=fragment)
    entry["answer_flag"] = flag
    entry["answer_flag_detail"] = BIOMES
    entry["blank_verification"]["content_found"] = content_found
    return entry


class StepTest(SimpleTestCase):
    """Runs _retranscribe_found_answers with the AI call faked."""

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


class TheContradictionIsReReadTest(StepTest):
    def test_an_off_topic_or_mismatched_not_found_is_re_read(self):
        for flag in (FLAG_OFF_TOPIC, FLAG_TYPE_MISMATCH):
            with self.subTest(flag=flag):
                _, mocked = self.run_step([contradicted(1, flag)])
                self.assertEqual(mocked.call_count, 1)

    def test_even_with_no_re_check_on_file(self):
        entry = contradicted(1)
        del entry["blank_verification"]
        _, mocked = self.run_step([entry])
        self.assertEqual(mocked.call_count, 1)

    def test_a_not_found_with_no_flag_and_no_writing_is_still_not_re_read(self):
        entry = found(1)
        entry["blank_verification"]["content_found"] = False
        for flag in (FLAG_NONE, FLAG_READING_PROBLEM, "BOGUS"):
            with self.subTest(flag=flag):
                _, mocked = self.run_step([dict(entry, answer_flag=flag)])
                mocked.assert_not_called()

    def test_a_recovery_keeps_the_first_readings_flag_when_the_reply_has_none(self):
        result, _ = self.run_step(
            [contradicted(1)], [answered(1, "<p>A biome is a place.</p>")]
        )
        self.assertEqual(result[0]["answer_flag"], FLAG_OFF_TOPIC)
        self.assertEqual(
            result[0]["answer_flag_detail"], BIOMES + " " + RECOVERED_ANSWER_NOTE
        )


class TheSecondReadingIsToldWhatWasSeenTest(StepTest):
    def _note(self, answers):
        _, mocked = self.run_step(answers)
        return mocked.call_args.kwargs["messages"][2]["content"][0]["text"]

    def test_the_note_quotes_what_the_re_check_saw(self):
        note = self._note(
            [
                found(1, fragment="A biome is when animals live somewhere."),
                found(2, fragment="I studied the desert."),
            ]
        )
        self.assertIn('Question 1: "A biome is when animals live somewhere."', note)
        self.assertIn('Question 2: "I studied the desert."', note)

    def test_writing_seen_with_no_quote_is_re_read_and_named(self):
        # The re-check saw writing but quoted none: still asked about.
        note = self._note([found(4, fragment="")])
        self.assertIn("Question 4: a re-check of the page found writing here.", note)

    def test_the_note_names_a_flagged_answer_with_no_quote(self):
        note = self._note([contradicted(3)])
        self.assertIn(
            "Question 3: the first reading flagged the writing here as off "
            "topic or in the wrong form.",
            note,
        )

    def test_the_note_rules_out_not_found_and_blank(self):
        note = " ".join(self._note([found(1)]).split())
        self.assertIn(
            "Do not return NOT_FOUND_IN_DOCUMENT or BLANK for these questions", note
        )
        self.assertNotIn("If you truly find nothing", note)

    def test_a_quote_with_braces_is_sent_as_written(self):
        note = self._note([found(1, fragment="f(x) = {x^2}")])
        self.assertIn('Question 1: "f(x) = {x^2}"', note)


class TheQuoteIsTheLastResortTest(StepTest):
    FRAGMENT = "A biome is when animals live somewhere."

    def assert_quoted(self, entry, flag):
        self.assertEqual(entry["answer_html"], f"<p>{self.FRAGMENT}</p>")
        self.assertEqual(entry["answer_status"], ANSWERED)
        self.assertIs(entry["partial_answer"], True)
        self.assertNotIn("retranscribed", entry)
        self.assertEqual(entry["answer_flag"], flag)
        self.assertTrue(entry["answer_flag_detail"].endswith(PARTIAL_ANSWER_NOTE))

    def test_a_reply_that_still_finds_nothing(self):
        entry = found(1, fragment=self.FRAGMENT)
        still = dict(entry, blank_verification=None)
        result, _ = self.run_step([entry], [still])
        self.assert_quoted(result[0], FLAG_READING_PROBLEM)
        self.assertEqual(result[0]["answer_flag_detail"], PARTIAL_ANSWER_NOTE)
        # The re-check's evidence stays on file.
        self.assertEqual(result[0]["blank_verification"], entry["blank_verification"])

    def test_every_route_that_recovers_nothing(self):
        for label, kwargs in (
            ("empty reply", {"returned": []}),
            ("blank reply", {"returned": [dict(answered(1), answer_status=BLANK)]}),
            ("provider failure", {"side_effect": RuntimeError("boom")}),
            ("no credits", {"side_effect": InsufficientCreditsError("none")}),
            ("feature off", {"side_effect": AIFeatureNotAvailableError("off")}),
        ):
            with self.subTest(label):
                result, _ = self.run_step([found(1, fragment=self.FRAGMENT)], **kwargs)
                self.assert_quoted(result[0], FLAG_READING_PROBLEM)

    def test_no_page_images_still_keeps_the_quote(self):
        result, mocked = self.run_step(
            [found(1, fragment=self.FRAGMENT)], content=[{"type": "text", "text": "x"}]
        )
        mocked.assert_not_called()
        self.assert_quoted(result[0], FLAG_READING_PROBLEM)

    def test_the_first_readings_flag_and_words_are_kept(self):
        result, _ = self.run_step(
            [contradicted(1, content_found=True, fragment=self.FRAGMENT)], []
        )
        self.assert_quoted(result[0], FLAG_OFF_TOPIC)
        self.assertEqual(
            result[0]["answer_flag_detail"], BIOMES + " " + PARTIAL_ANSWER_NOTE
        )

    def test_the_quote_is_escaped(self):
        result, _ = self.run_step([found(1, fragment="x < 3 & y > <b>2</b>")], [])
        self.assertEqual(
            result[0]["answer_html"],
            "<p>x &lt; 3 &amp; y &gt; &lt;b&gt;2&lt;/b&gt;</p>",
        )

    def test_a_recovered_answer_beats_the_quote(self):
        result, _ = self.run_step(
            [found(1, fragment=self.FRAGMENT)], [answered(1, "<p>in full</p>")]
        )
        self.assertEqual(result[0]["answer_html"], "<p>in full</p>")
        self.assertNotIn("partial_answer", result[0])

    def test_without_a_quote_the_entry_is_left_as_it_was(self):
        for fragment in (None, "", "   "):
            with self.subTest(fragment=fragment):
                entry = found(1, fragment=fragment)
                result, _ = self.run_step([entry], [])
                self.assertEqual(result, [entry])

    def test_only_the_questions_asked_about_are_touched(self):
        first = answered(1, "<p>56</p>")
        blank = {"question_number": 3, "answer_html": "", "answer_status": BLANK}
        result, _ = self.run_step([first, found(2), blank], [])
        self.assertEqual(result[0], first)
        self.assertEqual(result[2], blank)
        self.assertIs(result[1]["partial_answer"], True)


class TheBetaPaperTest(StepTest):
    """The 2026-10-16 upload, end to end through this step and the flags."""

    FRAGMENTS = [
        "A biome is when animals live somewhere.",
        "I studied the desert.",
        "It is hot and there is no rain",
        "Because I like animals.",
        "I choose A)",
    ]

    def _paper(self):
        return [
            contradicted(n, content_found=True, fragment=text)
            for n, text in enumerate(self.FRAGMENTS, start=1)
        ]

    def test_no_answer_is_left_empty_and_none_says_not_found(self):
        still = [dict(entry, blank_verification=None) for entry in self._paper()]
        result, mocked = self.run_step(self._paper(), side_effect=[reply(still)])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(
            [entry["answer_html"] for entry in result],
            [f"<p>{text}</p>" for text in self.FRAGMENTS],
        )
        kinds = [flag["kind"] for flag in submission_flags(result)]
        self.assertEqual(kinds, [OFF_TOPIC] * 5)
        self.assertNotIn(ANSWER_NOT_FOUND, kinds)


class WritingSeenButNotCopiedTest(SimpleTestCase):
    """Whatever is left with no text although the re-check saw writing is
    a reading problem, never "no answer could be found"."""

    def test_the_re_check_saw_writing(self):
        entry = found(1, fragment="")
        self.assertEqual(
            [flag["kind"] for flag in submission_flags([entry])], [READING_PROBLEM]
        )

    def test_the_re_check_saw_nothing(self):
        entry = found(1)
        entry["blank_verification"]["content_found"] = False
        self.assertEqual(
            [flag["kind"] for flag in submission_flags([entry])], [ANSWER_NOT_FOUND]
        )
        del entry["blank_verification"]
        self.assertEqual(
            [flag["kind"] for flag in submission_flags([entry])], [ANSWER_NOT_FOUND]
        )
