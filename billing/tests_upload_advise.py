"""Upload quote, slice 1: the quote's figures in the grading estimate's
shape (plan rev 3, section 4; BE-B-03, BE-B-04, BE-B-09).

p90 is the sum of the files' door figures. p50 is p90 divided by the measured
overestimate of the door's figure on successful extraction calls made after
the headroom changed (CALL_HEADROOM_SINCE), at least 30 calls of the task
type, else the measured 1.46. p50 never exceeds p90. Display credits: cost up,
balance down. A file already uploaded to the course counts 0.
"""

import hashlib
import importlib
import json
from datetime import timedelta
from typing import Any

from django.test import TestCase

from ai_processor.models import AICallRecord
from ai_processor.tests_real_chunked_extraction import make_teacher_with_credits
from billing.models import CONVERSION_FACTOR


def est() -> Any:
    return importlib.import_module("billing.estimates")


def make_call(task_type, estimate, total, *, created=None, outcome="charged"):
    row = AICallRecord.objects.create(
        task_type=task_type,
        feature="Upload",
        outcome=outcome,
        model_asked="m",
        prompt_version="v",
        estimate=estimate,
        total_tokens=total,
        charged_credits=total if outcome == "charged" else 0,
    )
    if created is not None:
        AICallRecord.objects.filter(pk=row.pk).update(created_at=created)
    return row


class Divisor(TestCase):
    def test_few_calls_use_the_measured_constant(self):
        divisor, sample = est().upload_overestimate("extract_answer")
        self.assertEqual((divisor, sample), (est().UPLOAD_DIVISOR_FALLBACK, 0))
        self.assertEqual(est().UPLOAD_DIVISOR_FALLBACK, 1.46)

    def test_thirty_calls_give_their_median(self):
        for _ in range(30):
            make_call("extract_answer", 20_000, 10_000)  # 2.0x each
        divisor, sample = est().upload_overestimate("extract_answer")
        self.assertEqual(sample, 30)
        self.assertAlmostEqual(divisor, 2.0)

    def test_twenty_nine_are_not_enough(self):
        for _ in range(29):
            make_call("extract_answer", 20_000, 10_000)
        divisor, sample = est().upload_overestimate("extract_answer")
        self.assertEqual(divisor, est().UPLOAD_DIVISOR_FALLBACK)

    def test_only_calls_after_the_headroom_change_count(self):
        before = est().CALL_HEADROOM_SINCE - timedelta(days=1)
        for _ in range(30):
            make_call("extract_answer", 24_000, 10_000, created=before)
        _, sample = est().upload_overestimate("extract_answer")
        self.assertEqual(sample, 0)

    def test_only_successful_charged_calls_with_both_numbers_count(self):
        for _ in range(30):
            make_call("extract_answer", 20_000, 10_000, outcome="provider_error")
        make_call("extract_answer", None, 10_000)
        make_call("extract_answer", 20_000, None)
        _, sample = est().upload_overestimate("extract_answer")
        self.assertEqual(sample, 0)

    def test_each_task_type_has_its_own_divisor(self):
        for _ in range(30):
            make_call("extract_assignment", 30_000, 10_000)
        divisor, _ = est().upload_overestimate("extract_answer")
        self.assertEqual(divisor, est().UPLOAD_DIVISOR_FALLBACK)
        divisor, sample = est().upload_overestimate("extract_assignment")
        self.assertEqual(sample, 30)
        self.assertAlmostEqual(divisor, 3.0)

    def test_headroom_digest_guard(self):
        """CALL_HEADROOM changed without CALL_HEADROOM_SINCE changing: older
        records would give a p50 about 2.4x too low (rev 2 T8). Edit the dict,
        move the date, update this digest."""
        call_estimates = importlib.import_module("ai_processor.call_estimates")
        digest = hashlib.sha256(
            json.dumps(call_estimates.CALL_HEADROOM, sort_keys=True).encode()
        ).hexdigest()
        self.assertEqual(digest, est().CALL_HEADROOM_DIGEST)
        self.assertEqual(digest, PINNED_HEADROOM_DIGEST)


PINNED_HEADROOM_DIGEST = "82e304f9f71068ddea2c1b18a39dae9a6749c8021b46b92de570da88b108c52c"  # pragma: allowlist secret


class Advise(TestCase):
    def setUp(self):
        self.teacher = make_teacher_with_credits("quote-advise@example.com")

    def spend(self, spendable):
        from billing.models import CreditWallet

        return CreditWallet.objects.get(user=self.teacher)

    def advise(self, figures, **kwargs):
        return est().upload_advise(self.teacher, "extract_answer", figures, **kwargs)

    def test_the_ten_keys(self):
        result = self.advise([CONVERSION_FACTOR * 10])
        self.assertEqual(
            set(result),
            {
                "p50",
                "p90",
                "verdict",
                "spendable",
                "projected_remaining_p50",
                "projected_remaining_p90",
                "source",
                "shape_basis",
                "sample_size",
                "n",
            },
        )
        self.assertEqual(result["shape_basis"], "upload_files")

    def test_p90_is_the_sum_rounded_up_once_in_display_credits(self):
        result = self.advise([CONVERSION_FACTOR * 3 + 1, CONVERSION_FACTOR * 2 + 1])
        # raw 5*F + 2 -> display 6 (rounded up once, on the total)
        self.assertEqual(result["p90"], 6)
        self.assertEqual(result["n"], 2)

    def test_p50_is_p90_over_the_divisor_and_never_above_p90(self):
        result = self.advise([CONVERSION_FACTOR * 146])
        self.assertEqual(result["p90"], 146)
        self.assertEqual(result["p50"], 100)  # 146 / 1.46
        self.assertEqual(result["source"], "rough")
        self.assertLessEqual(result["p50"], result["p90"])

    def test_source_says_upload_history_with_enough_calls(self):
        for _ in range(30):
            make_call("extract_answer", 20_000, 10_000)
        result = self.advise([CONVERSION_FACTOR * 100])
        self.assertEqual(result["source"], "upload_history")
        self.assertEqual(result["sample_size"], 30)
        self.assertEqual(result["p50"], 50)

    def test_a_divisor_below_one_clamps_p50_to_p90(self):
        for _ in range(30):
            make_call("extract_answer", 5_000, 10_000)
        result = self.advise([CONVERSION_FACTOR * 100])
        self.assertEqual(result["p50"], result["p90"])

    def test_already_uploaded_counts_zero(self):
        result = self.advise(
            [CONVERSION_FACTOR * 10, CONVERSION_FACTOR * 10], already_uploaded={1}
        )
        self.assertEqual(result["p90"], 10)

    def test_a_file_without_a_figure_is_left_out_of_n(self):
        result = self.advise([CONVERSION_FACTOR * 10, None])
        self.assertEqual(result["n"], 1)

    def test_verdict_from_the_display_figures(self):
        wallet = self.spend(0)
        display = wallet.total_remaining_credits() // CONVERSION_FACTOR
        small = self.advise([CONVERSION_FACTOR])
        self.assertEqual(small["verdict"], "SUFFICIENT")
        big = self.advise([CONVERSION_FACTOR * (display + 1000)])
        self.assertEqual(big["verdict"], "INSUFFICIENT")
        self.assertEqual(big["spendable"], display)
        self.assertEqual(big["projected_remaining_p90"], display - big["p90"])
