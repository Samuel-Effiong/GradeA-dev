"""
The last reachable branches: defence-in-depth guards, serializer rules and
cache-hit paths that the topic suites did not happen to touch.

Two of these exercise guards that are unreachable through the HTTP router
because an earlier layer already refuses the request. They are called
directly rather than through the client, deliberately: the point of
defence in depth is that the inner guard still holds if the outer one is
ever loosened, and that is only demonstrable by invoking it.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase

from classrooms.models import School
from users.models import Settings, UserTypes
from users.renderers import flatten_errors
from users.serializers import ChangePasswordSerializer, CustomUserSerializer
from users.views import CustomUserViewSet

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Final",
        "last_name": "Branch",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


class DefenceInDepthGuardTests(APITestCase):
    """
    Guards that the router never reaches, because permissions refuse the
    request first. They still have to be correct.
    """

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_the_queryset_is_empty_for_an_anonymous_request(self):
        """
        `get_queryset` is called by UserCacheMixin.get_cache_key before
        permissions run, so it must fail closed rather than raise - a raise
        here would surface as a 500 instead of a 401.
        """
        request = self.factory.get("/")
        request.user = AnonymousUser()
        view = CustomUserViewSet()
        view.request = request

        self.assertEqual(list(view.get_queryset()), [])

    def test_create_refuses_a_non_superadmin_even_if_permissions_are_bypassed(self):
        """
        `get_permissions` already restricts POST /users to super admins, so
        this inner check is redundant today - which is exactly why it needs
        a test: nothing else would notice if it stopped working.
        """
        teacher = make_user("depth.teacher@gmail.com")
        request = self.factory.post("/", {}, format="json")
        request.user = teacher
        view = CustomUserViewSet()
        view.request = request
        view.format_kwarg = None

        response = view.create(request)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("do not have permission", str(response.data).lower())

    def test_create_admits_a_superadmin(self):
        super_admin = make_user(
            "depth.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        request = self.factory.post("/", {}, format="json")
        request.user = super_admin
        view = CustomUserViewSet()
        view.request = request
        view.format_kwarg = None

        with patch(
            "users.views.viewsets.ModelViewSet.create",
            return_value="delegated",
        ) as mock_super:
            result = view.create(request)

        self.assertEqual(result, "delegated")
        mock_super.assert_called_once()


@override_settings(CACHES=LOCMEM_CACHE)
class MySettingsCacheTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("mysettings.cache@gmail.com")
        self.client.force_authenticate(user=self.user)

    def test_a_repeated_call_is_served_from_the_cache(self):
        url = reverse("settings-my-settings")
        first = self.client.get(url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        with patch("users.views.Settings.objects.get_or_create") as spy:
            second = self.client.get(url)

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data, first.data)
        spy.assert_not_called()

    def test_the_cache_is_per_user(self):
        other = make_user("mysettings.other@gmail.com")
        url = reverse("settings-my-settings")

        mine = self.client.get(url)
        self.client.force_authenticate(user=other)
        theirs = self.client.get(url)

        self.assertNotEqual(mine.data["id"], theirs.data["id"])
        self.assertEqual(theirs.data["id"], str(Settings.objects.get(user=other).pk))


class SerializerRuleBranchTests(APITestCase):
    """Validation arms of CustomUserSerializer not reached elsewhere."""

    def setUp(self):
        self.school = School.objects.create(name="Rule School")
        self.super_admin = make_user(
            "rule.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )

    def _as_super_admin(self, instance, data):
        request = APIRequestFactory().patch("/")
        request.user = self.super_admin
        return CustomUserSerializer(
            instance, data=data, partial=True, context={"request": request}
        )

    def test_a_superadmin_cannot_be_demoted_into_a_tenant_role_alone(self):
        """
        The user_type half of the platform-staff guard, with no `school` in
        the payload - the school half is covered in tests_superadmin_tenancy.
        """
        serializer = self._as_super_admin(
            self.super_admin, {"user_type": UserTypes.TEACHER}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("user_type", serializer.errors)

    def test_a_student_may_not_edit_their_name(self):
        student = make_user(
            "rule.student@student.local",
            user_type=UserTypes.STUDENT,
            first_name="Original",
        )
        serializer = CustomUserSerializer(
            student, data={"first_name": "Renamed"}, partial=True
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("first_name", serializer.errors)

    def test_a_student_may_still_edit_other_fields(self):
        student = make_user("rule.student2@student.local", user_type=UserTypes.STUDENT)
        serializer = CustomUserSerializer(student, data={"bio": "hi"}, partial=True)

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_personal_email_teacher_cannot_be_attached_to_a_school(self):
        """
        The individual track and the school track stay separate: a teacher
        on a consumer mailbox must be invited through the school's licence,
        not attached directly.
        """
        teacher = make_user("rule.personal@gmail.com")
        serializer = self._as_super_admin(teacher, {"school": str(self.school.pk)})

        self.assertFalse(serializer.is_valid())
        self.assertIn("school", serializer.errors)

    def test_a_business_email_teacher_may_be_attached_to_a_school(self):
        teacher = make_user("rule.business@acme-school.org")
        serializer = self._as_super_admin(teacher, {"school": str(self.school.pk)})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_a_system_generated_student_email_is_masked_in_output(self):
        """
        `@student.local` placeholders are not real mailboxes, so they are
        never shown back to a client.
        """
        student = make_user("masked.student@student.local", user_type=UserTypes.STUDENT)

        data = CustomUserSerializer(student).data

        self.assertIsNone(data["email"])
        self.assertTrue(data["is_system_generated_email"])

    def test_a_real_email_is_not_masked(self):
        teacher = make_user("visible.teacher@gmail.com")

        data = CustomUserSerializer(teacher).data

        self.assertEqual(data["email"], "visible.teacher@gmail.com")
        self.assertFalse(data["is_system_generated_email"])


class ChangePasswordSerializerRuleTests(APITestCase):
    def test_the_new_password_must_differ_from_the_current_one(self):
        serializer = ChangePasswordSerializer(
            data={"current_password": PASSWORD, "new_password": PASSWORD}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("same as the old", str(serializer.errors).lower())

    def test_a_genuinely_new_password_validates(self):
        serializer = ChangePasswordSerializer(
            data={
                "current_password": PASSWORD,
                "new_password": "something-else-entirely-31",  # pragma: allowlist secret
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)


class FlattenErrorsBranchTests(APITestCase):
    """`flatten_errors` walks arbitrary DRF error shapes."""

    def test_a_multi_key_mapping_is_labelled_per_field(self):
        message = flatten_errors({"email": ["Required."], "detail": "Also this."})

        self.assertIn("Email: Required.", message)
        self.assertIn("Also this.", message)

    def test_a_nested_mapping_is_flattened(self):
        message = flatten_errors({"profile": {"bio": ["Too long."]}})

        self.assertIn("Too long.", message)

    def test_a_single_detail_key_is_unwrapped(self):
        self.assertEqual(flatten_errors({"detail": "Not found."}), "Not found.")

    def test_a_bare_list_is_joined(self):
        message = flatten_errors(["First.", "Second."])

        self.assertIn("First.", message)
        self.assertIn("Second.", message)


class DefensiveFallthroughTests(APITestCase):
    """
    Guards that cannot be reached through normal input because an earlier
    layer already constrains it. Each is exercised by deliberately relaxing
    that outer layer - which is the only way to show the inner guard does
    what it claims, and the situation it is written for.
    """

    def test_a_stale_privileged_field_name_does_not_crash_the_serializer(self):
        """
        `__init__` walks PRIVILEGED_FIELDS and skips names that are not on
        the serializer. Today every name matches, so the skip never runs -
        but the guard exists so that renaming a field cannot turn every
        request into a KeyError.
        """
        with patch.object(
            CustomUserSerializer,
            "PRIVILEGED_FIELDS",
            ("user_type", "school", "a_field_that_no_longer_exists"),
        ):
            serializer = CustomUserSerializer()

            self.assertTrue(serializer.fields["user_type"].read_only)

    def test_platform_staff_may_be_patched_without_a_role_change(self):
        """
        The staff guard rejects a tenant role and rejects a school, but an
        edit that requests neither must pass straight through.
        """
        super_admin = make_user(
            "fallthrough.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        request = APIRequestFactory().patch("/")
        request.user = super_admin

        serializer = CustomUserSerializer(
            super_admin,
            data={"user_type": UserTypes.SUPER_ADMIN, "first_name": "Renamed"},
            partial=True,
            context={"request": request},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_login_tolerates_a_payload_with_no_email(self):
        """
        The lockout bookkeeping is keyed on finding the account first. If
        it cannot be found, login must still delegate to simplejwt rather
        than assuming a user object exists.
        """
        from users.serializers import CustomTokenObtainPairSerializer

        serializer = CustomTokenObtainPairSerializer()
        serializer.user = make_user("nolookup@gmail.com")

        with patch(
            "users.serializers.TokenObtainPairSerializer.validate",
            return_value={"access": "a", "refresh": "r"},
        ), patch("users.serializers.AnalyticsService.track_activity"):
            data = serializer.validate({"password": PASSWORD})

        self.assertIn("user", data)
        self.assertEqual(data["access"], "a")

    def test_an_unrecognised_otp_type_falls_through_to_the_generic_reply(self):
        """
        OTPSerializer restricts otp_type to two values, so neither branch
        matching is unreachable through the API. The view still has to
        answer safely if that validation is ever loosened - not crash, and
        not leak whether the account exists.
        """
        user = make_user("fallthrough.otp@gmail.com")

        class _FakeSerializer:
            validated_data = {"email": user.email, "otp_type": "SOMETHING_NEW"}

            def __init__(self, *args, **kwargs):
                pass

            def is_valid(self, raise_exception=False):
                return True

        with patch("users.views.OTPSerializer", _FakeSerializer):
            response = self.client.post(
                reverse("auth-otp"),
                {"email": user.email, "otp_type": "SOMETHING_NEW"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn("otp has been sent", str(response.data).lower())


class FlattenErrorsNonStringTests(APITestCase):
    def test_a_non_string_leaf_is_ignored_rather_than_crashing(self):
        """
        `_collect` handles str / list / Mapping. Anything else (an int, a
        None) simply contributes nothing - a DRF payload carrying one must
        not blow up the renderer.
        """
        self.assertEqual(flatten_errors(42), "An error occurred")

    def test_a_mapping_with_a_non_string_value_still_renders(self):
        message = flatten_errors({"code": 500, "detail": "Server error."})

        self.assertIn("Server error.", message)
