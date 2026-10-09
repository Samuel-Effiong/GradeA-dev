"""
H-194 (AUDIT-ANON-FLOOD): an anonymous caller probing the super-admin routes cannot
write an unbounded number of audit rows.

WHAT WAS WRONG (found by reading, 2026-10-07)
---------------------------------------------
An unauthenticated request to any `IsSuperAdmin` route is refused, and the
refusal is audited: `IsSuperAdmin` calls `emit_denied`, which writes one
ANONYMOUS `ADMIN_ACTION` / `DENIED` row per call. The failed-sign-in cap
(`audit.failed_auth_cap`) covered only `AUTH_LOGIN` and `ACCOUNT_REGISTER`
(`audit.emitter._CAPPED_ACTIONS`), so the anonymous throttle (60 a minute per
client) was the only bound. One client at that limit writes 86,400 rows a day
at about 0.85 KB each with indexes, kept 365 days (about 27 GB a year); many
clients multiply it. **That 60-a-minute bound is UNVERIFIED in deployment:**
the proxy count that decides which address is the throttle's key
(`NUM_PROXIES`) has not been checked against the real number of proxy hops,
so the throttle may not bound a client at all.

THE RULE (approved 2026-10-08)
------------------------------
An anonymous DENIED `ADMIN_ACTION` goes into the GLOBAL, no-target bucket of
the failed-auth cap, with the summary row. Whatever target the route names
(a URL `pk` the caller chooses) is ignored for the cap, so varying it does not
get round it. A signed-in refusal and a granted action are never capped.

KNOWN LIMIT: the global bucket is the one failed sign-ins of UNKNOWN addresses
also use (300 an hour by default), so an admin-route flood can use it up and
suppress those rows for the hour (their summary says so); a known account's
first five failures are still always written. A separate bucket is a follow-up.

Run with:
    python manage.py test audit.tests_anonymous_admin_denial_cap
"""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from audit.emitter import emit
from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass
from audit.failed_auth_cap import FAILED_AUTH_CAPPED
from audit.models import AuditEvent

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SMALL_CAPS = {
    "FAILED_AUTH_TARGET_FLOOR": 2,
    "FAILED_AUTH_TARGET_LIMIT": 4,
    "FAILED_AUTH_GLOBAL_LIMIT": 6,
    "FAILED_AUTH_WINDOW_SECONDS": 3600,
}
LIMIT = SMALL_CAPS["FAILED_AUTH_GLOBAL_LIMIT"]
ATTEMPTS = LIMIT + 4
ADMIN_ROUTE = reverse("super-admin-audit-events")


def denials():
    """The ADMIN_ACTION / DENIED rows of anonymous callers, real ones only."""
    return AuditEvent.objects.filter(
        action=AuditAction.ADMIN_ACTION.value,
        outcome=AuditOutcome.DENIED.value,
        actor_role=ActorRole.ANONYMOUS.value,
    ).exclude(reason_code=FAILED_AUTH_CAPPED)


def summaries():
    return AuditEvent.objects.filter(
        action=AuditAction.ADMIN_ACTION.value,
        outcome=AuditOutcome.DENIED.value,
        reason_code=FAILED_AUTH_CAPPED,
    )


@override_settings(CACHES=LOCMEM_CACHE, **SMALL_CAPS)
class AnonymousAdminDenialCapTests(TestCase):
    client: APIClient

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()

    def probe(self, times):
        statuses = set()
        for i in range(times):
            response = self.client.get(
                ADMIN_ROUTE, REMOTE_ADDR=f"10.9.{i // 200}.{i % 200 + 1}"
            )
            statuses.add(response.status_code)
        return statuses

    def test_the_route_exists_and_refuses_an_anonymous_caller(self):
        """Green on the old code too: the probe really reaches the refusal
        (not a 404), so the tests below measure what they say they measure."""
        statuses = self.probe(1)

        self.assertTrue(statuses <= {401, 403}, statuses)
        self.assertEqual(denials().count(), 1)

    def test_anonymous_probes_stop_writing_rows_at_the_global_limit(self):
        self.probe(ATTEMPTS)

        self.assertEqual(denials().count(), LIMIT)

    def test_the_first_suppressed_probe_leaves_one_summary_row(self):
        self.probe(ATTEMPTS)

        rows = list(summaries())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].metadata["cap"], "global")
        self.assertEqual(rows[0].metadata["limit"], LIMIT)
        self.assertEqual(rows[0].actor_role, ActorRole.ANONYMOUS.value)

    def test_naming_a_different_target_each_time_does_not_get_round_the_cap(self):
        """The route's target is a URL id the caller chooses; the cap must not
        be keyed on it (each distinct target would get its own floor)."""
        for _ in range(ATTEMPTS):
            emit(
                AuditAction.ADMIN_ACTION,
                actor=AnonymousUser(),
                target_type="SomeAdminView",
                target_id=uuid.uuid4(),
                outcome=AuditOutcome.DENIED,
                error_class=ErrorClass.USER,
            )

        self.assertEqual(denials().count(), LIMIT)

    def test_a_signed_in_refusal_is_never_capped(self):
        """Green on the old code too: a signed-in non-admin hitting an admin
        route needs a session and is not the anonymous flood."""
        teacher = User.objects.create_user(
            email="not.an.admin.flood@example.com",
            password="Flood-test-pw-1",  # pragma: allowlist secret
            user_type="TEACHER",
            is_active=True,
        )
        self.client.force_authenticate(user=teacher)

        for _ in range(ATTEMPTS):
            self.client.get(ADMIN_ROUTE)

        written = AuditEvent.objects.filter(
            action=AuditAction.ADMIN_ACTION.value,
            outcome=AuditOutcome.DENIED.value,
            actor_id=teacher.pk,
        ).count()
        self.assertEqual(written, ATTEMPTS)
