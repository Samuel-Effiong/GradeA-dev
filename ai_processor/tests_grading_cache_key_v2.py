"""BE-I-04 slice B: when a saved AI answer may be reused.

A saved answer is reused only when everything that was sent to the AI for
that question is the same: the whole question, the student's answer, the
assignment's title and instructions, the teacher's extra instructions as
they were spliced in, the grading prompt's version and the grading
settings' version. Before this slice the match looked at six fields of the
question and the answer only, so a teacher who edited the extra
instructions kept getting grades made under the old ones for up to three
days.

Also held here:
  * one reading of the settings per run: the lookup and the store of one
    run use the same reading, across every attempt of the retry loop;
  * a saved answer is stored in an envelope our code writes, with the model
    that answered beside the evaluation;
  * a "graded by" or "from cache" marker that arrives inside the AI's own
    reply is never kept;
  * the temperature of the calls is a named constant and part of the
    settings version.

STATED LIMIT, pinned by a test: the match does not look at the OTHER
questions, the other answers or the answer's place in a batch, all of
which the AI also sees in the same call.

Everything here goes through the grading pipeline with only the provider
call mocked, so the tests do not depend on how the key is built.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase, override_settings

# No second opinion in these classes: its 5% random sample would add a
# provider call now and then, and these tests count calls.
from ai_processor import grading_cache, grading_config, services
from ai_processor.grading_config import GradingConfig
from ai_processor.services import AIProcessor, Prompt

MAIN = services.MAIN_MODEL


def _ai_response(payload, model=MAIN):
    """A provider reply. `model` is a real string, or None on purpose for a
    reply that does not say which model answered (rule 14)."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _essay(number=1, **extra):
    question = {
        "question_number": number,
        "question_text": f"Essay question {number}?",
        "question_type": "ESSAY",
        "points": 10,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": 10},
            {"level": "good", "description": "Good", "points": 8},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": "A model essay.",
    }
    question.update(extra)
    return question


def _answer(number, text):
    return {"question_number": number, "answer_html": text}


def _evaluation(number=1, score=8, **extra):
    evaluation = {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": [f"Essay {number}"],
    }
    evaluation.update(extra)
    return evaluation


def _payload(evaluations):
    return {"question_evaluations": evaluations}


def _assignment(**changes):
    """What the grading code reads from an assignment, as plain values."""
    values = {
        "id": "assignment-1",
        "title": "Photosynthesis essay",
        "instructions": "Answer in full sentences.",
        "custom_ai_prompt": "Always require units.",
    }
    values.update(changes)
    return SimpleNamespace(**values)


ANSWER = [_answer(1, "<p>Essay 1 photosynthesis answer.</p>")]


class _PipelineCase(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def grade(self, question=None, assignment=None, answer=None):
        return self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[question or _essay(1)],
            answer_json=answer or ANSWER,
            assignment_model=assignment or _assignment(),
        )

    def assert_second_grading_is(self, mock_execute, fresh, **second):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade()
        self.assertEqual(mock_execute.call_count, 1)
        self.grade(**second)
        self.assertEqual(
            mock_execute.call_count,
            2 if fresh else 1,
            "the second grading should have been "
            + ("sent to the AI" if fresh else "answered from the saved answer"),
        )


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
@patch.object(AIProcessor, "execute_graded_task")
class WhatMustMatchTest(_PipelineCase):
    """Each of these changes something that is sent to the AI. The saved
    answer was made without it, so it must not be reused."""

    def test_the_same_submission_again_is_reused(self, mock_execute):
        self.assert_second_grading_is(mock_execute, fresh=False)

    def test_edited_teacher_instructions_are_a_fresh_grade(self, mock_execute):
        self.assert_second_grading_is(
            mock_execute,
            fresh=True,
            assignment=_assignment(custom_ai_prompt="Units are optional."),
        )

    def test_teacher_instructions_added_where_there_were_none(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(assignment=_assignment(custom_ai_prompt=""))
        self.grade(assignment=_assignment(custom_ai_prompt="Always require units."))
        self.assertEqual(mock_execute.call_count, 2)

    def test_an_edited_assignment_title_is_a_fresh_grade(self, mock_execute):
        self.assert_second_grading_is(
            mock_execute, fresh=True, assignment=_assignment(title="Respiration essay")
        )

    def test_edited_assignment_instructions_are_a_fresh_grade(self, mock_execute):
        self.assert_second_grading_is(
            mock_execute,
            fresh=True,
            assignment=_assignment(instructions="Bullet points are fine."),
        )

    def test_a_changed_additional_note_on_the_question_is_a_fresh_grade(
        self, mock_execute
    ):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(question=_essay(1, additional_notes="Accept diagrams."))
        self.grade(question=_essay(1, additional_notes="Do not accept diagrams."))
        self.assertEqual(mock_execute.call_count, 2)

    def test_a_changed_blooms_level_on_the_question_is_a_fresh_grade(
        self, mock_execute
    ):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(question=_essay(1, blooms_level="remember"))
        self.grade(question=_essay(1, blooms_level="evaluate"))
        self.assertEqual(mock_execute.call_count, 2)

    def test_a_changed_question_image_is_a_fresh_grade(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(question=_essay(1, question_image="https://img.test/one.png"))
        self.grade(question=_essay(1, question_image="https://img.test/two.png"))
        self.assertEqual(mock_execute.call_count, 2)

    def test_a_field_of_the_question_nobody_listed_is_part_of_the_match(
        self, mock_execute
    ):
        """The WHOLE question as sent, not a list of known fields."""
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(question=_essay(1, some_future_field="one"))
        self.grade(question=_essay(1, some_future_field="two"))
        self.assertEqual(mock_execute.call_count, 2)

    def test_a_changed_grading_setting_is_a_fresh_grade(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=15):
            self.grade()
        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=16):
            self.grade()
        self.assertEqual(mock_execute.call_count, 2)

    def test_a_changed_grading_prompt_is_a_fresh_grade(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade()
        edited = Prompt(
            str(services.GRADING_ASSIGNMENT_PROMPT) + "\nBe kind.",
            "GRADING_ASSIGNMENT_PROMPT_TEST:edited01",
        )
        with patch.object(services, "GRADING_ASSIGNMENT_PROMPT", edited):
            self.grade()
        self.assertEqual(mock_execute.call_count, 2)

    def test_another_assignment_with_the_same_question_is_a_fresh_grade(
        self, mock_execute
    ):
        self.assert_second_grading_is(
            mock_execute, fresh=True, assignment=_assignment(id="assignment-2")
        )


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
@patch.object(AIProcessor, "execute_graded_task")
class WhatMustNotBreakTheMatchTest(_PipelineCase):
    def test_a_new_release_still_reuses_the_saved_answer(self, mock_execute):
        """The release is recorded beside the settings version and is never
        part of the match: a deploy must not empty the store."""
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        with override_settings(GRADING_RELEASE_ID="release-41"):
            self.grade()
        with override_settings(GRADING_RELEASE_ID="release-42"):
            self.grade()
        self.assertEqual(mock_execute.call_count, 1)

    def test_teacher_instructions_that_are_switched_off_do_not_count(
        self, mock_execute
    ):
        """The match is on the instructions AS SPLICED. With the feature
        switched off nothing is spliced, so an edit changes nothing sent."""
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        with override_settings(GRADING_CUSTOM_INSTRUCTIONS_ENABLED=False):
            self.grade(assignment=_assignment(custom_ai_prompt="Always require units."))
            self.grade(assignment=_assignment(custom_ai_prompt="Units are optional."))
        self.assertEqual(mock_execute.call_count, 1)

    def test_whitespace_around_teacher_instructions_does_not_count(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.grade(assignment=_assignment(custom_ai_prompt="Always require units."))
        self.grade(assignment=_assignment(custom_ai_prompt="  Always require units.\n"))
        self.assertEqual(mock_execute.call_count, 1)

    def test_the_other_questions_are_not_part_of_the_match(self, mock_execute):
        """STATED LIMIT. Question 1 and its answer are the same in both
        papers; question 2 differs. Question 1's saved answer is reused
        although the AI first marked it in different company."""
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1), _evaluation(2, score=6)])
        )
        answers = [_answer(1, "<p>Essay 1 answer.</p>"), _answer(2, "<p>Essay 2.</p>")]
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1), _essay(2)],
            answer_json=answers,
            assignment_model=_assignment(),
        )
        mock_execute.return_value = _ai_response(_payload([_evaluation(2, score=5)]))
        second = self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1), _essay(2, model_answer="Another model essay.")],
            answer_json=answers,
            assignment_model=_assignment(),
        )
        self.assertEqual(mock_execute.call_count, 2)
        by_number = {e["question_number"]: e for e in second["question_evaluations"]}
        self.assertTrue(by_number[1].get("from_cache"))
        self.assertNotIn("from_cache", by_number[2])


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class OneReadingPerRunTest(TestCase):
    """The lookup and the store of one run use the same reading of the
    settings, and so does every attempt of the retry loop. A TestCase: a
    failed attempt goes through the refund scope."""

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def run_grading(self):
        return self.processor.extract_grade_with_retry(
            MagicMock(), [_essay(1)], ANSWER, assignment_model=_assignment()
        )

    def test_the_settings_are_read_exactly_once_for_a_run(self):
        with patch.object(
            AIProcessor,
            "execute_graded_task",
            return_value=_ai_response(_payload([_evaluation(1)])),
        ):
            with patch.object(
                GradingConfig, "read", side_effect=GradingConfig.read
            ) as read:
                self.run_grading()
        self.assertEqual(read.call_count, 1)

    def test_a_retried_run_still_reads_them_once(self):
        replies = [
            RuntimeError("the provider timed out"),
            _ai_response(_payload([_evaluation(1)])),
        ]
        with patch.object(AIProcessor, "execute_graded_task", side_effect=replies):
            with patch.object(
                GradingConfig, "read", side_effect=GradingConfig.read
            ) as read:
                result = self.run_grading()
        self.assertEqual(result["grading_summary"]["total_score"], 8)
        self.assertEqual(read.call_count, 1)

    def test_a_setting_changed_during_the_call_does_not_split_lookup_and_store(self):
        """The run starts under one setting; the setting changes while the
        AI is answering; the answer is stored under the reading the run
        STARTED with. A run that re-read the settings at the store would
        file it under the new value, and the next identical submission
        under the old value would miss."""
        changed = override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=16)

        def reply_and_change_the_setting(*args, **kwargs):
            changed.enable()
            return _ai_response(_payload([_evaluation(1)]))

        with override_settings(GRADING_SECOND_OPINION_HIGH_POINTS=15):
            with patch.object(
                AIProcessor,
                "execute_graded_task",
                side_effect=reply_and_change_the_setting,
            ) as first:
                try:
                    self.run_grading()
                finally:
                    changed.disable()
            self.assertEqual(first.call_count, 1)
            with patch.object(
                AIProcessor,
                "execute_graded_task",
                return_value=_ai_response(_payload([_evaluation(1)])),
            ) as second:
                self.run_grading()
            self.assertEqual(second.call_count, 0)


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
@patch.object(AIProcessor, "execute_graded_task")
class TheSavedAnswersEnvelopeTest(_PipelineCase):
    """What is written to the store is an envelope OUR code builds: the
    evaluation, and beside it the model that answered."""

    def stored_values(self, mock_execute, model):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]), model)
        with patch.object(grading_cache, "cache") as store:
            store.get.return_value = None
            self.grade()
        return [call.args[1] for call in store.set.call_args_list]

    def test_the_model_that_answered_is_stored_beside_the_evaluation(
        self, mock_execute
    ):
        values = self.stored_values(mock_execute, "backup/model-x")
        self.assertEqual(len(values), 1)
        self.assertEqual(set(values[0]), {"evaluation", "served_model"})
        self.assertEqual(values[0]["served_model"], "backup/model-x")
        self.assertEqual(values[0]["evaluation"]["question_number"], 1)
        self.assertEqual(values[0]["evaluation"]["score_awarded"], 8)

    def test_a_reply_that_names_no_model_is_stored_with_none(self, mock_execute):
        values = self.stored_values(mock_execute, None)
        self.assertEqual(len(values), 1)
        self.assertIn("served_model", values[0])
        self.assertIsNone(values[0]["served_model"])

    def test_the_keys_are_version_two(self, mock_execute):
        self.assertEqual(grading_cache.CACHE_VERSION, "v2")

    def test_an_entry_that_is_not_an_envelope_is_a_miss(self, mock_execute):
        """Whatever sits under a key and is not our envelope (an older
        shape, a damaged value) is graded fresh, never trusted."""
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        for bad in (
            dict(_evaluation(1), from_cache=True, graded_by=MAIN),
            {"evaluation": "not a dict", "served_model": MAIN},
            {"evaluation": _evaluation(1)},
            "a string",
            ["a", "list"],
        ):
            with self.subTest(bad=bad):
                mock_execute.reset_mock()
                with patch.object(grading_cache, "cache") as store:
                    store.get.return_value = bad
                    result = self.grade()
                self.assertEqual(mock_execute.call_count, 1)
                self.assertNotIn("from_cache", result["question_evaluations"][0])

    def test_a_reused_answer_is_marked_by_our_code_not_by_its_content(
        self, mock_execute
    ):
        """The model shown on a reused answer comes from the envelope's
        `served_model`, never from a marker inside the evaluation."""
        envelope = {
            "evaluation": dict(_evaluation(1), graded_by="the-reply-said-this"),
            "served_model": "backup/model-x",
        }
        with patch.object(grading_cache, "cache") as store:
            store.get.return_value = envelope
            result = self.grade()
        self.assertEqual(mock_execute.call_count, 0)
        evaluation = result["question_evaluations"][0]
        self.assertIs(evaluation.get("from_cache"), True)
        self.assertEqual(evaluation.get("graded_by"), "backup/model-x")


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
@patch.object(AIProcessor, "execute_graded_task")
class AMarkerInsideTheReplyIsNeverKeptTest(_PipelineCase):
    """A "graded by" or "from cache" value written by the AI itself would
    make later code skip the second opinion and the store, and would name
    a grader that did not grade."""

    def test_a_graded_by_in_the_reply_is_replaced_by_the_model_that_answered(
        self, mock_execute
    ):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, graded_by="deterministic")]), "backup/model-x"
        )
        result = self.grade()
        self.assertEqual(
            result["question_evaluations"][0]["graded_by"], "backup/model-x"
        )

    def test_a_from_cache_in_the_reply_is_dropped_and_the_answer_is_stored(
        self, mock_execute
    ):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, from_cache=True)])
        )
        with patch.object(grading_cache, "cache") as store:
            store.get.return_value = None
            result = self.grade()
        self.assertNotIn("from_cache", result["question_evaluations"][0])
        self.assertEqual(store.set.call_count, 1)

    def test_no_place_in_the_grading_service_keeps_a_replys_own_graded_by(
        self, mock_execute
    ):
        source = Path(services.__file__).read_text(encoding="utf-8")
        self.assertNotIn('setdefault("graded_by"', source)


class TheTemperatureIsPartOfTheSettingsVersionTest(SimpleTestCase):
    """SM ruling, 2026-10-06: the release cannot stand in for the
    temperature, because it changes on every deploy."""

    def test_it_is_a_named_constant_of_the_grading_service(self):
        self.assertEqual(getattr(services, "AI_TEMPERATURE", None), 0.0)

    def test_the_call_that_leaves_the_app_uses_the_constant(self):
        source = Path(services.__file__).read_text(encoding="utf-8")
        self.assertIn('"temperature": AI_TEMPERATURE', source)
        self.assertNotRegex(source, r'"temperature":\s*[0-9]')

    def test_it_is_in_the_settings_version(self):
        self.assertIn("AI_TEMPERATURE", grading_config.CODE_CONSTANTS)

    def test_changing_it_changes_the_version(self):
        before = GradingConfig.read().version
        with patch.object(services, "AI_TEMPERATURE", 0.7, create=True):
            after = GradingConfig.read().version
        self.assertNotEqual(after, before)
