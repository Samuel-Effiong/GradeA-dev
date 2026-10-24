"""
Authorization tests for the user-management and beta-gate endpoints.

These cover the negative cases the app previously had none of: before this
file existed there was not a single 401/403 assertion anywhere in `users/`,
the app that owns authentication.

Three concrete holes are pinned down here:

1. `PATCH /users/<id>` let any authenticated user edit any other user, and
   `user_type` was writable, so a student could promote themselves to
   SUPER_ADMIN.
2. `/whitelist` and `/waitlist` were `AllowAny`, leaking every signup email
   and letting an anonymous caller self-whitelist past the private beta.
3. Scoping must not break the one legitimate flow through this endpoint -
   a user editing their own profile - because `users/me` is GET-only and
   `SettingsViewSet` only covers display preferences, so there is no other
   write path for first_name/bio.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import BetaWhitelist, UserTypes, Waitlist

User = get_user_model()

# UserCacheMixin and the DRF throttles share the default cache. Pin it to
# LocMem so a run cannot inherit (or leak) state via the real Redis.
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class UserObjectScopingTests(APITestCase):
    def setUp(self):
        cache.clear()

        self.school = School.objects.create(name="Scoping School")
        self.other_school = School.objects.create(name="Other School")

        self.teacher = User.objects.create_user(
            email="scope.teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Scope",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            school=self.school,
        )
        self.other_teacher = User.objects.create_user(
            email="scope.other-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            school=self.other_school,
        )
        self.student = User.objects.create_user(
            email="scope.student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Scope",
            last_name="Student",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.unrelated_student = User.objects.create_user(
            email="scope.unrelated@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Unrelated",
            last_name="Student",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.super_admin = User.objects.create_user(
            email="scope.super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
        )

        self.session = Session.objects.create(name="Scope Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Scoped Course",
            teacher=self.teacher,
            session=self.session,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def detail_url(self, user):
        return reverse("user-detail", kwargs={"pk": user.pk})

    # --- the regression guard -------------------------------------------------
    # Self-service profile editing is the ONE legitimate use of this
    # endpoint. If scoping ever breaks it, users cannot change their own
    # name or bio anywhere in the product.

    def test_teacher_can_patch_own_profile(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            self.detail_url(self.teacher),
            {"first_name": "Renamed", "bio": "hello"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.first_name, "Renamed")
        self.assertEqual(self.teacher.bio, "hello")

    def test_student_can_patch_own_editable_profile_fields(self):
        """
        Students may not change their name - see CustomUserSerializer.validate,
        which is existing product policy - but they must still be able to edit
        the fields that are theirs, and this is the only endpoint that allows it.
        """
        self.client.force_authenticate(user=self.student)

        response = self.client.patch(self.detail_url(self.student), {"bio": "hi there"})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.student.refresh_from_db()
        self.assertEqual(self.student.bio, "hi there")

    def test_user_can_retrieve_own_record(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(self.detail_url(self.student))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # --- privilege escalation -------------------------------------------------

    def test_user_type_escalation_is_ignored(self):
        """A student PATCHing their own user_type must not become an admin."""
        self.client.force_authenticate(user=self.student)

        response = self.client.patch(
            self.detail_url(self.student),
            {"user_type": UserTypes.SUPER_ADMIN},
        )

        # Read-only fields are stripped rather than rejected, so this is a
        # 200 that quietly did nothing - assert on the DB, not the status.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertEqual(self.student.user_type, UserTypes.STUDENT)
        self.assertFalse(self.student.is_superuser)

    def test_school_reassignment_is_ignored(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.patch(
            self.detail_url(self.student),
            {"school": str(self.other_school.pk)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.school_id)

    # --- cross-user access ----------------------------------------------------

    def test_student_cannot_retrieve_another_user(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(self.detail_url(self.other_teacher))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_cannot_patch_another_user(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.patch(
            self.detail_url(self.other_teacher),
            {"first_name": "Hacked"},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other_teacher.refresh_from_db()
        self.assertEqual(self.other_teacher.first_name, "Other")

    def test_student_cannot_delete_another_user(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.delete(self.detail_url(self.other_teacher))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.other_teacher.pk).exists())

    def test_student_cannot_delete_self(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.delete(self.detail_url(self.student))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_teacher_cannot_patch_own_student(self):
        """Seeing a student in your roster is not permission to edit them."""
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            self.detail_url(self.student),
            {"bio": "Edited by teacher"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.bio, "Edited by teacher")

    def test_teacher_cannot_see_unenrolled_student(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self.detail_url(self.unrelated_student))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_see_teacher_from_other_school(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self.detail_url(self.other_teacher))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- super admin retains access -------------------------------------------

    def test_superadmin_can_retrieve_any_user(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(self.detail_url(self.other_teacher))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superadmin_can_set_privileged_fields(self):
        """The read-only carve-out must not lock admins out of admin work."""
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.patch(
            self.detail_url(self.teacher),
            {"user_type": UserTypes.SCHOOL_ADMIN},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.user_type, UserTypes.SCHOOL_ADMIN)

    def test_list_is_superadmin_only(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- cache isolation ------------------------------------------------------

    def test_cached_detail_is_not_shared_across_users(self):
        """
        UserCacheMixin caches retrieve responses. If the key were not
        per-requester, a victim populating the cache would serve their own
        record to an attacker asking for the same pk.
        """
        self.client.force_authenticate(user=self.other_teacher)
        primed = self.client.get(self.detail_url(self.other_teacher))
        self.assertEqual(primed.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.detail_url(self.other_teacher))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(CACHES=LOCMEM_CACHE)
class BetaGateAccessTests(APITestCase):
    """
    `/whitelist` and `/waitlist` shipped as AllowAny with the intended
    permission commented out beside them. That exposed every signup email
    and let anyone promote themselves past the private beta gate.

    Signup is open now (see users/tests_open_signup.py), so these rows no
    longer grant access - but they are still a list of real people's email
    addresses, so the endpoints stay superadmin-only.
    """

    def setUp(self):
        cache.clear()

        self.teacher = User.objects.create_user(
            email="gate.teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Gate",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.super_admin = User.objects.create_user(
            email="gate.super@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Gate",
            last_name="Super",
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
        )
        self.waitlist_entry = Waitlist.objects.create(email="hopeful@example.com")

    def test_anonymous_cannot_list_whitelist(self):
        response = self.client.get(reverse("whitelist-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_anonymous_cannot_list_waitlist(self):
        response = self.client.get(reverse("waitlist-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_non_admin_cannot_list_whitelist(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("whitelist-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_authenticated_non_admin_cannot_list_waitlist(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("waitlist-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_self_whitelist(self):
        response = self.client.post(
            reverse("whitelist-list"),
            {"email": "attacker@example.com"},
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(
            BetaWhitelist.objects.filter(email="attacker@example.com").exists()
        )

    def test_anonymous_cannot_transfer_waitlist_entry(self):
        response = self.client.post(
            reverse("waitlist-transfer", kwargs={"pk": self.waitlist_entry.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(
            BetaWhitelist.objects.filter(email="hopeful@example.com").exists()
        )
        self.assertTrue(Waitlist.objects.filter(pk=self.waitlist_entry.pk).exists())

    def test_superadmin_can_still_manage_whitelist(self):
        self.client.force_authenticate(user=self.super_admin)

        response = self.client.get(reverse("whitelist-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
