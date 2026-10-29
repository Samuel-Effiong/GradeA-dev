"""
dashboard/tests_real_ai_chat.py
===============================
REAL, unmocked provider calls through the dashboard AI chats.

Opt-in: set RUN_REAL_AI=1. Each test makes one billed call and needs the
network, so they are skipped by default - the same convention as
assignments/tests_real_extraction.py.

Why these exist alongside tests_dashboard_remediation.py: those tests prove
what the view PUTS in the context. They cannot prove the model can USE it -
a mock answers whatever the test author imagined. §8 found the school-admin
chat sending `{}` for teachers on every request, a defect no mocked test
noticed. So each test here plants facts that exist only in the dashboard
data (invented names, exact counts), asks a question only answerable from
them, and checks the model's answer contains them.

Both calls go through the real billing gate - an active subscription or
license allocation and a funded wallet - not a mocked one, and each asserts
the call was metered.

Assertions are about grounding, not wording: the model is non-deterministic,
so the test checks for the planted surname and number, never a sentence.
"""

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

import dashboard.views as dashboard_views
from ai_processor.models import ChatMessage
from assignments.models import Assignment, AssignmentStatus
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

RUN_REAL_AI = os.environ.get("RUN_REAL_AI") == "1"
SKIP_REASON = "Real AI call is opt-in and billed: set RUN_REAL_AI=1"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_user(kind, email, first, last, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password="real-chat-pass-123",  # pragma: allowlist secret
        user_type=kind,
        first_name=first,
        last_name=last,
        school=school,
        is_active=True,
    )


def fund(user, *, now):
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        # Sized far clear of the estimator's +20k baseline so a failure here
        # is about the chat, never the budget.
        total_credits=500_000,
        used_credits=0,
        expires_at=now + timedelta(days=25),
    )


class ContextSpy:
    """Record the context each chat actually sends, while still making the
    real call."""

    def __init__(self):
        self.contexts = []
        # The exact object the view calls, captured before it is patched.
        self._real = dashboard_views.ai_processor.custom_ai_prompt_retry

    def __call__(self, user, context, question, role, **kwargs):
        self.contexts.append(context)
        return self._real(user, context, question, role, **kwargs)


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
@override_settings(CACHES=LOCMEM, DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=True)
class RealTeacherAIChatTest(TestCase):
    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.teacher = make_user(
            UserTypes.TEACHER, "real-chat-teacher@audit.test", "Real", "Teacher"
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Real Dashboard Chat",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=500_000,
            overage_block_size=500,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        fund(self.teacher, now=now)

        # A realistic small teacher: 3 courses, 8 assignments each, 12
        # students each. Planted facts: ONE struggling student with an
        # invented surname, and ONE assignment with a distinctive title that
        # everyone did badly on.
        for c in range(3):
            session = Session.objects.create(name=f"Term {c}", teacher=self.teacher)
            course = Course.objects.create(
                name=f"Biology {c + 1}", teacher=self.teacher, session=session
            )
            students = [
                make_user(
                    UserTypes.STUDENT,
                    f"real-chat-s{c}-{i}@audit.test",
                    "Quintessa" if (c, i) == (0, 0) else f"Pupil{i}",
                    "Varga" if (c, i) == (0, 0) else f"Course{c}",
                )
                for i in range(12)
            ]
            for student in students:
                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )
            for a in range(8):
                hard = (c, a) == (1, 3)
                assignment = Assignment.objects.create(
                    title=(
                        "Chloroplast Pigment Chromatography"
                        if hard
                        else f"Worksheet {c}.{a}"
                    ),
                    course=course,
                    status=AssignmentStatus.PUBLISHED,
                    due_date=now - timedelta(days=30 - a),
                )
                for student in students:
                    if student.last_name == "Varga":
                        pct = 11
                    else:
                        pct = 31 if hard else 82
                    StudentSubmission.objects.create(
                        assignment=assignment,
                        student=student,
                        answers={"q1": "a"},
                        score=pct,
                        score_percentage=pct,
                        grading_confidence=91,
                        graded_at=now,
                        is_published=True,
                    )

    def test_model_answers_from_the_bounded_teacher_context(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        spy = ContextSpy()

        with patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry", side_effect=spy
        ):
            response = client.post(
                reverse("teacher-admin-custom-ai-prompt"),
                {
                    "prompt": (
                        "Using only my dashboard data: (1) give the full name of "
                        "the student with the lowest average score, and (2) give "
                        "the exact title of the assignment with the lowest "
                        "average score."
                    )
                },
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        answer = response.data["response"]
        print(f"\n[real teacher chat] context={len(spy.contexts[0])} chars\n{answer}\n")

        # The planted facts reached the model and it used them.
        self.assertIn("Varga", answer)
        self.assertIn("Chromatography", answer)
        # Nothing was silently dropped for this size of teacher.
        self.assertIn("Nothing was left out", spy.contexts[0])
        self.assertNotIn("UNAVAILABLE", spy.contexts[0])
        # Metered, and the exchange recorded once.
        self.assertTrue(
            CreditUsageLog.objects.filter(wallet__user=self.teacher).exists()
        )
        self.assertEqual(
            list(
                ChatMessage.objects.values_list("role", flat=True).order_by("timestamp")
            ),
            ["user", "assistant"],
        )


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
@override_settings(CACHES=LOCMEM, DASHBOARD_CUSTOM_AI_PROMPT_ENABLED=True)
class RealSchoolAdminAIChatTest(TestCase):
    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.school = School.objects.create(name="Real Chat Academy")
        self.admin = make_user(
            UserTypes.SCHOOL_ADMIN,
            "real-chat-admin@audit.test",
            "Real",
            "Admin",
            school=self.school,
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=500_000,
            carry_over_percent=25,
            is_active=True,
        )
        license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=365),
            is_active=True,
            auto_renew=True,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=self.admin,
            monthly_allocation=500_000,
            is_active=True,
            is_admin_allocation=True,
            next_credit_grant_at=now + timedelta(days=30),
        )
        fund(self.admin, now=now)

        # Planted: an invented teacher who runs exactly 4 courses, beside
        # two who run 1. Before the fix the model received `{}` for
        # teachers and could not answer this at all.
        for first, last, courses in (
            ("Ignatius", "Thornbury", 4),
            ("Marisol", "Quenby", 1),
            ("Dmitri", "Allard", 1),
        ):
            teacher = make_user(
                UserTypes.TEACHER,
                f"real-chat-{last.lower()}@audit.test",
                first,
                last,
                school=self.school,
            )
            for n in range(courses):
                session = Session.objects.create(
                    name=f"{last} term {n}", teacher=teacher
                )
                Course.objects.create(
                    name=f"{last} class {n}", teacher=teacher, session=session
                )

        # Another school's teacher with MORE courses: if tenancy leaked,
        # this is the name the model would give.
        other = School.objects.create(name="Elsewhere College")
        outsider = make_user(
            UserTypes.TEACHER,
            "real-chat-outsider@audit.test",
            "Bartholomew",
            "Zinnemann",
            school=other,
        )
        for n in range(9):
            session = Session.objects.create(name=f"outsider {n}", teacher=outsider)
            Course.objects.create(
                name=f"Outsider class {n}", teacher=outsider, session=session
            )

    def test_model_answers_from_the_teachers_section(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        spy = ContextSpy()

        with patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry", side_effect=spy
        ):
            response = client.post(
                reverse("school-admin-custom-ai-prompt"),
                {
                    "prompt": (
                        "Using only the teacher data provided: which teacher "
                        "teaches the most courses, and exactly how many courses "
                        "is that? Give the teacher's full name and the number."
                    )
                },
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        answer = response.data["response"]
        teachers_section = (
            spy.contexts[0].split("### TEACHERS METRICS")[1].split("###")[0]
        )
        print(
            f"\n[real school-admin chat] teachers section:\n{teachers_section}\n{answer}\n"
        )

        self.assertIn("Thornbury", teachers_section)
        self.assertNotIn("UNAVAILABLE", spy.contexts[0])
        self.assertIn("Thornbury", answer)
        self.assertRegex(answer, r"\b(4|four)\b")
        self.assertNotIn("Zinnemann", answer)
        self.assertNotIn("Zinnemann", spy.contexts[0])
        self.assertTrue(CreditUsageLog.objects.filter(wallet__user=self.admin).exists())
