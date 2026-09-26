"""
users/tests_superadmin_tenancy.py
=============================================
Keeps platform staff and tenant membership separate.

BACKGROUND -- QA reported superadmins appearing as the school admin of
schools. One of the two routes that allowed it ran through this
serializer: `school` and `user_type` are re-opened for writing when the
requester is a super admin (so POST /users can mint non-teacher accounts),
and nothing then stopped a super admin from PATCHing *themselves* to
user_type=SCHOOL_ADMIN with a school attached.

The resulting account is the worst of both worlds. Every school screen
selects admins on user_type=SCHOOL_ADMIN alone (classrooms/views.py), so
it starts being reported as that school's admin; meanwhile IsSuperAdmin
checks user_type too, so the same edit silently revokes the account's
superadmin access -- it can now administer neither the platform nor,
legitimately, the school.

The billing half of the same invariant is covered by
billing/tests/test_license_admin_user_guard.py.
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import School
from users.models import CustomUser, UserTypes


class SuperadminTenancySeparationTest(APITestCase):
    def setUp(self):
        cache.clear()
        if not hasattr(cache, "delete_pattern"):
            cache.delete_pattern = lambda x: None

        self.school = School.objects.create(name="Tenancy School")
        self.superadmin = CustomUser.objects.create_superuser(
            email="super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.is_active = True
        self.superadmin.save()
        self.client.force_authenticate(user=self.superadmin)
        self.self_url = reverse("user-detail", kwargs={"pk": self.superadmin.id})

    def test_superadmin_cannot_make_themselves_a_school_admin(self):
        """The exact shape QA described."""
        response = self.client.patch(
            self.self_url,
            {"school": str(self.school.id), "user_type": UserTypes.SCHOOL_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.superadmin.refresh_from_db()
        self.assertEqual(self.superadmin.user_type, UserTypes.SUPER_ADMIN)
        self.assertIsNone(self.superadmin.school_id)

    def test_superadmin_cannot_be_attached_to_a_school_at_all(self):
        """Even without changing user_type -- membership is the problem."""
        response = self.client.patch(
            self.self_url, {"school": str(self.school.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("school", response.data)

        self.superadmin.refresh_from_db()
        self.assertIsNone(self.superadmin.school_id)

    def test_one_superadmin_cannot_demote_another_into_a_tenant(self):
        """Not just self-edits -- the target's status is what matters."""
        other = CustomUser.objects.create_superuser(
            email="other-super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Super",
        )
        other.user_type = UserTypes.SUPER_ADMIN
        other.save()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": other.id}),
            {"school": str(self.school.id), "user_type": UserTypes.SCHOOL_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        other.refresh_from_db()
        self.assertEqual(other.user_type, UserTypes.SUPER_ADMIN)

    def test_a_schooled_account_cannot_be_promoted_to_superadmin(self):
        """The mirror image: no stale tenancy left hanging off staff."""
        school_admin = CustomUser.objects.create_user(
            email="admin@tenancy.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": school_admin.id}),
            {"user_type": UserTypes.SUPER_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user_type", response.data)
        school_admin.refresh_from_db()
        self.assertEqual(school_admin.user_type, UserTypes.SCHOOL_ADMIN)

    def test_promotion_is_allowed_when_the_school_is_cleared_in_the_same_request(self):
        """The guard states the remedy, so the remedy has to work."""
        teacher = CustomUser.objects.create_user(
            email="teacher@tenancy.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Tea",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": teacher.id}),
            {"user_type": UserTypes.SUPER_ADMIN, "school": None},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        teacher.refresh_from_db()
        self.assertEqual(teacher.user_type, UserTypes.SUPER_ADMIN)
        self.assertIsNone(teacher.school_id)

    def test_ordinary_tenant_assignment_still_works(self):
        """The guard must not block the legitimate operation it protects."""
        other_school = School.objects.create(name="Second School")
        teacher = CustomUser.objects.create_user(
            email="teacher2@tenancy.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Tea",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": teacher.id}),
            {"school": str(other_school.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        teacher.refresh_from_db()
        self.assertEqual(teacher.school_id, other_school.id)

    def test_superadmin_keeps_platform_access_after_a_rejected_edit(self):
        """The failed edit must leave the account fully usable."""
        self.client.patch(
            self.self_url,
            {"school": str(self.school.id), "user_type": UserTypes.SCHOOL_ADMIN},
            format="json",
        )

        response = self.client.get(reverse("school-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
