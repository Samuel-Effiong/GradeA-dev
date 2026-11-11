"""B1b-4 (NOTES §4): POST billing/estimate (plan §3.6; SM rulings Q5, Q6).

Advisory: it returns codes and numbers only, writes no job, and refuses
exactly where grade-all refuses: another teacher's assignment gets grade-all's
status code. A teacher with no credits is not refused; they get the verdict
INSUFFICIENT (FR-B-04's 0 boundary)."""

import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.enums import AuditAction
from audit.models import AuditEvent
from billing.models import AIJob, CreditWallet
from billing.testing.holds import ai_ready_teacher
from billing.tests.test_h38_part2_removed_teacher_routes import fund_wallet
from billing.tests.testing_estimates import make_assignment, papers
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

DISPATCH = "celery.app.task.Task.apply_async"


def student(tag):
    return CustomUser.objects.create_user(
        email=f"est.ep.{tag}@example.com",
        password="pw-12345678",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.STUDENT,
        is_active=True,
    )


class EndpointFixture(TestCase):
    """The endpoint's fixture, shared with B1b-6b's limit tests. No tests."""

    def setUp(self):
        cache.clear()
        self.teacher = ai_ready_teacher("est.ep.t@example.com", 10_000)
        self.assignment = make_assignment(self.teacher, "Endpoint")
        papers(self.teacher, self.assignment, [100] * 10)
        self.ungraded = [
            StudentSubmission.objects.create(
                assignment=self.assignment,
                student=student(f"s{index}"),
                answers=[{"question_number": 1, "answer_html": "x"}],
            )
            for index in range(3)
        ]
        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student("graded"),
            answers=[{"question_number": 1, "answer_html": "x"}],
            graded_at=timezone.now(),
        )
        self.url = reverse("billing-estimate")
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def post(self, body, client=None):
        return (client or self.client).post(self.url, body, format="json")


class EstimateEndpointTests(EndpointFixture):

    def test_all_ungraded_counts_only_the_ungraded_papers(self):
        response = self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["data"]
        self.assertEqual(body["n"], 3)
        self.assertEqual(body["p50"], 300)
        self.assertEqual(body["source"], "assignment")
        self.assertEqual(body["verdict"], "SUFFICIENT")
        self.assertEqual(body["spendable"], 10_000)

    def test_listed_submissions_count_only_those_of_this_assignment(self):
        elsewhere = make_assignment(self.teacher, "Elsewhere")
        stray = StudentSubmission.objects.create(
            assignment=elsewhere,
            student=student("stray"),
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        response = self.post(
            {
                "assignment_id": str(self.assignment.pk),
                "submission_ids": [str(self.ungraded[0].pk), str(stray.pk)],
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["n"], 1)

    def test_the_body_is_codes_and_numbers_only(self):
        body = self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        ).json()["data"]
        self.assertEqual(
            set(body),
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
        self.assertNotIn("message", body)

    def test_it_writes_no_job_and_holds_nothing(self):
        jobs = AIJob.objects.count()
        with patch(DISPATCH) as dispatch:
            self.post(
                {
                    "assignment_id": str(self.assignment.pk),
                    "submission_ids": "all_ungraded",
                }
            )
        dispatch.assert_not_called()
        self.assertEqual(AIJob.objects.count(), jobs)
        self.assertEqual(
            CreditWallet.objects.get(user=self.teacher).reserved_credits, 0
        )

    def test_a_teacher_with_no_credits_gets_insufficient_not_a_refusal(self):
        CreditWallet.objects.get(user=self.teacher).buckets.all().delete()
        response = self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        )
        self.assertNotEqual(response.status_code, 402, "refused like grade-all")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["spendable"], 0)
        self.assertEqual(response.json()["data"]["verdict"], "INSUFFICIENT")

    def test_another_teachers_assignment_is_refused_as_grade_all_refuses_it(self):
        intruder = ai_ready_teacher("est.ep.intruder@example.com", 10_000)
        fund_wallet(intruder)
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=intruder)
        with patch(DISPATCH):
            grade_all = client.post(
                reverse("assignment-grade-all", args=[self.assignment.pk]),
                {},
                format="json",
            )
        estimate = self.post(
            {
                "assignment_id": str(self.assignment.pk),
                "submission_ids": "all_ungraded",
            },
            client=client,
        )
        self.assertEqual(grade_all.status_code, 404, grade_all.content)
        self.assertEqual(estimate.status_code, grade_all.status_code)

    def test_a_student_is_refused(self):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.ungraded[0].student)
        response = self.post(
            {
                "assignment_id": str(self.assignment.pk),
                "submission_ids": "all_ungraded",
            },
            client=client,
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_refused(self):
        response = self.post(
            {
                "assignment_id": str(self.assignment.pk),
                "submission_ids": "all_ungraded",
            },
            client=APIClient(raise_request_exception=False),
        )
        self.assertEqual(response.status_code, 401)

    def test_a_bad_body_is_a_400(self):
        for body in (
            {},
            {"assignment_id": "not-a-uuid", "submission_ids": "all_ungraded"},
            {"assignment_id": str(self.assignment.pk), "submission_ids": "some"},
            {"assignment_id": str(self.assignment.pk)},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)

    def test_one_request_audit_row_and_no_other_audit_row(self):
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        )
        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(new.count(), 1)
        row = new.get()
        self.assertEqual(row.metadata.get("route"), "billing-estimate")
        self.assertEqual(row.action, AuditAction.STATE_CHANGE)
        self.assertEqual(row.actor_id, self.teacher.pk)


# B1b-6b (H-1; owner 2026-11-11, SM ruling option (a), NOTES §6.7.1): a
# listed body holds at most 500 ids; "all_ungraded" has no limit.
LIMIT = 500
LIMIT_TEXT = "Ensure this field has no more than 500 elements."
# team/approved_wording_b1b_2026-11-09.md, H-1, amended 2026-11-11.
OVER_LIMIT_MESSAGE = (
    "You can get a cost estimate for up to 500 papers at a time. "
    "Pick fewer papers, or choose all ungraded papers."
)


class EstimateListLimitTests(EndpointFixture):
    """The three H-1 tests."""

    def listed(self, count):
        ids = [str(paper.pk) for paper in self.ungraded]
        ids += [str(uuid.uuid4()) for _ in range(count - len(ids))]
        return self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": ids}
        )

    def test_500_listed_ids_are_accepted(self):
        response = self.listed(LIMIT)
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response.json()["data"]["n"], 3)

    def test_501_listed_ids_are_refused_with_the_drf_message(self):
        response = self.listed(LIMIT + 1)
        self.assertEqual(response.status_code, 400, "501 ids were accepted")
        body = response.json()
        self.assertEqual(body["error"]["field_errors"]["submission_ids"], [LIMIT_TEXT])
        # The owner-approved message (NOTES §6.7.4), byte for byte.
        self.assertEqual(body["message"], OVER_LIMIT_MESSAGE)

    def test_all_ungraded_has_no_limit(self):
        for index in range(LIMIT + 1 - len(self.ungraded)):
            StudentSubmission.objects.create(
                assignment=self.assignment,
                student=student(f"many{index}"),
                answers=[{"question_number": 1, "answer_html": "x"}],
            )
        response = self.post(
            {"assignment_id": str(self.assignment.pk), "submission_ids": "all_ungraded"}
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response.json()["data"]["n"], LIMIT + 1)
