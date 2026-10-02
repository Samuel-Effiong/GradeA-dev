"""
Golden-master regression tripwire for the EXTRACTION benchmark.

WHY THIS EXISTS

The extraction prompt is over 11,000 tokens of hand-written English, and
until this benchmark the only way to know whether an edit to it helped or
hurt was to pay for a live run and read the JSON. That is a check people
run when they remember, which in practice means after something has
already broken.

Replay makes it free and deterministic: recorded model responses in, our
own pipeline in between, an exact-equality assertion out. Any difference
at all is a change WE made — the model cannot drift, because it never
runs. That makes the assertion both safe and extremely sensitive.

WHAT IT CATCHES THAT LIVE RUNS DO NOT

A live run measures the model. This measures everything AROUND the model:
the renderer that builds the document, the ProseMirror conversion, the
scorer, and the dataset. A refactor that quietly stopped emitting the
question-type label, or that broke rubric table rendering, would leave a
live run looking fine (the model would just do worse, within noise) while
failing here immediately and specifically.

REGENERATING THE SNAPSHOT

Only when the numbers are MEANT to move — new recordings, a corrected
ground truth, an intentional scoring change:

    UPDATE_EXTRACTION_GOLDEN=1 python manage.py test \\
        ai_processor.tests_extraction_benchmark_golden

Then read `git diff` on the fixture and satisfy yourself that every change
is one you intended. Never regenerate to make a red test go green — the
whole value of this file is that it disagrees with you.

Run with:
    python manage.py test ai_processor.tests_extraction_benchmark_golden
"""

import json
import os
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.test import TestCase
from django.utils import timezone

from ai_processor.benchmark.extraction_runner import (
    EXTRACTION_RECORDINGS_DIR,
    execute_extraction_benchmark,
)
from ai_processor.benchmark.extraction_scoring import METRICS
from ai_processor.benchmark.runner import MODE_REPLAY

GOLDEN_PATH = (
    Path(__file__).resolve().parent / "benchmark" / "golden" / "extraction_replay.json"
)
HAS_RECORDINGS = (EXTRACTION_RECORDINGS_DIR / "responses.json.gz").exists()


def make_benchmark_teacher():
    """
    A teacher the benchmark can execute as.

    Replay spends no credits, but it still runs the tier check and credit
    estimation that wrap every model call (see
    extraction_benchmark._resolve_user), so a real subscribed user with a
    wallet has to exist. Mirrors BenchmarkFixtureMixin._make_teacher in
    tests_grading_benchmark.py.
    """
    from billing.models import (
        BillingInterval,
        CreditBucket,
        CreditBucketType,
        CreditWallet,
        PlanCategory,
        PlanTier,
        SubscriptionPlan,
        UserSubscription,
    )
    from users.models import CustomUser, UserTypes

    teacher = CustomUser.objects.create_user(
        email=f"extraction-golden-{timezone.now().timestamp()}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        name=f"extraction-golden-{teacher.id}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=5_000_000,
        carry_over_percent=0,
        is_active=True,
    )
    now = timezone.now()
    UserSubscription.objects.create(
        user=teacher,
        plan=plan,
        is_active=True,
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        is_trial=False,
        auto_renew=False,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=5_000_000,
        used_credits=0,
        expires_at=now + timedelta(days=30),
    )
    return teacher


def snapshot(run):
    """
    The comparable part of a run.

    Wall-clock timings and token counts are excluded: elapsed_seconds is
    machine-dependent, and while replayed token counts ARE stable they
    describe the recording rather than our behaviour, so pinning them
    would make a harmless re-record fail this test for no reason.
    """
    return {
        "cases": run["cases"],
        "cases_passed": run["cases_passed"],
        "overall": run["overall"],
        "weakest_metric": run["weakest_metric"],
        "passed": run["passed"],
        "rates": {metric: run["rates"][metric] for metric in METRICS},
        "counts": {metric: run["counts"][metric] for metric in METRICS},
        "strict_failures": sorted(run["strict_failures"]),
        "per_case": sorted(
            (
                {
                    "case": result["case"],
                    "passed": result["passed"],
                    "expected_questions": result["expected_questions"],
                    "actual_questions": result["actual_questions"],
                    "strict_failures": sorted(result["strict_failures"]),
                    "metrics": [
                        {
                            "question_number": entry["question_number"],
                            "found": entry["found"],
                            "metrics": entry["metrics"],
                        }
                        for entry in result["per_question"]
                    ],
                }
                for result in run["results"]
            ),
            key=lambda item: item["case"],
        ),
    }


@skipUnless(
    HAS_RECORDINGS,
    "No extraction recordings committed; run "
    "`manage.py extraction_benchmark --mode record` (billed) first.",
)
class ExtractionGoldenTest(TestCase):
    def _run(self):
        return execute_extraction_benchmark(
            make_benchmark_teacher(),
            mode=MODE_REPLAY,
            recordings_dir=EXTRACTION_RECORDINGS_DIR,
        )

    def test_replay_matches_the_committed_snapshot(self):
        current = snapshot(self._run())

        if os.environ.get("UPDATE_EXTRACTION_GOLDEN"):
            GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
            self.skipTest(f"Rewrote {GOLDEN_PATH}. Review the diff.")

        self.assertTrue(
            GOLDEN_PATH.exists(),
            f"No golden snapshot at {GOLDEN_PATH}. Create it with "
            "UPDATE_EXTRACTION_GOLDEN=1.",
        )
        expected = json.loads(GOLDEN_PATH.read_text())
        self.assertEqual(
            current,
            expected,
            "Extraction benchmark replay no longer matches the committed "
            "snapshot. Replay serves fixed recorded responses, so this is a "
            "change in OUR code, never the model drifting.",
        )

    def test_replay_makes_no_network_calls(self):
        # The property that makes this runnable in CI. If the tape ever
        # falls through to a live call, the suite starts spending money
        # silently — so prove the transport is never reached.
        from unittest.mock import patch

        with patch("openai.OpenAI") as client:
            self._run()
        client.assert_not_called()

    def test_replay_is_deterministic_within_a_process(self):
        self.assertEqual(snapshot(self._run()), snapshot(self._run()))

    def test_every_case_is_covered_by_the_recordings(self):
        # A recording set that has drifted from the dataset would raise
        # MissingRecordingError per case and surface as an extraction
        # failure. Assert none did, so a stale recording is reported as
        # itself rather than as a mysterious accuracy drop.
        run = self._run()
        for result in run["results"]:
            with self.subTest(case=result["case"]):
                self.assertIsNone(result.get("error"))
