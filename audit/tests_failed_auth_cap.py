"""S1b: the failed-auth cap (audit.failed_auth_cap; SM rulings incl. v2's
H1/H2/N3). Small limits so the edges are cheap to reach."""

import threading
import uuid
from typing import Any, cast
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit import failed_auth_cap
from audit.emitter import emit
from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass
from audit.failed_auth_cap import FAILED_AUTH_CAPPED
from audit.models import AuditEvent
from users.models import UserTypes

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SMALL_CAPS = {
    "FAILED_AUTH_TARGET_FLOOR": 2,
    "FAILED_AUTH_TARGET_LIMIT": 4,
    "FAILED_AUTH_GLOBAL_LIMIT": 6,
    "FAILED_AUTH_WINDOW_SECONDS": 3600,
}


def make_user(email, user_type=UserTypes.TEACHER):
    return User.objects.create_user(
        email=email,
        password="Cap-test-pw-1",  # pragma: allowlist secret
        first_name="Cap",
        last_name="Test",
        user_type=user_type,
        is_active=True,
        email_verified_at=timezone.now(),
    )


def fail(target=None, actor=None, outcome=AuditOutcome.FAILURE, action=None):
    """One failed sign-in, as the doors record it."""
    return emit(
        action or AuditAction.AUTH_LOGIN,
        actor=actor or AnonymousUser(),
        target_type="CustomUser",
        target_id=target.pk if target is not None else None,
        outcome=outcome,
        error_class=ErrorClass.USER,
        reason_code="INVALID_CODE",
        metadata={"auth_method": "password"},
    )


def individual():
    return AuditEvent.objects.exclude(reason_code=FAILED_AUTH_CAPPED)


def summaries():
    return AuditEvent.objects.filter(reason_code=FAILED_AUTH_CAPPED).order_by(
        "occurred_at", "pk"
    )


@override_settings(CACHES=LOCMEM_CACHE, **SMALL_CAPS)
class FailedAuthCapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.account = make_user("cap.target@example.com")

    def test_past_the_target_limit_failures_become_a_summary(self):
        for _ in range(4 + 1):
            fail(self.account)

        self.assertEqual(individual().count(), 4)
        summary = summaries().get()
        self.assertEqual(summary.action, AuditAction.AUTH_LOGIN)
        self.assertEqual(summary.outcome, AuditOutcome.FAILURE)
        self.assertEqual(summary.actor_role, ActorRole.ANONYMOUS)
        self.assertEqual(summary.target_id, self.account.pk)
        self.assertEqual(
            summary.metadata,
            {
                "cap": "target",
                "suppressed_so_far": 1,
                "limit": 4,
                "window_seconds": 3600,
            },
        )

    def test_summaries_are_written_at_1_and_10_suppressed(self):
        for _ in range(4 + 12):
            fail(self.account)

        self.assertEqual(
            [s.metadata["suppressed_so_far"] for s in summaries()], [1, 10]
        )
        self.assertEqual(individual().count(), 4)

    def test_a_known_accounts_floor_is_written_even_when_the_global_cap_is_spent(self):
        """v2's H1: a junk-email spray can't blind real accounts."""
        for _ in range(6 + 3):
            fail(target=None)  # the no-target bucket spends the global cap
        before = individual().count()

        fail(self.account)
        fail(self.account)

        self.assertEqual(individual().count(), before + 2)

    def test_past_the_floor_the_global_cap_applies_to_a_known_account(self):
        for _ in range(6):
            fail(target=None)
        fail(self.account)
        fail(self.account)  # the floor: written

        fail(self.account)  # past the floor, global spent: suppressed

        self.assertEqual(individual().filter(target_id=self.account.pk).count(), 2)
        self.assertEqual(summaries().last().metadata["cap"], "global")

    def test_an_unknown_target_is_under_the_global_cap_from_the_first(self):
        for _ in range(6 + 1):
            fail(target=None)

        self.assertEqual(individual().count(), 6)
        summary = summaries().get()
        self.assertIsNone(summary.target_id)
        self.assertEqual(summary.metadata["cap"], "global")
        self.assertEqual(summary.metadata["limit"], 6)

    def test_successes_and_lock_denials_are_never_capped(self):
        for _ in range(10):
            fail(self.account)

        emit(
            AuditAction.AUTH_LOGIN,
            actor=self.account,
            target_type="CustomUser",
            target_id=self.account.pk,
        )
        fail(self.account, outcome=AuditOutcome.DENIED)

        self.assertTrue(individual().filter(outcome=AuditOutcome.SUCCESS).exists())
        self.assertTrue(individual().filter(outcome=AuditOutcome.DENIED).exists())

    def test_a_signed_in_requesters_failures_are_never_capped(self):
        """v2's H2: change-password failures; suppressing one would make S1's
        middleware fall back to an uncapped STATE_CHANGE."""
        for _ in range(10):
            fail(self.account, actor=self.account)

        self.assertEqual(individual().count(), 10)
        self.assertFalse(summaries().exists())

    def test_account_registration_failures_are_capped_too(self):
        for _ in range(6 + 1):
            fail(target=None, action=AuditAction.ACCOUNT_REGISTER)

        self.assertEqual(summaries().get().action, AuditAction.ACCOUNT_REGISTER)

    def test_the_keys_hold_the_account_id_never_an_email(self):
        for _ in range(4 + 1):
            fail(self.account)

        keys = [key for key in cast(Any, cache)._cache if "failed_auth" in key]
        self.assertTrue(any(str(self.account.pk) in key for key in keys))
        self.assertFalse(any("@" in key for key in keys))

    def test_the_cap_fails_open(self):
        broken = MagicMock()
        for method in ("add", "incr", "set", "get"):
            getattr(broken, method).side_effect = ConnectionError("cache down")
        with patch.object(failed_auth_cap, "cache", broken):
            for _ in range(10):
                fail(self.account)

        self.assertEqual(individual().count(), 10)
        self.assertFalse(summaries().exists())


@override_settings(
    CACHES=LOCMEM_CACHE,
    FAILED_AUTH_TARGET_FLOOR=1,
    FAILED_AUTH_TARGET_LIMIT=1,
    FAILED_AUTH_GLOBAL_LIMIT=1000,
)
class CappedDoorThroughTheMiddlewareTests(TestCase):
    """A door failure held back by the cap is counted in its summary; S2's
    door fallback must not write an INVALID_REQUEST in its place. Three
    attempts: written (the floor), suppressed with the first summary, and
    suppressed with no summary - the one where only the request's
    `suppressed` mark stops the fallback."""

    def test_a_suppressed_door_failure_leaves_only_the_summary(self):
        cache.clear()
        self.addCleanup(cache.clear)
        account = make_user("cap.door@example.com")
        User.objects.filter(pk=account.pk).update(
            is_active=False, activation_token="123456"
        )

        for index in range(3):
            APIClient().post(
                reverse("auth-verify"),
                {"email": account.email, "token": "000000"},
                format="json",
                REMOTE_ADDR=f"10.9.0.{index + 1}",
            )

        reasons = sorted(
            r or "" for r in AuditEvent.objects.values_list("reason_code", flat=True)
        )
        self.assertEqual(reasons, [FAILED_AUTH_CAPPED, "INVALID_CODE"])


@override_settings(
    FAILED_AUTH_TARGET_FLOOR=1,
    FAILED_AUTH_TARGET_LIMIT=1,
    FAILED_AUTH_GLOBAL_LIMIT=1_000_000,
)
class ConcurrentSummaryTests(TransactionTestCase):
    """v2's N3 / Gate 3: on the shared Redis cache (the project's own
    CACHES, not locmem), 20 simultaneous suppressed failures write each
    threshold's summary exactly once: suppressed 1 and 10."""

    THREADS = 20

    def test_each_threshold_summary_is_written_exactly_once(self):
        account = make_user(f"cap.race.{uuid.uuid4().hex[:8]}@example.com")
        fail(account)  # the floor, written
        barrier = threading.Barrier(self.THREADS, timeout=30)

        def racer():
            try:
                barrier.wait()
                fail(account)
            finally:
                connection.close()

        threads = [threading.Thread(target=racer) for _ in range(self.THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(individual().filter(target_id=account.pk).count(), 1)
        thresholds = [s.metadata["suppressed_so_far"] for s in summaries()]
        self.assertEqual(len(thresholds), 2)
        self.assertEqual(set(thresholds), {1, 10})
