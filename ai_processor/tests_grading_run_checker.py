"""BE-I-04 slice C, the delta after verification: tests handed over by the
Next-stage Checker and adopted here (Senior Manager's ruling, 2026-10-07).
Each was seen red in the Checker's run under the break named below, and
again in this slice's own mutant battery (docs/evidence/epic-i-be-i-04-c).

PC1  a part's reply that is REJECTED AFTER it was parsed whole (every
     answer present, a quote the student never wrote) is not counted.
     The older test rejects a reply that holds no answers at all, so a
     count taken too early adds nothing there. (Break: the reply is
     counted before its evidence check.)
PC2  every call inside the grading service to a method that takes the run
     hands the run on by name. A site that drops it reads the settings
     live through the helper, and the scan for live settings reads does
     not see that. (Break: one site reads the evidence mode without the
     run.) STATED LIMIT: the list of methods is kept by hand; a new method
     that takes the run must be added to it.
PC3  for a run whose calls were all fresh, the word the rate reads and the
     label's flag are the same word, over every mix of main, backup,
     off-list and unnamed models. (Break: the rate's word ignores an
     unnamed model.)
PC4  a second opinion given by a model ON THE BACKUP LIST (the default
     arrangement) sets neither the flag nor the rate's word; a second
     opinion that failed leaves no model behind. (Breaks: a second opinion
     counted as the grader's answers; the second model recorded when it is
     asked, not when its reply is kept.)
PC6  every grading that made a fresh call gives the unknown rate exactly
     one sample: "unknown" a 1, "no" a 0, "yes" a 0. Written before the
     code that makes it so.

Every provider reply has a real string model, or None on purpose.
"""

import ast
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor import services
from ai_processor.grading_run import GradingRun
from ai_processor.services import AIProcessor

MAIN = services.MAIN_MODEL
BACKUP = services.GRADING_FALLBACK_MODELS[0]
OFF_LIST = "someone/else-entirely"
LONG_PAPER = services.GRADING_QUESTIONS_PER_CHUNK + 2

SUMMARY_REPLY = {
    "overall_performance_analysis": "Solid work overall.",
    "grader_meta_analysis": "Consistent.",
    "grading_confidence": 90,
    "recommendations": [],
}

ASSIGNMENT = SimpleNamespace(
    id="0c-assignment", title="A paper", instructions="", custom_ai_prompt=""
)


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


def _answer(number):
    return {"question_number": number, "answer_html": f"<p>Essay {number} answer.</p>"}


def _evaluation(number, score=8, quote=None):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": [quote or f"Essay {number} answer"],
    }


def _is_a_batch_call(kwargs):
    return kwargs.get("response_schema") is services.GRADING_BATCH_RESPONSE_SCHEMA


def _numbers_asked(kwargs):
    text = str(kwargs.get("user_prompt"))
    return [n for n in range(1, LONG_PAPER + 1) if f"Essay question {n}?" in text]


class _Case(TestCase):
    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def grade(self, questions, answers):
        run = GradingRun.start()
        result = self.processor.extract_grade_with_retry(
            MagicMock(), questions, answers, assignment_model=ASSIGNMENT, run=run
        )
        return run, result


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False,
    GRADING_EVIDENCE_ENFORCEMENT=services.MODE_STRICT,
)
class PC1ARejectedWholeReplyIsNotCountedTest(_Case):
    @patch.object(AIProcessor, "execute_graded_task")
    def test_a_backups_reply_rejected_for_its_quote_leaves_nothing(self, mock_execute):
        state = {"rejected": 0, "first_part_calls": 0}

        def reply(**kwargs):
            if not _is_a_batch_call(kwargs):
                return _reply(SUMMARY_REPLY, MAIN)
            asked = _numbers_asked(kwargs)
            if 1 in asked:
                state["first_part_calls"] += 1
                if state["first_part_calls"] == 1:
                    state["rejected"] += 1
                    # Every answer is there; the quotes are not the student's.
                    return _reply(
                        {
                            "question_evaluations": [
                                _evaluation(n, quote="words the student never wrote")
                                for n in asked
                            ]
                        },
                        BACKUP,
                    )
            return _reply(
                {"question_evaluations": [_evaluation(n) for n in asked]}, MAIN
            )

        mock_execute.side_effect = reply
        questions = [_essay(n) for n in range(1, LONG_PAPER + 1)]
        answers = [_answer(n) for n in range(1, LONG_PAPER + 1)]
        run, result = self.grade(questions, answers)

        # The reply really was asked for twice and the paper really was
        # marked in parts, in one attempt (else this shows nothing).
        self.assertEqual(state["rejected"], 1)
        self.assertEqual(state["first_part_calls"], 2)
        self.assertEqual(len(result["question_evaluations"]), LONG_PAPER)

        label = run.label()
        self.assertEqual(label["grading_fallback_used"], "no")
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(run.fresh_backup_used(), "no")
        self.assertEqual(run.audit_models()["models_served"], [MAIN])
        self.assertEqual(len(run.fresh_answers), LONG_PAPER)


#: Methods of the grading service that take the run. Every call to one of
#: them from inside services.py must hand the run on.
TAKES_THE_RUN = {
    "_grading_setting",
    "_evidence_mode",
    "_grading_response_schema",
    "_question_image_content_blocks",
    "_custom_instructions_block",
    "_match_context",
    "_partition_cached",
    "_store_cache_evaluations",
    "_maybe_run_second_opinion",
    "_grade_question_batch",
    "_build_overall_grading_summary",
    "_grade_student_submission_impl",
    "grade_student_submission",
}


def _is_the_run(node):
    """`run`, or `run or GradingRun.start()`."""
    if isinstance(node, ast.Name) and node.id == "run":
        return True
    return (
        isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and isinstance(node.values[0], ast.Name)
        and node.values[0].id == "run"
    )


def _calls_that_drop_the_run(source):
    dropped, seen = [], set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None
        )
        if name not in TAKES_THE_RUN:
            continue
        seen.add(name)
        handed = any(_is_the_run(arg) for arg in node.args) or any(
            keyword.arg == "run" and _is_the_run(keyword.value)
            for keyword in node.keywords
        )
        if not handed:
            dropped.append(f"{name} at line {node.lineno}")
    return dropped, seen


class PC2EveryCallHandsTheRunOnTest(SimpleTestCase):
    def test_no_call_inside_the_grading_service_drops_the_run(self):
        source = Path(services.__file__).read_text()
        dropped, seen = _calls_that_drop_the_run(source)
        # The scan found every name (else a rename would blind it).
        self.assertEqual(seen, TAKES_THE_RUN)
        self.assertEqual(dropped, [])

    def test_the_scan_sees_a_dropped_run(self):
        dropped, _ = _calls_that_drop_the_run(
            "def f(self, run):\n"
            "    self._evidence_mode()\n"
            "    self._evidence_mode(run)\n"
            "    _grading_setting(None, 'X')\n"
            "    self._grade_question_batch(a=1, run=run)\n"
        )
        self.assertEqual(
            dropped, ["_evidence_mode at line 2", "_grading_setting at line 4"]
        )


class PC3TheRatesWordIsTheFlagWhenAllWasFreshTest(SimpleTestCase):
    def test_every_mix_of_fresh_calls(self):
        kinds = [MAIN, BACKUP, OFF_LIST, None]
        mixes = 0
        for size in (1, 2, 3):
            for answers in itertools.combinations_with_replacement(kinds, size):
                for summary in [(), *[(kind,) for kind in kinds]]:
                    run = GradingRun.start()
                    for model in answers:
                        run.keep_answers(model, 1)
                    for model in summary:
                        run.keep_call(model)
                    mixes += 1
                    with self.subTest(answers=answers, summary=summary):
                        self.assertEqual(
                            run.fresh_backup_used(),
                            run.label()["grading_fallback_used"],
                        )
                        self.assertIn(run.fresh_backup_used(), ("yes", "no", "unknown"))
        self.assertGreater(mixes, 100)

    def test_the_words_are_not_all_one_word(self):
        """So the comparison above cannot pass by both sides being constant."""
        seen = set()
        for model in (MAIN, BACKUP, OFF_LIST, None):
            run = GradingRun.start()
            run.keep_answers(model, 1)
            seen.add(run.fresh_backup_used())
        self.assertEqual(seen, {"yes", "no", "unknown"})


SECOND_OPINION_ON = {
    "GRADING_SECOND_OPINION_ENABLED": True,
    "GRADING_SECOND_OPINION_MODELS": [BACKUP],
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 0,
    "GRADING_SECOND_OPINION_HIGH_POINTS": 1,
    "GRADING_SECOND_OPINION_SAMPLE_RATE": 0,
}


class PC4ASecondOpinionByABackupTest(_Case):
    @patch.object(AIProcessor, "execute_graded_task")
    def test_it_sets_neither_the_flag_nor_the_rates_word(self, mock_execute):
        calls = {"second": 0}

        def reply(**kwargs):
            if kwargs.get("override_model"):
                calls["second"] += 1
                return _reply({"question_evaluations": [_evaluation(1, 15)]}, BACKUP)
            return _reply({"question_evaluations": [_evaluation(1, 15)]}, MAIN)

        mock_execute.side_effect = reply
        with override_settings(**SECOND_OPINION_ON):
            run, _ = self.grade([_essay(1, points=20)], [_answer(1)])
        self.assertGreaterEqual(calls["second"], 1)
        label = run.label()
        self.assertEqual(label["grading_fallback_used"], "no")
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(run.fresh_backup_used(), "no")
        self.assertEqual(
            run.audit_models(),
            {
                "models_served": [MAIN],
                "models_reused": [],
                "models_second_opinion": [BACKUP],
            },
        )

    @patch.object(AIProcessor, "execute_graded_task")
    def test_a_second_opinion_that_failed_leaves_no_model(self, mock_execute):
        calls = {"second": 0}

        def reply(**kwargs):
            if kwargs.get("override_model"):
                calls["second"] += 1
                raise RuntimeError("the second model is down")
            return _reply({"question_evaluations": [_evaluation(1, 15)]}, MAIN)

        mock_execute.side_effect = reply
        with override_settings(**SECOND_OPINION_ON):
            run, result = self.grade([_essay(1, points=20)], [_answer(1)])
        self.assertGreaterEqual(calls["second"], 1)
        self.assertIn("error", result.get("second_opinion", {}))
        self.assertEqual(run.audit_models()["models_second_opinion"], [])
        self.assertEqual(run.audit_models()["models_served"], [MAIN])
        self.assertEqual(run.label()["grading_fallback_used"], "no")


class PC6EveryMeasuredGradingGivesTheUnknownRateOneSampleTest(SimpleTestCase):
    """SM ruling, 2026-10-07: the unknown rate is over ALL runs
    that made a fresh call: "unknown" a 1, "no" a 0 and "yes" a 0. The
    backup rate is over known runs: "yes" 1, "no" 0, "unknown" no sample.
    Red against the code as at d37f6a7e, where "yes" gave the unknown
    rate no sample."""

    def samples(self, word):
        from audit import emitter
        from audit.enums import AuditAction, AuditOutcome

        with patch.object(emitter, "audit_metrics") as metrics:
            emitter._emit_alertable_metrics(
                AuditAction.GRADING_COMPLETED,
                AuditOutcome.SUCCESS,
                {"metadata": {"fresh_backup_used": word}},
            )
        return sorted(
            (call.args[0], call.args[1])
            for call in metrics.distribution.call_args_list
            if call.args[0] in ("model_fallback_rate", "model_unknown_rate")
        )

    def test_yes_no_and_unknown_each_give_one_unknown_sample(self):
        self.assertEqual(
            self.samples("no"),
            [("model_fallback_rate", 0.0), ("model_unknown_rate", 0.0)],
        )
        self.assertEqual(self.samples("unknown"), [("model_unknown_rate", 1.0)])
        self.assertEqual(
            self.samples("yes"),
            [("model_fallback_rate", 1.0), ("model_unknown_rate", 0.0)],
        )
