"""Epic E2: shared builders for the grade-category, excuse and copy tests.

Holds no tests; named `tests_` so the grade guard reads it as test code (its
one percentage is a fixture submission's own input, not a grade figure).
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

categories_on = override_settings(GRADE_CATEGORIES_ENABLED=True, CACHES=LOCMEM)
categories_off = override_settings(GRADE_CATEGORIES_ENABLED=False, CACHES=LOCMEM)
copy_on = override_settings(ASSIGNMENT_COPY_ENABLED=True, CACHES=LOCMEM)
copy_off = override_settings(ASSIGNMENT_COPY_ENABLED=False, CACHES=LOCMEM)


def make_user(user_type, *, school=None, tag=None):
    tag = tag or uuid.uuid4().hex[:8]
    user = CustomUser.objects.create_user(
        email=f"e2-{user_type.lower()}-{tag}@e2.test",
        password="e2-pass-123",  # nosec B106 # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.first_name, user.last_name = "E2", user_type.title()
    if school is not None:
        user.school = school
    user.save()
    return user


def make_teacher(**kwargs):
    return make_user(UserTypes.TEACHER, **kwargs)


def make_student(**kwargs):
    return make_user(UserTypes.STUDENT, **kwargs)


def make_course(teacher, name=None):
    term = Session.objects.create(name=f"Term {uuid.uuid4().hex[:6]}", teacher=teacher)
    return Course.objects.create(
        name=name or f"Course {uuid.uuid4().hex[:6]}", teacher=teacher, session=term
    )


def enroll(student, course, status=EnrollmentStatusType.ENROLLED):
    return StudentCourse.objects.create(
        student=student, course=course, enrollment_status=status
    )


def make_assignment(course, title=None, *, total_points=100, **fields):
    fields.setdefault("status", AssignmentStatus.PUBLISHED)
    return Assignment.objects.create(
        course=course,
        title=title or f"A {uuid.uuid4().hex[:6]}",
        raw_input=f"raw {uuid.uuid4().hex}",
        total_points=total_points,
        **fields,
    )


def grade(student, assignment, score, *, max_points=None, published=True):
    """A graded submission, saved through the model so the final-grade
    receivers run."""
    points = max_points if max_points is not None else assignment.total_points
    return StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers={"q1": "a"},
        score=Decimal(str(score)),
        max_points=max_points,
        score_percentage=(
            (Decimal(str(score)) / Decimal(str(points)) * 100).quantize(Decimal("0.01"))
            if points
            else None
        ),
        graded_at=timezone.now(),
        is_published=published,
    )


def past(days=1):
    return timezone.now() - timedelta(days=days)


def future(days=1):
    return timezone.now() + timedelta(days=days)


def stored_grade(student, course):
    return StudentCourse.objects.get(student=student, course=course).final_grade


def fund_wallet(user, credits=500_000):
    """A live MONTHLY bucket, so a credit-gated door gets past billing."""
    from billing.models import CreditBucket, CreditBucketType, CreditWallet

    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=25),
    )
    return wallet
