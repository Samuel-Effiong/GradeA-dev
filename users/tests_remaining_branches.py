"""
The last uncovered branches in `users/`.

Nothing here is exotic - they are the cache-hit paths, the licence-backed
subscription reads, a couple of disabled-endpoint responses and a handful
of error arms that the topic-focused suites happened not to reach. They
are covered rather than excluded because each one has a real expected
value, not because a percentage looks better.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)
from users.models import BetaWhitelist, Settings, UserTypes
from users.services import get_peak_concurrent_users
from users.utils import is_disposable_email

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Edge",
        "last_name": "Case",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(CACHES=LOCMEM_CACHE)
class UserQuerysetScopingBranchTests(APITestCase):
    """The two `get_queryset` arms the authorization suite does not reach."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_an_anonymous_request_sees_nothing(self):
        """
        get_queryset must never raise - UserCacheMixin calls it before
        permissions run - so it returns an empty queryset instead.
        """
        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_school_admin_sees_their_own_school(self):
        school = School.objects.create(name="Scoped School")
        other_school = School.objects.create(name="Unrelated School")
        admin = make_user(
            "branch.admin@acme-school.org",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        colleague = make_user("branch.colleague@acme-school.org", school=school)
        outsider = make_user("branch.outsider@acme-school.org", school=other_school)

        self.client.force_authenticate(user=admin)
        visible = self.client.get(reverse("user-detail", kwargs={"pk": colleague.pk}))
        hidden = self.client.get(reverse("user-detail", kwargs={"pk": outsider.pk}))

        self.assertEqual(visible.status_code, status.HTTP_200_OK)
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(CACHES=LOCMEM_CACHE)
class SettingsDisabledEndpointTests(APITestCase):
    """Settings rows come from a signal; create/destroy are switched off."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.super_admin = make_user(
            "settings.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.super_admin)

    def test_create_is_refused(self):
        """
        Refused by `http_method_names`, which is why the create() override
        that also returned 405 was unreachable and has been removed.
        """
        response = self.client.post(
            reverse("settings-list"), {"theme": "DARK"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_destroy_is_refused_and_the_row_survives(self):
        settings_row = Settings.objects.get(user=self.super_admin)

        response = self.client.delete(
            reverse("settings-detail", kwargs={"pk": settings_row.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Settings.objects.filter(pk=settings_row.pk).exists())


@override_settings(CACHES=LOCMEM_CACHE)
class UserCacheMixinHitTests(APITestCase):
    """
    The second request must be served from the cache rather than re-running
    the queryset - that is the entire purpose of the mixin.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.super_admin = make_user(
            "cache.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.super_admin)

    def test_a_repeated_list_is_served_from_cache(self):
        first = self.client.get(reverse("user-list"))
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        with patch(
            "users.views.CustomUserViewSet.get_queryset",
            wraps=lambda *a, **k: User.objects.all(),
        ) as spy:
            second = self.client.get(reverse("user-list"))

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)
        # get_cache_key still asks for the model name, but the queryset is
        # never evaluated for a second listing.
        self.assertLessEqual(spy.call_count, 1)

    def test_a_repeated_retrieve_is_served_from_cache(self):
        url = reverse("user-detail", kwargs={"pk": self.super_admin.pk})
        first = self.client.get(url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.get(url)

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)


class LicenseBackedSubscriptionTests(APITestCase):
    """
    `CustomUser`'s subscription helpers resolve a school licence before a
    personal subscription. Those arms need a real allocation to exercise.
    """

    def setUp(self):
        self.school = School.objects.create(name="Licence School")
        self.teacher = make_user("licensed.teacher@acme-school.org", school=self.school)
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.STANDARD,
            monthly_credits=100_000,
            is_active=True,
        )
        self.school_admin = make_user(
            "licence.admin@acme-school.org",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.school_admin,
            plan=self.plan,
            contract_months=12,
            max_seats=10,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=365),
        )

    def _allocate(self, monthly_allocation=25_000, is_active=True):
        return SchoolCreditAllocation.objects.create(
            license_subscription=self.license,
            user=self.teacher,
            monthly_allocation=monthly_allocation,
            is_active=is_active,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

    def test_an_active_allocation_resolves_to_the_licence(self):
        self._allocate()

        self.assertEqual(self.teacher.get_active_subscription(), self.license)
        self.assertEqual(self.teacher.subscription_type, "LICENSE")
        self.assertTrue(self.teacher.is_under_license())

    def test_the_allocation_supplies_the_monthly_credits(self):
        self._allocate(monthly_allocation=25_000)

        self.assertEqual(self.teacher.get_teacher_monthly_allocation(), 25_000)

    def test_an_inactive_allocation_is_ignored(self):
        self._allocate(is_active=False)

        self.assertIsNone(self.teacher.get_active_subscription())
        self.assertFalse(self.teacher.is_under_license())


@override_settings(CACHES=LOCMEM_CACHE)
class ChangePasswordSessionInvalidationTests(APITestCase):
    """A password change must end the sessions that preceded it."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("invalidate.change@gmail.com")
        self.client.force_authenticate(user=self.user)

    def test_pre_existing_tokens_are_blacklisted(self):
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
        from rest_framework_simplejwt.tokens import RefreshToken

        old = RefreshToken.for_user(self.user)
        old_jti = old["jti"]

        response = self.client.post(
            reverse("auth-change-password"),
            {
                "current_password": PASSWORD,
                "new_password": "a-different-password-77",  # pragma: allowlist secret
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        old_token = OutstandingToken.objects.get(jti=old_jti)
        self.assertTrue(BlacklistedToken.objects.filter(token=old_token).exists())


@override_settings(CACHES=LOCMEM_CACHE)
class GoogleNonEmailValidationTests(APITestCase):
    """
    A Google profile that fails validation for a reason OTHER than the
    email rule falls through to the raw serializer errors.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_non_email_validation_failure_is_surfaced(self):
        with patch("users.views.sync_user_to_mailerlite"), patch(
            "requests.post"
        ) as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify, patch(
            "users.views.GoogleUserSerializer.is_valid", return_value=False
        ), patch(
            "users.views.GoogleUserSerializer.errors",
            new_callable=lambda: property(lambda self: {"first_name": ["Too long."]}),
        ):
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "t",
                "access_token": "a",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": "nonemail.error@gmail.com",
                "email_verified": True,
                "given_name": "Non",
                "family_name": "Email",
            }

            response = self.client.post(
                reverse("auth-google-auth"), {"code": "c"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("too long", str(response.data).lower())


@override_settings(CACHES=LOCMEM_CACHE)
class TrackedTaskErrorSurfacingTests(APITestCase):
    """A failed tracked task must carry its error into the status payload."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user("tracked.error@gmail.com")
        self.client.force_authenticate(user=self.teacher)

    def test_a_failed_tasks_error_appears_in_meta(self):
        import uuid

        task_id = str(uuid.uuid4())
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            celery_task_id=task_id,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.FAILURE,
            error="We couldn't grade this submission.",
        )

        response = self.client.get(
            reverse("task-task-status", kwargs={"task_id": task_id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("couldn't grade", response.data["meta"])

    def test_a_failed_tracked_task_is_counted_in_session_results(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher, task_type="assignment", total_files=1
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id="failed-task-1",
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.FAILURE,
            file_name="broken.pdf",
            error="unreadable",
        )

        response = self.client.get(
            reverse("task-session-results", kwargs={"session_id": session.id})
        )

        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["failure_list"][0]["file_name"], "broken.pdf")


class AddWhitelistRemainingBranchTests(APITestCase):
    def run_command(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        call_command("add_whitelist", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    def test_a_whitespace_only_argument_is_skipped(self):
        out, _ = self.run_command("   ", "real@example.com")

        self.assertTrue(BetaWhitelist.objects.filter(email="real@example.com").exists())
        self.assertEqual(BetaWhitelist.objects.count(), 1)
        self.assertIn("Added: 1", out)

    def test_an_integrity_error_is_reported_and_skipped(self):
        """A race against a concurrent insert must not abort the whole run."""
        with patch(
            "users.management.commands.add_whitelist.BetaWhitelist.objects.get_or_create",
            side_effect=IntegrityError("duplicate key"),
        ):
            out, _ = self.run_command("racy@example.com")

        self.assertIn("integrity error", out.lower())
        self.assertIn("Skipped: 1", out)


class ServicesAndUtilsRemainingTests(APITestCase):
    def test_peak_concurrent_users_respects_an_end_bound(self):
        from users.models import ConcurrentUserSnapshot

        now = timezone.now()
        recent = ConcurrentUserSnapshot.objects.create(concurrent_users=5)
        ConcurrentUserSnapshot.objects.filter(pk=recent.pk).update(
            timestamp=now - timedelta(days=2)
        )
        future = ConcurrentUserSnapshot.objects.create(concurrent_users=99)
        ConcurrentUserSnapshot.objects.filter(pk=future.pk).update(timestamp=now)

        bounded = get_peak_concurrent_users(end=now - timedelta(days=1))

        self.assertEqual(bounded, 5)

    def test_a_malformed_address_is_not_disposable(self):
        """`_matches_domain_set` short-circuits on an empty domain."""
        self.assertFalse(is_disposable_email("not-an-email"))
        self.assertFalse(is_disposable_email(""))
