"""AI-call record, slice 2: what a reading's run turns into a label.

Two rules live in `ai_processor/reading_run.py` and are tried here on their
own, state by state:

  * the re-check's pair (result word, model word): one function builds it
    from what the run saw at the provider call, and the pair is always one
    the allowed table lists;
  * the vote over the answers AS SAVED (ruling 5): each saved answer votes
    for the model of the reply that supplied it; a placeholder the
    completeness check inserted has no vote; an answer the check dropped has
    none; an answer repeated in a reply votes once; an answer made of two
    parts counts once for each part's model.

The pipeline tests (`tests_reading_pipeline`) show the same rules reached
through the real extraction with only the provider call replaced.
"""

import itertools

from django.test import SimpleTestCase

from ai_processor.reading_run import ReadingRun
from ai_processor.services import ANSWERS_EXTRACTION_PROMPT, MAIN_MODEL
from students import step_label

UNKNOWN = "unknown"


def _answer(number, html="work"):
    return {"question_number": number, "answer_html": html, "answer_status": "ANSWERED"}


def _call(run, reply_model=None, replied=True):
    """A call that left and, if `replied`, got a reply naming `reply_model`."""
    run.recheck.mark_call_left()
    if replied:
        run.recheck.mark_reply_received(reply_model)


class TheRecheckPairTest(SimpleTestCase):
    """The four states of the design note (ruling 2), one test each, then the
    rest of the table."""

    def test_no_call_left_is_not_run_beside_not_run(self):
        run = ReadingRun()
        self.assertEqual(run.recheck_pair(), ("not_run", "not_run"))

    def test_a_call_left_and_raised_is_failed_beside_unknown(self):
        run = ReadingRun()
        _call(run, replied=False)
        self.assertEqual(run.recheck_pair(), ("failed", UNKNOWN))

    def test_a_reply_received_and_then_lost_keeps_the_providers_name(self):
        """After the reply a cancellation or the credit step can raise. The
        reply was received and named, so the name is kept; it was not used,
        so the result is failed."""
        run = ReadingRun()
        _call(run, "check/model")
        self.assertEqual(run.recheck_pair(), ("failed", "check/model"))

    def test_a_reply_received_and_used_is_confirmed_blank(self):
        run = ReadingRun()
        _call(run, "check/model")
        run.mark_recheck_used(found_writing=False)
        self.assertEqual(run.recheck_pair(), ("confirmed_blank", "check/model"))

    def test_a_reply_received_and_used_with_a_finding_is_found_writing(self):
        run = ReadingRun()
        _call(run, "check/model")
        run.mark_recheck_used(found_writing=True)
        self.assertEqual(run.recheck_pair(), ("found_writing", "check/model"))

    def test_an_unnamed_reply_that_could_not_be_used_is_failed_unknown(self):
        run = ReadingRun()
        _call(run, None)
        self.assertEqual(run.recheck_pair(), ("failed", UNKNOWN))

    def test_an_unnamed_reply_that_was_used_is_unknown(self):
        for found in (True, False):
            with self.subTest(found=found):
                run = ReadingRun()
                _call(run, None)
                run.mark_recheck_used(found_writing=found)
                result, model = run.recheck_pair()
                self.assertEqual(model, UNKNOWN)
                self.assertEqual(
                    result, "found_writing" if found else "confirmed_blank"
                )

    def test_a_provider_name_equal_to_one_of_our_words_is_recorded_unknown(self):
        for word in ("not_run", "unknown", "unlabelled"):
            with self.subTest(word=word):
                run = ReadingRun()
                _call(run, word)
                self.assertEqual(run.recheck_pair(), ("failed", UNKNOWN))

    def test_a_reply_cannot_be_used_before_it_was_received(self):
        run = ReadingRun()
        with self.assertRaises(RuntimeError):
            run.mark_recheck_used(found_writing=False)
        run.recheck.mark_call_left()
        with self.assertRaises(RuntimeError):
            run.mark_recheck_used(found_writing=True)

    def test_a_new_attempt_forgets_the_recheck(self):
        run = ReadingRun()
        _call(run, "check/model")
        run.mark_recheck_used(found_writing=True)
        run.begin_attempt()
        self.assertEqual(run.recheck_pair(), ("not_run", "not_run"))

    def test_every_state_the_run_can_be_in_gives_an_allowed_pair(self):
        names = (None, "check/model", "unknown", "not_run", "unlabelled")
        for left, replied, name, used, found in itertools.product(
            (False, True), (False, True), names, (False, True), (False, True)
        ):
            if not left and (replied or used):
                continue
            if used and not replied:
                continue
            run = ReadingRun()
            if left:
                _call(run, name, replied=replied)
            if used:
                run.mark_recheck_used(found_writing=found)
            with self.subTest(
                left=left, replied=replied, name=name, used=used, found=found
            ):
                pair = run.recheck_pair()
                self.assertTrue(step_label.pair_is_allowed(*pair), pair)  # type: ignore[attr-defined]

    def test_the_two_stand_in_words_never_appear_beside_a_different_word(self):
        for left, replied, name in itertools.product(
            (False, True), (False, True), (None, "m/x", "not_run")
        ):
            if not left and replied:
                continue
            run = ReadingRun()
            if left:
                _call(run, name, replied=replied)
            result, model = run.recheck_pair()
            with self.subTest(left=left, replied=replied, name=name):
                self.assertEqual(result == "not_run", model == "not_run")
                self.assertNotIn("unlabelled", (result, model))


class TheAllowedTableTest(SimpleTestCase):
    """The table of the design note, section 4, as a function."""

    def test_the_rows_of_the_table_are_allowed(self):
        for pair in (
            ("unlabelled", "unlabelled"),
            ("not_run", "not_run"),
            ("failed", "some/model"),
            ("failed", "unknown"),
            ("confirmed_blank", "some/model"),
            ("confirmed_blank", "unknown"),
            ("found_writing", "some/model"),
            ("found_writing", "unknown"),
        ):
            with self.subTest(pair=pair):
                self.assertTrue(step_label.pair_is_allowed(*pair))  # type: ignore[attr-defined]

    def test_a_stand_in_word_beside_any_other_word_is_refused(self):
        for pair in (
            ("not_run", "some/model"),
            ("not_run", "unknown"),
            ("not_run", "unlabelled"),
            ("unlabelled", "not_run"),
            ("unlabelled", "some/model"),
            ("unlabelled", "unknown"),
            ("failed", "not_run"),
            ("failed", "unlabelled"),
            ("confirmed_blank", "not_run"),
            ("confirmed_blank", "unlabelled"),
            ("found_writing", "not_run"),
            ("found_writing", "unlabelled"),
        ):
            with self.subTest(pair=pair):
                self.assertFalse(step_label.pair_is_allowed(*pair))  # type: ignore[attr-defined]

    def test_a_result_word_that_does_not_exist_is_refused(self):
        self.assertFalse(step_label.pair_is_allowed("maybe", "some/model"))  # type: ignore[attr-defined]
        self.assertFalse(step_label.pair_is_allowed("", ""))  # type: ignore[attr-defined]


class TheVoteTest(SimpleTestCase):
    """reading_model(saved): the vote over the answers as saved."""

    def test_the_model_that_supplied_most_saved_answers_wins(self):
        run = ReadingRun()
        run.keep_answers([_answer(1), _answer(2)], model="a/one")
        run.keep_answers([_answer(3)], model="b/two")
        saved = [_answer(1), _answer(2), _answer(3)]
        self.assertEqual(run.reading_model(saved), "a/one")

    def test_a_placeholder_has_no_vote(self):
        """The check inserted entries for questions 2 and 3: they came from
        no model. Counted for the main model they would outvote the one
        real answer."""
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="other/model")
        saved = [_answer(1), _answer(2, ""), _answer(3, "")]
        self.assertNotEqual(MAIN_MODEL, "other/model")
        self.assertEqual(run.reading_model(saved), "other/model")

    def test_an_answer_the_check_dropped_has_no_vote(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        run.keep_answers([_answer(2), _answer(3)], model="b/two")
        saved = [_answer(1)]  # 2 and 3 were numbering drift, dropped
        self.assertEqual(run.reading_model(saved), "a/one")

    def test_an_answer_repeated_in_a_reply_votes_once(self):
        run = ReadingRun()
        run.keep_answers([_answer(1), _answer(1, "again")], model="a/one")
        run.keep_answers([_answer(2), _answer(3)], model="b/two")
        saved = [_answer(1), _answer(2), _answer(3)]
        self.assertEqual(run.reading_model(saved), "b/two")

    def test_an_answer_of_two_parts_counts_for_each_parts_model(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        run.add_origin(_answer(1), model="b/two")
        run.keep_answers([_answer(2)], model="b/two")
        saved = [_answer(1), _answer(2)]
        self.assertEqual(run.reading_model(saved), "b/two")

    def test_a_later_reply_that_wins_a_question_replaces_the_earlier_origin(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        run.keep_answers([_answer(1, "better")], model="b/two")
        self.assertEqual(run.reading_model([_answer(1)]), "b/two")

    def test_a_tie_goes_to_the_main_model_when_it_is_a_leader(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/early")
        run.keep_answers([_answer(2)], model=MAIN_MODEL)
        self.assertEqual(run.reading_model([_answer(1), _answer(2)]), MAIN_MODEL)

    def test_a_tie_otherwise_goes_to_the_first_by_alphabetical_order(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="z/late")
        run.keep_answers([_answer(2)], model="b/early")
        self.assertEqual(run.reading_model([_answer(1), _answer(2)]), "b/early")

    def test_the_order_of_arrival_does_not_matter(self):
        first = ReadingRun()
        first.keep_answers([_answer(1)], model="z/late")
        first.keep_answers([_answer(2)], model="b/early")
        second = ReadingRun()
        second.keep_answers([_answer(2)], model="b/early")
        second.keep_answers([_answer(1)], model="z/late")
        saved = [_answer(1), _answer(2)]
        self.assertEqual(first.reading_model(saved), second.reading_model(saved))

    def test_the_spelling_of_a_question_number_does_not_hide_an_origin(self):
        run = ReadingRun()
        run.keep_answers([_answer(3)], model="a/one")
        for spelling in ("3", "Q3", "Question 3", 3):
            with self.subTest(spelling=spelling):
                self.assertEqual(run.reading_model([_answer(spelling)]), "a/one")

    def test_a_reply_that_named_no_model_is_unknown(self):
        run = ReadingRun()
        run.keep_answers([_answer(1), _answer(2)], model=None)
        self.assertEqual(run.reading_model([_answer(1), _answer(2)]), UNKNOWN)

    def test_a_named_model_beats_an_unnamed_one_on_a_tie(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model=None)
        run.keep_answers([_answer(2)], model="z/named")
        self.assertEqual(run.reading_model([_answer(1), _answer(2)]), "z/named")

    def test_a_provider_name_equal_to_one_of_our_words_is_unnamed(self):
        for word in ("unknown", "not_run", "unlabelled"):
            with self.subTest(word=word):
                run = ReadingRun()
                run.keep_answers([_answer(1)], model=word)
                self.assertEqual(run.reading_model([_answer(1)]), UNKNOWN)

    def test_no_saved_answer_with_a_reply_behind_it_is_unknown(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        self.assertEqual(run.reading_model([_answer(7, "")]), UNKNOWN)
        self.assertEqual(run.reading_model([]), UNKNOWN)

    def test_an_entry_that_is_not_an_object_is_skipped(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        self.assertEqual(run.reading_model(["text", None, _answer(1)]), "a/one")

    def test_a_new_attempt_forgets_every_origin(self):
        run = ReadingRun()
        run.keep_answers([_answer(1)], model="a/one")
        run.begin_attempt()
        self.assertEqual(run.reading_model([_answer(1)]), UNKNOWN)

    def test_the_answers_themselves_are_never_changed_or_tagged(self):
        """The origin lives beside the answers, never inside them."""
        answer = _answer(1)
        before = dict(answer)
        run = ReadingRun()
        run.keep_answers([answer], model="a/one")
        run.reading_model([answer])
        self.assertEqual(answer, before)


class TheLabelFieldsTest(SimpleTestCase):
    def _run_with_config(self):
        from ai_processor.reading_config import ReadingConfig

        run = ReadingRun()
        run.config = ReadingConfig.read()
        return run

    def test_the_six_names_and_nothing_else(self):
        run = self._run_with_config()
        fields = run.label_fields([])
        self.assertEqual(
            set(fields),
            {
                "answers_model",
                "answers_prompt_version",
                "answers_config_version",
                "answers_release",
                "answers_recheck_model",
                "answers_recheck_result",
            },
        )

    def test_the_values_come_from_what_the_run_kept(self):
        run = self._run_with_config()
        run.reading.keep("a/one", 0, ANSWERS_EXTRACTION_PROMPT.version)
        run.keep_answers([_answer(1)], model="a/one")
        _call(run, "check/model")
        run.mark_recheck_used(found_writing=True)
        fields = run.label_fields([_answer(1)])
        self.assertEqual(fields["answers_model"], "a/one")
        self.assertEqual(
            fields["answers_prompt_version"], ANSWERS_EXTRACTION_PROMPT.version
        )
        self.assertEqual(fields["answers_config_version"], run.config.version)
        self.assertEqual(fields["answers_release"], run.config.release)
        self.assertEqual(fields["answers_recheck_model"], "check/model")
        self.assertEqual(fields["answers_recheck_result"], "found_writing")

    def test_a_run_with_no_kept_reply_says_unknown_and_never_unlabelled(self):
        run = self._run_with_config()
        fields = run.label_fields([])
        self.assertEqual(fields["answers_model"], UNKNOWN)
        self.assertEqual(fields["answers_prompt_version"], UNKNOWN)
        self.assertNotIn("unlabelled", fields.values())

    def test_a_run_given_no_reading_settings_refuses_to_label(self):
        run = ReadingRun()
        with self.assertRaises(RuntimeError):
            run.label_fields([])

    def test_every_value_fits_its_column(self):
        run = self._run_with_config()
        run.keep_answers([_answer(1)], model="m" * 400)
        fields = run.label_fields([_answer(1)])
        limits = {
            "answers_model": 255,
            "answers_prompt_version": 128,
            "answers_config_version": 128,
            "answers_release": 64,
            "answers_recheck_model": 255,
            "answers_recheck_result": 32,
        }
        for name, limit in limits.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(fields[name]), limit)
