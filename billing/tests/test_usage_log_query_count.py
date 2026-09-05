"""
billing/tests/test_usage_log_query_count.py
===========================================
Guards the credit-usage-log list endpoint against an N+1.

`CreditUsageLogSerializer` exposes `bucket_type` via
`source="bucket.bucket_type"`, so serializing a page walks the `bucket`
relation once per row. The viewset did not `select_related`, so a 20-row
page issued 24 queries — measured, not inferred — and this is the
endpoint whose table grows without bound as a teacher consumes credits.

The assertion is a ceiling rather than an exact count: the baseline
includes auth and the pagination COUNT, which are not what this test is
about. What matters is that it does not scale with the number of rows,
so the test builds a page of 20 and asserts a budget far below 4 + 20.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from billing.models import CreditBucket, CreditBucketType, CreditUsageLog, CreditWallet
from users.models import UserTypes

CustomUser = get_user_model()

PAGE_ROWS = 20

#: Comfortably above the fixed baseline (auth, COUNT, the page itself) and
#: comfortably below the 24 an unfixed N+1 produced for this page size.
QUERY_BUDGET = 10


class CreditUsageLogListQueryCountTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="usage-log-queries@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

        # A distinct bucket per row: one shared bucket would be served from
        # the query cache and hide the very N+1 this guards.
        for index in range(PAGE_ROWS):
            bucket = CreditBucket.objects.create(
                wallet=self.wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=100,
                used_credits=0,
            )
            CreditUsageLog.record(
                wallet=self.wallet,
                bucket=bucket,
                amount=1,
                feature=f"Feature {index}",
            )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_query_count_stays_within_budget(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("credit-usage-log-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), PAGE_ROWS)
        self.assertLessEqual(
            len(captured.captured_queries),
            QUERY_BUDGET,
            f"credit-usage-log list used {len(captured.captured_queries)} "
            f"queries for {PAGE_ROWS} rows — the bucket relation is being "
            "walked per row again (add select_related on the viewset).",
        )

    def test_the_serialized_bucket_type_is_still_correct(self):
        """The optimisation must not change what the endpoint returns."""
        response = self.client.get(reverse("credit-usage-log-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for row in response.data["results"]:
            self.assertEqual(row["bucket_type"], CreditBucketType.MONTHLY)
