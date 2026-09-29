"""
AUTHZ-PATCHPW: PATCH /users/<id> must not set the caller's own password.

Real JWTs through the real authentication chain. The attacker model is a
stolen access token: it authenticates, but it does not carry the password.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import CustomUser, UserTypes
from users.tokens import EpochRefreshToken

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "Original-Passw0rd-1"  # pragma: allowlist secret
ATTACKER_PASSWORD = "Attacker-Passw0rd-9"  # pragma: allowlist secret
WIDE = {"login": "1000000/hour"}


def make(email, user_type=UserTypes.TEACHER, **kw):
    kw.setdefault("is_active", True)
    kw.setdefault("email_verified_at", timezone.now())
    return CustomUser.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=email.split("@")[0].replace(".", " ").title(),
        last_name="Tester",
        user_type=user_type,
        **kw,
    )


@override_settings(CACHES=LOCMEM)
class PatchPasswordTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        p = patch.dict(SimpleRateThrottle.THROTTLE_RATES, WIDE)
        p.start()
        self.addCleanup(p.stop)
        self.teacher = make("teach.er@gmail.com")
        self.other_teacher = make("other.teach@gmail.com")
        self.student = make("stu.dent@student.local", UserTypes.STUDENT)
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.superadmin = make(
            "root@example.com",
            UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )

    def client_for(self, user):
        c = APIClient()
        c.credentials(
            HTTP_AUTHORIZATION=f"Bearer {EpochRefreshToken.for_user(user).access_token}"
        )
        return c

    def url(self, user):
        return reverse("user-detail", kwargs={"pk": user.pk})

    def password_is_unchanged(self, user):
        user.refresh_from_db()
        return user.check_password(PASSWORD)

    def login_status(self, email, password):
        return (
            APIClient()
            .post(
                reverse("login"), {"email": email, "password": password}, format="json"
            )
            .status_code
        )


class SelfServiceRejectionTests(PatchPasswordTests):
    def test_patching_your_own_password_is_a_400_that_names_the_right_endpoint(self):
        r = self.client_for(self.teacher).patch(
            self.url(self.teacher), {"password": ATTACKER_PASSWORD}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, r.content)
        self.assertIn("/auth/change-password", r.content.decode())
        self.assertTrue(self.password_is_unchanged(self.teacher))
        self.assertEqual(
            self.login_status(self.teacher.email, ATTACKER_PASSWORD),
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.login_status(self.teacher.email, PASSWORD), status.HTTP_200_OK
        )

    def test_form_encoded_body_is_rejected_too(self):
        r = self.client_for(self.teacher).patch(
            self.url(self.teacher), {"password": ATTACKER_PASSWORD}
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.password_is_unchanged(self.teacher))

    def test_a_mixed_request_is_refused_whole_nothing_is_half_applied(self):
        r = self.client_for(self.teacher).patch(
            self.url(self.teacher),
            {"password": ATTACKER_PASSWORD, "bio": "changed"},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.teacher.refresh_from_db()
        self.assertNotEqual(self.teacher.bio, "changed")
        self.assertTrue(self.teacher.check_password(PASSWORD))

    def test_every_role_is_rejected_on_its_own_account(self):
        for user in (self.student, self.superadmin):
            r = self.client_for(user).patch(
                self.url(user), {"password": ATTACKER_PASSWORD}, format="json"
            )
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, user.email)
            self.assertTrue(self.password_is_unchanged(user), user.email)

    def test_put_is_not_an_alternative_route(self):
        r = self.client_for(self.teacher).put(
            self.url(self.teacher),
            {"email": self.teacher.email, "password": ATTACKER_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(self.password_is_unchanged(self.teacher))


class UnaffectedBehaviourTests(PatchPasswordTests):
    def test_profile_edits_without_a_password_still_work(self):
        r = self.client_for(self.teacher).patch(
            self.url(self.teacher), {"bio": "hello"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.bio, "hello")

    def test_a_full_profile_patch_with_unchanged_email_still_works(self):
        r = self.client_for(self.teacher).patch(
            self.url(self.teacher),
            {"email": self.teacher.email, "first_name": "Renamed"},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)

    def test_change_password_endpoint_still_works_including_forced_change(self):
        self.teacher.must_change_password = True
        self.teacher.save()
        r = self.client_for(self.teacher).post(
            reverse("auth-change-password"),
            {"current_password": PASSWORD, "new_password": ATTACKER_PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password(ATTACKER_PASSWORD))
        self.assertFalse(self.teacher.must_change_password)

    def test_superadmin_can_still_set_another_users_password(self):
        r = self.client_for(self.superadmin).patch(
            self.url(self.teacher), {"password": ATTACKER_PASSWORD}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password(ATTACKER_PASSWORD))

    def test_superadmin_create_with_a_password_still_works(self):
        r = self.client_for(self.superadmin).post(
            reverse("user-list"),
            {
                "email": "newly.created@gmail.com",
                "first_name": "New",
                "last_name": "Person",
                "password": ATTACKER_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        self.assertTrue(
            CustomUser.objects.get(email="newly.created@gmail.com").check_password(
                ATTACKER_PASSWORD
            )
        )


class IsolationTests(PatchPasswordTests):
    def test_nobody_else_can_set_a_users_password(self):
        cases = [
            (self.other_teacher, self.teacher),  # teacher vs unrelated teacher
            (self.teacher, self.student),  # teacher vs their own student
            (self.student, self.teacher),  # student vs teacher
        ]
        for actor, target in cases:
            r = self.client_for(actor).patch(
                self.url(target), {"password": ATTACKER_PASSWORD}, format="json"
            )
            self.assertIn(
                r.status_code,
                (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
                (actor.email, target.email, r.status_code),
            )
            self.assertTrue(self.password_is_unchanged(target), target.email)

    def test_a_school_admin_cannot_set_a_teachers_password(self):
        from classrooms.models import School

        school = School.objects.create(name="Sch")
        admin = make("admin@company.com", UserTypes.SCHOOL_ADMIN, school=school)
        teacher = make("in.school@company.com", school=school)
        r = self.client_for(admin).patch(
            self.url(teacher), {"password": ATTACKER_PASSWORD}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.password_is_unchanged(teacher))
