"""B1b-4 (NOTES §4): the estimate band, its tiers and the verdict (plan
§3.6, §9 item 6; SM rulings Q1-Q3, 2026-11-09)."""

import importlib
import math
from types import SimpleNamespace
from unittest.mock import patch

from django.db.models import QuerySet
from django.test import TestCase

from ai_processor.models import AICallRecord
from ai_processor.services import AIProcessor
from audit.models import AuditEvent
from billing.models import AIJob, BillingSettings, CreditUsageLog, CreditWallet
from billing.testing.holds import ai_ready_teacher
from billing.tests.testing_estimates import make_assignment, paper, papers
from students.models import BackgroundTaskStatus, StudentSubmission
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"

# importlib (the B1a precedent, NOTES §21 step 3): mypy stays quiet about
# names that do not exist until the product commit; the import still fails
# at the red.
estimates = importlib.import_module("billing.estimates")


def teacher(tag):
    return ai_ready_teacher(f"est.{tag}@example.com", 10_000)


class PaperCostTests(TestCase):
    """Q1: a paper's cost is the sum of its charged grading calls, for task
    rows that ended SUCCESS. Failed and refunded calls, and papers whose task
    row did not succeed, are left out."""

    def setUp(self):
        self.teacher = teacher("cost")
        self.assignment = make_assignment(self.teacher)

    def costs(self):
        return sorted(
            row["cost"]
            for row in estimates.paper_costs(assignment_id=self.assignment.pk)
        )

    def test_a_paper_costs_the_sum_of_its_charged_calls(self):
        paper(self.teacher, self.assignment, 300, calls=3)
        self.assertEqual(self.costs(), [300])

    def test_a_failed_call_is_left_out_of_the_paper_cost(self):
        task = paper(self.teacher, self.assignment, 200)
        AICallRecord.objects.create(
            task_type="grade_assignment",
            feature="Grading Assignment",
            outcome="provider_error",
            charged_credits=999,
            assignment_id=self.assignment.pk,
            billed_user_id=self.teacher.pk,
            processing_task_id=task.pk,
        )
        self.assertEqual(self.costs(), [200])

    def test_a_refunded_call_is_left_out_of_the_paper_cost(self):
        task = paper(self.teacher, self.assignment, 200)
        refunded = AICallRecord.objects.create(
            task_id="refunded-call",
            task_type="grade_assignment",
            feature="Grading Assignment",
            outcome="charged",
            charged_credits=500,
            assignment_id=self.assignment.pk,
            billed_user_id=self.teacher.pk,
            processing_task_id=task.pk,
        )
        wallet = CreditWallet.objects.get(user=self.teacher)
        CreditUsageLog.objects.create(
            wallet=wallet,
            bucket=wallet.buckets.get(),
            amount=refunded.charged_credits,
            feature="Grading Assignment",
            task_type="grade_assignment",
            task_id="refunded-call",
            is_refunded=True,
        )
        self.assertEqual(self.costs(), [200])

    def test_a_paper_whose_task_row_did_not_succeed_is_left_out(self):
        paper(self.teacher, self.assignment, 100)
        for status in (
            BackgroundTaskStatus.FAILURE,
            BackgroundTaskStatus.PENDING,
            BackgroundTaskStatus.STARTED,
            BackgroundTaskStatus.CANCELLED,
        ):
            paper(self.teacher, self.assignment, 700, status=status)
        self.assertEqual(self.costs(), [100])

    def test_history_older_than_ninety_days_is_left_out(self):
        paper(self.teacher, self.assignment, 100, days_ago=89)
        paper(self.teacher, self.assignment, 700, days_ago=91)
        self.assertEqual(self.costs(), [100])

    def test_the_reads_return_plain_values_never_rows_or_querysets(self):
        paper(self.teacher, self.assignment, 100)
        rows = estimates.paper_costs(assignment_id=self.assignment.pk)
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIs(type(row), dict)
            for value in row.values():
                self.assertNotIsInstance(value, (QuerySet, AICallRecord))


PLAIN = (dict, list, tuple, int, float, str, bool, type(None))


def assert_plain(test, value, where):
    test.assertIsInstance(value, PLAIN, where)
    if isinstance(value, dict):
        for key, item in value.items():
            assert_plain(test, item, f"{where}[{key!r}]")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_plain(test, item, f"{where}[{index}]")


class OnlyPlainValuesTests(TestCase):
    """SM ruling 2026-11-08: billing/estimates.py may read AICallRecord, but
    its public functions return numbers and plain dicts only, never rows or
    querysets."""

    def test_every_public_function_returns_plain_values(self):
        user = teacher("plain")
        assignment = make_assignment(user, "Plain")
        papers(user, assignment, [100] * 10)
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=10):
            results = {
                "paper_costs": estimates.paper_costs(assignment_id=assignment.pk),
                "band": estimates.band([1, 2, 3], 3),
                "estimate": estimates.estimate(assignment, 3, teacher=user),
                "verdict": estimates.verdict(1, 2, 3),
                "advise": estimates.advise(user, assignment, 3),
                "past_batches": estimates.past_batches(days=90),
                "backtest": estimates.backtest(days=90),
                "cold_start_ratio": estimates.cold_start_ratio(days=90),
                "drift_reasons": estimates.drift_reasons(estimates.backtest(days=90)),
                "shape_of": estimates.shape_of(assignment),
                "spendable_of": estimates.spendable_of(user),
                "question_bucket": estimates.question_bucket(3),
                "size_band": estimates.size_band(3),
                "emit_drift_metric": estimates.emit_drift_metric(["a reason"]),
                "drift_check": estimates.drift_check(),
            }
        public = {
            name
            for name in dir(estimates)
            if not name.startswith("_")
            and callable(getattr(estimates, name))
            and getattr(getattr(estimates, name), "__module__", "")
            == "billing.estimates"
        }
        self.assertEqual(
            public - set(results),
            set(),
            "a new public function needs a plain-values check here",
        )
        for name, value in results.items():
            assert_plain(self, value, name)


class BandTests(TestCase):
    """The band of a batch of n papers from one source's paper costs."""

    def test_p50_is_n_times_the_median_paper(self):
        band = estimates.band([100, 200, 300, 400, 500], 4)
        self.assertEqual(band["p50"], 4 * 300)

    def test_below_ten_papers_p90_is_n_times_the_p90_paper(self):
        costs = list(range(10, 110, 10))  # 10..100, nearest-rank p90 = 90
        band = estimates.band(costs, 9)
        self.assertEqual(band["p90"], 9 * 90)

    def test_from_ten_papers_p90_is_n_mean_plus_1_28_sigma_root_n(self):
        costs = list(range(10, 110, 10))
        mean = sum(costs) / len(costs)
        sigma = math.sqrt(sum((c - mean) ** 2 for c in costs) / len(costs))
        band = estimates.band(costs, 16)
        self.assertEqual(band["p90"], math.ceil(16 * mean + 1.28 * sigma * 4))

    def test_one_hundred_papers_use_root_n_not_n(self):
        costs = [100, 300] * 20
        band = estimates.band(costs, 100)
        # mean 200, sigma 100: 100*200 + 1.28*100*10 = 21,280 (sigma*n: 32,800)
        self.assertEqual(band["p90"], 21_280)

    def test_no_papers_is_a_zero_band(self):
        band = estimates.band([100, 200], 0)
        self.assertEqual((band["p50"], band["p90"]), (0, 0))


class VerdictTests(TestCase):
    """FR-B-04's boundaries (M-B8)."""

    def test_exactly_p90_is_sufficient(self):
        self.assertEqual(estimates.verdict(500, 900, 900), "SUFFICIENT")

    def test_one_below_p90_is_marginal(self):
        self.assertEqual(estimates.verdict(500, 900, 899), "MARGINAL")

    def test_exactly_p50_is_marginal(self):
        self.assertEqual(estimates.verdict(500, 900, 500), "MARGINAL")

    def test_one_below_p50_is_insufficient(self):
        self.assertEqual(estimates.verdict(500, 900, 499), "INSUFFICIENT")

    def test_zero_is_insufficient(self):
        self.assertEqual(estimates.verdict(500, 900, 0), "INSUFFICIENT")


class TierTests(TestCase):
    """The first source with enough history wins, in plan order: this
    assignment (n >= 10), the teacher's papers of the same shape (n >= 30),
    all papers of the same shape (n >= 30), all papers scaled per question
    (3b, n >= 30), then the rough cold start."""

    def setUp(self):
        self.teacher = teacher("tier")
        self.other = teacher("tier-other")
        self.assignment = make_assignment(self.teacher, "Target", questions=3)
        self.sibling = make_assignment(self.teacher, "Sibling", questions=4)
        self.foreign = make_assignment(self.other, "Foreign", questions=2)

    def estimate(self, n=5):
        return estimates.estimate(self.assignment, n, teacher=self.teacher)

    def test_ten_papers_of_this_assignment_win(self):
        papers(self.teacher, self.assignment, [100] * 10)
        papers(self.teacher, self.sibling, [900] * 30, questions=4)
        result = self.estimate()
        self.assertEqual(result["source"], "assignment")
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["p50"], 500)

    def test_nine_papers_of_this_assignment_fall_through_to_the_teachers_shape(self):
        papers(self.teacher, self.assignment, [100] * 9)
        papers(self.teacher, self.sibling, [200] * 21, questions=4)
        result = self.estimate()
        self.assertEqual(result["source"], "teacher_shape")
        self.assertEqual(result["sample_size"], 30)

    def test_the_teachers_shape_wins_over_all_papers_of_the_shape(self):
        papers(self.teacher, self.sibling, [200] * 30, questions=4)
        papers(self.other, self.foreign, [900] * 30, questions=2)
        result = self.estimate()
        self.assertEqual(result["source"], "teacher_shape")
        self.assertEqual(result["p50"], 5 * 200)

    def test_twenty_nine_of_the_teachers_shape_fall_through_to_all_papers(self):
        papers(self.teacher, self.sibling, [200] * 29, questions=4)
        papers(self.other, self.foreign, [300] * 1, questions=2)
        result = self.estimate()
        self.assertEqual(result["source"], "shape")
        self.assertEqual(result["sample_size"], 30)

    def test_another_shape_is_not_the_same_shape(self):
        papers(self.other, self.foreign, [300] * 30, questions=12)
        result = self.estimate()
        self.assertNotIn(result["source"], ("teacher_shape", "shape"))

    def test_images_make_another_shape(self):
        papers(self.other, self.foreign, [300] * 30, questions=2, images=1)
        result = self.estimate()
        self.assertNotIn(result["source"], ("teacher_shape", "shape"))

    def test_tier_3b_scales_any_shape_per_question(self):
        papers(self.other, self.foreign, [600] * 30, questions=12)
        result = self.estimate()
        self.assertEqual(result["source"], "per_question")
        # 600 / 12 questions = 50 a question; the target has 3: 150 a paper.
        self.assertEqual(result["p50"], 5 * 150)

    def test_the_shape_is_read_from_the_assignment(self):
        papers(self.teacher, self.assignment, [100] * 10)
        self.assertEqual(self.estimate()["shape_basis"], "assignment_questions")
        pictured = make_assignment(self.teacher, "Pictured", questions=3, images=1)
        self.assertEqual(estimates.shape_of(pictured), ("1-5", True))
        self.assertEqual(estimates.shape_of(self.assignment), ("1-5", False))

    def test_a_question_image_makes_the_assignment_a_picture_shape(self):
        pictured = make_assignment(self.teacher, "Pictured 2", questions=7, images=1)
        self.assertEqual(estimates.shape_of(pictured), ("6-10", True))

    def test_no_question_image_makes_the_assignment_a_text_shape(self):
        plain = make_assignment(self.teacher, "Plain 2", questions=7, images=0)
        self.assertEqual(estimates.shape_of(plain), ("6-10", False))

    def test_the_question_buckets(self):
        for questions, bucket in (
            (0, "1-5"),
            (1, "1-5"),
            (5, "1-5"),
            (6, "6-10"),
            (10, "6-10"),
            (11, "11-20"),
            (20, "11-20"),
            (21, "21+"),
        ):
            with self.subTest(questions=questions):
                self.assertEqual(estimates.question_bucket(questions), bucket)


class ColdStartTests(TestCase):
    """Tier 4 (§9 item 6(d), SM Q3): one representative submission's prompt
    estimate x the BillingSettings factor x n, source "rough", p90 x 1.5.
    Read-only: no call record, no hold, no audit row, no provider call."""

    def setUp(self):
        self.teacher = teacher("cold")
        self.assignment = make_assignment(self.teacher, "Cold", questions=2)
        student = CustomUser.objects.create_user(
            email="est.cold.student@example.com",
            password="pw-12345678",  # pragma: allowlist secret  # nosec B106
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "an answer"}],
        )

    def estimate(self, n=4):
        return estimates.estimate(
            self.assignment, n, teacher=self.teacher, representative=self.submission
        )

    def test_the_rough_estimate_uses_the_settings_factor_and_p90_times_1_5(self):
        settings_row = BillingSettings.load()
        settings_row.cold_start_cost_factor = 2.0
        settings_row.save()
        with patch.object(
            AIProcessor, "estimate_messages_cost", return_value=1_000
        ) as cost:
            result = self.estimate()
        cost.assert_called_once()
        self.assertEqual(result["source"], "rough")
        self.assertEqual(result["p50"], 4 * 1_000 * 2)
        self.assertEqual(result["p90"], math.ceil(4 * 1_000 * 2 * 1.5))
        self.assertEqual(result["sample_size"], 0)

    def test_the_cold_start_reads_only(self):
        before = (
            AICallRecord.objects.count(),
            AIJob.objects.count(),
            AuditEvent.objects.count(),
            CreditWallet.objects.get(user=self.teacher).reserved_credits,
        )
        with patch.object(AIProcessor, PROVIDER) as provider:
            result = self.estimate()
        provider.assert_not_called()
        self.assertEqual(result["source"], "rough")
        self.assertEqual(
            (
                AICallRecord.objects.count(),
                AIJob.objects.count(),
                AuditEvent.objects.count(),
                CreditWallet.objects.get(user=self.teacher).reserved_credits,
            ),
            before,
        )

    def test_without_a_representative_the_rough_estimate_is_still_given(self):
        with patch.object(AIProcessor, "estimate_messages_cost", return_value=900):
            result = estimates.estimate(self.assignment, 2, teacher=self.teacher)
        self.assertEqual(result["source"], "rough")
        self.assertGreater(result["p50"], 0)


class AdviceTests(TestCase):
    """The endpoint's payload: the band, the verdict against spendable, and
    the projected balance at both ends."""

    def test_the_payload(self):
        user = teacher("advice")
        assignment = make_assignment(user, "Advice")
        papers(user, assignment, [100] * 10)
        with patch.object(estimates, "spendable_of", return_value=700):
            result = estimates.advise(user, assignment, 5)
        self.assertEqual(
            {key: result[key] for key in ("p50", "verdict", "spendable", "n")},
            {"p50": 500, "verdict": "MARGINAL", "spendable": 700, "n": 5},
        )
        self.assertEqual(result["projected_remaining_p50"], 200)
        self.assertEqual(result["projected_remaining_p90"], 700 - result["p90"])
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

    def test_spendable_is_the_wallets_free_balance(self):
        user = teacher("spend")
        wallet = CreditWallet.objects.get(user=user)
        CreditWallet.objects.filter(pk=wallet.pk).update(reserved_credits=4_000)
        self.assertEqual(estimates.spendable_of(user), 10_000 - 4_000)

    def test_a_teacher_without_a_wallet_has_nothing_spendable(self):
        bare = SimpleNamespace(pk=None)
        self.assertEqual(estimates.spendable_of(bare), 0)
