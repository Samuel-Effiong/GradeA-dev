"""AI-call record, slice 0: the vote that names a step's model, moved out of
BE-I-04's grading run into one helper that every step shares.

BE-I-04's rule (ai_processor/grading_run.py, `_grading_model`): the model
that marked the most answers wins; on a tie the main model if it is among
the leaders, else the first by plain alphabetical order of the provider's
exact text; an unnamed model (None) loses every tie to a named one. It
depends only on the counts, never on the order of arrival.

Slice 0 changes NO behaviour. This module holds:

  * the rule itself, tested on the helper (`ai_processor.vote.majority_model`);
  * a frozen copy of the rule as it was written before the move, run against
    the grading run over many seeded random vote patterns, so the move cannot
    change an answer;
  * a test that the grading run really calls the shared helper (so the rule
    has one home, not two copies).

Nothing here touches the provider or the database.
"""

import random
from collections import Counter
from unittest.mock import patch

from django.test import SimpleTestCase

from ai_processor import grading_run, services
from ai_processor.grading_run import GradingRun
from ai_processor.vote import majority_model

MAIN = "main/model"
BACKUP = "backup/model"
ALPHA = "a/model"
BETA = "b/model"


class TheRuleOnTheHelperTest(SimpleTestCase):
    def test_the_model_with_most_votes_wins(self):
        self.assertEqual(majority_model({ALPHA: 3, BETA: 1}, MAIN), ALPHA)

    def test_a_single_model_wins(self):
        self.assertEqual(majority_model({BETA: 1}, MAIN), BETA)

    def test_a_two_way_tie_goes_to_the_main_model(self):
        self.assertEqual(majority_model({ALPHA: 2, MAIN: 2}, MAIN), MAIN)

    def test_a_three_way_tie_goes_to_the_main_model(self):
        self.assertEqual(majority_model({ALPHA: 1, MAIN: 1, BETA: 1}, MAIN), MAIN)

    def test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet(self):
        self.assertEqual(majority_model({BETA: 2, ALPHA: 2}, MAIN), ALPHA)

    def test_the_main_model_loses_to_a_clear_majority(self):
        self.assertEqual(majority_model({MAIN: 1, ALPHA: 2}, MAIN), ALPHA)

    def test_an_unnamed_model_loses_a_tie_to_a_named_one(self):
        self.assertEqual(majority_model({None: 2, BETA: 2}, MAIN), BETA)

    def test_an_unnamed_model_that_has_the_most_votes_wins_as_none(self):
        self.assertIsNone(majority_model({None: 3, BETA: 2}, MAIN))

    def test_only_unnamed_leaders_give_none(self):
        self.assertIsNone(majority_model({None: 1}, MAIN))

    def test_no_votes_give_none(self):
        self.assertIsNone(majority_model({}, MAIN))

    def test_a_model_with_no_votes_is_not_a_leader(self):
        self.assertEqual(majority_model({ALPHA: 0, BETA: 1}, MAIN), BETA)

    def test_only_zero_votes_give_none(self):
        self.assertIsNone(majority_model({ALPHA: 0, MAIN: 0}, MAIN))

    def test_a_main_model_that_is_none_is_never_preferred(self):
        # A step whose main model is unknown still decides a tie by alphabet.
        self.assertEqual(majority_model({BETA: 1, ALPHA: 1}, None), ALPHA)

    def test_the_order_of_arrival_does_not_change_the_answer(self):
        counts = [(ALPHA, 2), (BETA, 2), (None, 2), (MAIN, 1)]
        answers = set()
        rng = random.Random(8101)
        for _ in range(40):
            rng.shuffle(counts)
            answers.add(majority_model(dict(counts), MAIN))
        self.assertEqual(answers, {ALPHA})

    def test_a_counter_is_accepted_as_it_is(self):
        self.assertEqual(
            majority_model(Counter([ALPHA, ALPHA, BETA]), MAIN),
            ALPHA,
        )

    def test_the_input_is_not_changed(self):
        votes = {ALPHA: 2, BETA: 2, None: 1}
        before = dict(votes)
        majority_model(votes, MAIN)
        self.assertEqual(votes, before)

    def test_the_providers_exact_text_is_returned(self):
        odd = "Vendor/Model-1.5@2026-01-01 (preview)"
        self.assertEqual(majority_model({odd: 1}, MAIN), odd)


def _reference_grading_model(fresh, reused, main):
    """The rule exactly as `GradingRun._grading_model` wrote it before slice
    0 moved it. Frozen here on purpose: it must not follow the helper."""
    votes = Counter(fresh + reused)
    if not votes:
        return grading_run.MODEL_DETERMINISTIC
    most = max(votes.values())
    leaders = [model for model, count in votes.items() if count == most]
    named = sorted(model for model in leaders if model is not None)
    if main in named:
        return main
    if named:
        return named[0]
    return grading_run.MODEL_UNKNOWN


class TheMoveChangesNoAnswerTest(SimpleTestCase):
    """The grading run gives the same model as the rule did before the move,
    for many vote patterns (a fixed seed: the same cases on every run)."""

    POOL = [MAIN, BACKUP, ALPHA, BETA, "zzz/last", None]

    def setUp(self):
        for name, value in (
            ("MAIN_MODEL", MAIN),
            ("GRADING_FALLBACK_MODELS", [BACKUP]),
        ):
            patcher = patch.object(services, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_same_model_over_many_random_patterns(self):
        rng = random.Random(20261008)
        for case in range(400):
            fresh = [rng.choice(self.POOL) for _ in range(rng.randint(0, 7))]
            reused = [rng.choice(self.POOL) for _ in range(rng.randint(0, 5))]
            run = GradingRun.start()
            run.fresh_answers.extend(fresh)
            run.reused_answers.extend(reused)
            with self.subTest(case=case, fresh=fresh, reused=reused):
                self.assertEqual(
                    run.label()["grading_model"],
                    _reference_grading_model(fresh, reused, MAIN)[:255],
                )


class TheGradingRunUsesTheSharedHelperTest(SimpleTestCase):
    """One home for the rule: the grading run calls the helper, and does not
    keep a second copy."""

    def setUp(self):
        for name, value in (
            ("MAIN_MODEL", MAIN),
            ("GRADING_FALLBACK_MODELS", [BACKUP]),
        ):
            patcher = patch.object(services, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_run_calls_the_helper_with_the_counts_and_the_main_model(self):
        run = GradingRun.start()
        run.fresh_answers.extend([ALPHA, ALPHA])
        run.reused_answers.append(BETA)
        with patch.object(
            grading_run, "majority_model", return_value="spy/model"
        ) as spy:
            label = run.label()
        spy.assert_called_once()
        votes, main = spy.call_args.args
        self.assertEqual(dict(votes), {ALPHA: 2, BETA: 1})
        self.assertEqual(main, MAIN)
        self.assertEqual(label["grading_model"], "spy/model")

    def test_a_helper_answer_of_none_reads_unknown(self):
        run = GradingRun.start()
        run.fresh_answers.append(None)
        with patch.object(grading_run, "majority_model", return_value=None):
            self.assertEqual(run.label()["grading_model"], grading_run.MODEL_UNKNOWN)

    def test_with_no_answer_marked_the_helper_is_not_asked(self):
        run = GradingRun.start()
        run.other_calls.append(MAIN)
        with patch.object(grading_run, "majority_model") as spy:
            label = run.label()
        spy.assert_not_called()
        self.assertEqual(label["grading_model"], grading_run.MODEL_DETERMINISTIC)
