"""BE-I-04 slice C: what a run's label says, from what the run kept.

A grading run records, as our code read them from the provider's replies:
the model of each freshly marked answer, the model of each kept call that
marked no answer (the summary call), the model that first produced each
reused answer, and, apart, the model of a second-opinion call. From those
it derives the six label fields and the three lists of the audit entry.

The rules, from the Senior Manager's rulings of 2026-10-06:

  * the model is the one that marked the most ANSWERS, fresh and reused
    together; the summary call marks none and does not vote. On a tie the
    main model if it is among the leaders, else the first by plain
    alphabetical order; "unknown" loses every tie to a named model;
  * backup used is "yes" if any kept call or reused answer came from a
    backup model (also together with an unknown one); "unknown" if none
    came from a backup and any came from a model the provider did not name
    or that is neither the main model nor a backup; "no" if all came from
    the main model; "not_applicable" if the run made no AI call and reused
    nothing;
  * second-opinion calls are in none of this;
  * only kept replies count: a new attempt starts from nothing.

Nothing here touches the provider or the database.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_processor import services
from ai_processor.grading_run import GradingRun
from students import grading_label
from students.grading_label import LABEL_FIELDS

MAIN = "main/model"
BACKUP = "backup/model"
OTHER = "other/model"


class _RunCase(SimpleTestCase):
    def setUp(self):
        for name, value in (
            ("MAIN_MODEL", MAIN),
            ("GRADING_FALLBACK_MODELS", [BACKUP]),
        ):
            patcher = patch.object(services, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run_ = GradingRun.start()

    def label(self):
        return self.run_.label()

    def assert_model_and_flag(self, model, flag):
        label = self.label()
        self.assertEqual(label["grading_model"], model)
        self.assertEqual(label["grading_fallback_used"], flag)


class TheWordsAreTheLabelsWordsTest(SimpleTestCase):
    """ai_processor does not import the students app, so the run keeps its
    own copy of the label's words. They must be the same words."""

    def test_each_word_of_the_run_equals_the_labels(self):
        from ai_processor import grading_run

        for name in (
            "STRICTNESS_NOT_YET_SET",
            "MODEL_DETERMINISTIC",
            "MODEL_UNKNOWN",
            "FALLBACK_YES",
            "FALLBACK_NO",
            "FALLBACK_UNKNOWN",
            "FALLBACK_NOT_APPLICABLE",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(grading_run, name, None), getattr(grading_label, name)
                )

    def test_the_cut_for_an_audit_item_is_the_audit_limit(self):
        from ai_processor import grading_run
        from audit import metadata

        self.assertEqual(
            getattr(grading_run, "AUDIT_ITEM_MAX_LENGTH", None),
            metadata.MAX_ITEM_STRING,
        )


class TheLabelsShapeTest(_RunCase):
    def test_it_has_exactly_the_six_fields(self):
        self.assertEqual(set(self.label()), set(LABEL_FIELDS))

    def test_every_value_is_a_string_that_fits_its_column(self):
        from students.tests_grading_label_fields import MAX_LENGTHS

        self.run_.keep_answers(MAIN, 3)
        for name, value in self.label().items():
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                self.assertLessEqual(len(value), MAX_LENGTHS[name])

    def test_no_value_is_ever_the_placeholder(self):
        """ "unlabelled" means only "no label recorded"."""
        for prepare in (lambda run: None, lambda run: run.keep_answers(None, 1)):
            run = GradingRun.start()
            prepare(run)
            for name, value in run.label().items():
                with self.subTest(name=name):
                    self.assertNotEqual(value, grading_label.UNLABELLED)

    def test_the_versions_and_the_release_come_from_the_runs_reading(self):
        with override_settings(GRADING_RELEASE_ID="release-42"):
            run = GradingRun.start()
        with override_settings(
            GRADING_RELEASE_ID="release-43", GRADING_MAX_IMAGES_PER_CALL=123456
        ):
            label = run.label()
        self.assertEqual(label["grading_release"], "release-42")
        self.assertEqual(label["grading_config_version"], run.config.version)
        self.assertEqual(label["grading_prompt_version"], run.prompt_version)

    def test_strictness_is_not_yet_set(self):
        self.assertEqual(
            self.label()["grading_strictness"], grading_label.STRICTNESS_NOT_YET_SET
        )

    def test_a_long_model_name_is_cut_to_the_column_not_refused(self):
        self.run_.keep_answers("m" * 400, 1)
        self.assertEqual(self.label()["grading_model"], "m" * 255)


class TheBackupFlagTest(_RunCase):
    def test_no_ai_call_and_nothing_reused_is_not_applicable(self):
        self.assert_model_and_flag(
            grading_label.MODEL_DETERMINISTIC, grading_label.FALLBACK_NOT_APPLICABLE
        )

    def test_the_main_model_only_is_no(self):
        self.run_.keep_answers(MAIN, 4)
        self.run_.keep_call(MAIN)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_NO)

    def test_any_backup_answer_is_yes(self):
        self.run_.keep_answers(MAIN, 9)
        self.run_.keep_answers(BACKUP, 1)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_YES)

    def test_a_backup_summary_call_alone_is_yes(self):
        self.run_.keep_answers(MAIN, 10)
        self.run_.keep_call(BACKUP)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_YES)

    def test_a_reused_answer_first_made_by_a_backup_is_yes(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_reused(BACKUP)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_YES)

    def test_main_with_an_unnamed_model_is_unknown(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_answers(None, 1)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_UNKNOWN)

    def test_main_with_a_model_on_neither_list_is_unknown(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_answers(OTHER, 1)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_UNKNOWN)

    def test_backup_with_an_unnamed_model_is_yes(self):
        self.run_.keep_answers(BACKUP, 2)
        self.run_.keep_answers(None, 1)
        self.assert_model_and_flag(BACKUP, grading_label.FALLBACK_YES)

    def test_an_unnamed_summary_call_alone_is_unknown(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_call(None)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_UNKNOWN)

    def test_a_wholly_reused_run_is_judged_by_the_first_models(self):
        self.run_.keep_reused(MAIN)
        self.run_.keep_reused(MAIN)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_NO)

    def test_the_lists_are_read_from_the_runs_reading_not_live(self):
        """The main model and the backup list as they were when the run
        started decide the flag."""
        self.run_.keep_answers(MAIN, 1)
        with patch.object(services, "MAIN_MODEL", "another/main"):
            self.assert_model_and_flag(MAIN, grading_label.FALLBACK_NO)


class TheModelIsTheOneThatMarkedTheMostAnswersTest(_RunCase):
    def test_the_majority_of_answers_wins(self):
        self.run_.keep_answers(MAIN, 2)
        self.run_.keep_answers(BACKUP, 10)
        self.assertEqual(self.label()["grading_model"], BACKUP)

    def test_fresh_and_reused_answers_are_counted_together(self):
        self.run_.keep_answers(MAIN, 2)
        self.run_.keep_reused(BACKUP)
        self.run_.keep_reused(BACKUP)
        self.run_.keep_reused(BACKUP)
        self.assertEqual(self.label()["grading_model"], BACKUP)

    def test_the_summary_call_does_not_vote(self):
        self.run_.keep_answers(MAIN, 1)
        for _ in range(5):
            self.run_.keep_call(BACKUP)
        self.assertEqual(self.label()["grading_model"], MAIN)

    def test_a_two_way_tie_goes_to_the_main_model(self):
        self.run_.keep_answers(BACKUP, 5)
        self.run_.keep_answers(MAIN, 5)
        self.assertEqual(self.label()["grading_model"], MAIN)

    def test_a_tie_without_the_main_model_goes_to_the_first_by_alphabet(self):
        self.run_.keep_answers("zeta/model", 2)
        self.run_.keep_answers("alpha/model", 2)
        self.run_.keep_answers(MAIN, 1)
        self.assertEqual(self.label()["grading_model"], "alpha/model")

    def test_a_three_way_tie_goes_to_the_main_model(self):
        for model in ("zeta/model", MAIN, "alpha/model"):
            self.run_.keep_answers(model, 3)
        self.assertEqual(self.label()["grading_model"], MAIN)

    def test_unknown_loses_a_tie_to_a_named_model(self):
        self.run_.keep_answers(None, 4)
        self.run_.keep_answers("zeta/model", 4)
        self.assertEqual(self.label()["grading_model"], "zeta/model")

    def test_unknown_wins_when_it_marked_the_most(self):
        self.run_.keep_answers(None, 5)
        self.run_.keep_answers(MAIN, 4)
        self.assertEqual(self.label()["grading_model"], grading_label.MODEL_UNKNOWN)

    def test_the_order_of_arrival_does_not_change_the_answer(self):
        first = GradingRun.start()
        second = GradingRun.start()
        pairs = [("zeta/model", 2), ("alpha/model", 2), (None, 2)]
        for model, count in pairs:
            first.keep_answers(model, count)
        for model, count in reversed(pairs):
            second.keep_answers(model, count)
        self.assertEqual(first.label(), second.label())
        self.assertEqual(first.label()["grading_model"], "alpha/model")

    def test_the_providers_exact_text_is_kept(self):
        self.run_.keep_answers("Main/Model:2026-09-01", 1)
        self.assertEqual(self.label()["grading_model"], "Main/Model:2026-09-01")


class OnlyKeptRepliesCountTest(_RunCase):
    def test_a_new_attempt_starts_from_nothing(self):
        self.run_.keep_answers(BACKUP, 5)
        self.run_.keep_call(BACKUP)
        self.run_.keep_reused(BACKUP)
        self.run_.keep_second_opinion(BACKUP)
        self.run_.begin_attempt()
        self.run_.keep_answers(MAIN, 5)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_NO)
        self.assertEqual(
            self.run_.audit_models(),
            {"models_served": [MAIN], "models_reused": [], "models_second_opinion": []},
        )

    def test_a_new_attempt_keeps_the_reading(self):
        config = self.run_.config
        prompt_version = self.run_.prompt_version
        self.run_.begin_attempt()
        self.assertIs(self.run_.config, config)
        self.assertEqual(self.run_.prompt_version, prompt_version)


class TheSecondOpinionIsApartTest(_RunCase):
    def test_it_is_in_neither_the_model_nor_the_flag(self):
        self.run_.keep_answers(MAIN, 2)
        for _ in range(9):
            self.run_.keep_second_opinion(BACKUP)
        self.assert_model_and_flag(MAIN, grading_label.FALLBACK_NO)

    def test_a_second_opinion_alone_does_not_make_an_ai_grading(self):
        self.run_.keep_second_opinion(BACKUP)
        self.assert_model_and_flag(
            grading_label.MODEL_DETERMINISTIC, grading_label.FALLBACK_NOT_APPLICABLE
        )


class TheAuditListsTest(_RunCase):
    def test_three_lists_each_distinct_and_sorted(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_answers(BACKUP, 1)
        self.run_.keep_call(MAIN)
        self.run_.keep_reused("zeta/model")
        self.run_.keep_reused("alpha/model")
        self.run_.keep_reused("alpha/model")
        self.run_.keep_second_opinion(OTHER)
        self.assertEqual(
            self.run_.audit_models(),
            {
                "models_served": [BACKUP, MAIN],
                "models_reused": ["alpha/model", "zeta/model"],
                "models_second_opinion": [OTHER],
            },
        )

    def test_the_summary_calls_model_is_among_the_served(self):
        self.run_.keep_answers(MAIN, 3)
        self.run_.keep_call(BACKUP)
        self.assertEqual(self.run_.audit_models()["models_served"], [BACKUP, MAIN])

    def test_an_unnamed_model_is_the_explicit_word_unknown(self):
        self.run_.keep_answers(None, 1)
        self.run_.keep_reused(None)
        self.run_.keep_second_opinion(None)
        for name, models in self.run_.audit_models().items():
            with self.subTest(name=name):
                self.assertEqual(models, [grading_label.MODEL_UNKNOWN])

    def test_empty_lists_when_there_was_none(self):
        self.assertEqual(
            self.run_.audit_models(),
            {"models_served": [], "models_reused": [], "models_second_opinion": []},
        )

    def test_every_item_passes_the_audit_limits(self):
        from audit import metadata

        self.run_.keep_answers("m" * 400, 1)
        self.run_.keep_reused("r" * 400)
        for name, models in self.run_.audit_models().items():
            for item in models:
                with self.subTest(name=name):
                    self.assertLessEqual(len(item), metadata.MAX_ITEM_STRING)
                    self.assertIsNone(metadata._EMAIL_SHAPED.search(item))

    def test_a_model_name_shaped_like_an_address_is_not_dropped_silently(self):
        """Audit metadata drops anything with an "@". A provider name that
        held one must still be counted: it is recorded with the "@"
        replaced, not lost."""
        self.run_.keep_answers("vendor@region/model", 1)
        served = self.run_.audit_models()["models_served"]
        self.assertEqual(len(served), 1)
        self.assertNotIn("@", served[0])
        self.assertIn("vendor", served[0])


class ALongModelNameTest(SimpleTestCase):
    """A provider's model name is not ours to bound. It is classified by
    its exact text BEFORE anything is cut, so a long backup name can never
    become a quiet "no"."""

    def run_with(self, main, backups):
        with patch.object(services, "MAIN_MODEL", main):
            with patch.object(services, "GRADING_FALLBACK_MODELS", backups):
                return GradingRun.start()

    def test_a_backup_name_longer_than_an_audit_item_is_still_yes(self):
        backup = "backup/" + "b" * 143  # 150 characters
        run = self.run_with(MAIN, [backup])
        run.keep_answers(backup, 1)
        label = run.label()
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)
        self.assertEqual(label["grading_model"], backup)
        served = run.audit_models()["models_served"]
        self.assertEqual(len(served), 1)
        self.assertLessEqual(len(served[0]), 64)
        self.assertTrue(backup.startswith(served[0][:32]))

    def test_a_backup_name_longer_than_the_column_is_still_yes(self):
        backup = "backup/" + "b" * 293  # 300 characters
        run = self.run_with(MAIN, [backup])
        run.keep_answers(backup, 1)
        label = run.label()
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)
        self.assertEqual(label["grading_model"], backup[:255])

    def test_a_main_name_longer_than_the_column_is_no(self):
        main = "main/" + "m" * 295  # 300 characters
        run = self.run_with(main, [BACKUP])
        run.keep_answers(main, 1)
        label = run.label()
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_NO)
        self.assertEqual(label["grading_model"], main[:255])

    def test_a_name_that_only_differs_after_the_cut_is_not_taken_for_the_main_model(
        self,
    ):
        """Two names with the same first 255 characters: one is the main
        model, the other is on neither list. Classified by the cut text the
        other would read "no"; by the exact text it is "unknown"."""
        prefix = "vendor/" + "x" * 292
        main, other = prefix + "A", prefix + "B"
        run = self.run_with(main, [BACKUP])
        run.keep_answers(other, 1)
        self.assertEqual(
            run.label()["grading_fallback_used"], grading_label.FALLBACK_UNKNOWN
        )

    def test_a_name_that_only_differs_after_the_cut_is_not_taken_for_a_backup(self):
        prefix = "vendor/" + "x" * 292
        backup, other = prefix + "A", prefix + "B"
        run = self.run_with(MAIN, [backup])
        run.keep_answers(MAIN, 1)
        run.keep_answers(other, 1)
        self.assertEqual(
            run.label()["grading_fallback_used"], grading_label.FALLBACK_UNKNOWN
        )


class TheFreshCallsClassificationTest(_RunCase):
    """One word for the run's FRESH calls only, worked out on the exact
    names by the same rule as the label's flag: yes, no, unknown, or
    no_fresh_call. The backup measurement reads this and nothing else
    (SM ruling, 2026-10-06). Reused answers and second opinions are not
    fresh calls."""

    def assert_fresh(self, expected):
        self.assertEqual(self.run_.fresh_backup_used(), expected)

    def test_no_call_at_all(self):
        self.assert_fresh("no_fresh_call")

    def test_a_wholly_reused_run_made_no_fresh_call(self):
        self.run_.keep_reused(BACKUP)
        self.assert_fresh("no_fresh_call")

    def test_the_main_model_only(self):
        self.run_.keep_answers(MAIN, 2)
        self.run_.keep_call(MAIN)
        self.assert_fresh("no")

    def test_a_backup_answer(self):
        self.run_.keep_answers(MAIN, 9)
        self.run_.keep_answers(BACKUP, 1)
        self.assert_fresh("yes")

    def test_a_backup_summary_call(self):
        self.run_.keep_answers(MAIN, 9)
        self.run_.keep_call(BACKUP)
        self.assert_fresh("yes")

    def test_main_with_an_unnamed_model(self):
        self.run_.keep_answers(MAIN, 1)
        self.run_.keep_answers(None, 1)
        self.assert_fresh("unknown")

    def test_backup_with_an_unnamed_model(self):
        self.run_.keep_answers(BACKUP, 1)
        self.run_.keep_call(None)
        self.assert_fresh("yes")

    def test_a_reused_backup_answer_does_not_make_the_fresh_calls_yes(self):
        """The label's flag is "yes" here; the fresh calls had no backup."""
        self.run_.keep_answers(MAIN, 1)
        self.run_.keep_reused(BACKUP)
        self.assert_fresh("no")
        self.assertEqual(
            self.label()["grading_fallback_used"], grading_label.FALLBACK_YES
        )

    def test_a_second_opinion_is_not_a_fresh_call_of_the_grader(self):
        self.run_.keep_answers(MAIN, 1)
        self.run_.keep_second_opinion(BACKUP)
        self.assert_fresh("no")

    def test_two_names_with_the_same_first_64_characters_are_not_confused(self):
        prefix = "vendor/" + "x" * 57  # 64 characters
        backup, other = prefix + "-backup", prefix + "-other"
        with patch.object(services, "GRADING_FALLBACK_MODELS", [backup]):
            fresh_backup = GradingRun.start()
            fresh_other = GradingRun.start()
        fresh_backup.keep_answers(backup, 1)
        fresh_other.keep_answers(other, 1)
        self.assertEqual(fresh_backup.fresh_backup_used(), "yes")
        self.assertEqual(fresh_other.fresh_backup_used(), "unknown")

    def test_it_is_one_short_word_the_audit_entry_can_hold(self):
        from audit import metadata

        for prepare in (
            lambda run: None,
            lambda run: run.keep_answers(MAIN, 1),
            lambda run: run.keep_answers(BACKUP, 1),
            lambda run: run.keep_answers(None, 1),
        ):
            run = GradingRun.start()
            prepare(run)
            word = run.fresh_backup_used()
            self.assertIn(word, ("yes", "no", "unknown", "no_fresh_call"))
            self.assertLessEqual(len(word), metadata.MAX_STRING)
