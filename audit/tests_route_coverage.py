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
from django.urls import (
    URLPattern,
    URLResolver,
    clear_url_caches,
    get_resolver,
    path,
    reverse,
)
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass
from audit.models import AuditEvent
from audit.request_audit import (
    ANONYMOUS_AUDITED_ROUTES,
    EXCLUDED_ROUTES,
    INVALID_REQUEST,
    SERVER_ERROR,
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


def _write_methods(callback):
    view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    actions = getattr(callback, "actions", None)
    if actions:
        return [m for m in WRITE_METHODS if m in actions]
    if view_cls is not None:
        return [m for m in WRITE_METHODS if hasattr(view_cls, m)]
    return ["post"]  # a function view has no method map: probe it with POST


def unnamed_write_routes():
    """Paths of write routes with no URL name, outside the Django admin.
    The guard keys every decision on the name, so an unnamed write route
    would be outside it altogether (v2's G1): every one must be named."""
    return sorted(
        route_path
        for route_path, name, callback in _walk(get_resolver().url_patterns)
        if not name and not route_path.startswith("admin/") and _write_methods(callback)
    )


def write_routes():
    """{(url_name, method): [path kwarg names]} for every named write route,
    except the Django admin (D7; see DjangoAdminTests). Unnamed routes are
    refused by `unnamed_write_routes`."""
    routes = {}
    for route_path, name, callback in _walk(get_resolver().url_patterns):
        if not name or name.startswith("admin:"):
            continue
        kwargs = sorted(
            set(re.findall(r"\(\?P<(\w+)>", route_path))
            | {seg.split(":")[-1] for seg in re.findall(r"<([^>]+)>", route_path)}
        )
        if "format" in kwargs:  # DRF's format-suffix twin of a real route
            continue
        for method in _write_methods(callback):
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

    def test_every_write_route_outside_the_admin_is_named(self):
        """v2's G1: an unnamed write route would be outside every check."""
        self.assertEqual(unnamed_write_routes(), [])

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


class ReasonCodeCatalogueTests(TestCase):
    """The SM's merge-order ruling: S6a's emitter refuses any reason code
    outside audit.enums.ReasonCode, so every code S2 emits must be in it -
    or a refused or crashed request would silently leave zero events."""

    def test_every_code_s2_emits_is_in_the_catalogue(self):
        from audit.enums import ReasonCode
        from AutoGrader.reason_codes import AUDIT_ONLY_CODES

        for code in (INVALID_REQUEST, SERVER_ERROR):
            with self.subTest(code=code):
                self.assertIn(code, ReasonCode.values)
                self.assertIn(ReasonCode(code), AUDIT_ONLY_CODES)


class AnonymousDoorRefusalUnitTests(TestCase):
    """`emit_anonymous_refusal` in isolation: a 4xx other than 429 on a
    registered door, or a 5xx on any non-excluded write - for an anonymous
    request only."""

    def request_to(self, route, user=None):
        from django.contrib.auth.models import AnonymousUser

        return SimpleNamespace(
            user=user or AnonymousUser(),
            resolver_match=SimpleNamespace(view_name=route, func=None, kwargs={}),
            META={},
            method="POST",
        )

    def refuse(self, request, status_code):
        from audit.request_audit import emit_anonymous_refusal

        emit_anonymous_refusal(request, SimpleNamespace(status_code=status_code))
        return list(AuditEvent.objects.values_list("reason_code", flat=True))

    def test_a_refused_door_request_records_one_invalid_request(self):
        self.assertEqual(
            self.refuse(self.request_to("auth-verify"), 400), [INVALID_REQUEST]
        )

    def test_a_throttled_request_records_nothing(self):
        """The throttle refused it before the view ran; recording it would let
        a throttled caller write unlimited rows."""
        self.assertEqual(self.refuse(self.request_to("auth-verify"), 429), [])

    def test_a_crashed_door_records_one_server_error(self):
        """SM ruling on v2's N1: a crash must not leave zero trace."""
        self.assertEqual(
            self.refuse(self.request_to("auth-verify"), 500), [SERVER_ERROR]
        )
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.AUTH_LOGIN)
        self.assertEqual(event.error_class, ErrorClass.SYSTEM)

    def test_any_crashed_anonymous_write_records_one_server_error(self):
        self.assertEqual(
            self.refuse(self.request_to("some-open-route"), 502), [SERVER_ERROR]
        )
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.STATE_CHANGE)
        self.assertEqual(event.metadata["route"], "some-open-route")

    def test_an_excluded_route_that_crashes_records_nothing(self):
        self.assertEqual(self.refuse(self.request_to("stripe-webhook"), 500), [])

    def test_a_crashed_read_records_nothing(self):
        request = self.request_to("some-open-route")
        request.method = "GET"
        self.assertEqual(self.refuse(request, 500), [])

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


class _OpenWrite(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        return Response({"ok": True}, status=201)


# The real URLconf plus one unnamed and one named open write route (v2's
# probe). UnnamedRouteGuardTests points ROOT_URLCONF at this module; a real
# module path, since get_resolver() caches on the URLconf.
from AutoGrader import urls as _root_urls  # noqa: E402

urlpatterns = list(_root_urls.urlpatterns) + [
    path("route-coverage-probe/unnamed/", _OpenWrite.as_view()),
    path(
        "route-coverage-probe/named/",
        _OpenWrite.as_view(),
        name="route-coverage-probe-named",
    ),
]


@override_settings(CACHES=LOCMEM_CACHE, ROOT_URLCONF=__name__)
class UnnamedRouteGuardTests(TestCase):
    """The guard itself catches an unnamed write route (G1), and still sees
    a named one."""

    def setUp(self):
        clear_url_caches()
        self.addCleanup(clear_url_caches)

    def test_an_unnamed_write_route_is_caught(self):
        self.assertEqual(unnamed_write_routes(), ["route-coverage-probe/unnamed/"])

    def test_a_named_one_is_in_the_guard(self):
        self.assertIn(("route-coverage-probe-named", "post"), write_routes())


@override_settings(CACHES=LOCMEM_CACHE)
class CrashedDoorTests(TestCase):
    """v2's P1, through the real stack: a door that crashes records one
    SERVER_ERROR failure, not nothing."""

    def test_a_crashed_registration_records_one_server_error(self):
        cache.clear()
        self.addCleanup(cache.clear)
        client = APIClient(raise_request_exception=False, REMOTE_ADDR="10.9.0.1")
        with patch(
            "users.views.CustomUserSerializer.is_valid",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                reverse("auth-register"), {"email": "x@example.com"}, format="json"
            )

        self.assertEqual(response.status_code, 500)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ACCOUNT_REGISTER)
        self.assertEqual(event.reason_code, SERVER_ERROR)
        self.assertEqual(event.error_class, ErrorClass.SYSTEM)
        self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
        self.assertNotIn("x@example.com", str(event.metadata))
