"""BE-I-04 slice C: what the grading service puts into a run.

`tests_grading_run_label` holds the rules that turn what a run kept into a
label. This module holds the other half: that the grading service keeps the
right things, on every route by which a grade is produced, with only the
provider call replaced.

  * a short paper (one call), a long paper marked in parts, a paper
    answered wholly from saved answers, wholly by fixed rules, and mixed;
  * only KEPT replies count: a reply that was rejected and retried, and a
    whole attempt that failed, leave nothing behind;
  * a second opinion is recorded apart;
  * the grading code reads its settings from the run's one reading, not
    live, and nothing outside the settings-version module reads a
    grade-shaping setting at all.

Every provider reply here has a real string model, or None on purpose
(rule 14).
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor import grading_config, services
from ai_processor.grading_run import GradingRun
from ai_processor.services import AIProcessor
from students import grading_label

MAIN = services.MAIN_MODEL
BACKUP = services.GRADING_FALLBACK_MODELS[0]
SECOND = "second/opinion-model"
LONG_PAPER = 12
assert LONG_PAPER > services.GRADING_QUESTIONS_PER_CHUNK  # marked in parts

SUMMARY_REPLY = {
    "overall_performance_analysis": "Solid work overall.",
    "grader_meta_analysis": "Consistent scoring across batches.",
    "grading_confidence": 90,
    "recommendations": ["Review question 3."],
}


def _reply(payload, model):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _essay(number, points=10):
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


def _objective(number):
    return {
        "question_number": number,
        "question_text": f"Objective question {number}?",
        "question_type": "OBJECTIVE",
        "points": 5,
        "options": ["London", "Berlin", "Paris", "Madrid"],
        "rubric": [],
        "model_answer": "Paris",
    }


def _answer(number, text=None):
    return {
        "question_number": number,
        "answer_html": text or f"<p>Essay {number} answer.</p>",
    }


def _evaluation(number, score=8):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": [f"Essay {number} answer"],
    }


def _evaluations(numbers):
    return {"question_evaluations": [_evaluation(n) for n in numbers]}


def _is_a_batch_call(kwargs):
    return kwargs.get("response_schema") is services.GRADING_BATCH_RESPONSE_SCHEMA


def _is_the_first_batch(kwargs):
    return "Essay question 1?" in str(kwargs.get("user_prompt"))


ASSIGNMENT = SimpleNamespace(
    id="assignment-1", title="A paper", instructions="", custom_ai_prompt=""
)


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class _RouteCase(TestCase):
    """A TestCase: a failed attempt goes through the refund scope."""

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def grade(self, questions, answers, run=None):
        run = run or GradingRun.start()
        self.processor.extract_grade_with_retry(
            MagicMock(), questions, answers, assignment_model=ASSIGNMENT, run=run
        )
        return run

    def assert_run(self, run, model, flag, served, reused=(), second_opinion=()):
        label = run.label()
        self.assertEqual(label["grading_model"], model)
        self.assertEqual(label["grading_fallback_used"], flag)
        self.assertEqual(
            run.audit_models(),
            {
                "models_served": sorted(served),
                "models_reused": sorted(reused),
                "models_second_opinion": sorted(second_opinion),
            },
        )


@patch.object(AIProcessor, "execute_graded_task")
class AShortPaperTest(_RouteCase):
    def one(self, mock_execute, model):
        mock_execute.return_value = _reply(_evaluations([1]), model)
        return self.grade([_essay(1)], [_answer(1)])

    def test_marked_by_the_main_model(self, mock_execute):
        run = self.one(mock_execute, MAIN)
        self.assert_run(run, MAIN, grading_label.FALLBACK_NO, [MAIN])

    def test_marked_by_a_backup_model(self, mock_execute):
        run = self.one(mock_execute, BACKUP)
        self.assert_run(run, BACKUP, grading_label.FALLBACK_YES, [BACKUP])

    def test_marked_by_a_model_the_provider_did_not_name(self, mock_execute):
        run = self.one(mock_execute, None)
        self.assert_run(
            run,
            grading_label.MODEL_UNKNOWN,
            grading_label.FALLBACK_UNKNOWN,
            [grading_label.MODEL_UNKNOWN],
        )

    def test_a_grader_named_inside_the_reply_does_not_reach_the_label(
        self, mock_execute
    ):
        """Nothing the AI writes about itself is trusted: the reply says a
        backup graded it and that it came from the store; the provider's
        response, as our code read it, says the main model answered."""
        payload = {
            "question_evaluations": [
                dict(_evaluation(1), graded_by=BACKUP, from_cache=True)
            ],
            "grading_model": BACKUP,
        }
        mock_execute.return_value = _reply(payload, MAIN)
        run = self.grade([_essay(1)], [_answer(1)])
        self.assert_run(run, MAIN, grading_label.FALLBACK_NO, [MAIN])

    def test_one_call_marking_three_answers_votes_three_times(self, mock_execute):
        """The majority is per ANSWER: a reused answer by a backup against
        three fresh ones by the main model."""
        mock_execute.return_value = _reply(_evaluations([9]), BACKUP)
        self.grade([_essay(9)], [_answer(9)])
        mock_execute.return_value = _reply(_evaluations([1, 2, 3]), MAIN)
        run = self.grade(
            [_essay(n) for n in (1, 2, 3, 9)], [_answer(n) for n in (1, 2, 3, 9)]
        )
        self.assert_run(run, MAIN, grading_label.FALLBACK_YES, [MAIN], reused=[BACKUP])


@patch.object(AIProcessor, "execute_graded_task")
class ReusedAndFixedRuleTest(_RouteCase):
    def test_a_wholly_reused_paper_names_the_model_that_first_answered(
        self, mock_execute
    ):
        mock_execute.return_value = _reply(_evaluations([1]), BACKUP)
        self.grade([_essay(1)], [_answer(1)])
        mock_execute.reset_mock()
        run = self.grade([_essay(1)], [_answer(1)])
        self.assertEqual(mock_execute.call_count, 0)
        self.assert_run(run, BACKUP, grading_label.FALLBACK_YES, [], reused=[BACKUP])

    def test_a_reused_answer_whose_first_model_was_not_named_is_unknown(
        self, mock_execute
    ):
        mock_execute.return_value = _reply(_evaluations([1]), None)
        self.grade([_essay(1)], [_answer(1)])
        run = self.grade([_essay(1)], [_answer(1)])
        self.assert_run(
            run,
            grading_label.MODEL_UNKNOWN,
            grading_label.FALLBACK_UNKNOWN,
            [],
            reused=[grading_label.MODEL_UNKNOWN],
        )

    def test_a_wholly_fixed_rule_paper_is_deterministic(self, mock_execute):
        run = self.grade([_objective(1)], [_answer(1, "<p>Paris</p>")])
        self.assertEqual(mock_execute.call_count, 0)
        self.assert_run(
            run,
            grading_label.MODEL_DETERMINISTIC,
            grading_label.FALLBACK_NOT_APPLICABLE,
            [],
        )

    def test_a_mixed_paper_counts_only_the_answers_an_ai_marked(self, mock_execute):
        """One fixed-rule answer, one reused answer first made by a backup,
        one fresh answer by the main model: a tie of one each, which the
        main model wins; the fixed-rule answer does not vote."""
        mock_execute.return_value = _reply(_evaluations([2]), BACKUP)
        self.grade([_essay(2)], [_answer(2)])
        mock_execute.return_value = _reply(_evaluations([3]), MAIN)
        run = self.grade(
            [_objective(1), _essay(2), _essay(3)],
            [_answer(1, "<p>Paris</p>"), _answer(2), _answer(3)],
        )
        self.assert_run(run, MAIN, grading_label.FALLBACK_YES, [MAIN], reused=[BACKUP])


def _long_paper():
    numbers = range(1, LONG_PAPER + 1)
    return [_essay(n) for n in numbers], [_answer(n) for n in numbers]


@patch.object(AIProcessor, "execute_graded_task")
class ALongPaperMarkedInPartsTest(_RouteCase):
    def test_a_backup_marking_one_part_is_seen(self, mock_execute):
        """The old record took the model of the final summary call only: a
        backup marking one part of a long paper left no trace."""

        def reply(**kwargs):
            if not _is_a_batch_call(kwargs):
                return _reply(SUMMARY_REPLY, MAIN)
            model = MAIN if _is_the_first_batch(kwargs) else BACKUP
            return _reply(_evaluations(range(1, LONG_PAPER + 1)), model)

        mock_execute.side_effect = reply
        run = self.grade(*_long_paper())
        self.assertEqual(mock_execute.call_count, 3)
        self.assert_run(run, MAIN, grading_label.FALLBACK_YES, [BACKUP, MAIN])

    def test_the_part_with_the_most_answers_names_the_model(self, mock_execute):
        def reply(**kwargs):
            if not _is_a_batch_call(kwargs):
                return _reply(SUMMARY_REPLY, MAIN)
            model = BACKUP if _is_the_first_batch(kwargs) else MAIN
            return _reply(_evaluations(range(1, LONG_PAPER + 1)), model)

        mock_execute.side_effect = reply
        run = self.grade(*_long_paper())
        self.assert_run(run, BACKUP, grading_label.FALLBACK_YES, [BACKUP, MAIN])

    def test_a_backup_summary_call_is_flagged_but_does_not_name_the_model(
        self, mock_execute
    ):
        def reply(**kwargs):
            if not _is_a_batch_call(kwargs):
                return _reply(SUMMARY_REPLY, BACKUP)
            return _reply(_evaluations(range(1, LONG_PAPER + 1)), MAIN)

        mock_execute.side_effect = reply
        run = self.grade(*_long_paper())
        self.assert_run(run, MAIN, grading_label.FALLBACK_YES, [BACKUP, MAIN])

    def test_a_rejected_reply_that_is_retried_is_not_counted(self, mock_execute):
        """The first reply to the first part comes from a backup and holds
        no evaluations; it is rejected and asked again."""
        state = {"rejected": False}

        def reply(**kwargs):
            if not _is_a_batch_call(kwargs):
                return _reply(SUMMARY_REPLY, MAIN)
            if _is_the_first_batch(kwargs) and not state["rejected"]:
                state["rejected"] = True
                return _reply({"question_evaluations": []}, BACKUP)
            return _reply(_evaluations(range(1, LONG_PAPER + 1)), MAIN)

        mock_execute.side_effect = reply
        run = self.grade(*_long_paper())
        self.assertTrue(state["rejected"])
        self.assert_run(run, MAIN, grading_label.FALLBACK_NO, [MAIN])

    def test_a_whole_attempt_that_failed_leaves_nothing_behind(self, mock_execute):
        """Attempt one: the first part is marked by a backup and kept, then
        the second part fails for good, so the attempt is thrown away.
        Attempt two is marked by the main model throughout."""
        state = {"failures": 0, "second_attempt": False}

        def reply(**kwargs):
            if state["second_attempt"]:
                if not _is_a_batch_call(kwargs):
                    return _reply(SUMMARY_REPLY, MAIN)
                return _reply(_evaluations(range(1, LONG_PAPER + 1)), MAIN)
            if _is_a_batch_call(kwargs) and _is_the_first_batch(kwargs):
                if state["failures"]:
                    state["second_attempt"] = True
                    return _reply(_evaluations(range(1, LONG_PAPER + 1)), MAIN)
                return _reply(_evaluations(range(1, LONG_PAPER + 1)), BACKUP)
            state["failures"] += 1
            raise RuntimeError("the provider timed out")

        mock_execute.side_effect = reply
        run = self.grade(*_long_paper())
        self.assertTrue(state["second_attempt"])
        self.assertGreaterEqual(state["failures"], 1)
        self.assert_run(run, MAIN, grading_label.FALLBACK_NO, [MAIN])


class ASecondOpinionIsRecordedApartTest(TestCase):
    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_its_model_is_in_its_own_list_and_nowhere_else(self, mock_execute):
        def reply(**kwargs):
            model = SECOND if kwargs.get("override_model") else MAIN
            return _reply({"question_evaluations": [_evaluation(1, score=15)]}, model)

        mock_execute.side_effect = reply
        with override_settings(
            GRADING_SECOND_OPINION_ENABLED=True,
            GRADING_SECOND_OPINION_MODELS=[SECOND],
            GRADING_SECOND_OPINION_MIN_CONFIDENCE=0,
            GRADING_SECOND_OPINION_HIGH_POINTS=1,
            GRADING_SECOND_OPINION_SAMPLE_RATE=0,
        ):
            run = GradingRun.start()
            self.processor.extract_grade_with_retry(
                MagicMock(),
                [_essay(1, points=20)],
                [_answer(1)],
                assignment_model=ASSIGNMENT,
                run=run,
            )
        self.assertGreaterEqual(mock_execute.call_count, 2)
        label = run.label()
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_NO)
        self.assertEqual(
            run.audit_models(),
            {
                "models_served": [MAIN],
                "models_reused": [],
                "models_second_opinion": [SECOND],
            },
        )


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class TheGradingCodeReadsTheRunsReadingTest(TestCase):
    """A setting changed while a run is going must not change how that run
    grades: the label would then name settings the grade was not made
    under."""

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_fixed_rule_marking_stays_on_for_a_run_that_started_with_it_on(
        self, mock_execute
    ):
        with override_settings(GRADING_DETERMINISTIC_OBJECTIVE=True):
            run = GradingRun.start()
        with override_settings(GRADING_DETERMINISTIC_OBJECTIVE=False):
            self.processor.extract_grade_with_retry(
                MagicMock(),
                [_objective(1)],
                [_answer(1, "<p>Paris</p>")],
                assignment_model=ASSIGNMENT,
                run=run,
            )
        self.assertEqual(mock_execute.call_count, 0)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_fixed_rule_marking_stays_off_for_a_run_that_started_with_it_off(
        self, mock_execute
    ):
        mock_execute.return_value = _reply(
            {
                "question_evaluations": [
                    dict(_evaluation(1, score=5), evidence_quotes=["Paris"])
                ]
            },
            MAIN,
        )
        with override_settings(GRADING_DETERMINISTIC_OBJECTIVE=False):
            run = GradingRun.start()
        with override_settings(GRADING_DETERMINISTIC_OBJECTIVE=True):
            self.processor.extract_grade_with_retry(
                MagicMock(),
                [_objective(1)],
                [_answer(1, "<p>Paris</p>")],
                assignment_model=ASSIGNMENT,
                run=run,
            )
        self.assertEqual(mock_execute.call_count, 1)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_a_second_opinion_switched_on_mid_run_is_not_asked_for(self, mock_execute):
        mock_execute.return_value = _reply(
            {"question_evaluations": [_evaluation(1, score=15)]}, MAIN
        )
        with override_settings(GRADING_SECOND_OPINION_ENABLED=False):
            run = GradingRun.start()
        with override_settings(
            GRADING_SECOND_OPINION_ENABLED=True,
            GRADING_SECOND_OPINION_MODELS=[SECOND],
            GRADING_SECOND_OPINION_MIN_CONFIDENCE=0,
            GRADING_SECOND_OPINION_HIGH_POINTS=1,
            GRADING_SECOND_OPINION_SAMPLE_RATE=0,
        ):
            self.processor.extract_grade_with_retry(
                MagicMock(),
                [_essay(1, points=20)],
                [_answer(1)],
                assignment_model=ASSIGNMENT,
                run=run,
            )
        self.assertEqual(mock_execute.call_count, 1)


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class TheStartOfRunReadingIsEverywhereTest(TestCase):
    """A grade-shaping setting changes while the AI is answering. The
    lookup key, the store key and the label must all show the reading the
    run STARTED with."""

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def grade(self, run=None):
        run = run or GradingRun.start()
        self.processor.extract_grade_with_retry(
            MagicMock(), [_essay(1)], [_answer(1)], assignment_model=ASSIGNMENT, run=run
        )
        return run

    def test_the_label_and_both_keys_show_the_reading_the_run_started_with(self):
        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=15):
            version_at_15 = grading_config.GradingConfig.read().version
        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=16):
            version_at_16 = grading_config.GradingConfig.read().version
        self.assertNotEqual(version_at_15, version_at_16)
        changed = override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=16)

        def reply_and_change_the_setting(**kwargs):
            changed.enable()
            return _reply(_evaluations([1]), MAIN)

        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=15):
            with patch.object(
                AIProcessor,
                "execute_graded_task",
                side_effect=reply_and_change_the_setting,
            ) as first:
                try:
                    run = self.grade()
                finally:
                    changed.disable()
            self.assertEqual(first.call_count, 1)
            # The label: the version of the reading the run started with.
            self.assertEqual(run.label()["grading_config_version"], version_at_15)
            # The store key: an identical paper under the starting setting
            # finds the saved answer.
            with patch.object(AIProcessor, "execute_graded_task") as again:
                reused = self.grade()
            self.assertEqual(again.call_count, 0)
            self.assertEqual(reused.audit_models()["models_reused"], [MAIN])
        # And it was not filed under the setting it changed to.
        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=16):
            with patch.object(
                AIProcessor,
                "execute_graded_task",
                return_value=_reply(_evaluations([1]), MAIN),
            ) as under_the_new_setting:
                self.grade()
        self.assertEqual(under_the_new_setting.call_count, 1)


#: Files that may read a grade-shaping setting live, and why.
LIVE_READS_ALLOWED = {
    "ai_processor/grading_config.py": "the one place that takes the reading",
    "ai_processor/management/commands/grading_benchmark.py": (
        "an offline tool that prints the settings it was run under; it "
        "grades nothing that is saved"
    ),
}


def _live_reads_of_grade_shaping_settings():
    names = set(grading_config.GRADE_SHAPING_SETTINGS)
    base = Path(settings.BASE_DIR)
    found = []
    for path in sorted(base.glob("*/**/*.py")):
        relative = path.relative_to(base).as_posix()
        if (
            relative in LIVE_READS_ALLOWED
            or relative.startswith(("docs/", "AutoGrader/settings"))
            or "/migrations/" in relative
            or "/tests" in relative
            or path.name.startswith("tests")
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in names
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
            ):
                found.append(f"{relative}:{node.lineno} settings.{node.attr}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in names
            ):
                setting = str(node.args[1].value)
                found.append(f"{relative}:{node.lineno} getattr {setting}")
    return found


class NoGradeShapingSettingIsReadLiveTest(SimpleTestCase):
    def test_nothing_outside_the_settings_version_module_reads_one(self):
        self.assertEqual(
            _live_reads_of_grade_shaping_settings(),
            [],
            "A grade-shaping setting is read from Django settings directly. "
            "Read it from the run's one reading (run.config.get(NAME)), so "
            "the grade and its label cannot be made under different "
            "settings (BE-I-04).",
        )

    def test_the_scan_sees_a_live_read_when_there_is_one(self):
        """Guard on the guard: the scan is not blind."""
        source = (
            "from django.conf import settings\n"
            "a = settings.GRADING_MAX_IMAGES_PER_CALL\n"
            'b = getattr(settings, "GRADING_EVIDENCE_ENFORCEMENT", "strict")\n'
        )
        names = set(grading_config.GRADE_SHAPING_SETTINGS)
        hits = 0
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute) and node.attr in names:
                hits += 1
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "getattr"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in names
            ):
                hits += 1
        self.assertEqual(hits, 2)

    def test_every_allowed_file_exists(self):
        for relative in LIVE_READS_ALLOWED:
            with self.subTest(file=relative):
                self.assertTrue((Path(settings.BASE_DIR) / relative).exists())
