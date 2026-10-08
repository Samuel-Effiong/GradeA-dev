"""
Tests for the targeted blank re-read (_verify_blank_answers).

This is the one step that can RECOVER a lost answer rather than merely
flag it, so the tests are weighted toward its two safety properties:

  * it costs nothing when there is nothing to check (a fully answered
    submission must not pay for a second call);
  * it can never make things worse - no failure mode of the re-read may
    lose an answer, change a transcription, or fail an extraction that
    had already succeeded.

Run with:
    python manage.py test ai_processor.tests_blank_verification
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import (
    AIFeatureNotAvailableError,
    AIProcessor,
    InsufficientCreditsError,
)


def image(url="data:image/jpeg;base64,PAGE"):
    return {"type": "image_url", "image_url": {"url": url}}


def entry(number, status=BLANK, html="", notes="nothing seen"):
    return {
        "question_number": number,
        "answer_html": html,
        "answer_status": status,
        "transcription_notes": notes,
    }


def response(findings):
    fake = MagicMock()
    fake.choices = [MagicMock()]
    fake.choices[0].message.content = json.dumps({"findings": findings})
    fake.usage.total_tokens = 10
    fake.model = "verifier-model"
    return fake


def finding(number, found=True, fragment="x = 5", page=1, observed="handwriting"):
    return {
        "question_number": number,
        "observed": observed,
        "page": page,
        "verbatim_fragment": fragment,
        "content_found": found,
    }


class BlankVerificationTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def verify(self, answers, findings=None, side_effect=None, content=None):
        target = "ai_processor.services.AIProcessor.execute_graded_task"
        kwargs = (
            {"side_effect": side_effect}
            if side_effect is not None
            else {"return_value": response(findings or [])}
        )
        with patch(target, **kwargs) as mocked:
            result = self.processor._verify_blank_answers(
                user=None,
                content=content if content is not None else [image()],
                answers=answers,
            )
        return result, mocked

    # ── cost control ──────────────────────────────────────────────────
    def test_no_blanks_makes_no_call(self):
        # THE AFFORDABILITY PROPERTY. A fully answered submission pays
        # nothing for this feature.
        answers = [entry(1, ANSWERED, "work"), entry(2, ANSWERED, "work")]
        result, mocked = self.verify(answers)
        mocked.assert_not_called()
        self.assertEqual(result, answers)

    def test_blanks_make_exactly_one_call(self):
        _, mocked = self.verify([entry(1), entry(2), entry(3, ANSWERED, "w")])
        self.assertEqual(mocked.call_count, 1)

    def test_not_found_entries_are_also_re_read(self):
        # The completeness gate's placeholders are the likeliest misses.
        _, mocked = self.verify([entry(1, NOT_FOUND_IN_DOCUMENT)])
        self.assertEqual(mocked.call_count, 1)

    def test_illegible_is_not_re_read(self):
        # Something WAS transcribed, so nothing is hiding.
        _, mocked = self.verify([entry(1, ILLEGIBLE, "best guess")])
        mocked.assert_not_called()

    @override_settings(ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS=2)
    def test_too_many_blanks_skips_the_re_read(self):
        result, mocked = self.verify([entry(n) for n in (1, 2, 3)])
        mocked.assert_not_called()
        self.assertEqual(len(result), 3)

    def test_no_images_skips_the_re_read(self):
        _, mocked = self.verify([entry(1)], content=[{"type": "text", "text": "x"}])
        mocked.assert_not_called()

    @override_settings(ANSWER_BLANK_VERIFICATION_MAX_PAGES=2)
    def test_too_many_pages_skips_the_re_read(self):
        # A 40-page script would blow up a single-call re-read; skipping
        # can only cost a recovery, never produce a wrong flag.
        pages = [image(f"data:image/jpeg;base64,PAGE{n}") for n in range(3)]
        result, mocked = self.verify([entry(1)], content=pages)
        mocked.assert_not_called()
        self.assertEqual(result[0]["answer_status"], BLANK)

    @override_settings(ANSWER_BLANK_VERIFICATION_MAX_PAGES=3)
    def test_page_count_at_the_cap_still_runs(self):
        pages = [image(f"data:image/jpeg;base64,PAGE{n}") for n in range(3)]
        _, mocked = self.verify([entry(1)], [], content=pages)
        self.assertEqual(mocked.call_count, 1)

    def test_every_page_is_sent_to_the_re_read(self):
        # A missing answer could be on any page, so a partial view would
        # simply miss it.
        pages = [image(f"data:image/jpeg;base64,PAGE{n}") for n in range(4)]
        _, mocked = self.verify([entry(1)], [], content=pages)
        sent = mocked.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(sum(1 for p in sent if p.get("type") == "image_url"), 4)

    @override_settings(ANSWER_BLANK_VERIFICATION_ENABLED=False)
    def test_disabled_makes_no_call(self):
        answers = [entry(1)]
        result, mocked = self.verify(answers)
        mocked.assert_not_called()
        self.assertIs(result, answers)

    # ── the recovery it exists for ────────────────────────────────────
    def test_content_found_upgrades_blank_to_not_found(self):
        # THE POINT. "The student skipped it" is a claim we now have
        # evidence against, so it must be withdrawn.
        result, _ = self.verify([entry(1)], [finding(1)])
        self.assertEqual(result[0]["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_content_found_records_the_evidence(self):
        result, _ = self.verify([entry(1)], [finding(1, fragment="x = 5", page=2)])
        verification = result[0]["blank_verification"]
        self.assertTrue(verification["content_found"])
        self.assertEqual(verification["verbatim_fragment"], "x = 5")
        self.assertEqual(verification["page"], 2)

    def test_content_found_annotates_the_notes_for_the_teacher(self):
        result, _ = self.verify([entry(1)], [finding(1, fragment="x = 5")])
        self.assertIn("RE-READ FOUND WRITING", result[0]["transcription_notes"])
        self.assertIn("x = 5", result[0]["transcription_notes"])

    def test_the_fragment_never_becomes_the_answer(self):
        # A fragment is proof something is there, NOT a transcription of
        # it. Promoting it would grade the student on a scrap.
        result, _ = self.verify([entry(1)], [finding(1, fragment="x = 5")])
        self.assertEqual(result[0]["answer_html"], "")

    def test_confirmed_blank_is_left_alone(self):
        # The re-read agreeing must not disturb anything.
        answers = [entry(1)]
        result, _ = self.verify(answers, [finding(1, found=False, fragment=None)])
        self.assertEqual(result[0]["answer_status"], BLANK)
        self.assertNotIn("blank_verification", result[0])

    def test_empty_findings_leave_everything_alone(self):
        result, _ = self.verify([entry(1)], [])
        self.assertEqual(result[0]["answer_status"], BLANK)

    def test_only_the_named_question_is_changed(self):
        answers = [entry(1), entry(2), entry(3, ANSWERED, "work")]
        result, _ = self.verify(answers, [finding(2)])
        self.assertEqual(result[0]["answer_status"], BLANK)
        self.assertEqual(result[1]["answer_status"], NOT_FOUND_IN_DOCUMENT)
        self.assertEqual(result[2]["answer_status"], ANSWERED)

    def test_string_question_numbers_match(self):
        result, _ = self.verify([entry("2")], [finding(2)])
        self.assertEqual(result[0]["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_a_finding_can_never_downgrade_an_answered_question(self):
        # Even a confused verifier naming an ANSWERED question must not
        # be able to discard a real transcription.
        answers = [entry(1, ANSWERED, "the real work")]
        result, _ = self.verify(answers, [finding(1)])
        self.assertEqual(result[0]["answer_status"], ANSWERED)
        self.assertEqual(result[0]["answer_html"], "the real work")

    # ── never make things worse ───────────────────────────────────────
    def test_model_error_leaves_the_extraction_untouched(self):
        answers = [entry(1), entry(2, ANSWERED, "work")]
        with self.assertLogs("ai_processor.services", "ERROR"):
            result, _ = self.verify(answers, side_effect=RuntimeError("boom"))
        self.assertEqual(result, answers)

    def test_unparseable_response_leaves_the_extraction_untouched(self):
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message.content = "not json"
        answers = [entry(1)]
        with self.assertLogs("ai_processor.services", "ERROR"):
            with patch(
                "ai_processor.services.AIProcessor.execute_graded_task",
                return_value=bad,
            ):
                result = self.processor._verify_blank_answers(None, [image()], answers)
        self.assertEqual(result, answers)

    def test_out_of_credits_is_not_an_error(self):
        # The teacher already paid for the extraction; an unaffordable
        # extra check must not fail it, or log as a fault.
        answers = [entry(1)]
        result, _ = self.verify(
            answers, side_effect=InsufficientCreditsError("no credits")
        )
        self.assertEqual(result, answers)

    def test_feature_unavailable_is_not_an_error(self):
        answers = [entry(1)]
        result, _ = self.verify(
            answers, side_effect=AIFeatureNotAvailableError("not on this tier")
        )
        self.assertEqual(result, answers)

    def test_malformed_findings_are_ignored(self):
        result, _ = self.verify([entry(1)], ["junk", None, 42, {}])
        self.assertEqual(result[0]["answer_status"], BLANK)

    def test_non_list_answers_pass_through(self):
        self.assertIsNone(self.processor._verify_blank_answers(None, [image()], None))

    def test_non_dict_entries_survive_untouched(self):
        answers = ["junk", entry(1)]
        result, _ = self.verify(answers, [finding(1)])
        self.assertEqual(result[0], "junk")

    def test_input_entries_are_not_mutated(self):
        original = entry(1)
        snapshot = dict(original)
        self.verify([original], [finding(1)])
        self.assertEqual(original, snapshot)

    def test_finding_without_a_fragment_still_flags(self):
        # "I can see writing but cannot quote it" is still evidence.
        result, _ = self.verify([entry(1)], [finding(1, fragment=None)])
        self.assertEqual(result[0]["answer_status"], NOT_FOUND_IN_DOCUMENT)
        self.assertIsNone(result[0]["blank_verification"]["verbatim_fragment"])

    @override_settings(ANSWER_BLANK_VERIFICATION_MODEL="some/other-model")
    def test_configured_model_is_passed_as_an_override(self):
        _, mocked = self.verify([entry(1)], [])
        self.assertEqual(mocked.call_args.kwargs["override_model"], "some/other-model")

    @override_settings(ANSWER_BLANK_VERIFICATION_MODEL="")
    def test_unset_model_uses_default_routing(self):
        _, mocked = self.verify([entry(1)], [])
        self.assertIsNone(mocked.call_args.kwargs["override_model"])

    def test_the_prompt_names_only_the_blank_questions(self):
        _, mocked = self.verify([entry(1), entry(5), entry(9, ANSWERED, "w")], [])
        text = mocked.call_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("[1, 5]", text)
        self.assertNotIn("9", text.split("question(s)")[1].split("\n")[0])
