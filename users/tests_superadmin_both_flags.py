"""H-19: two superadmin gates accepted EITHER flag instead of both.

IsSuperAdmin, and every other superadmin check in the app, requires
`is_superuser AND user_type == SUPER_ADMIN`. Two places used `or`:

  * SettingsViewSet.get_queryset (users/views.py) - widened the queryset to
    every user's Settings row, for retrieve AND partial_update;
  * SchoolViewSet.monthly_token_usage (classrooms/views.py) - let the caller
    name any ?school_id=.

`CustomUserManager.create_superuser()` sets is_superuser but leaves
user_type at its TEACHER default, so a `manage.py createsuperuser` account -
meant for Django admin - got app-level superadmin reach on exactly these
two endpoints, while IsSuperAdmin refused it everywhere else. The reverse
half-account (user_type SUPER_ADMIN, is_superuser False) got the same.

Each refusal test has a matching test proving a real superadmin (both flags)
and the ordinary owner/school-admin flows still work.
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import School
from users.models import CustomUser, Settings, UserTypes

PASSWORD = "password123"  # pragma: allowlist secret


class SuperadminGateFixture(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        self.school_a = School.objects.create(name="H19 School A")
        self.school_b = School.objects.create(name="H19 School B")

        self.teacher = CustomUser.objects.create_user(
            email="h19-teacher@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            school=self.school_a,
        )
        self.school_admin_b = CustomUser.objects.create_user(
            email="h19-schooladmin-b@example.com",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school_b,
        )
        # Exactly what `manage.py createsuperuser` produces.
        self.django_admin = CustomUser.objects.create_superuser(
            email="h19-djadmin@example.com", password=PASSWORD
        )
        self.type_only_superadmin = CustomUser.objects.create_user(
            email="h19-typeonly@example.com",
            password=PASSWORD,
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=False,
        )
        self.superadmin = CustomUser.objects.create_user(
            email="h19-superadmin@example.com",
            password=PASSWORD,
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )

        self.half_superadmins = (self.django_admin, self.type_only_superadmin)

    def as_user(self, user):
        self.client.force_authenticate(user=user)


class SettingsGateTest(SuperadminGateFixture):
    def setUp(self):
        super().setUp()
        # Created by users.signals.create_default_settings_and_wallet.
        self.victim_settings = Settings.objects.get(user=self.teacher)

    def detail_url(self, settings_obj):
        return reverse("settings-detail", kwargs={"pk": settings_obj.pk})

    def test_create_superuser_leaves_user_type_teacher(self):
        # The precondition that made the `or` exploitable.
        self.assertTrue(self.django_admin.is_superuser)
        self.assertEqual(self.django_admin.user_type, UserTypes.TEACHER)

    def test_half_superadmin_cannot_read_another_users_settings(self):
        for user in self.half_superadmins:
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.client.get(self.detail_url(self.victim_settings))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertNotIn(str(self.teacher.id), response.content.decode())

    def test_half_superadmin_cannot_edit_another_users_settings(self):
        for user in self.half_superadmins:
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.client.patch(
                    self.detail_url(self.victim_settings),
                    {"notify_weekly_summary": True},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.victim_settings.refresh_from_db()
                self.assertFalse(self.victim_settings.notify_weekly_summary)

    # --- legitimate flows ---------------------------------------------------

    def test_half_superadmin_still_reaches_own_settings(self):
        for user in self.half_superadmins:
            with self.subTest(user=user.email):
                self.as_user(user)
                own = Settings.objects.get(user=user)
                response = self.client.patch(
                    self.detail_url(own), {"notify_weekly_summary": True}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                own.refresh_from_db()
                self.assertTrue(own.notify_weekly_summary)

    def test_true_superadmin_can_read_and_edit_another_users_settings(self):
        self.as_user(self.superadmin)
        url = self.detail_url(self.victim_settings)

        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        response = self.client.patch(
            url, {"notify_weekly_summary": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.victim_settings.refresh_from_db()
        self.assertTrue(self.victim_settings.notify_weekly_summary)

    def test_teacher_can_edit_own_settings(self):
        self.as_user(self.teacher)
        response = self.client.patch(
            self.detail_url(self.victim_settings),
            {"notify_weekly_summary": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class MonthlyTokenUsageGateTest(SuperadminGateFixture):
    url_name = "school-monthly-token-usage"

    def get_usage(self, **params):
        return self.client.get(reverse(self.url_name), params)

    def test_half_superadmin_cannot_read_any_schools_usage(self):
        for user in self.half_superadmins:
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.get_usage(school_id=str(self.school_a.id))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn("month", response.content.decode())

    def test_school_admin_cannot_name_another_school(self):
        self.as_user(self.school_admin_b)
        response = self.get_usage(school_id=str(self.school_a.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- legitimate flows ---------------------------------------------------

    def test_true_superadmin_can_read_any_schools_usage(self):
        self.as_user(self.superadmin)
        response = self.get_usage(school_id=str(self.school_a.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 12)

    def test_school_admin_still_reads_own_school_without_school_id(self):
        self.as_user(self.school_admin_b)
        response = self.get_usage()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 12)
