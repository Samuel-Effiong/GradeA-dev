"""BE-I-04 slice B, the delta after the Checker's verdict: two properties
that had no test able to fail.

* The answer side's parts of the saved-answer key (text | status | notes)
  cannot run together. The builder's boundary tests covered the parts of
  the context only; a key that joined status and notes into one part
  passed every test.
* One reading per run on the long-paper path. A version-moving setting and
  the teacher-instructions switch both change inside the first chunk's
  call; every later prompt of the run (the second chunk's and the summary
  call's) still carries the teacher's text, and the answers are filed under
  the reading the run started with. Before this, only a direct call of the
  splice helper was tested, so the chunk call's and the summary call's own
  use of the run's reading were unguarded.

Both classes were written by the Next-stage Checker as probes of its own
(both passed at 6ca94c09 in the checker's run) and handed over for adoption
by the Senior Manager's ruling of 2026-10-06. They are as the checker wrote
them; this docstring and the two class names are the builder's.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor import grading_cache, services
from ai_processor.services import AIProcessor

MAIN = services.MAIN_MODEL
CHUNK = services.GRADING_QUESTIONS_PER_CHUNK
LONG = CHUNK + 2


def reply(payload, model=MAIN):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def essay(number=1, **extra):
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


def evaluation(number=1, score=8, **extra):
    value = {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": [f"Essay {number}"],
    }
    value.update(extra)
    return value


def assignment(**changes):
    values = {
        "id": "probe-assignment",
        "title": "Probe title",
        "instructions": "Probe instructions.",
        "custom_ai_prompt": "Always require units.",
    }
    values.update(changes)
    return SimpleNamespace(**values)


SUMMARY = {
    "overall_performance_analysis": "Solid work overall.",
    "grader_meta_analysis": "Consistent scoring.",
    "grading_confidence": 90,
    "recommendations": ["Review question 3."],
}


def long_paper():
    questions = [essay(n) for n in range(1, LONG + 1)]
    answers = [
        {"question_number": n, "answer_html": f"<p>Essay {n} answer.</p>"}
        for n in range(1, LONG + 1)
    ]
    return questions, answers


def chunked(model=MAIN, on_first_call=None, seen=None, **marker):
    state = {"calls": 0}

    def respond(**kwargs):
        state["calls"] += 1
        if seen is not None:
            seen.append(kwargs)
        if state["calls"] == 1 and on_first_call is not None:
            on_first_call()
        if kwargs.get("response_schema") is services.GRADING_BATCH_RESPONSE_SCHEMA:
            return reply(
                {
                    "question_evaluations": [
                        dict(
                            evaluation(n),
                            evidence_quotes=[f"Essay {n} answer"],
                            **marker,
                        )
                        for n in range(1, LONG + 1)
                    ]
                },
                model,
            )
        return reply(SUMMARY, model)

    return respond


class TheAnswerSidesPartsCannotRunTogetherTest(SimpleTestCase):
    """The builder's boundary tests cover the context's parts. These are
    the answer side's neighbours: text | status | notes."""

    CONTEXT = grading_cache.MatchContext(
        assignment_id="a",
        assignment_title="t",
        assignment_instructions="i",
        custom_instructions="c",
        prompt_version="P:0",
        config_version="cfg:0",
    )

    def key(self, text, status, notes):
        return grading_cache.build_cache_key(
            essay(1),
            text,
            model_name="m",
            context=self.CONTEXT,
            answer_status=status,
            transcription_notes=notes,
        )

    def test_status_and_notes_cannot_run_together(self):
        self.assertNotEqual(self.key("x", "ab", "c"), self.key("x", "a", "bc"))
        self.assertNotEqual(self.key("x", "ab", ""), self.key("x", "a", "b"))
        self.assertNotEqual(self.key("x", "ab", None), self.key("x", "", "ab"))

    def test_text_and_status_cannot_run_together(self):
        self.assertNotEqual(self.key("xa", "b", "n"), self.key("x", "ab", "n"))
        self.assertNotEqual(self.key("xab", "", "n"), self.key("x", "ab", "n"))


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class OneReadingPerRunOnALongPaperTest(TestCase):
    """One reading per run on the chunked path: a version-moving setting and
    the teacher-instructions switch both change inside the first chunk's
    call. Every later prompt of the run still carries the teacher's text,
    and the answers are filed under the reading the run started with."""

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def run_grading(self):
        questions, answers = long_paper()
        return self.processor.extract_grade_with_retry(
            MagicMock(), questions, answers, assignment_model=assignment()
        )

    def test_the_run_keeps_its_starting_reading(self):
        changed = override_settings(
            GRADING_SECOND_OPINION_HIGH_POINTS=16,
            GRADING_CUSTOM_INSTRUCTIONS_ENABLED=False,
        )
        seen = []
        with override_settings(
            GRADING_SECOND_OPINION_HIGH_POINTS=15,
            GRADING_CUSTOM_INSTRUCTIONS_ENABLED=True,
        ):
            with patch.object(
                AIProcessor,
                "execute_graded_task",
                side_effect=chunked(on_first_call=changed.enable, seen=seen),
            ) as first:
                try:
                    self.run_grading()
                finally:
                    changed.disable()
            self.assertEqual(first.call_count, 3)
            for number, kwargs in enumerate(seen, 1):
                prompt_text = json.dumps(kwargs, default=str)
                with self.subTest(call=number):
                    self.assertIn("Always require units.", prompt_text)
            with patch.object(
                AIProcessor, "execute_graded_task", side_effect=chunked()
            ) as second:
                self.run_grading()
            self.assertEqual(second.call_count, 0)
