"""Enrollment and roster membership.

This is the tenancy-critical surface of the app: who may join which course,
and what happens to a student's record when they leave one. It lives here
rather than in `views.py` so the rules can be read, tested and reasoned
about without a request object in hand.

Callers are responsible for authorization - every function here assumes the
`course` it is handed has already been scoped to the requesting teacher
(`CourseViewSet.get_queryset()`). Nothing here re-checks ownership, and
nothing here should be called with a course the caller hasn't scoped.
"""

import logging

from django.db import transaction
from django.utils import timezone

from users.models import ACTIVATION_TOKEN_VALIDITY, CustomUser, UserTypes
from users.services import otp_manager

from ..models import Course, EnrollmentStatusType, StudentCourse
from . import notifications

logger = logging.getLogger(__name__)


class EnrollmentError(Exception):
    """A rule this operation must not break (already enrolled, not enrolled).

    Carries a message meant for the teacher, not a stack trace. Views map it
    to a 400.
    """


#: What a teacher is told when an existing account may not join their course
#: because it belongs to a different school.
#:
#: Deliberately generic. The specific reason - that this address exists AND
#: is enrolled under another school - would turn the enrolment form into an
#: oracle: a teacher could enumerate addresses and learn which belong to
#: students at other schools, which is precisely the cross-tenant
#: information the rule exists to protect. The teacher still learns the
#: action failed and that it is an account-level problem they cannot fix by
#: retrying, and the server log carries the full detail for an admin.
CROSS_SCHOOL_REJECTION_MESSAGE = (
    "This account cannot be added to this school. If you believe this is a "
    "mistake, contact your school administrator."
)


def normalize_email(value):
    """Fold an address to the form the rest of the project stores.

    Login, Google sign-in, registration and school-admin creation all do
    `.strip().lower()`; the enrollment paths did not, and Postgres compares
    the unique `email` column case-SENSITIVELY. So "Target@b.test" did not
    match an existing "target@b.test" and was treated as a brand-new
    address - creating a SECOND account for a real person's mailbox, which
    both bypassed the cross-school rule (a fresh account has no school
    association) and mailed that person an invitation to an account that
    was not theirs. Production already contains one address with
    case-duplicate accounts, so this is not hypothetical.
    """
    return (value or "").strip().lower()


def find_account_by_email(email):
    """Look up an account by address, case-insensitively.

    `iexact` rather than an exact match on the normalised value, so the
    legacy rows that were stored before normalisation (2 in production) are
    still found rather than being shadowed by a new duplicate.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None
    return (
        CustomUser.objects.filter(email__iexact=normalized)
        .order_by("date_joined")
        .first()
    )


def schools_associated_with(student):
    """Every school this account is already tied to.

    Ownership is DERIVED, not read off a single column. In this codebase a
    student's own `school` FK is essentially never populated - measured
    against production, all 122 student accounts have `school_id = NULL`,
    because it is only set at creation from `course.teacher.school`, and
    most teachers are individual-track with no school themselves. Trusting
    that column would therefore have made this rule a no-op for every real
    student.

    The relation that actually carries ownership is the one the rest of the
    app already scopes by: student -> enrollments -> course -> teacher ->
    school. That is what this reads, plus the student's own FK when it does
    happen to be set.
    """
    school_ids = set(
        StudentCourse.objects.filter(student=student)
        .exclude(course__teacher__school__isnull=True)
        .values_list("course__teacher__school_id", flat=True)
    )
    if student.school_id:
        school_ids.add(student.school_id)
    return school_ids


def check_existing_account_may_join(student, course):
    """Raise EnrollmentError unless `student` may be enrolled into `course`.

    Applies to every path that attaches an ALREADY-EXISTING account to a
    course - single add, bulk import and direct add - so the rule cannot
    hold on one route and not another.

    The rules, in order:
      * a non-student account (teacher, school admin, superadmin) is never
        enrolled as a student, matching the check the single-add serializer
        has always applied;
      * an account already associated with a school may only join a course
        whose teacher belongs to that same school;
      * an account associated with no school at all may join freely - there
        is no ownership to violate, and this is the ordinary case for a
        student created by an individual-track teacher.

    Nothing here writes. A rejected attempt must leave the account's
    school, its enrollments and every other tenant row exactly as they
    were, so the caller raises instead of "fixing up" ownership.
    """
    if student.user_type != UserTypes.STUDENT:
        raise EnrollmentError(
            f"This email belongs to a {student.get_user_type_display().lower()} "
            "account and cannot be added as a student."
        )

    target_school_id = course.teacher.school_id if course.teacher else None
    student_school_ids = schools_associated_with(student)

    if not student_school_ids:
        return

    if student_school_ids == {target_school_id}:
        return

    # Logged with the detail deliberately withheld from the response, so an
    # administrator investigating "why was this rejected" has it.
    logger.warning(
        "Refused to enroll student %s into course %s: account is associated "
        "with school(s) %s but the course belongs to school %s.",
        student.pk,
        course.pk,
        sorted(str(s) for s in student_school_ids),
        target_school_id,
    )
    raise EnrollmentError(CROSS_SCHOOL_REJECTION_MESSAGE)


def enroll_student_by_email(*, course, email):
    """Add a student to `course` by email address, inviting them if needed.

    Three cases, in the order they are checked:
      * already enrolled -> EnrollmentError, nothing changes;
      * existing active account -> enrolled immediately, told they're in;
      * existing inactive account, or no account at all -> a PENDING
        enrollment plus an activation link to finish registration.

    Returns (student, is_new_student).
    """
    with transaction.atomic():
        # Lock the course row for the duration. The caller has already
        # confirmed ownership against the scoped queryset; re-fetching under
        # select_for_update() here (rather than locking up front) keeps that
        # ownership check's 404 distinguishable from a failure in here.
        course = Course.objects.select_for_update().get(pk=course.pk)
        email = normalize_email(email)
        student = find_account_by_email(email)

        if student is None:
            student = _create_pending_student(course=course, email=email)
            _create_enrollment(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.PENDING,
            )
            notifications.send_course_invitation_email(
                student, course, student.activation_token
            )
            return student, True

        if StudentCourse.objects.filter(student=student, course=course).exists():
            raise EnrollmentError("Student is already enrolled in this course.")

        # Same gate as bulk import and direct add - see
        # check_existing_account_may_join.
        check_existing_account_may_join(student, course)

        if student.is_active:
            _create_enrollment(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            notifications.send_added_to_course_email(student, course)
            return student, False

        activation_token = ensure_fresh_activation_token(student)
        _create_enrollment(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        notifications.send_course_invitation_email(student, course, activation_token)
        return student, True


def ensure_fresh_activation_token(student):
    """Return a usable activation token, reissuing only if the current one
    is missing or expired - so re-inviting a student doesn't needlessly
    invalidate a link they may already have open."""
    if (
        not student.activation_token
        or not student.activation_expires
        or student.activation_expires < timezone.now()
    ):
        return student.renew_activation_token()
    return student.activation_token


def _create_pending_student(*, course, email):
    return CustomUser.objects.create(
        email=email,
        user_type=UserTypes.STUDENT,
        is_active=False,
        school=course.teacher.school,
        activation_token=otp_manager.generate_otp(),
        activation_expires=timezone.now() + ACTIVATION_TOKEN_VALIDITY,
    )


def _create_enrollment(*, student, course, enrollment_status, auto_added=False):
    return StudentCourse.objects.create(
        student=student,
        course=course,
        enrollment_status=enrollment_status,
        auto_added=auto_added,
    )


def remove_student_from_course(*, course, student_id):
    """Delete one student's enrollment in one course. Nothing else.

    Explicitly NOT a user deletion. This used to call `student.delete()` as
    well, which destroyed the student's whole account and CASCADE-removed
    every enrollment, submission and grade they held under other teachers
    and other schools - a roster tidy-up in one classroom silently wiping
    another school's records, irreversibly.

    Accounts created for a roster and then unenrolled are therefore left
    behind on purpose; a retention policy for them is a product decision,
    not something this function should improvise.
    """
    with transaction.atomic():
        enrollment = StudentCourse.objects.filter(
            course=course, student_id=student_id
        ).first()

        if enrollment is None:
            raise EnrollmentError("Student is not enrolled in this course.")

        student = enrollment.student
        enrollment.delete()

    notifications.send_removed_from_course_email(student, course)
    return student


def renew_student_activation(*, token):
    """Reissue an expired activation link for a student with a pending
    enrollment, and tell both the student and their teacher.

    Returns (student, enrollment, new_token, expiry).
    """
    student = CustomUser.objects.filter(activation_token=token, is_active=False).first()

    if student is None:
        raise EnrollmentError("Invalid token or user not found.")

    enrollment = (
        StudentCourse.objects.filter(
            student=student, enrollment_status=EnrollmentStatusType.PENDING
        )
        .select_related("course", "course__teacher")
        .first()
    )

    if enrollment is None:
        raise EnrollmentError("No pending enrollment found for this user.")

    new_token = student.renew_activation_token()
    # Mirrors the expiry renew_activation_token() just set on the student
    # (users.models.ACTIVATION_TOKEN_VALIDITY).
    expiry_date = timezone.now() + ACTIVATION_TOKEN_VALIDITY

    notifications.send_token_renewal_emails(
        student, enrollment.course, new_token, expiry_date
    )
    return student, enrollment, new_token, expiry_date
