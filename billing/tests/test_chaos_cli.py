"""
billing/tests/test_chaos_cli.py
=================================
Wiring for `manage.py run_stripe_live_qa --chaos`: argument validation
and dispatch to chaos.run_chaos_walk / chaos.shrink_chaos_failure. The
walk and shrinker logic themselves are covered in test_chaos.py; this
file only proves the command line hands them the right arguments and
turns their result into the right exit behaviour.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from billing.live_qa.chaos import ChaosWalkResult, ExecutedStep, ShrinkResult


class ChaosCliTests(TestCase):
    def _run(self, *args, **kwargs):
        out = StringIO()
        call_command(
            "run_stripe_live_qa", *args, stdout=out, stderr=StringIO(), **kwargs
        )
        return out.getvalue()

    def test_chaos_without_seed_is_a_command_error(self):
        with self.assertRaises(CommandError):
            self._run("--chaos")

    def test_chaos_calls_run_chaos_walk_with_seed_and_steps(self):
        passing = ChaosWalkResult(seed=7, steps=12)
        passing.executed = [ExecutedStep(index=0, action="advance_boundary", note="ok")]
        with patch(
            "billing.live_qa.chaos.run_chaos_walk", return_value=passing
        ) as mock_walk:
            self._run("--chaos", "--seed", "7", "--steps", "12")
        mock_walk.assert_called_once_with(7, 12)

    def test_a_passing_walk_does_not_raise(self):
        passing = ChaosWalkResult(seed=1, steps=5)
        with patch("billing.live_qa.chaos.run_chaos_walk", return_value=passing):
            # Must not raise.
            self._run("--chaos", "--seed", "1", "--steps", "5")

    def test_a_failing_walk_raises_command_error(self):
        failing = ChaosWalkResult(seed=1, steps=5, infra_error="boom")
        with patch("billing.live_qa.chaos.run_chaos_walk", return_value=failing):
            with self.assertRaises(CommandError):
                self._run("--chaos", "--seed", "1", "--steps", "5")

    def test_default_steps_is_thirty(self):
        passing = ChaosWalkResult(seed=1, steps=30)
        with patch(
            "billing.live_qa.chaos.run_chaos_walk", return_value=passing
        ) as mock_walk:
            self._run("--chaos", "--seed", "1")
        mock_walk.assert_called_once_with(1, 30)

    def test_shrink_calls_shrink_chaos_failure_not_run_chaos_walk(self):
        shrink_result = ShrinkResult(seed=1, original_steps=20, minimal_steps=6)
        with patch(
            "billing.live_qa.chaos.shrink_chaos_failure", return_value=shrink_result
        ) as mock_shrink, patch("billing.live_qa.chaos.run_chaos_walk") as mock_walk:
            self._run("--chaos", "--seed", "1", "--steps", "20", "--shrink")
        mock_shrink.assert_called_once_with(1, 20)
        mock_walk.assert_not_called()

    def test_shrink_with_no_repro_is_a_command_error(self):
        shrink_result = ShrinkResult(seed=1, original_steps=20, minimal_steps=None)
        with patch(
            "billing.live_qa.chaos.shrink_chaos_failure", return_value=shrink_result
        ):
            with self.assertRaises(CommandError):
                self._run("--chaos", "--seed", "1", "--steps", "20", "--shrink")

    def test_shrink_that_finds_a_minimum_does_not_raise(self):
        shrink_result = ShrinkResult(seed=1, original_steps=20, minimal_steps=4)
        with patch(
            "billing.live_qa.chaos.shrink_chaos_failure", return_value=shrink_result
        ):
            # Must not raise -- a successful shrink is not itself a failure.
            self._run("--chaos", "--seed", "1", "--steps", "20", "--shrink")
