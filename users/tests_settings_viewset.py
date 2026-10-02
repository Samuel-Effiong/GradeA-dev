"""
Tenant scoping for SettingsViewSet.

This viewset had NO tests at all, despite being a full ModelViewSet over a
per-user row: `get_queryset` returns `Settings.objects.filter(user=user)`
for everyone except super admins, `list` is superadmin-only, and create /
destroy are deliberately disabled. None of that was pinned down, so a
queryset that quietly widened - or a `list` that lost its IsSuperAdmin -
would have shipped silently.

Settings rows are created by a post_save signal on CustomUser, so the
fixtures below read the existing row rather than creating one.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import Settings, ThemeType, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class SettingsScopingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        self.teacher = User.objects.create_user(
            email="settings.teacher@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Settings",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.other_teacher = User.objects.create_user(
            email="settings.other@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.super_admin = User.objects.create_user(
            email="settings.super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Settings",
            last_name="Super",
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
        )

        # Created by users.signals.create_default_settings_and_wallet.
        self.settings = Settings.objects.get(user=self.teacher)
        self.other_settings = Settings.objects.get(user=self.other_teacher)

    def detail_url(self, settings_obj):
        return reverse("settings-detail", kwargs={"pk": settings_obj.pk})

    # --- the legitimate flow --------------------------------------------------

    def test_owner_can_retrieve_own_settings(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self.detail_url(self.settings))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.settings.pk))

    def test_owner_can_update_own_settings(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            self.detail_url(self.settings),
            {"theme": ThemeType.DARK, "notify_weekly_summary": True},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.theme, ThemeType.DARK)
        self.assertTrue(self.settings.notify_weekly_summary)

    def test_my_settings_returns_the_callers_own_row(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("settings-my-settings"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.settings.pk))

    def test_my_settings_self_heals_a_missing_row(self):
        """
        The signal that creates Settings can fail silently, and there is no
        other way for a user to get a row - so this action creates one
        rather than 404ing.
        """
        Settings.objects.filter(user=self.teacher).delete()
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("settings-my-settings"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Settings.objects.filter(user=self.teacher).exists())

    # --- the tenant boundary --------------------------------------------------

    def test_user_cannot_retrieve_another_users_settings(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self.detail_url(self.other_settings))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_cannot_update_another_users_settings(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            self.detail_url(self.other_settings), {"theme": ThemeType.DARK}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other_settings.refresh_from_db()
        self.assertNotEqual(self.other_settings.theme, ThemeType.DARK)

    def test_user_cannot_reassign_their_settings_to_another_user(self):
        """`user` is read-only; a writable one would let a row be stolen."""
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            self.detail_url(self.settings), {"user": str(self.other_teacher.pk)}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.user_id, self.teacher.pk)

    def test_list_is_superadmin_only(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("settings-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_access_is_rejected(self):
        response = self.client.get(reverse("settings-my-settings"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- super admin ----------------------------------------------------------

    def test_superadmin_can_list_all_settings(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(reverse("settings-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superadmin_can_retrieve_another_users_settings(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(self.detail_url(self.other_settings))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # --- disabled write paths -------------------------------------------------

    def test_create_is_disabled(self):
        """Rows come from the signal; a second row per user is not a thing."""
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(reverse("settings-list"), {"theme": ThemeType.DARK})

        self.assertIn(
            response.status_code,
            (
                status.HTTP_405_METHOD_NOT_ALLOWED,
                status.HTTP_403_FORBIDDEN,
            ),
        )

    def test_destroy_is_disabled(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.delete(self.detail_url(self.settings))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Settings.objects.filter(pk=self.settings.pk).exists())

    def test_cached_my_settings_is_not_shared_between_users(self):
        """
        my_settings caches under a per-user key. If that key were not
        per-requester, the first caller would serve their preferences to
        everyone who asked next.
        """
        self.client.force_authenticate(user=self.teacher)
        primed = self.client.get(reverse("settings-my-settings"))
        self.assertEqual(primed.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.other_teacher)
        response = self.client.get(reverse("settings-my-settings"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.other_settings.pk))
        self.assertNotEqual(response.data["id"], str(self.settings.pk))
