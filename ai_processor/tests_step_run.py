"""AI-call record, slice 0: `StepRun`, what one AI step carries.

A step is one kind of AI call that affects a grade and is not the grading
itself: reading an assignment, reading a student's answers, the blank-answer
re-check, generating an assignment, the feedback wording. Like BE-I-04's
`GradingRun`, a `StepRun` gathers, as our code read them from the provider's
replies, WHICH MODEL answered. Never anything the AI wrote about itself.

The rules (BE-I-04's, carried over; the design note, section 0 and 2):

  * only KEPT replies count; `begin_attempt()` empties what was gathered;
  * the model is the provider's exact text, or "unknown"; never the model we
    asked for;
  * the majority and tie rule is BE-I-04's, shared through
    `ai_processor.vote.majority_model`;
  * a provider name that equals one of our fixed words ("unlabelled",
    "unknown", "not_run") is recorded as "unknown", so a fixed word in a
    model column is always ours (accepted by the Senior Manager, 7 October
    2026, with a test for each word);
  * two facts are marked at the provider call itself: "the call left" and
    "a reply was received", with the name read from it.

Nothing here touches the provider or the database.
"""

import inspect

from django.test import SimpleTestCase

from ai_processor import step_run
from ai_processor.step_run import (
    FIXED_MODEL_WORDS,
    MODEL_UNKNOWN,
    NOT_RUN,
    UNLABELLED,
    StepRun,
)

MAIN = "main/model"
ALPHA = "a/model"
BETA = "b/model"


class TheFixedWordsTest(SimpleTestCase):
    def test_the_words_are_exactly_these(self):
        self.assertEqual(step_run.UNLABELLED, "unlabelled")
        self.assertEqual(step_run.MODEL_UNKNOWN, "unknown")
        self.assertEqual(step_run.NOT_RUN, "not_run")
        self.assertEqual(step_run.FAILED, "failed")
        self.assertEqual(step_run.CONFIRMED_BLANK, "confirmed_blank")
        self.assertEqual(step_run.FOUND_WRITING, "found_writing")
        self.assertEqual(step_run.SOURCE_EXTRACTED, "extracted")
        self.assertEqual(step_run.SOURCE_GENERATED, "generated")

    def test_the_fixed_model_words_are_the_three_that_may_not_be_a_providers(self):
        self.assertEqual(
            FIXED_MODEL_WORDS, frozenset({"unlabelled", "unknown", "not_run"})
        )


class GatheringTest(SimpleTestCase):
    def test_a_new_step_has_kept_nothing(self):
        run = StepRun("answers")
        self.assertEqual(dict(run.votes()), {})
        self.assertIsNone(run.model(MAIN))

    def test_a_kept_reply_votes_once_per_item_it_supplied(self):
        run = StepRun("answers")
        run.keep(ALPHA, 3)
        run.keep(BETA, 1)
        self.assertEqual(dict(run.votes()), {ALPHA: 3, BETA: 1})

    def test_a_reply_that_supplied_no_item_is_kept_but_does_not_vote(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 0)
        self.assertEqual(dict(run.votes()), {})
        self.assertTrue(run.has_kept_reply())

    def test_nothing_kept_means_no_kept_reply(self):
        self.assertFalse(StepRun("answers").has_kept_reply())

    def test_a_negative_count_votes_for_nobody(self):
        run = StepRun("answers")
        run.keep(ALPHA, -4)
        self.assertEqual(dict(run.votes()), {})

    def test_an_unnamed_reply_votes_as_none(self):
        run = StepRun("answers")
        for unnamed in (None, "", 7, ["x"], b"bytes"):
            run.keep(unnamed, 1)
        self.assertEqual(dict(run.votes()), {None: 5})

    def test_a_new_attempt_starts_from_nothing(self):
        run = StepRun("answers")
        run.keep(ALPHA, 2)
        run.mark_call_left()
        run.mark_reply_received(ALPHA)
        run.begin_attempt()
        self.assertEqual(dict(run.votes()), {})
        self.assertFalse(run.has_kept_reply())
        self.assertFalse(run.call_left)
        self.assertFalse(run.reply_received)
        self.assertIsNone(run.reply_model)

    def test_the_step_keeps_its_name_across_attempts(self):
        run = StepRun("recheck")
        run.begin_attempt()
        self.assertEqual(run.step, "recheck")


class TheModelTest(SimpleTestCase):
    def test_the_majority_wins(self):
        run = StepRun("answers")
        run.keep(ALPHA, 2)
        run.keep(BETA, 1)
        self.assertEqual(run.model(MAIN), ALPHA)

    def test_a_tie_goes_to_the_main_model_then_to_alphabet(self):
        run = StepRun("answers")
        run.keep(ALPHA, 1)
        run.keep(MAIN, 1)
        self.assertEqual(run.model(MAIN), MAIN)
        other = StepRun("answers")
        other.keep(BETA, 1)
        other.keep(ALPHA, 1)
        self.assertEqual(other.model(MAIN), ALPHA)

    def test_unnamed_loses_a_tie_to_a_named_model(self):
        run = StepRun("answers")
        run.keep(None, 1)
        run.keep(BETA, 1)
        self.assertEqual(run.model(MAIN), BETA)

    def test_only_unnamed_replies_read_unknown(self):
        run = StepRun("answers")
        run.keep(None, 2)
        self.assertEqual(run.model(MAIN), MODEL_UNKNOWN)

    def test_a_kept_reply_with_no_item_and_no_name_reads_unknown(self):
        # A reply was kept (so there IS a label to write) but it supplied
        # nothing and the provider named no model: the word, never a blank.
        run = StepRun("assignment")
        run.keep(None, 0)
        self.assertEqual(run.model(MAIN), MODEL_UNKNOWN)

    def test_a_kept_reply_with_no_item_reads_unknown_even_when_it_is_named(self):
        # Senior Manager, 8 October 2026 (ruling 5b): when no reply holds a
        # question the label has no vote and reads "unknown", the same rule
        # as BE-I-04's (a kept call with no answer names nobody).
        run = StepRun("assignment")
        run.keep(ALPHA, 0)
        self.assertTrue(run.has_kept_reply())
        self.assertEqual(run.model(MAIN), MODEL_UNKNOWN)

    def test_a_reply_with_no_item_does_not_outvote_one_that_has_items(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 0)
        run.keep(BETA, 2)
        self.assertEqual(run.model(MAIN), BETA)

    def test_nothing_kept_gives_none_so_the_caller_chooses_its_word(self):
        self.assertIsNone(StepRun("answers").model(MAIN))

    def test_the_providers_exact_text_is_kept(self):
        odd = "Vendor/Model-1.5@2026-01-01 (preview)"
        run = StepRun("answers")
        run.keep(odd, 1)
        self.assertEqual(run.model(MAIN), odd)

    def test_the_model_asked_for_is_never_read_from_the_run(self):
        # The run holds only what `keep` was given. A StepRun has no notion
        # of the model that was asked for.
        parameters = inspect.signature(StepRun.__init__).parameters
        self.assertEqual(list(parameters), ["self", "step"])


class ThePromptVersionTest(SimpleTestCase):
    """Each label also names the prompt that was sent. The version is read
    where the call is made (a step can have two prompts, e.g. the text and
    the image extraction), never guessed by the caller: a kept reply brings
    the version of the prompt it answered. A reply votes once for its
    version, however many items it supplied."""

    def test_nothing_kept_gives_no_version(self):
        self.assertIsNone(StepRun("answers").prompt_version())

    def test_one_reply_with_a_version_gives_that_version(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 3, prompt_version="extract-v7")
        self.assertEqual(run.prompt_version(), "extract-v7")

    def test_the_version_most_replies_used_wins(self):
        # The winner is neither the first nor the last version seen.
        run = StepRun("assignment")
        run.keep(ALPHA, 1, prompt_version="image-v2")
        run.keep(ALPHA, 1, prompt_version="text-v1")
        run.keep(ALPHA, 1, prompt_version="image-v2")
        run.keep(ALPHA, 1, prompt_version="text-v1")
        run.keep(ALPHA, 1, prompt_version="image-v2")
        run.keep(ALPHA, 1, prompt_version="other-v3")
        self.assertEqual(run.prompt_version(), "image-v2")

    def test_a_reply_votes_once_however_many_items_it_supplied(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 40, prompt_version="text-v1")
        run.keep(ALPHA, 1, prompt_version="image-v2")
        run.keep(ALPHA, 1, prompt_version="image-v2")
        self.assertEqual(run.prompt_version(), "image-v2")

    def test_a_tie_goes_to_the_first_by_alphabet(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 1, prompt_version="b-v1")
        run.keep(ALPHA, 1, prompt_version="a-v1")
        self.assertEqual(run.prompt_version(), "a-v1")

    def test_a_reply_that_supplied_no_item_still_brings_its_version(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 0, prompt_version="extract-v7")
        self.assertEqual(run.prompt_version(), "extract-v7")

    def test_a_reply_with_no_version_does_not_vote(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 1)
        run.keep(ALPHA, 1, prompt_version="extract-v7")
        self.assertEqual(run.prompt_version(), "extract-v7")

    def test_replies_without_any_version_give_none(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 1)
        self.assertIsNone(run.prompt_version())

    def test_a_new_attempt_forgets_the_prompt_versions(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 1, prompt_version="extract-v7")
        run.begin_attempt()
        self.assertIsNone(run.prompt_version())

    def test_the_version_is_the_exact_text(self):
        run = StepRun("assignment")
        run.keep(ALPHA, 1, prompt_version="  Extract v7 (2026-10)  ")
        self.assertEqual(run.prompt_version(), "  Extract v7 (2026-10)  ")


class AProviderNameThatIsOneOfOurFixedWordsTest(SimpleTestCase):
    """Accepted by the Senior Manager on 7 October 2026: one test per word."""

    def assert_recorded_as_unknown(self, word):
        run = StepRun("answers")
        run.keep(word, 2)
        self.assertEqual(run.model(MAIN), MODEL_UNKNOWN)
        self.assertEqual(dict(run.votes()), {None: 2})

    def test_unlabelled_as_a_providers_name_reads_unknown(self):
        self.assert_recorded_as_unknown("unlabelled")

    def test_unknown_as_a_providers_name_reads_unknown(self):
        self.assert_recorded_as_unknown("unknown")

    def test_not_run_as_a_providers_name_reads_unknown(self):
        self.assert_recorded_as_unknown("not_run")

    def test_the_fixed_word_loses_a_tie_like_any_unnamed_model(self):
        run = StepRun("answers")
        run.keep("not_run", 1)
        run.keep(ALPHA, 1)
        self.assertEqual(run.model(MAIN), ALPHA)

    def test_only_an_exact_match_is_a_fixed_word(self):
        # "Unknown", "unknown " and "not-run" are not our words; they are
        # whatever the provider said, and are kept as it said it.
        for name in ("Unknown", "unknown ", "not-run", "UNLABELLED"):
            with self.subTest(name=name):
                run = StepRun("answers")
                run.keep(name, 1)
                self.assertEqual(run.model(MAIN), name)


class TheMarksAtTheProviderCallTest(SimpleTestCase):
    """ "The call left" and "a reply was received" are recorded at the
    provider call itself (design note section 4, ruling 2), never inferred
    from the kind of exception that came later."""

    def test_a_new_step_has_marked_nothing(self):
        run = StepRun("recheck")
        self.assertFalse(run.call_left)
        self.assertFalse(run.reply_received)
        self.assertIsNone(run.reply_model)

    def test_the_call_left_is_marked_before_any_reply(self):
        run = StepRun("recheck")
        run.mark_call_left()
        self.assertTrue(run.call_left)
        self.assertFalse(run.reply_received)

    def test_a_reply_received_records_the_name_read_from_it(self):
        run = StepRun("recheck")
        run.mark_call_left()
        run.mark_reply_received(ALPHA)
        self.assertTrue(run.reply_received)
        self.assertEqual(run.reply_model, ALPHA)

    def test_a_reply_that_named_no_model_records_unknown(self):
        run = StepRun("recheck")
        run.mark_call_left()
        run.mark_reply_received(None)
        self.assertTrue(run.reply_received)
        self.assertEqual(run.reply_model, MODEL_UNKNOWN)

    def test_a_reply_named_with_a_fixed_word_records_unknown(self):
        for word in sorted(FIXED_MODEL_WORDS):
            with self.subTest(word=word):
                run = StepRun("recheck")
                run.mark_call_left()
                run.mark_reply_received(word)
                self.assertEqual(run.reply_model, MODEL_UNKNOWN)

    def test_a_reply_cannot_be_marked_before_the_call_left(self):
        run = StepRun("recheck")
        with self.assertRaises(RuntimeError):
            run.mark_reply_received(ALPHA)

    def test_marks_are_not_votes(self):
        run = StepRun("recheck")
        run.mark_call_left()
        run.mark_reply_received(ALPHA)
        self.assertEqual(dict(run.votes()), {})
        self.assertFalse(run.has_kept_reply())


class TheWordsBetweenTheTwoAppsTest(SimpleTestCase):
    def test_the_step_modules_words_equal_the_students_apps(self):
        from students import step_label

        for name in (
            "UNLABELLED",
            "MODEL_UNKNOWN",
            "NOT_RUN",
            "FAILED",
            "CONFIRMED_BLANK",
            "FOUND_WRITING",
            "SOURCE_EXTRACTED",
            "SOURCE_GENERATED",
            "FIXED_MODEL_WORDS",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(step_run, name, None), getattr(step_label, name, "<none>")
                )

    def test_unlabelled_and_unknown_are_the_gradings_words(self):
        from students import grading_label

        self.assertEqual(UNLABELLED, grading_label.UNLABELLED)
        self.assertEqual(MODEL_UNKNOWN, grading_label.MODEL_UNKNOWN)
        self.assertEqual(NOT_RUN, "not_run")
