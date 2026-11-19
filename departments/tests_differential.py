"""Epic F1 D0: the membership-invariance differential (plan §7.2).

For B (T4, in no department), record status and body of every readable route
the resolver serves, then add B to D1 (which holds A, T5, and T1) and record
again: everything must be equal, modulo timestamps, per-request error
references and the department routes themselves. Repeated for a restricted
membership, for the flag turned editable, and for a student (A's own
student). `teacher_course_access_q` and `reachable_courses` for B are
compared too.

The M-rows are absolute and named; D0 is relative and complete: it catches a
widened rule on a route nobody wrote a row for, by route name, including
routes F2 and D add later.

Five ways the differential could pass for the wrong reason, each closed:
1. Caches: "after" could be the cached "before". The cache is cleared
   between snapshots, and each "after" is taken cold and warm; the two must
   agree.
2. Only B's own ids: every route with a path parameter is called with the ids
   of A's and T1's private objects (course, student, enrollment, topic,
   assignment, submission, wallet, bucket, allocation, the teacher).
3. A route that answers 404 to everyone: a positive control in the same run;
   A reads their own objects with 200 on the named routes, and a minimum
   number of A's swept URLs answer 200.
4. The route list is pinned: a readable route removed or added without
   updating PINNED_READ_ROUTES fails by name (INTERFACE.md: a new readable
   route goes into the doc and this list in the same change).
5. The normalizer strips only timestamps and error references, and a test
   mutates one field of one response and proves the comparison notices.
Written against INTERFACE.md only.
"""

import json
import re
from typing import Any
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from departments.tests_matrix_fixture import (
    DEPARTMENT_READ_ROUTES,
    SWEEP_EXCLUDED,
    allocation_id,
    build_world,
    jwt_client,
    object_ids,
    read_routes,
    urls_with_ids,
)

flag_on = override_settings(DEPARTMENTS_ENABLED=True)

#: Every readable route at staging e8fc8cc99 plus the four F1 routes.
PINNED_READ_ROUTES = (
    frozenset(
        {
            "admin-credits-all-grants",
            "admin-credits-grant-detail",
            "admin-credits-grants-summary",
            "admin-credits-user-grant-history",
            "analytics-credit-usage-stats",
            "analytics-custom-ai-prompt-history",
            "analytics-detail",
            "analytics-intent-signal-detail",
            "analytics-intent-signals",
            "analytics-list",
            "analytics-log-view",
            "analytics-summary",
            "api-root",
            "assignment-detail",
            "assignment-download-pdf",
            "assignment-generation-session-detail",
            "assignment-generation-session-list",
            "assignment-list",
            "beat-health",
            "beta-chart-credit-usage-distribution",
            "beta-chart-daily-credit-consumption",
            "beta-chart-daily-credit-consumption-grouping",
            "beta-chart-detail",
            "beta-chart-feature-mix-across-groups",
            "beta-chart-feature-usage-mix",
            "beta-chart-intent-signal-distribution",
            "beta-chart-list",
            "beta-chart-peak-usage-hours",
            "beta-chart-weekly-credit-trends",
            "beta-profile-detail",
            "beta-profile-list",
            "billing-invoice-detail",
            "billing-invoice-list",
            "billing-job-detail",
            "category-template-detail",
            "category-template-list",
            "client-identity",
            "course-categories",
            "course-detail",
            "course-grade-breakdown",
            "course-grading-policy",
            "course-list",
            "course-my-courses",
            "course-student-summary",
            "credit-bucket-detail",
            "credit-bucket-list",
            "credit-ledger-detail",
            "credit-ledger-list",
            "credit-request-counts",
            "credit-request-list",
            "credit-usage-log-detail",
            "credit-usage-log-list",
            "credit-wallet-detail",
            "credit-wallet-list",
            "dashboard-concurrency",
            "dashboard-custom-ai-prompt-history",
            "dashboard-platform-adoption",
            "dashboard-platform-ai-performance",
            "dashboard-platform-usage",
            "dashboard-scaling-signals",
            "dashboard-schools",
            "dashboard-students",
            "dashboard-teachers",
            "health",
            "license-overage-offline-request-detail",
            "license-overage-offline-request-list",
            "license-subscription-active",
            "license-subscription-billing-history",
            "license-subscription-detail",
            "license-subscription-list",
            "license-subscription-renewal-info",
            "payment-method-list",
            "qa-console",
            "qa-console-action",
            "qa-console-new-subscriber",
            "qa-console-reset",
            "qa-console-runs-create",
            "qa-console-runs-detail",
            "qa-console-runs-list",
            "qa-console-state",
            "schema",
            "school-admin-assignment-activity-over-time-chart",
            "school-admin-at-risk-trend",
            "school-admin-audit-events",
            "school-admin-course-overview-chart",
            "school-admin-course-peformance",
            "school-admin-custom-ai-prompt-history",
            "school-admin-students",
            "school-admin-summary",
            "school-admin-teacher-detail",
            "school-admin-teacher-performance",
            "school-admin-unit-performance",
            "school-admins-summary",
            "school-credit-allocation-detail",
            "school-credit-allocation-list",
            "school-detail",
            "school-list",
            "school-monthly-token-usage",
            "school-teacher-summary",
            "session-detail",
            "session-list",
            "settings-detail",
            "settings-list",
            "settings-my-settings",
            "stripe-webhook",
            "stripe-webhook-thin",
            "student-assignments",
            "student-course-detail",
            "student-course-list",
            "student-course-my-students",
            "student-data-export",
            "student-overview",
            "student-status-summary",
            "student-submission-detail",
            "student-submission-list",
            "student-submission-teacher-feedback",
            "student-summary",
            "subscription-credit-buckets",
            "subscription-credit-carry-over-history",
            "subscription-credit-ledger",
            "subscription-credit-overage-status",
            "subscription-credit-summary",
            "subscription-credit-usage-logs",
            "subscription-credit-wallet",
            "subscription-get-my-subscription",
            "subscription-history",
            "subscription-plan",
            "subscription-plan-detail",
            "subscription-plan-list",
            "subscription-status",
            "super-admin-audit-events",
            "swagger-ui",
            "tag-impact",
            "tag-list",
            "task-session-results",
            "task-sessions",
            "task-task-status",
            "teacher-admin-all-students",
            "teacher-admin-assignments",
            "teacher-admin-courses",
            "teacher-admin-custom-ai-prompt-history",
            "teacher-admin-overview",
            "teacher-admin-students",
            "topic-detail",
            "topic-list",
            "user-detail",
            "user-list",
            "user-me",
            "user-subscription-detail",
            "user-subscription-list",
            "waitlist-detail",
            "waitlist-list",
            "whitelist-detail",
            "whitelist-list",
        }
    )
    | DEPARTMENT_READ_ROUTES
)

#: Routes whose answer changes between two identical requests for reasons
#: unrelated to who may see what. Each needs a reason.
NONDETERMINISTIC: dict[str, str] = {}

_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?"
)
_REFERENCE = re.compile(r'"reference"\s*:\s*"[^"]*"')


def normalize(text):
    """Only timestamps and per-request error references go."""
    text = _TIMESTAMP.sub("<ts>", text)
    return _REFERENCE.sub('"reference":"<ref>"', text)


def content(response):
    if getattr(response, "streaming", False):
        raw = b"".join(response.streaming_content)
    else:
        raw = response.content or b""
    return raw.decode("utf-8", "replace")


def differences(before, after):
    """The URLs whose (status, body) differ, or that one side lacks."""
    return sorted(
        url for url in set(before) | set(after) if before.get(url) != after.get(url)
    )


class D0Differential(TestCase):
    w: Any
    world = staticmethod(build_world)

    @classmethod
    def setUpTestData(cls):
        with override_settings(DEPARTMENTS_ENABLED=True):
            cls.w = cls.world()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("celery.app.task.Task.apply_async")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a = self.w.data["t5"]
        self.colleague = self.w.data["t1"]
        self.b = self.w.t4

    def id_pools(self):
        return [
            object_ids(self.a, allocation_id(self.a.teacher)),
            object_ids(self.colleague, allocation_id(self.colleague.teacher)),
        ]

    def urls(self):
        urls = []
        for name, kwargs in sorted(read_routes().items()):
            if (
                name in SWEEP_EXCLUDED
                or name in NONDETERMINISTIC
                or name in DEPARTMENT_READ_ROUTES
            ):
                continue
            for pool in self.id_pools():
                urls.extend((name, url) for url in urls_with_ids(name, kwargs, pool))
        return sorted(set(urls))

    def snapshot(self, user, urls):
        client = jwt_client(user)
        result = {}
        for _, url in urls:
            response = client.get(url)
            result[url] = (response.status_code, normalize(content(response)))
        return result

    def access_helpers(self, user):
        from classrooms.models import Course, reachable_courses, teacher_course_access_q

        return (
            sorted(
                Course.objects.filter(teacher_course_access_q(user)).values_list(
                    "id", flat=True
                )
            ),
            sorted(reachable_courses(user).values_list("id", flat=True)),
        )

    def assertSame(self, before, after, what):
        changed = differences(before, after)
        self.assertEqual(
            changed, [], f"{what}: {len(changed)} URLs changed: {changed[:20]}"
        )

    def cold_and_warm(self, user, urls):
        cache.clear()
        cold = self.snapshot(user, urls)
        warm = self.snapshot(user, urls)
        self.assertSame(cold, warm, "cold vs warm")
        return cold

    def test_joining_a_department_changes_nothing_a_teacher_or_student_can_read(self):
        from departments.models import DepartmentMembership
        from departments.tests_factories import add_member

        urls = self.urls()
        self.assertGreater(len(urls), 300)
        before = self.cold_and_warm(self.b, urls)
        student_before = self.cold_and_warm(self.w.st, urls)
        helpers_before = self.access_helpers(self.b)

        membership = add_member(self.w.d1, self.b, can_edit=False)
        self.assertSame(before, self.cold_and_warm(self.b, urls), "restricted member")
        self.assertEqual(self.access_helpers(self.b), helpers_before)

        DepartmentMembership.objects.filter(pk=membership.pk).update(can_edit=True)
        self.assertSame(before, self.cold_and_warm(self.b, urls), "editable member")
        self.assertEqual(self.access_helpers(self.b), helpers_before)
        self.assertSame(student_before, self.cold_and_warm(self.w.st, urls), "student")

    def test_positive_control_a_reads_their_own_objects(self):
        from django.urls import reverse

        client = jwt_client(self.a.teacher)
        for name, kind in (
            ("course-detail", "course"),
            ("assignment-detail", "assignment"),
            ("student-submission-detail", "submission"),
            ("topic-detail", "topic"),
            ("student-course-detail", "enrollment"),
        ):
            with self.subTest(route=name):
                pk = self.id_pools()[0][kind]
                response = client.get(reverse(name, kwargs={"pk": pk}))
                self.assertEqual(response.status_code, 200, content(response))
        own = self.snapshot(
            self.a.teacher, [u for u in self.urls() if str(self.a.course.id) in u[1]]
        )
        self.assertGreaterEqual(
            sum(1 for status, _ in own.values() if status == 200), 3
        )

    def test_the_readable_route_list_is_pinned(self):
        live = set(read_routes())
        self.assertEqual(sorted(live - PINNED_READ_ROUTES), [], "new readable routes")
        self.assertEqual(sorted(PINNED_READ_ROUTES - live), [], "routes gone")


class D0ComparisonNotices(TestCase):
    def test_one_changed_field_is_noticed(self):
        before = {"/x": (200, json.dumps({"id": 1, "name": "a"}))}
        after = {"/x": (200, json.dumps({"id": 1, "name": "b"}))}
        self.assertEqual(differences(before, after), ["/x"])
        self.assertEqual(differences(before, dict(before)), [])
        self.assertEqual(differences(before, {}), ["/x"])
        self.assertEqual(differences(before, {"/x": (404, before["/x"][1])}), ["/x"])

    def test_the_normalizer_strips_only_timestamps_and_references(self):
        self.assertEqual(
            normalize(
                '{"at":"2026-11-19T10:00:00.123Z","reference":"abc","n":7319447}'
            ),
            '{"at":"<ts>","reference":"<ref>","n":7319447}',
        )
        text = json.dumps(
            {"balance": "7,319,447", "id": "0b1c", "name": "Qxvorpalzesta"}
        )
        self.assertEqual(normalize(text), text)
