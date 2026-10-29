"""
H-256: every request to the AI provider (`AIProcessor.client`, OpenRouter
through the OpenAI SDK 1.107.1) waits at most 10 s to connect and
AI_PROVIDER_TIMEOUT_SECONDS (default 300 s) for each read, and is tried at
most three times (max_retries=2) before the call fails.

Before, the client took the SDK's own defaults: 600 s per read, three tries,
so one call that never answered held a grading worker for up to 30 minutes,
past the grading task's own 25-minute kill point.

A call that times out is never charged (the charge follows the reply), and a
run that fails on one refunds what it charged before (billing's refund scope);
the last test drives that through the real client with the timeout raised
where the network would raise it.

Run with:
    python manage.py test ai_processor.tests_h256_provider_timeout
"""

import json
from unittest.mock import patch

import httpx
import openai
from django.test import SimpleTestCase, override_settings

from ai_processor.services import AIProcessor
from ai_processor.tests_grading_pipeline import (
    ANSWERS_12Q,
    RUBRIC_12Q,
    GradingPipelineTestBase,
    _batch_evaluations_json,
)
from billing.models import CreditUsageLog

# The reads, writes and pool waits share the setting; the connect wait is
# fixed: a host that does not take the connection in 10 s is down.
TIMEOUT_AT_THE_DEFAULT = {"connect": 10.0, "read": 300.0, "write": 300.0, "pool": 300.0}


def reply(request, content, tokens):
    """A chat completion as the provider sends it, parsed by the SDK itself."""
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "gen-h256",
            "object": "chat.completion",
            "created": 0,
            "model": "test/model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": tokens - 1,
                "completion_tokens": 1,
                "total_tokens": tokens,
            },
        },
    )


class ProviderStandIn:
    """Stands in for the HTTP layer under the SDK: each request the SDK sends
    is kept, and answered by `answer(request, number)` (1-based). The SDK's
    own retry, its timeout and its error mapping all run for real."""

    def __init__(self, processor, answer):
        self.requests = []
        self.answer = answer
        self.patches = [
            patch.object(processor.client._client, "send", side_effect=self.send),
            # The SDK sleeps 0.5 to 8 s between tries.
            patch("openai._base_client.time.sleep"),
        ]

    def send(self, request, **kwargs):
        self.requests.append(request)
        return self.answer(request, len(self.requests))

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def never_answers(request, number):
    raise httpx.ReadTimeout("The read operation timed out", request=request)


def ask(processor):
    return processor.get_ai_model_function()(
        system_prompt="s", user_prompt="u", task_type="grade_assignment"
    )


class ProviderTimeoutTests(SimpleTestCase):
    def test_every_try_carries_the_timeout(self):
        processor = AIProcessor()
        with ProviderStandIn(processor, never_answers) as provider:
            with self.assertRaises(openai.APITimeoutError):
                ask(processor)
        self.assertTrue(provider.requests)
        for request in provider.requests:
            self.assertEqual(request.extensions["timeout"], TIMEOUT_AT_THE_DEFAULT)

    @override_settings(AI_PROVIDER_TIMEOUT_SECONDS=42)
    def test_the_read_timeout_follows_the_setting(self):
        processor = AIProcessor()
        with ProviderStandIn(processor, never_answers) as provider:
            with self.assertRaises(openai.APITimeoutError):
                ask(processor)
        self.assertEqual(
            provider.requests[0].extensions["timeout"],
            {"connect": 10.0, "read": 42.0, "write": 42.0, "pool": 42.0},
        )

    def test_a_provider_that_never_answers_is_tried_three_times(self):
        processor = AIProcessor()
        self.assertEqual(processor.client.max_retries, 2)
        with ProviderStandIn(processor, never_answers) as provider:
            with self.assertRaises(openai.APITimeoutError):
                ask(processor)
        self.assertEqual(len(provider.requests), 3)


class TimedOutRunRefundsTest(GradingPipelineTestBase):
    def test_a_run_that_times_out_refunds_what_it_charged(self):
        teacher = self._make_teacher_with_credits()
        wallet = teacher.credit_wallet
        starting_balance = wallet.total_remaining_credits()

        def first_batch_then_silence(request, number):
            # The first request is the first batch of questions: answered and
            # charged. Every request after it times out.
            if number == 1:
                return reply(request, _batch_evaluations_json(1), tokens=1_000)
            return never_answers(request, number)

        processor = AIProcessor()
        with ProviderStandIn(processor, first_batch_then_silence) as provider:
            with self.assertRaises(Exception) as caught:
                processor.grade_student_submission(teacher, RUBRIC_12Q, ANSWERS_12Q)

        chain: list[type] = []
        error: BaseException | None = caught.exception
        while error is not None and len(chain) < 10:
            chain.append(type(error))
            error = error.__cause__ or error.__context__
        self.assertIn(openai.APITimeoutError, chain)
        self.assertGreater(len(provider.requests), 1)

        charges = CreditUsageLog.objects.filter(wallet=wallet)
        self.assertEqual(charges.count(), 1, "a call that timed out is never charged")
        self.assertEqual(charges.get().amount, 1_000)
        self.assertTrue(charges.get().is_refunded)
        wallet.refresh_from_db()
        self.assertEqual(wallet.total_remaining_credits(), starting_balance)


class RequestShapeTest(SimpleTestCase):
    """The stand-in answers the shape the pipeline reads (a control: green
    before and after the change)."""

    def test_the_stand_in_reply_is_a_chat_completion(self):
        processor = AIProcessor()
        with ProviderStandIn(processor, lambda r, n: reply(r, "{}", tokens=7)):
            response = ask(processor)
        self.assertEqual(response.usage.total_tokens, 7)
        self.assertEqual(json.loads(response.choices[0].message.content), {})
