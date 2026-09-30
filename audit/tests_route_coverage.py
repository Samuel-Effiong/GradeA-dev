"""S2 (plan 08 §3): no state-changing route escapes the audit.

A new route must not quietly go unaudited. These tests enumerate every route
the URL resolver serves with POST, PUT, PATCH or DELETE, and fail - naming
the route - when one is neither audited nor excluded with a reason:

1. Every write route an ANONYMOUS caller can reach (probed by firing it,
   not by reading permission classes) is an anonymous sign-in door
   (`ANONYMOUS_AUDITED_ROUTES`) or excluded (`EXCLUDED_ROUTES`).
2. Sweep: every other write route, fired as a superadmin with an empty body
   and made-up ids, leaves exactly ONE event naming the requester (plus any
   side-effect events naming others). A 400/404/405 is fine: FAILURE is
   recorded too, so no per-route fixture is needed. Each route runs in its
   own savepoint, so one route's side effects never reach the next.
3. Every anonymous door, fired with an empty body, leaves exactly one event.
4. Every registry entry still names a real route (stale entries fail).

Outbound calls: the H-39 network guard (installed by the test runner) blocks
real network calls; Celery dispatch is stubbed with a real task id; mail
uses Django's test backend. No bare MagicMock can reach a response (rule 14).
"""

import re
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.enums import ActorRole, AuditAction, AuditOutcome
from audit.models import AuditEvent
from audit.request_audit import (
    ANONYMOUS_AUDITED_ROUTES,
    EXCLUDED_ROUTES,
    INVALID_REQUEST,
)
from users.models import UserTypes

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
WRITE_METHODS = ("post", "put", "patch", "delete")

# Routes that refuse anyone but a signed-in superadmin with a bare 404 ("no
# hint this exists"; billing/qa_console.py). For them a 404 to an anonymous
# caller is a refusal, not reachability. Kept explicit - a 404 in general is
# not a refusal, since an open route given a made-up id answers 404 too.
CONCEALED_ROUTES = frozenset(
    {
        "qa-console",
        "qa-console-state",
        "qa-console-new-subscriber",
        "qa-console-action",
        "qa-console-reset",
        "qa-console-runs-list",
        "qa-console-runs-create",
        "qa-console-runs-detail",
    }
)


def _walk(patterns, prefix="", namespace=None):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(
                pattern.url_patterns,
                prefix + str(pattern.pattern),
                pattern.namespace or namespace,
            )
        elif isinstance(pattern, URLPattern):
            name = pattern.name
            if namespace and name:
                name = f"{namespace}:{name}"
            yield prefix + str(pattern.pattern), name, pattern.callback


def write_routes():
    """{(url_name, method): [path kwarg names]} for every write route,
    except the Django admin (D7; see DjangoAdminTests). A function view has
    no method map, so it is probed with POST."""
    routes = {}
    for path, name, callback in _walk(get_resolver().url_patterns):
        if not name or name.startswith("admin:"):
            continue
        kwargs = sorted(
            set(re.findall(r"\(\?P<(\w+)>", path))
            | {seg.split(":")[-1] for seg in re.findall(r"<([^>]+)>", path)}
        )
        if "format" in kwargs:  # DRF's format-suffix twin of a real route
            continue
        view_cls = getattr(callback, "cls", None) or getattr(
            callback, "view_class", None
        )
        actions = getattr(callback, "actions", None)
        if actions:
            methods = [m for m in WRITE_METHODS if m in actions]
        elif view_cls is not None:
            methods = [m for m in WRITE_METHODS if hasattr(view_cls, m)]
        else:
            methods = ["post"]
        for method in methods:
            routes[(name, method)] = kwargs
    return routes


def url_for(name, kwarg_names):
    """The route's URL with made-up ids: a UUID where the pattern takes one,
    else 1."""
    for value in (uuid.uuid4(), 1):
        try:
            return reverse(name, kwargs={k: value for k in kwarg_names})
        except Exception:  # noqa: BLE001 - try the next id shape
            continue
    raise AssertionError(f"cannot build a URL for {name}")


def stub_celery(test):
    """Dispatched tasks are recorded, not run or queued: a real task id
    comes back, so nothing a view renders is a mock (rule 14)."""
    patcher = patch(
        "celery.app.task.Task.apply_async",
        return_value=SimpleNamespace(id="route-coverage-task"),
    )
    patcher.start()
    test.addCleanup(patcher.stop)


def fire(client, method, url):
    return getattr(client, method)(url, {}, format="json")


@override_settings(CACHES=LOCMEM_CACHE)
class RouteCoverageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        stub_celery(self)
        self.routes = write_routes()

    def test_there_are_write_routes_to_guard(self):
        self.assertGreater(len(self.routes), 100)

    def test_every_anonymous_write_route_is_a_door_or_excluded(self):
        """Fired anonymously: anything not refused with 401/403 (or sent to
        a login page) is reachable, and must be audited or excluded."""
        unaccounted = []
        for index, ((name, method), kwarg_names) in enumerate(self.routes.items()):
            with transaction.atomic():
                client = APIClient(
                    raise_request_exception=False,
                    REMOTE_ADDR=f"10.2.{index // 250}.{index % 250 + 1}",
                )
                response = fire(client, method, url_for(name, kwarg_names))
                transaction.set_rollback(True)
            refused = (
                response.status_code in (401, 403)
                or (
                    response.status_code == 302
                    and "login" in response.get("Location", "")
                )
                or (response.status_code == 404 and name in CONCEALED_ROUTES)
            )
            if refused:
                continue
            if name not in ANONYMOUS_AUDITED_ROUTES and name not in EXCLUDED_ROUTES:
                unaccounted.append(f"{method.upper()} {name} -> {response.status_code}")
        self.assertEqual(
            unaccounted,
            [],
            "anonymous write routes with no audit decision: add a named "
            "event (ANONYMOUS_AUDITED_ROUTES) or an EXCLUDED_ROUTES reason",
        )

    def test_every_write_route_leaves_one_event_naming_the_superadmin(self):
        admin = User.objects.create_superuser(
            email="sweep.superadmin@example.com",
            password="Sweep-pw-1",  # pragma: allowlist secret
            first_name="Sweep",
            last_name="Admin",
        )
        admin.user_type = UserTypes.SUPER_ADMIN
        admin.save()
        client = APIClient(raise_request_exception=False)
        client.force_login(admin)
        client.force_authenticate(user=admin)

        wrong = []
        for (name, method), kwarg_names in self.routes.items():
            if name in EXCLUDED_ROUTES or name in ANONYMOUS_AUDITED_ROUTES:
                continue
            with transaction.atomic():
                before = set(AuditEvent.objects.values_list("pk", flat=True))
                response = fire(client, method, url_for(name, kwarg_names))
                naming_admin = list(
                    AuditEvent.objects.exclude(pk__in=before)
                    .filter(actor_id=admin.pk)
                    .values_list("action", flat=True)
                )
                transaction.set_rollback(True)
            if len(naming_admin) != 1:
                wrong.append(
                    f"{method.upper()} {name} -> {response.status_code}: "
                    f"{naming_admin}"
                )
        self.assertEqual(
            wrong,
            [],
            "each write must leave exactly one event naming the requester",
        )

    def test_every_anonymous_door_records_a_refused_request(self):
        """An empty body is refused before the door's own event; the
        middleware records one FAILURE - ANONYMOUS, no target, no body."""
        for index, name in enumerate(sorted(ANONYMOUS_AUDITED_ROUTES)):
            action, auth_method = ANONYMOUS_AUDITED_ROUTES[name]
            with self.subTest(route=name):
                before = set(AuditEvent.objects.values_list("pk", flat=True))
                client = APIClient(
                    raise_request_exception=False, REMOTE_ADDR=f"10.3.0.{index + 1}"
                )
                response = client.post(reverse(name), {}, format="json")
                events = AuditEvent.objects.exclude(pk__in=before)

                self.assertEqual(response.status_code // 100, 4, response.status_code)
                self.assertEqual(events.count(), 1, list(events.values("action")))
                event = events.get()
                self.assertEqual(event.action, action)
                self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
                self.assertNotEqual(event.outcome, AuditOutcome.SUCCESS)
                self.assertEqual(event.metadata.get("auth_method"), auth_method)

    def test_every_registry_entry_names_a_real_route(self):
        names = {name for name, _ in self.routes}
        for registry in (ANONYMOUS_AUDITED_ROUTES, EXCLUDED_ROUTES, CONCEALED_ROUTES):
            for name in registry:
                with self.subTest(route=name):
                    self.assertIn(name, names, f"stale entry: {name}")

    def test_every_exclusion_says_why(self):
        for name, reason in EXCLUDED_ROUTES.items():
            with self.subTest(route=name):
                self.assertGreater(len(reason.strip()), 30)


@override_settings(CACHES=LOCMEM_CACHE)
class SelfRegistrationTests(TestCase):
    """SM ruling: a new account from POST /auth/register is ACCOUNT_REGISTER
    - actor ANONYMOUS (nobody is signed in yet), target the new account."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in (
            "users.serializers.send_user_activation_email",
            "users.views.sync_user_to_mailerlite",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = APIClient()

    def test_a_new_account_is_recorded_with_the_account_as_target(self):
        response = self.client.post(
            reverse("auth-register"),
            {
                "email": "route.coverage.teacher@gmail.com",
                "password": "Some-strong-password-1",  # pragma: allowlist secret
                "first_name": "Route",
                "last_name": "Coverage",
            },
            format="json",
        )

        self.assertIn(response.status_code, (200, 201), response.content)
        account = User.objects.get(email="route.coverage.teacher@gmail.com")
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ACCOUNT_REGISTER)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.target_id, account.pk)
        self.assertEqual(event.metadata, {"auth_method": "self_registration"})

    def test_a_malformed_registration_is_one_invalid_request_failure(self):
        response = self.client.post(
            reverse("auth-register"),
            {"email": "not-an-email", "password": "x"},  # pragma: allowlist secret
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ACCOUNT_REGISTER)
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.reason_code, INVALID_REQUEST)
        self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
        self.assertIsNone(event.target_id)
        self.assertNotIn("not-an-email", str(event.metadata))


@override_settings(CACHES=LOCMEM_CACHE)
class DjangoAdminTests(TestCase):
    """D7: the Django admin is out of the sweep, but a superadmin's admin
    write is still traced through request.user."""

    def test_an_admin_write_leaves_one_event_naming_the_superadmin(self):
        admin = User.objects.create_superuser(
            email="admin.site@example.com",
            password="Admin-site-pw-1",  # pragma: allowlist secret
            first_name="Admin",
            last_name="Site",
        )
        admin.user_type = UserTypes.SUPER_ADMIN
        admin.email_verified_at = timezone.now()
        admin.save()
        self.client.force_login(admin)

        response = self.client.post(reverse("admin:users_waitlist_add"), {})

        self.assertIn(response.status_code, (200, 302))
        events = AuditEvent.objects.filter(actor_id=admin.pk)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().metadata.get("route"), "admin:users_waitlist_add")


class AnonymousDoorRefusalUnitTests(TestCase):
    """`emit_anonymous_door_refusal` in isolation: only a 4xx other than 429,
    for an anonymous request, on a registered door."""

    def request_to(self, route, user=None):
        from django.contrib.auth.models import AnonymousUser

        return SimpleNamespace(
            user=user or AnonymousUser(),
            resolver_match=SimpleNamespace(view_name=route),
            META={},
            method="POST",
        )

    def refuse(self, request, status_code):
        from audit.request_audit import emit_anonymous_door_refusal

        emit_anonymous_door_refusal(request, SimpleNamespace(status_code=status_code))
        return list(AuditEvent.objects.values_list("reason_code", flat=True))

    def test_a_refused_door_request_records_one_invalid_request(self):
        self.assertEqual(
            self.refuse(self.request_to("auth-verify"), 400), [INVALID_REQUEST]
        )

    def test_a_throttled_request_records_nothing(self):
        """The throttle refused it before the view ran; recording it would let
        a throttled caller write unlimited rows."""
        self.assertEqual(self.refuse(self.request_to("auth-verify"), 429), [])

    def test_a_server_error_is_not_a_malformed_request(self):
        self.assertEqual(self.refuse(self.request_to("auth-verify"), 500), [])

    def test_a_route_that_is_not_a_door_records_nothing(self):
        self.assertEqual(self.refuse(self.request_to("auth-otp"), 400), [])

    def test_a_signed_in_requester_is_left_to_the_generic_event(self):
        user = User.objects.create_user(
            email="door.signed.in@example.com",
            password="Door-pw-1",  # pragma: allowlist secret
            first_name="Door",
            last_name="SignedIn",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.assertEqual(self.refuse(self.request_to("auth-verify", user), 400), [])
