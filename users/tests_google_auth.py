"""
`POST /auth/google-auth` - every branch, plus a real call to Google.

Before this file, the whole Google sign-in path had exactly one test (the
happy path in tests_open_signup.py). Everything that can go wrong with a
third-party OAuth exchange - a network failure, a non-2xx from Google, a
response with no id_token, a forged token, an unverified email - was
unexercised, on an endpoint that CREATES ACCOUNTS and is reachable
unauthenticated.

On mocks vs. the real provider
------------------------------
A complete successful exchange cannot be automated: it needs a one-time
authorization code that only Google issues after a human consents in a
browser. What CAN be verified for real, and is verified here in
`LiveGoogleEndpointContractTests`, is the FAILURE contract - the half this
code has to get right without ever having been run against the real thing:

  * Google's real token endpoint, asked to redeem a bogus code, answers
    with an HTTP error (not a 200 carrying an error body), which is what
    makes `raise_for_status()` the correct check.
  * The real `google-auth` library, given a forged token, raises
    ValueError - which is what the view's `except ValueError` depends on.

Those tests use throwaway client credentials, never this deployment's, and
they only ever ask Google to REJECT something - nothing is created,
consumed or billed. They skip when the network is unavailable so an
offline run stays green.
"""

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserGoogleCredentials, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def network_available(host="oauth2.googleapis.com", port=443, timeout=5):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


# Calling Google for real is OPT-IN, not "whenever the machine happens to have
# a route to it". Reachability is not consent: a CI runner has outbound
# internet, so a probe-only gate made every CI run depend on Google being up
# and not rate-limiting us - a red build caused by someone else's service.
#
# With CI_REQUIRE_NETWORK=1 the calls run and LiveContractSkipGuardTests turns
# an unreachable provider into a FAILURE, so an opted-in run cannot quietly
# skip the contract it was asked to check. Without it, the probe is not even
# attempted: the connect attempt itself cost every run a wait at import time.
LIVE_GOOGLE_OPT_IN = os.environ.get("CI_REQUIRE_NETWORK") == "1"
NETWORK_OK = network_available() if LIVE_GOOGLE_OPT_IN else False


@override_settings(CACHES=LOCMEM_CACHE)
class GoogleAuthViewTests(APITestCase):
    """Every branch of the view, with Google's responses stubbed."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("auth-google-auth")
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def call(self, *, token_response=None, id_info=None, post_side_effect=None):
        """Drive the endpoint with a stubbed Google."""
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            if post_side_effect is not None:
                mocked_post.side_effect = post_side_effect
            else:
                mocked_post.return_value.raise_for_status.return_value = None
                mocked_post.return_value.json.return_value = token_response or {}

            if isinstance(id_info, Exception):
                mocked_verify.side_effect = id_info
            else:
                mocked_verify.return_value = id_info or {}

            return self.client.post(self.url, {"code": "oauth-code"}, format="json")

    def valid_token_response(self, **overrides):
        payload = {
            "id_token": "fake-id-token",
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
        }
        payload.update(overrides)
        return payload

    def valid_id_info(self, **overrides):
        info = {
            "email": "google.user@gmail.com",
            "email_verified": True,
            "given_name": "Google",
            "family_name": "User",
        }
        info.update(overrides)
        return info

    # --- input / upstream failures -------------------------------------------

    def test_missing_code_is_rejected(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_a_network_failure_reaching_google_is_a_400_not_a_500(self):
        response = self.call(
            post_side_effect=requests.exceptions.ConnectionError("dns failure")
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_a_timeout_reaching_google_is_a_400_not_a_500(self):
        response = self.call(post_side_effect=requests.exceptions.Timeout("slow"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_non_2xx_from_google_is_a_400_not_a_500(self):
        """This is the shape the live contract test below confirms is real."""
        with patch("requests.post") as mocked_post:
            mocked_post.return_value.raise_for_status.side_effect = (
                requests.exceptions.HTTPError("400 invalid_grant")
            )

            response = self.client.post(self.url, {"code": "bad"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_a_response_without_an_id_token_is_rejected(self):
        response = self.call(
            token_response={"access_token": "only-this"},
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_a_forged_token_is_rejected(self):
        """`verify_oauth2_token` raises ValueError on a bad signature."""
        response = self.call(
            token_response=self.valid_token_response(),
            id_info=ValueError("Token signature verification failed"),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_an_unverified_google_email_cannot_create_an_account(self):
        """
        Without this, anyone able to add an unverified address to a Google
        account could claim someone else's email here.
        """
        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(email_verified=False),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    # --- account creation -----------------------------------------------------

    def test_a_new_personal_account_is_created_and_logged_in(self):
        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        user = User.objects.get(email="google.user@gmail.com")
        self.assertTrue(user.is_active)
        self.assertEqual(user.user_type, UserTypes.TEACHER)
        self.assertEqual(user.registration_method, "GOOGLE")
        self.assertIsNotNone(user.email_verified_at)

    def test_a_google_signup_is_recorded_as_google_and_pre_verified(self):
        """
        Regression for a live bug this suite found: the view passed
        registration_method / email_verified_at into a serializer whose
        Meta.fields contains neither, so DRF dropped both silently. Every
        Google account was stored as registration_method=EMAIL with a null
        email_verified_at.
        """
        self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        user = User.objects.get(email="google.user@gmail.com")
        self.assertEqual(user.registration_method, "GOOGLE")
        self.assertNotEqual(user.registration_method, "EMAIL")
        self.assertIsNotNone(user.email_verified_at)

    def test_a_google_signup_is_not_sent_an_activation_email(self):
        """
        The user-visible half of the same bug. CustomUserSerializer.create()
        sends the "verify your email" mail whenever registration_method is
        EMAIL, so a dropped GOOGLE value meant every Google signup was
        emailed a verification link for an address Google had already
        verified.
        """
        with patch("users.serializers.send_user_activation_email") as mock_email:
            self.call(
                token_response=self.valid_token_response(),
                id_info=self.valid_id_info(),
            )

        mock_email.assert_not_called()

    def test_email_verified_at_is_not_client_writable_on_registration(self):
        """
        The fix injects those fields through save(), NOT by adding them to
        the serializer - this serializer also backs /auth/register, where a
        writable email_verified_at would let any caller mark their own
        address verified and skip the activation flow entirely.
        """
        with patch("users.serializers.send_user_activation_email"):
            response = self.client.post(
                reverse("auth-register"),
                {
                    "email": "self.verifier@gmail.com",
                    "password": "a-strong-password-42",  # pragma: allowlist secret
                    "first_name": "Self",
                    "last_name": "Verifier",
                    "email_verified_at": timezone.now().isoformat(),
                    "is_active": True,
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user = User.objects.get(email="self.verifier@gmail.com")
        self.assertIsNone(user.email_verified_at)
        self.assertFalse(user.is_active)

    def test_a_google_account_cannot_be_logged_into_with_a_password(self):
        """
        set_unusable_password: a Google identity must not also become a
        password credential an attacker can target.
        """
        self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        user = User.objects.get(email="google.user@gmail.com")
        self.assertFalse(user.has_usable_password())

    def test_a_business_google_account_is_refused_with_guidance(self):
        """
        Google sign-in mints an individual TEACHER account, and those
        require a personal address - a school account is created by the
        school admin instead.
        """
        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(email="principal@acme-school.org"),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("school admin", str(response.data).lower())
        self.assertFalse(
            User.objects.filter(email="principal@acme-school.org").exists()
        )

    def test_a_disposable_google_account_is_refused(self):
        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(email="throwaway@mailinator.com"),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    # --- existing accounts ----------------------------------------------------

    def test_an_existing_user_is_logged_in_without_being_duplicated(self):
        existing = User.objects.create_user(
            email="google.user@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Existing",
            last_name="User",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.filter(email=existing.email).count(), 1)

    def test_a_mixed_case_google_address_matches_the_existing_lowercase_user(self):
        """
        Regression: without lowercasing, a mixed-case address missed the
        lookup, fell into the create branch and died on the unique
        constraint - a 500 on an ordinary sign-in.
        """
        User.objects.create_user(
            email="google.user@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Existing",
            last_name="User",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(email="Google.User@Gmail.COM"),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.count(), 1)

    # --- existing accounts: the resurrect / deactivation carve-out ------------

    def _existing(self, *, is_active, email_verified_at, email="google.user@gmail.com"):
        user = User.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Existing",
            last_name="User",
            user_type=UserTypes.TEACHER,
            is_active=is_active,
        )
        User.objects.filter(pk=user.pk).update(email_verified_at=email_verified_at)
        user.refresh_from_db()
        return user

    def test_a_never_verified_account_is_completed_by_google(self):
        """
        Registered by email, never clicked the link, then signed in with
        Google. Google proving mailbox ownership is stronger evidence than
        the 6-digit code, so the account is completed rather than left in
        limbo.
        """
        user = self._existing(is_active=False, email_verified_at=None)

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)

    def test_tokens_from_a_completed_account_actually_work(self):
        """
        The bug this closes: the endpoint answered 200 with tokens while
        leaving the account inactive, so SimpleJWT rejected those very
        tokens with "User is inactive" on the next request - sign-in
        appeared to succeed and then immediately logged the user out.
        """
        self._existing(is_active=False, email_verified_at=None)

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )
        access = response.data["access"]

        me = self.client.get(reverse("user-me"), HTTP_AUTHORIZATION=f"Bearer {access}")

        self.assertEqual(me.status_code, status.HTTP_200_OK)

    def test_a_deactivated_account_is_refused_and_stays_deactivated(self):
        """
        THE CARVE-OUT. This account was verified once and later switched
        off deliberately. Mailbox ownership says nothing about whether that
        decision should be reversed, so Google sign-in must not reverse it.
        """
        deactivated_at = timezone.now() - timezone.timedelta(days=30)
        user = self._existing(is_active=False, email_verified_at=deactivated_at)

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        user.refresh_from_db()
        self.assertFalse(user.is_active, "Google sign-in re-activated a banned account")

    def test_a_deactivated_account_is_issued_no_tokens(self):
        self._existing(
            is_active=False,
            email_verified_at=timezone.now() - timezone.timedelta(days=30),
        )

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_a_deactivated_account_does_not_get_google_credentials_stored(self):
        """The refusal must happen before any state is written."""
        user = self._existing(
            is_active=False,
            email_verified_at=timezone.now() - timezone.timedelta(days=30),
        )

        self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertFalse(UserGoogleCredentials.objects.filter(user=user).exists())

    def test_a_deactivated_account_is_told_why(self):
        self._existing(
            is_active=False,
            email_verified_at=timezone.now() - timezone.timedelta(days=30),
        )

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertIn("deactivated", str(response.data).lower())

    def test_an_active_verified_account_is_not_rewritten(self):
        """The ordinary returning user: sign in, change nothing."""
        verified_at = timezone.now() - timezone.timedelta(days=5)
        user = self._existing(is_active=True, email_verified_at=verified_at)

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.email_verified_at, verified_at)

    def test_an_active_but_unverified_account_gets_marked_verified(self):
        """
        Google has verified the address, so the null verification stamp is
        simply stale - correct it rather than leaving the account looking
        unverified forever.
        """
        user = self._existing(is_active=True, email_verified_at=None)

        response = self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertIsNotNone(user.email_verified_at)

    def test_completing_an_account_queues_the_mailing_list_sync(self):
        """
        queue_sync no-ops on an inactive user, so this is the first moment
        a never-verified signup may legitimately reach the mailing list.
        """
        self._existing(is_active=False, email_verified_at=None)

        with patch("users.views.sync_user_to_mailerlite") as mock_sync:
            self.call(
                token_response=self.valid_token_response(),
                id_info=self.valid_id_info(),
            )

        self.assertTrue(mock_sync.delay.called or mock_sync.called)

    def test_an_unchanged_account_does_not_requeue_the_sync(self):
        self._existing(
            is_active=True,
            email_verified_at=timezone.now() - timezone.timedelta(days=5),
        )

        with patch("users.views.sync_user_to_mailerlite") as mock_sync:
            self.call(
                token_response=self.valid_token_response(),
                id_info=self.valid_id_info(),
            )

        self.assertFalse(mock_sync.delay.called or mock_sync.called)

    def test_a_deactivated_account_cannot_be_revived_by_repeated_attempts(self):
        """Hammering the endpoint must not eventually flip the account on."""
        user = self._existing(
            is_active=False,
            email_verified_at=timezone.now() - timezone.timedelta(days=30),
        )

        for _ in range(5):
            response = self.call(
                token_response=self.valid_token_response(),
                id_info=self.valid_id_info(),
            )
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        user.refresh_from_db()
        self.assertFalse(user.is_active)

    # --- stored credentials ---------------------------------------------------

    def test_google_credentials_are_stored_with_an_expiry(self):
        before = timezone.now()

        self.call(
            token_response=self.valid_token_response(expires_in=3600),
            id_info=self.valid_id_info(),
        )

        user = User.objects.get(email="google.user@gmail.com")
        credentials = UserGoogleCredentials.objects.get(user=user)
        self.assertEqual(credentials.access_token, "fake-access-token")
        self.assertEqual(credentials.refresh_token, "fake-refresh-token")
        self.assertGreater(credentials.token_expiry, before)

    def test_a_second_sign_in_updates_credentials_rather_than_duplicating(self):
        self.call(
            token_response=self.valid_token_response(),
            id_info=self.valid_id_info(),
        )
        self.call(
            token_response=self.valid_token_response(access_token="second-access"),
            id_info=self.valid_id_info(),
        )

        user = User.objects.get(email="google.user@gmail.com")
        self.assertEqual(UserGoogleCredentials.objects.filter(user=user).count(), 1)
        self.assertEqual(
            UserGoogleCredentials.objects.get(user=user).access_token, "second-access"
        )

    def test_a_sign_in_without_a_refresh_token_keeps_the_stored_one(self):
        """
        Google only returns a refresh_token on the FIRST consent. Writing
        the absent value through would wipe the one we already have and
        break offline access.
        """
        self.call(
            token_response=self.valid_token_response(refresh_token="original-refresh"),
            id_info=self.valid_id_info(),
        )

        response_payload = self.valid_token_response()
        response_payload.pop("refresh_token")
        self.call(token_response=response_payload, id_info=self.valid_id_info())

        user = User.objects.get(email="google.user@gmail.com")
        self.assertEqual(
            UserGoogleCredentials.objects.get(user=user).refresh_token,
            "original-refresh",
        )


class GoogleSignInCompletesPendingEnrollmentTests(APITestCase):
    """
    A teacher's course invitation is PENDING until the student finishes
    registering, and finishing registration is what promotes it to
    ENROLLED. The emailed-link flow (register_student) does that; signing
    in with Google is the OTHER way a student can finish registering, and
    it did not.

    That was invisible while PENDING still granted course access. It
    stopped being invisible once assignments started enforcing
    classrooms.models.COURSE_ACCESS_ENROLLMENT_STATUSES: an invited
    student who used the Google button instead of the emailed link would
    authenticate perfectly well and then find the course they were invited
    to completely empty.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("auth-google-auth")
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

        self.teacher = User.objects.create_user(
            email="pending-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Pending",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Term P", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Invited Course", teacher=self.teacher, session=self.session
        )

    def _invited_student(self, email="invited.student@gmail.com"):
        """Exactly what bulk_add_students creates for a brand-new invitee."""
        student = User.objects.create(
            email=email,
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token="token-123",
            activation_expires=timezone.now() + timedelta(hours=24),
        )
        enrollment = StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        return student, enrollment

    def _sign_in_as(self, email):
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Invited",
                "family_name": "Student",
            }
            return self.client.post(self.url, {"code": "oauth-code"}, format="json")

    def test_google_sign_in_promotes_a_pending_invitation_to_enrolled(self):
        student, enrollment = self._invited_student()

        response = self._sign_in_as(student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        enrollment.refresh_from_db()
        self.assertEqual(
            enrollment.enrollment_status,
            EnrollmentStatusType.ENROLLED,
            "the invitation was never completed, so the student is locked out "
            "of the course they were invited to",
        )
        student.refresh_from_db()
        self.assertTrue(student.is_active)

    def test_every_pending_invitation_is_promoted_not_just_the_first(self):
        student, first = self._invited_student()
        other_course = Course.objects.create(
            name="Second Invited Course",
            teacher=self.teacher,
            session=self.session,
        )
        second = StudentCourse.objects.create(
            student=student,
            course=other_course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        self._sign_in_as(student.email)

        for enrollment in (first, second):
            enrollment.refresh_from_db()
            self.assertEqual(
                enrollment.enrollment_status, EnrollmentStatusType.ENROLLED
            )

    def test_a_withdrawn_enrollment_is_not_resurrected_by_signing_in(self):
        """
        Only PENDING is promoted. A student a teacher deliberately removed
        must not get back in by using the Google button.
        """
        student, enrollment = self._invited_student()
        enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
        enrollment.save(update_fields=["enrollment_status"])

        self._sign_in_as(student.email)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.WITHDRAWN)

    def test_a_withdrawn_student_still_sees_no_assignments_after_signing_in(self):
        """
        The end-to-end form of the guarantee, not just the database field.

        A teacher removed this student from the course. Signing in with
        Google must not be a way back in - so this follows the sign-in all
        the way through to the assignments endpoint and asserts the course
        content is still not reachable.
        """
        from assignments.models import Assignment, AssignmentStatus

        student, enrollment = self._invited_student()
        enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
        enrollment.save(update_fields=["enrollment_status"])
        Assignment.objects.create(
            title="Course Content They Lost Access To",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
            questions=[{"question_number": 1, "question_text": "q"}],
        )

        response = self._sign_in_as(student.email)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        student.refresh_from_db()
        self.client.force_authenticate(user=student)
        listing = self.client.get(reverse("assignment-list"))

        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertNotIn(
            "Course Content They Lost Access To",
            listing.content.decode(),
            "signing in with Google let a withdrawn student back into the "
            "course they were removed from",
        )

    def test_an_established_account_signing_in_again_promotes_nothing(self):
        """
        The promotion is gated on the account having still been unverified
        - i.e. genuinely mid-registration. A student who is already set up
        and simply signs in again must not have unrelated pending
        invitations silently accepted on their behalf.
        """
        student, enrollment = self._invited_student()
        student.is_active = True
        student.email_verified_at = timezone.now()
        student.save(update_fields=["is_active", "email_verified_at"])

        self._sign_in_as(student.email)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.PENDING)


class LiveGoogleEndpointContractTests(TestCase):
    """
    REAL calls to Google. No mocks in this class.

    These pin the two upstream behaviours the view's error handling is
    built on, neither of which had ever been checked against the actual
    provider. Nothing here creates or consumes anything: both calls ask
    Google to reject deliberately invalid input, using throwaway client
    credentials rather than this deployment's.
    """

    def setUp(self):
        if not LIVE_GOOGLE_OPT_IN:
            self.skipTest(
                "live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1"
            )
        if not NETWORK_OK:
            self.skipTest("No network access to oauth2.googleapis.com")

    def test_google_rejects_a_bogus_code_with_an_http_error_status(self):
        """
        The view relies on `raise_for_status()` to notice a failed
        exchange. That is only correct if Google signals failure with a
        non-2xx STATUS rather than a 200 carrying an error body - so this
        asks the real endpoint and checks.
        """
        body = urllib.parse.urlencode(
            {
                "client_id": "grade-a-plus-contract-test.apps.googleusercontent.com",
                "client_secret": "not-a-real-secret",  # pragma: allowlist secret
                "code": "definitely-not-a-valid-authorization-code",
                "grant_type": "authorization_code",
                "redirect_uri": "http://localhost/none",
            }
        ).encode()

        request = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                self.fail(
                    "Google returned "
                    f"{response.status} for an invalid code; the view's "
                    "raise_for_status() check would not fire."
                )
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            self.assertGreaterEqual(exc.code, 400)
            # A machine-readable error field is what makes this diagnosable.
            self.assertIn("error", payload)

    def test_the_real_google_library_raises_value_error_on_a_forged_token(self):
        """
        The view catches ValueError and answers "Invalid Google token
        signature". If google-auth ever raised something else, that except
        clause would stop catching and a forged token would become a 500.
        """
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        # Structurally a JWT, signed by nobody.
        # A structurally-valid JWT signed by nobody - the whole point is
        # that it is NOT a credential and Google must reject it.
        forged = (
            "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZha2UiLCJ0eXAiOiJKV1QifQ"  # pragma: allowlist secret
            ".eyJpc3MiOiJhY2NvdW50cy5nb29nbGUuY29tIiwiZW1h"  # pragma: allowlist secret
            "aWwiOiJhdHRhY2tlckBnbWFpbC5jb20iLCJleHAiOjk5OTk5OTk5OTl9"  # pragma: allowlist secret
            ".not-a-real-signature"
        )

        with self.assertRaises(ValueError):
            id_token.verify_oauth2_token(
                forged, google_requests.Request(), "fake-client-id"
            )


class LiveContractSkipGuardTests(TestCase):
    """Stop the live contract tests from silently skipping forever in CI."""

    def test_network_is_required_when_ci_says_so(self):
        if os.environ.get("CI_REQUIRE_NETWORK") == "1":
            self.assertTrue(
                NETWORK_OK,
                "CI_REQUIRE_NETWORK=1 but oauth2.googleapis.com was "
                "unreachable, so the live Google contract tests skipped.",
            )
        else:
            self.skipTest("CI_REQUIRE_NETWORK not set")
