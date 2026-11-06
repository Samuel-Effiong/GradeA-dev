"""H-296: the owner-approved server wording (doc "Backend Message Rewording
for Approval", approved 2026-11-05 as written; Publish All keeps 200 with
the reworded text and zero counts).

API tests pin the behaviour changes (success messages, not-found, form
errors, Publish All); the sweep pins every reworded string: the old text is
gone from the code and the approved text is there.
"""

import os
import re
import uuid
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.exceptions import ErrorDetail
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_cache_matrix_g3 import make_active_user, question
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from billing.tests.test_h38_part2_removed_teacher_routes import fund_wallet
from classrooms.models import Course, Session
from students.models import StudentSubmission
from users.models import UserTypes
from users.renderers import flatten_errors
from users.serializers import CustomTokenObtainPairSerializer
from users.tokens import REVOKED_MESSAGE

DISPATCH = "celery.app.task.Task.apply_async"
NOT_FOUND = "We couldn't find that {}. It may have been deleted."


def msg(response):
    return response.json()["message"]


class _Teacher(TestCase):
    client: APIClient

    def setUp(self):
        self.teacher = make_active_user(
            "h296-t@grade.test", UserTypes.TEACHER, first_name="Pat"
        )
        fund_wallet(self.teacher)
        self.session = Session.objects.create(name="Fall", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Cells",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(1)],
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)


class SuccessMessageTests(_Teacher):
    def test_1_1_create_session(self):
        r = self.client.post(reverse("session-list"), {"name": "Spring"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(msg(r), "Session 'Spring' created.")

    def test_1_2_rename_session(self):
        r = self.client.patch(
            reverse("session-detail", args=[self.session.pk]),
            {"name": "Autumn"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(msg(r), "Session renamed to 'Autumn'.")

    def test_1_3_create_course(self):
        r = self.client.post(
            reverse("course-list"),
            {"name": "Chemistry", "description": "x", "session": str(self.session.pk)},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(msg(r), "Course 'Chemistry' created.")

    def test_1_4_edit_course(self):
        r = self.client.patch(
            reverse("course-detail", args=[self.course.pk]),
            {"description": "new"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(msg(r), "Changes to 'Biology' saved.")

    def test_1_5_direct_add(self):
        r = self.client.post(
            reverse("course-direct-add-student", args=[self.course.pk]),
            {"email": "kim@grade.test", "first_name": "Kim", "last_name": "Doe"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(msg(r), "Kim Doe added to Biology.")

    def test_1_6_edit_assignment(self):
        r = self.client.patch(
            reverse("assignment-detail", args=[self.assignment.pk]),
            {"title": "Cells 2"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(msg(r), "Assignment saved.")


class NotFoundTests(_Teacher):
    def _get(self, name):
        r = self.client.get(reverse(name, args=[uuid.uuid4()]))
        self.assertEqual(r.status_code, 404, r.content)
        return msg(r)

    def test_2_1_course(self):
        self.assertEqual(self._get("course-detail"), NOT_FOUND.format("course"))

    def test_2_2_assignment(self):
        self.assertEqual(self._get("assignment-detail"), NOT_FOUND.format("assignment"))

    def test_2_3_submission(self):
        self.assertEqual(
            self._get("student-submission-detail"), NOT_FOUND.format("submission")
        )

    def test_2_4_session(self):
        self.assertEqual(self._get("session-detail"), NOT_FOUND.format("session"))

    def test_2_5_any_other_item(self):
        self.assertEqual(
            self._get("student-course-detail"),
            "We couldn't find that item. It may have been deleted.",
        )


class FormErrorUnitTests(SimpleTestCase):
    def test_3_1_required(self):
        self.assertEqual(
            flatten_errors(
                {"name": [ErrorDetail("This field is required.", code="required")]}
            ),
            "Enter name.",
        )

    def test_3_2_blank(self):
        self.assertEqual(
            flatten_errors(
                {"name": [ErrorDetail("This field may not be blank.", code="blank")]}
            ),
            "Enter name.",
        )

    def test_3_3_min_length(self):
        e = ErrorDetail(
            "Ensure this value has at least 2 characters (it has 1).", code="min_length"
        )
        self.assertEqual(
            flatten_errors({"first_name": [e]}),
            "First name needs at least 2 characters.",
        )

    def test_3_4_max_length(self):
        e = ErrorDetail(
            "Ensure this value has no more than 100 characters.", code="max_length"
        )
        self.assertEqual(
            flatten_errors({"name": [e]}), "Use 100 characters or fewer for name."
        )

    def test_3_5_email(self):
        e = ErrorDetail("Enter a valid email address.", code="invalid")
        self.assertEqual(
            flatten_errors({"email": [e]}),
            "Enter a valid email address, like name@school.edu.",
        )

    def test_3_6_email_taken(self):
        e = ErrorDetail("User with this email already exists.", code="unique")
        self.assertEqual(
            flatten_errors({"email": [e]}),
            "An account with this email already exists. Sign in or reset your password.",
        )

    def test_3_7_several_errors_are_plain_sentences(self):
        out = flatten_errors(
            {
                "course": [ErrorDetail("x", code="required")],
                "raw_input": [ErrorDetail("x", code="required")],
            }
        )
        self.assertEqual(out, "Enter course. Enter assignment content.")

    def test_3_8_to_3_10_labels(self):
        for field, label in (
            ("raw_input", "assignment content"),
            ("schedule_time", "grading date and time"),
            ("current_password", "your current password"),
        ):
            self.assertEqual(
                flatten_errors({field: [ErrorDetail("x", code="required")]}),
                f"Enter {label}.",
            )

    def test_an_unknown_code_keeps_the_old_form(self):
        self.assertEqual(
            flatten_errors({"name": [ErrorDetail("Odd.", code="odd")]}), "Name: Odd."
        )

    def test_plain_text_without_a_code_keeps_the_old_form(self):
        self.assertEqual(flatten_errors({"name": ["Odd."]}), "Name: Odd.")


class FormErrorApiTests(_Teacher):
    def test_blank_session_name(self):
        r = self.client.post(reverse("session-list"), {"name": ""}, format="json")
        self.assertEqual((r.status_code, msg(r)), (400, "Enter name."))

    def test_direct_add_bad_fields(self):
        r = self.client.post(
            reverse("course-direct-add-student", args=[self.course.pk]),
            {"email": "nope", "first_name": "K", "last_name": "D"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            msg(r),
            "First name needs at least 2 characters. Last name needs at least 2 characters. "
            "Enter a valid email address, like name@school.edu.",
        )

    def test_create_assignment_empty(self):
        r = self.client.post(reverse("assignment-list"), {}, format="json")
        self.assertEqual(
            (r.status_code, msg(r)), (400, "Enter course. Enter assignment content.")
        )

    def test_schedule_grading_without_a_time(self):
        r = self.client.post(
            reverse(
                "assignment-schedule-grade-all-submission", args=[self.assignment.pk]
            ),
            {},
            format="json",
        )
        self.assertEqual((r.status_code, msg(r)), (400, "Enter grading date and time."))

    def test_sign_up_with_a_taken_email(self):
        c = APIClient(raise_request_exception=False)
        r = c.post(
            reverse("auth-register"),
            {
                "email": "h296-t@grade.test",
                "password": "longpassword1",  # pragma: allowlist secret
                "first_name": "Al",
                "last_name": "Be",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn(
            "An account with this email already exists. Sign in or reset your password.",
            msg(r),
        )


class AccountTextTests(_Teacher):
    def test_4_1_reset_code_requested(self):
        c = APIClient(raise_request_exception=False)
        r = c.post(
            reverse("auth-otp"),
            {"email": "nobody@grade.test", "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        self.assertEqual(
            msg(r), "If an account exists for that email, we've sent it a 6-digit code."
        )

    def test_4_2_change_password_code_requested(self):
        with patch(DISPATCH), patch("celery.app.task.Task.delay"):
            r = self.client.post(
                reverse("auth-request-change-password"), {}, format="json"
            )
        self.assertEqual(
            msg(r), "We've emailed you a 6-digit code to confirm your password change."
        )

    def test_4_3_reset_fails(self):
        c = APIClient(raise_request_exception=False)
        r = c.post(
            reverse("auth-reset-password"),
            {
                "email": "h296-t@grade.test",
                "otp": "000000",
                "new_password": "anotherpass1",  # pragma: allowlist secret
            },
            format="json",
        )
        self.assertEqual(
            msg(r),
            "We couldn't reset your password. Check the code we emailed you (it may have expired) and try again.",
        )

    def test_4_5_incomplete_link(self):
        c = APIClient(raise_request_exception=False)
        r = c.post(
            reverse("auth-verify"), {"email": "h296-t@grade.test"}, format="json"
        )
        self.assertEqual(
            msg(r),
            "This link is incomplete. Open the link from your email again, or request a new one.",
        )

    def test_4_8_signed_out_elsewhere(self):
        self.assertEqual(
            REVOKED_MESSAGE, "You've been signed out. Please sign in again."
        )

    def test_4_9_session_expired_on_sign_out(self):
        r = self.client.post(
            reverse("auth-logout"), {"refresh": "not-a-token"}, format="json"
        )
        self.assertEqual(
            (r.status_code, msg(r)),
            (400, "Your session has expired. Please sign in again."),
        )

    def test_4_12_sign_in_lock(self):
        text = "Too many failed sign-in attempts. Please try again later."
        self.assertEqual(CustomTokenObtainPairSerializer.LOCKED_MESSAGE, text)
        self.assertEqual(REASON_CODES[ReasonCode.ACCOUNT_LOCKED].message, text)


class BillingTextTests(_Teacher):
    def test_5_1_to_5_3_credit_pack_limit(self):
        spec = REASON_CODES[ReasonCode.OVERAGE_CAP_EXCEEDED]
        self.assertEqual(
            spec.message,
            "You can buy up to {max_blocks} credit packs per billing period. "
            "Reduce the quantity to {remaining} or fewer.",
        )
        self.assertEqual(
            spec.alternative_messages["cap_reached"],
            "You've bought the maximum of {max_blocks} credit packs for this billing period. "
            "You can buy more when your next period starts.",
        )
        self.assertEqual(
            spec.remediation,
            "Choose fewer credit packs, or wait until your next billing period.",
        )

    def test_5_9_and_5_10_no_subscription(self):
        nosub = make_active_user("h296-nosub@grade.test", UserTypes.TEACHER)
        c = APIClient(raise_request_exception=False)
        c.force_authenticate(user=nosub)
        self.assertEqual(
            msg(c.get(reverse("subscription-status"))),
            "You don't have an active subscription.",
        )
        self.assertEqual(
            msg(c.post(reverse("subscription-cancel"), {}, format="json")),
            "You don't have an active subscription to cancel.",
        )


class PublishAllNothingGradedTests(_Teacher):
    def test_8_200_with_reworded_text_and_zero_counts(self):
        student = make_active_user(
            "h296-s@grade.test", UserTypes.STUDENT, first_name="Sam"
        )
        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        r = self.client.post(
            reverse("assignment-publish-all-grades", args=[self.assignment.pk]),
            {},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(
            body["message"],
            "No graded submissions to publish yet. Grade them first, then publish.",
        )
        self.assertEqual(body["data"]["total_graded"], 0)
        self.assertEqual(body["data"]["ungraded_count"], 1)
        self.assertEqual(len(body["data"]["skipped"]), 1)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (old text, approved text, file the approved text lives in)
SWEEP = [
    (
        "An OTP has been sent if an account",
        "If an account exists for that email, we've sent it a 6-digit code.",
        "users/views.py",
    ),
    (
        "An OTP code has been sent to your email",
        "We've emailed you a 6-digit code to confirm your password change.",
        "users/views.py",
    ),
    (
        "Invalid email, OTP code, or new password.",
        "We couldn't reset your password. Check the code we emailed you (it may have expired) and try again.",
        "users/views.py",
    ),
    (
        "Invalid password change code. Please try again.",
        "That code isn't right or has expired. Request a new code and try again.",
        "users/views.py",
    ),
    (
        "Email and Token are required.",
        "This link is incomplete. Open the link from your email again, or request a new one.",
        "users/views.py",
    ),
    (
        "Invalid or expired activation token.",
        "This activation link is invalid or has expired. Request a new one.",
        "users/views.py",
    ),
    (
        "Invalid token or user not found.",
        "This invitation link is invalid or has expired. Ask your teacher to send a new one.",
        "classrooms/services/enrollment.py",
    ),
    (
        "Token has been revoked. Please log in again.",
        "You've been signed out. Please sign in again.",
        "users/tokens.py",
    ),
    (
        'ParseError("Invalid or expired token")',
        "Your session has expired. Please sign in again.",
        "users/views.py",
    ),
    (
        "Google did not return an ID token",
        "We couldn't sign you in with Google. Try again, or sign in with your email and password.",
        "users/views.py",
    ),
    (
        "Invalid Google token signature",
        "We couldn't sign you in with Google. Try again, or sign in with your email and password.",
        "users/views.py",
    ),
    (
        "Too many failed login attempts",
        "Too many failed sign-in attempts. Please try again later.",
        "users/serializers.py",
    ),
    (
        "complete your overage purchase",
        "Taking you to secure checkout to buy your extra credits.",
        "billing/views.py",
    ),
    (
        "couldn't confirm the overage price",
        "We couldn't get the price for extra credits right now. You haven't been charged. Try again in a moment.",
        "billing/overage_pricing.py",
    ),
    (
        "does not support overage credit purchases",
        "Extra credits can't be bought on your current plan.",
        "billing/stripe_service.py",
    ),
    (
        "has no associated Stripe subscription",
        "Your plan was set up by our team, so it can't be changed here. Contact support to upgrade.",
        "billing/stripe_service.py",
    ),
    (
        "subscribe again via select-plan",
        "This subscription has ended and can't be resumed. Choose a plan to subscribe again.",
        "billing/stripe_service.py",
    ),
    (
        '"No active subscription found"',
        "You don't have an active subscription.",
        "billing/views.py",
    ),
    (
        '"No active subscription found to cancel"',
        "You don't have an active subscription to cancel.",
        "billing/views.py",
    ),
    (
        '"No active subscription found to resume"',
        "There's no subscription to resume.",
        "billing/views.py",
    ),
    (
        '"No credit wallet found"',
        "We couldn't find your credit balance. Contact support if this keeps happening.",
        "billing/views.py",
    ),
    (
        '"No subscription found"',
        "You don't have a subscription yet. Choose a plan to get started.",
        "billing/views.py",
    ),
    ("Course ID is required.", "Choose a course first.", "assignments/views.py"),
    ("Topic ID is required.", "Choose a topic first.", "assignments/views.py"),
    (
        "Invalid Course ID format",
        "We couldn't find that course. Go back and select it again.",
        "assignments/views.py",
    ),
    (
        "A student with the exact name",
        " is already enrolled in this course.",
        "classrooms/models.py",
    ),
    (
        "Invalid file upload. Only images",
        "This file can't be uploaded. Use a PDF or an image (JPEG, PNG, GIF or WebP).",
        "students/views.py",
    ),
    (
        "This licence isn't active",
        "This license isn't active, so teachers can't be added to it.",
        "AutoGrader/reason_codes.py",
    ),
    (
        "Renew the licence",
        "Renew the license, or contact us.",
        "AutoGrader/reason_codes.py",
    ),
    (
        "Your licence has",
        "Your license has {availability}",
        "AutoGrader/reason_codes.py",
    ),
    (
        "teacher on this licence",
        "This teacher isn't an active teacher on this license.",
        "billing/license_service.py",
    ),
    (
        "This licence has",
        "Remove a teacher or increase max seats.",
        "billing/license_service.py",
    ),
    (
        "No graded submissions found to publish",
        "No graded submissions to publish yet. Grade them first, then publish.",
        "assignments/views.py",
    ),
]


def _code_files():
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [
            d
            for d in dns
            if d not in {"migrations", "docs", "node_modules", "__pycache__", "tests"}
            and not d.startswith(".")
        ]
        for fn in fns:
            if fn.endswith(".py") and not fn.startswith("test") and "_test" not in fn:
                yield os.path.join(dp, fn)


class SweepTests(SimpleTestCase):
    def test_old_texts_are_gone(self):
        sources = {p: open(p, encoding="utf-8").read() for p in _code_files()}
        left = [
            (old, os.path.relpath(p, ROOT))
            for old, _, _ in SWEEP
            for p, s in sources.items()
            if old in s
        ]
        self.assertEqual(left, [])

    def test_approved_texts_are_in_place(self):
        def joined(path):
            # Adjacent string pieces split over lines read as one string.
            src = open(os.path.join(ROOT, path), encoding="utf-8").read()
            return re.sub(r"[\"']\s*\n?\s*f?[\"']", "", src)

        missing = [(new, f) for _, new, f in SWEEP if new not in joined(f)]
        self.assertEqual(missing, [])
