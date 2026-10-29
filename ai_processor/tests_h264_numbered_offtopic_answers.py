"""
H-264: a numbered paper for another assignment is not piled under question 1.

Seen on beta (2026-10-27, submission 452f823f): a 4th grade biomes lab
report with six numbered answers ("Question 1:" to "Question 6:") uploaded
to a 75-question true-or-false quiz on Dr. Martin Luther King Jr. The first
reading put all six answers in ONE entry under question 1 (ANSWERED,
OFF_TOPIC) and returned questions 2-75 NOT_FOUND_IN_DOCUMENT with no flag.
Nothing recovered them:
  * the re-check skipped the paper: 74 blanks is above its cap of 12;
  * the second reading had no targets: questions 2-75 had no re-check
    writing and no flag, and question 1 was ANSWERED.

The owner's ruling of 2026-10-22 (H-253): the student's writing always goes
under its question. So:
  * A. after the first reading, an ANSWERED entry flagged OFF_TOPIC or
    TYPE_MISMATCH that holds the student's own numbered sections (two or
    more, in order, numbers in the assignment) sends the questions those
    numbers name, when they are NOT_FOUND with no flag, to the second
    reading with that entry, and the reply puts each under its number;
    the pile is replaced only when every section came back;
  * B. above the re-check's cap, the lowest-numbered `cap` blanks are
    re-checked instead of none;
  * C. the reading prompt has an example of this paper.

Builds on H-242's and H-253's fixtures.

Run with:
    python manage.py test ai_processor.tests_h264_numbered_offtopic_answers
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ai_processor.extraction_flags import (
    ANSWER_NOT_FOUND,
    OFF_TOPIC,
    SUBMISSION_MISMATCH,
    flags_from_extraction,
    submission_flags,
    upload_mismatch_flag,
)
from ai_processor.extraction_schemas import (
    ANSWERED,
    FLAG_NONE,
    FLAG_OFF_TOPIC,
    FLAG_TYPE_MISMATCH,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import (
    ANSWERS_EXTRACTION_PROMPT,
    PROMPT_DIR,
    RECOVERED_ANSWER_NOTE,
    AIProcessor,
)
from ai_processor.tests_h242_retranscription import TARGET, image, reply

QUIZ = "<p>The Legacy of Dr. Martin Luther King Jr. (True or False), 75 questions</p>"
QUESTIONS = 75

#: The six answers on the uploaded page, as the student numbered them.
SECTIONS = [
    "A biome is when animals live somewhere.",
    "I studied the desert. There are plants and animals.",
    "It is hot and there is no rain so animals are hot.",
    "Because I like animals.",
    "I choose A)",
    "I made a box with sand. It was fun. I learned nothing.",
]
PILE_HTML = "".join(
    f"<p>Question {n}: {text}</p>" for n, text in enumerate(SECTIONS, start=1)
)
REPORT = "A biomes lab report; nothing in it is about Dr. Martin Luther King Jr."


def pile(html=PILE_HTML, flag=FLAG_OFF_TOPIC, number=1):
    """Question 1 as the first reading returned it: every answer in one."""
    return {
        "question_number": number,
        "answer_html": html,
        "answer_status": ANSWERED,
        "answer_flag": flag,
        "answer_flag_detail": REPORT if flag != FLAG_NONE else "",
        "transcription_notes": (
            "Page contains a complete biomes lab report with 6 questions and "
            "student answers; no MLK content present."
        ),
        "confidence": 90,
    }


def not_found(number):
    """Questions 2-75 as the first reading returned them."""
    return {
        "question_number": number,
        "answer_html": "",
        "answer_status": NOT_FOUND_IN_DOCUMENT,
        "answer_flag": FLAG_NONE,
        "answer_flag_detail": "",
        "transcription_notes": "No content for this question found in submission.",
        "confidence": 100,
    }


def first_reading(first=None, questions=QUESTIONS):
    """The first reading's answers: the pile, then a NOT_FOUND for the rest."""
    return [first if first is not None else pile()] + [
        not_found(n) for n in range(2, questions + 1)
    ]


def section(number, text=None):
    """The second reading's answer for one of the student's sections."""
    return {
        "question_number": number,
        "answer_html": f"<p>{text if text is not None else SECTIONS[number - 1]}</p>",
        "answer_status": ANSWERED,
        "answer_flag": FLAG_OFF_TOPIC,
        "answer_flag_detail": "About biomes, not this question.",
        "transcription_notes": f"the student's answer numbered {number}",
        "confidence": 90,
    }


#: Added by H-264 to the detail of the entry the pile was split out of.
SPLIT_NOTE_START = "The first reading put several of the student's numbered answers"


class StepTest(SimpleTestCase):
    """Runs _retranscribe_found_answers with the AI call faked."""

    def setUp(self):
        self.processor = AIProcessor()

    def run_step(self, answers, returned=None, side_effect=None):
        kwargs = (
            {"side_effect": side_effect}
            if side_effect is not None
            else {"return_value": reply(returned or [])}
        )
        with patch(TARGET, **kwargs) as mocked:
            result = self.processor._retranscribe_found_answers(
                None, [image()], QUIZ, answers
            )
        return result, mocked

    def note(self, mocked):
        return mocked.call_args.kwargs["messages"][2]["content"][0]["text"]


class TheBetaPaperTest(StepTest):
    """The 2026-10-27 upload, through this step and the flags."""

    def test_each_answer_goes_under_its_own_number(self):
        answers = first_reading()
        result, mocked = self.run_step(answers, [section(n) for n in range(1, 7)])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(result), QUESTIONS)
        for n in range(1, 7):
            with self.subTest(question=n):
                entry = result[n - 1]
                self.assertEqual(entry["question_number"], n)
                self.assertEqual(entry["answer_status"], ANSWERED)
                self.assertEqual(entry["answer_flag"], FLAG_OFF_TOPIC)
                self.assertEqual(entry["answer_html"], f"<p>{SECTIONS[n - 1]}</p>")
                self.assertIs(entry["retranscribed"], True)
        for n in range(2, 7):
            self.assertTrue(
                result[n - 1]["answer_flag_detail"].endswith(RECOVERED_ANSWER_NOTE)
            )
        self.assertIn(SPLIT_NOTE_START, result[0]["answer_flag_detail"])
        # 7-75 exactly as the first reading left them.
        self.assertEqual(result[6:], answers[6:])

    def test_the_flags_are_six_off_topic_and_the_upload_mismatch(self):
        result, _ = self.run_step(first_reading(), [section(n) for n in range(1, 7)])
        kinds = [
            flag["kind"]
            for flag in submission_flags(
                result, matches=False, mismatch_detail="A biomes lab report."
            )
        ]
        self.assertEqual(
            kinds, [SUBMISSION_MISMATCH] + [OFF_TOPIC] * 6 + [ANSWER_NOT_FOUND] * 69
        )
        self.assertIsNotNone(
            upload_mismatch_flag(result, matches=False, detail="A biomes lab report.")
        )

    def test_only_the_pile_and_the_numbers_it_names_are_asked_about(self):
        _, mocked = self.run_step(first_reading(), [])
        note = self.note(mocked)
        self.assertIn("question(s) [1, 2, 3, 4, 5, 6]", note)
        self.assertNotIn("Question 7:", note)

    def test_the_note_says_where_each_answer_was_put(self):
        _, mocked = self.run_step(first_reading(), [])
        note = self.note(mocked)
        self.assertIn(
            "- Question 1: the first reading put the student's answers numbered "
            "1, 2, 3, 4, 5, 6 all under this question. Transcribe here only the "
            "answer to question 1; the others go under their own numbers.",
            note,
        )
        self.assertIn(
            "- Question 2: the first reading put this answer under question 1. "
            "The student's answer numbered 2 begins: \"Question 2: I studied the "
            'desert. There are plants and animals."',
            note,
        )

    def test_a_reply_question_not_asked_about_is_ignored(self):
        answers = first_reading()
        result, _ = self.run_step(
            answers,
            [section(n) for n in range(1, 7)]
            + [dict(section(1, "x"), question_number=7)],
        )
        self.assertEqual(result[6], answers[6])


class ThePileIsKeptUnlessEverySectionCameBackTest(StepTest):
    """Never drop text: the pile is replaced only when the reply returned
    question 1 AND every other number the pile names."""

    def test_question_1_missing_from_the_reply(self):
        result, _ = self.run_step(first_reading(), [section(n) for n in range(2, 7)])
        self.assertEqual(result[0], pile())
        for n in range(2, 7):
            self.assertEqual(result[n - 1]["answer_html"], f"<p>{SECTIONS[n - 1]}</p>")

    def test_some_sections_missing_from_the_reply(self):
        answers = first_reading()
        result, _ = self.run_step(answers, [section(n) for n in (1, 2, 3)])
        self.assertEqual(result[0], pile())
        self.assertEqual(result[1]["answer_html"], f"<p>{SECTIONS[1]}</p>")
        self.assertEqual(result[2]["answer_html"], f"<p>{SECTIONS[2]}</p>")
        self.assertEqual(result[3:], answers[3:])

    def test_a_failed_or_empty_second_reading_changes_nothing(self):
        for label, kwargs in (
            ("empty reply", {"returned": []}),
            ("provider failure", {"side_effect": RuntimeError("boom")}),
        ):
            with self.subTest(label):
                answers = first_reading()
                result, mocked = self.run_step(answers, **kwargs)
                self.assertEqual(mocked.call_count, 1)
                self.assertEqual(result, answers)


class WhatCountsAsAPileTest(StepTest):
    def assert_split(self, html, numbers=(1, 2)):
        answers = first_reading(pile(html))
        result, mocked = self.run_step(answers, [section(n) for n in numbers])
        self.assertEqual(mocked.call_count, 1, html)
        self.assertEqual(result[1]["answer_status"], ANSWERED, html)

    def assert_untouched(self, answers):
        result, mocked = self.run_step(answers, [section(1), section(2)])
        mocked.assert_not_called()
        self.assertEqual(result, answers)

    def test_each_way_of_numbering_at_the_start_of_a_paragraph_or_line(self):
        for html in (
            "<p>Question 1: a</p><p>Question 2: b</p>",
            "<p>Question 1 (5 marks) a</p><p>Question 2 (5 marks) b</p>",
            "<p>Q1. a</p><p>Q2. b</p>",
            "<p>Q 1 a</p><p>Q 2 b</p>",
            "<p>1. a</p><p>2. b</p>",
            "<p>1) a</p><p>2) b</p>",
            "<p>1. a<br>2. b</p>",
            "<ol><li>1. a</li><li>2. b</li></ol>",
            "<p>question 1: a</p>\n<p>QUESTION 2: b</p>",
        ):
            with self.subTest(html=html):
                self.assert_split(html)

    def test_the_numbers_need_not_start_at_1(self):
        # The pile's own number is asked about whatever it holds.
        self.assert_split("<p>Intro.</p><p>2. b</p><p>3. c</p>", numbers=(1, 2, 3))

    def test_writing_with_no_numbered_sections_changes_nothing(self):
        # H-242's one-piece rule: an unnumbered essay stays under question 1.
        for html in (
            "<p>A biome is when animals live somewhere. I studied the desert.</p>",
            "<p>I got 2. Then 3. Then 4.</p>",
            "<p>3.5 is more than 2.5</p><p>4.5 too</p>",
        ):
            with self.subTest(html=html):
                self.assert_untouched(first_reading(pile(html)))

    def test_one_numbered_section_is_not_a_pile(self):
        self.assert_untouched(first_reading(pile("<p>Question 1: a biome</p>")))

    def test_numbers_out_of_order_are_not_a_pile(self):
        self.assert_untouched(first_reading(pile("<p>2. b</p><p>1. a</p>")))

    def test_numbers_outside_the_assignment_are_not_counted(self):
        answers = first_reading(pile("<p>1. a</p><p>5. b</p><p>6. c</p>"), questions=4)
        self.assert_untouched(answers)

    def test_an_outside_number_does_not_split_a_section(self):
        # questions=4, so "7." is outside the assignment. Without the
        # `in numbers` filter in _numbered_sections, "7. baz" would start
        # its own section and section 2 would lose it.
        answers = first_reading(
            pile("<p>1. foo</p><p>2. bar</p><p>7. baz</p>"), questions=4
        )
        _, mocked = self.run_step(
            answers, [section(1, "foo"), section(2, "bar 7. baz")]
        )
        note = self.note(mocked)
        self.assertIn("7. baz", note)

    def test_a_named_question_with_its_own_answer_is_not_touched(self):
        answers = first_reading(pile("<p>1. a</p><p>2. b</p>"))
        answers[1] = section(2, "its own")
        self.assert_untouched(answers)

    def test_a_named_question_with_a_flag_is_left_to_h253(self):
        # Already a target by H-253's route; nothing extra is asked.
        answers = first_reading(pile("<p>1. a</p><p>2. b</p>"))
        answers[1]["answer_flag"] = FLAG_OFF_TOPIC
        result, mocked = self.run_step(answers, [])
        self.assertIn("question(s) [2]", self.note(mocked))

    def test_an_answer_on_topic_is_not_a_pile(self):
        # A numbered list that IS the answer to question 1, with the later
        # pages missing: the reader did not call it off topic, so it stays.
        self.assert_untouched(
            first_reading(pile("<p>1. Rosa Parks</p><p>2. Selma</p>", FLAG_NONE))
        )

    def test_a_pile_in_the_wrong_form_counts(self):
        answers = first_reading(pile("<p>1. a</p><p>2. b</p>", FLAG_TYPE_MISMATCH))
        _, mocked = self.run_step(answers, [])
        self.assertEqual(mocked.call_count, 1)

    def test_a_pile_under_a_later_question(self):
        answers = [not_found(1), pile("<p>1. a</p><p>2. b</p><p>3. c</p>", number=2)]
        answers += [not_found(n) for n in range(3, 6)]
        result, mocked = self.run_step(
            answers, [section(1, "a"), section(2, "b"), section(3, "c")]
        )
        self.assertIn("question(s) [1, 2, 3]", self.note(mocked))
        self.assertEqual(
            [entry["answer_html"] for entry in result[:3]],
            ["<p>a</p>", "<p>b</p>", "<p>c</p>"],
        )


def recheck_reply(findings):
    fake = MagicMock()
    fake.choices = [MagicMock()]
    fake.choices[0].message.content = json.dumps({"findings": findings})
    fake.usage.total_tokens = 10
    fake.model = "verifier-model"
    return fake


class TheRecheckCapTest(SimpleTestCase):
    """B: above the cap, the lowest-numbered `cap` blanks are re-checked."""

    def setUp(self):
        self.processor = AIProcessor()

    def verify(self, answers, findings=()):
        with patch(TARGET, return_value=recheck_reply(list(findings))) as mocked:
            result = self.processor._verify_blank_answers(
                user=None, content=[image()], answers=answers
            )
        return result, mocked

    def asked(self, mocked):
        return mocked.call_args.kwargs["messages"][0]["content"][0]["text"]

    def test_the_beta_paper_has_its_first_12_blanks_re_checked(self):
        _, mocked = self.verify(first_reading())
        self.assertEqual(mocked.call_count, 1)
        self.assertIn(f"question(s) {list(range(2, 14))}", self.asked(mocked))

    def test_the_rest_are_logged_as_not_checked(self):
        with self.assertLogs("ai_processor.services", "INFO") as logs:
            self.verify(first_reading())
        self.assertTrue(
            any(
                "74 blank(s) exceeds the cap of 12" in line and "62 not checked" in line
                for line in logs.output
            ),
            logs.output,
        )

    def test_the_lowest_numbered_whatever_the_order(self):
        _, mocked = self.verify(list(reversed(first_reading())))
        self.assertIn(f"question(s) {list(range(2, 14))}", self.asked(mocked))

    def test_a_finding_for_a_question_not_asked_about_is_not_applied(self):
        findings = [
            {
                "question_number": n,
                "observed": "writing",
                "page": 1,
                "verbatim_fragment": "I studied the desert.",
                "content_found": True,
            }
            for n in (2, 40)
        ]
        result, _ = self.verify(first_reading(), findings)
        self.assertTrue(result[1]["blank_verification"]["content_found"])
        self.assertNotIn("blank_verification", result[39])

    @override_settings(ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS=0)
    def test_a_cap_of_0_checks_nothing(self):
        answers = first_reading()
        result, mocked = self.verify(answers)
        mocked.assert_not_called()
        self.assertEqual(result, answers)

    def test_at_or_under_the_cap_every_blank_is_re_checked(self):
        _, mocked = self.verify(first_reading(questions=13))
        self.assertIn(f"question(s) {list(range(2, 14))}", self.asked(mocked))


class TheWholeReadingTest(SimpleTestCase):
    """extract_answer_with_retry on the beta reply: reading, re-check of the
    first 12 blanks (nothing found), second reading of 1-6."""

    def test_the_beta_reply_ends_with_six_answers_and_the_upload_flag(self):
        processor = AIProcessor()
        first = {
            "answers": first_reading(),
            "submission_matches_assignment": False,
            "mismatch_detail": "A 4th grade biomes lab report, not this quiz.",
        }
        second = reply([section(n) for n in range(1, 7)])
        with patch.object(
            AIProcessor, "extract_answer_image", return_value=first
        ), patch(TARGET, side_effect=[recheck_reply([]), second]) as mocked:
            result = processor.extract_answer_with_retry(None, [image()], QUIZ)
        self.assertEqual(mocked.call_count, 2)
        answers = result["answers"]
        self.assertEqual(
            [entry["answer_html"] for entry in answers[:6]],
            [f"<p>{text}</p>" for text in SECTIONS],
        )
        self.assertTrue(
            all(
                entry["answer_status"] == NOT_FOUND_IN_DOCUMENT for entry in answers[6:]
            )
        )
        kinds = [flag["kind"] for flag in flags_from_extraction(result)]
        self.assertEqual(kinds[0], SUBMISSION_MISMATCH)
        self.assertEqual(kinds[1:7], [OFF_TOPIC] * 6)


class ThePromptTest(SimpleTestCase):
    """C: the reading prompt shows a numbered paper for another assignment."""

    def test_the_prompt_has_the_numbered_paper_example(self):
        prompt = " ".join(str(ANSWERS_EXTRACTION_PROMPT).split())
        for phrase in (
            "Example 4: A Numbered Paper for Another Assignment",
            '"question_count": 75',
            "Questions 7 to 75: one entry each, like question 7.",
            '"submission_matches_assignment": false',
            "it is NOT one piece of work",
        ):
            self.assertIn(phrase, prompt)

    def test_the_one_piece_rule_and_the_flag_rule_are_kept(self):
        prompt = str(ANSWERS_EXTRACTION_PROMPT)
        self.assertIn("whole piece under the FIRST question", prompt)
        self.assertIn("**A flag never moves an answer.**", prompt)

    def test_the_prompt_is_version_5_and_version_4_is_archived(self):
        self.assertTrue(
            ANSWERS_EXTRACTION_PROMPT.version.startswith(
                "ANSWERS_EXTRACTION_PROMPT_HTML_5:"
            )
        )
        self.assertFalse((PROMPT_DIR / "ANSWERS_EXTRACTION_PROMPT_HTML_4.txt").exists())
        self.assertTrue(
            (
                Path(PROMPT_DIR)
                / "prompts_archive"
                / "ANSWERS_EXTRACTION_PROMPT_HTML_4.txt"
            ).exists()
        )
