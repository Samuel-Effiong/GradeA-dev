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
import secrets

from django.db import transaction
from django.utils import timezone

from users.admin_power import holds_admin_power
from users.models import ACTIVATION_TOKEN_VALIDITY, CustomUser, UserActivity, UserTypes
from users.services import generate_temporary_password

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

#: What a teacher is told when the address belongs to an account that is not
#: a student (a teacher, a school admin, a super admin) - H-71.
#:
#: Deliberately neutral, for the same reason as the message above: naming
#: the role ("belongs to a teacher account") let a teacher probe any address
#: and learn who on the platform is staff. The server log carries the
#: account id and its type for an admin.
NOT_A_STUDENT_MESSAGE = "This email can't be added as a student."


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


#: The domain of the address the roster gives a student who has none
#: (H-99). Such an account is a teacher-managed roster entry: it is never
#: signed into, and its address is a server-made key, never a mailbox.
PLACEHOLDER_EMAIL_DOMAIN = "@student.local"

#: How many fresh addresses to try before giving up. With a 64-bit token a
#: single retry is already vanishingly unlikely; the loop is for the
#: principle (never attach, never fail on a coincidence), not the odds.
PLACEHOLDER_EMAIL_ATTEMPTS = 5


def is_placeholder_email(email):
    """True for an address in the placeholder domain, in any letter case
    and with surrounding spaces."""
    return normalize_email(email).endswith(PLACEHOLDER_EMAIL_DOMAIN)


def new_placeholder_email(first_name, last_name):
    """A fresh placeholder address for a student added with no email.

    The name parts are only for a human reading the table; the token is
    what makes it unique. It used to be `first.last<0-9999>`, and a new
    student who drew a suffix already in use was given the EXISTING
    account instead of a new one (H-99). 64 random bits now, and the
    caller still checks the address is free and never attaches on a match.
    """
    safe_first = "".join(c for c in first_name.lower() if c.isalnum())[:20]
    safe_last = "".join(c for c in last_name.lower() if c.isalnum())[:20]
    return f"{safe_first}.{safe_last}.{secrets.token_hex(8)}{PLACEHOLDER_EMAIL_DOMAIN}"


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
    # H-203, THE PRINCIPLE: a STUDENT-typed row that carries admin power is not
    # an account a teacher can attach to a course (and re-activate with an
    # emailed password): the answer for an address that cannot be enrolled.
    if student.user_type != UserTypes.STUDENT or holds_admin_power(student):
        logger.info(
            "Refused to enrol non-student account %s (%s) in course %s",
            student.pk,
            student.user_type,
            course.pk,
        )
        raise EnrollmentError(NOT_A_STUDENT_MESSAGE)

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


#: What a teacher is told when the address belongs to an account someone
#: deactivated. Only reached after check_existing_account_may_join passes, so
#: it never tells a teacher anything about another school's accounts.
#: H-152: the one answer of both routes of the old code-based student
#: sign-up (POST /auth/register/student and the renewal of a code), to every
#: request, whatever it sends. User's decision of 2026-10-07: the door is
#: closed outright. A student does not name themselves, and nothing mints
#: such a code any more; what is left is converted by the one-off command
#: backfill_pending_student_invites or healed by the teacher's next add.
OLD_INVITATION_CLOSED_MESSAGE = (
    "Invitations of this kind are no longer used. "
    "Ask your teacher to add you to the class again."
)

DEACTIVATED_ACCOUNT_MESSAGE = (
    "This student's account is disabled. Contact support if they should have access."
)


class AccountDisabledError(EnrollmentError):
    """The address belongs to an account that was deactivated, not one that
    never finished onboarding. A teacher's action never re-enables it (SM
    product rule 2026-09-29): no reactivation, no email, no enrollment."""


def has_signed_in(student):
    """Whether this account has ever signed in.

    `last_login` is stamped on every successful password or Google sign-in
    (users.services.stamp_last_login, since the retire-student-token-signup
    change); before that it was never maintained, so older sign-ins show
    only as a UserActivity row (written on authenticated requests). Either
    counts.
    """
    return student.last_login is not None or (
        UserActivity.objects.filter(user=student).exists()
    )


def was_never_activated(student):
    """An inactive row that never finished onboarding: never verified and
    never signed in. Only such a row may be (re)activated by a teacher's add.

    `email_verified_at` is the activation signal every old door stamped
    (/auth/register/student, /auth/verify, Google sign-in), and sign-in
    activity covers accounts that were used without it (a new-scheme
    student who logged in with the emailed password). An account a
    superadmin deactivated after it was ever used has one or the other.

    Known edge: an account deactivated before it was EVER used (never
    verified, never signed in) is indistinguishable from a legacy pending
    row - nothing records who set is_active=False - so it is treated as
    one and re-invited.
    """
    return (
        not student.is_active
        and student.email_verified_at is None
        and not has_signed_in(student)
    )


def _has_no_name(student):
    """No first name AND no last name. Half a name is a name."""
    return (
        not (student.first_name or "").strip() and not (student.last_name or "").strip()
    )


def enroll_student_by_email(
    *,
    course,
    email,
    first_name="",
    middle_name="",
    last_name="",
    fill_empty_name=False,
):
    """Add a student to `course` by email address, inviting them if needed.

    The names are used when a brand-new account is created. An existing
    account's names are never overwritten. H-148: with `fill_empty_name`
    (the single add and the class-list import, where the teacher gives
    the name) an existing account that has NO name is given that name; a student cannot name
    themselves and until this nobody could name such an account. The
    enrolment's own rule (one exact name per course, StudentCourse.clean)
    then applies to the filled name, and a refusal undoes the filling: all
    of it is one transaction.

    Mirrors the license-teacher invite (billing/license_service.py): a
    newly invited student is active immediately with a system-generated
    temporary password, and logs straight in instead of clicking an
    activation link first.

    The cases, in the order they are checked:
      * already enrolled -> EnrollmentError, nothing changes;
      * an inactive account that was ever activated or used, i.e. one
        someone deactivated -> AccountDisabledError, nothing changes and
        nothing is sent (see was_never_activated);
      * active account that has ever signed in -> enrolled immediately,
        told they're in; password untouched, sessions untouched;
      * no account at all, or an existing account that has never signed in
        (a legacy never-activated is_active=False row, or an active one with
        no sign-in signal - see has_signed_in) -> PENDING enrollment plus a
        fresh temporary password and a login-credentials email.

    `must_change_password` is informational and deliberately NOT used here:
    a student can use the app indefinitely without clearing it, so treating
    it as "still onboarding" reset real students' passwords on every add
    (SM ruling 2026-09-28; a pre-existing bug on the single-add path).

    Returns (student, invited): True when a login-credentials email with a
    new password was sent, False when an existing student was enrolled.
    """
    with transaction.atomic():
        # Lock the course row for the duration. The caller has already
        # confirmed ownership against the scoped queryset; re-fetching under
        # select_for_update() here (rather than locking up front) keeps that
        # ownership check's 404 distinguishable from a failure in here.
        course = Course.objects.select_for_update().get(pk=course.pk)
        email = normalize_email(email)
        if is_placeholder_email(email):
            # H-99: a placeholder address is a server-made key to a
            # roster-only student, not a mailbox. Supplied by a caller it
            # would attach that student to this course, so it is refused
            # with the same neutral answer as a staff address.
            logger.info(
                "Refused a caller-supplied placeholder address for course %s",
                course.pk,
            )
            raise EnrollmentError(NOT_A_STUDENT_MESSAGE)
        student = find_account_by_email(email)

        if student is None:
            student, generated_password = _create_new_student(
                course=course,
                email=email,
                first_name=first_name,
                middle_name=middle_name,
                last_name=last_name,
            )
            _create_enrollment(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.PENDING,
            )
            notifications.send_student_login_invitation_email(
                student, course, generated_password
            )
            return student, True

        if StudentCourse.objects.filter(student=student, course=course).exists():
            raise EnrollmentError("Student is already enrolled in this course.")

        # Same gate as bulk import and direct add - see
        # check_existing_account_may_join.
        check_existing_account_may_join(student, course)

        if not student.is_active and not was_never_activated(student):
            # Deactivated on purpose (e.g. the admin "Mark selected users as
            # inactive" action). Refused before any write or email.
            logger.warning(
                "Refused to enroll student %s into course %s: the account is "
                "deactivated.",
                student.pk,
                course.pk,
            )
            raise AccountDisabledError(DEACTIVATED_ACCOUNT_MESSAGE)

        name_fields = []
        if fill_empty_name and first_name and last_name and _has_no_name(student):
            student.first_name = first_name
            student.middle_name = middle_name
            student.last_name = last_name
            name_fields = ["first_name", "middle_name", "last_name"]

        if student.is_active and has_signed_in(student):
            if name_fields:
                student.save(update_fields=name_fields)
            _create_enrollment(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            notifications.send_added_to_course_email(student, course)
            return student, False

        # Never signed in - e.g. invited to a different course and never
        # logged in - or a legacy never-activated row from before this change.
        # A fresh password every resend: the previous one's
        # plaintext can't be recovered from the stored hash to put in this
        # email, so there's nothing to reuse. is_active is force-set True
        # here too, healing any legacy row this encounters.
        generated_password = generate_temporary_password(student)
        student.set_password(generated_password)
        student.is_active = True
        student.must_change_password = True
        # A legacy pending row's old activation code is dead once the row is
        # active (both code doors match is_active=False only); clear it so
        # no student row carries a code after onboarding.
        student.activation_token = None
        student.activation_expires = None
        student.save(
            update_fields=[
                "password",
                "is_active",
                "must_change_password",
                "activation_token",
                "activation_expires",
                *name_fields,
            ]
        )

        _create_enrollment(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        notifications.send_student_login_invitation_email(
            student, course, generated_password
        )
        return student, True


def _create_new_student(*, course, email, first_name="", middle_name="", last_name=""):
    """Create a brand-new student account, active immediately with a
    system-generated temporary password.

    Returns (student, generated_password) - the plaintext password only
    ever exists here and in the email it's handed to; it is never stored.
    """
    student = CustomUser.objects.create(
        email=email,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        user_type=UserTypes.STUDENT,
        is_active=True,
        school=course.teacher.school,
    )
    generated_password = generate_temporary_password(student)
    student.set_password(generated_password)
    student.must_change_password = True
    student.save()
    return student, generated_password


def _create_enrollment(*, student, course, enrollment_status, auto_added=False):
    return StudentCourse.objects.create(
        student=student,
        course=course,
        enrollment_status=enrollment_status,
        auto_added=auto_added,
    )


def activate_pending_enrollments_on_login(student):
    """Flip every PENDING enrollment for `student` to ENROLLED.

    Called from every successful student login (users/serializers.py's
    CustomTokenObtainPairSerializer.validate) - a cheap no-op query when
    nothing is pending, so there's no separate "is this their first
    login" tracking to maintain. One bulk .update() so a student invited
    to several courses before ever logging in has all of them activate
    together.
    """
    StudentCourse.objects.filter(
        student=student, enrollment_status=EnrollmentStatusType.PENDING
    ).update(enrollment_status=EnrollmentStatusType.ENROLLED)


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
    # H-47: student rows only. A pending teacher's 6-digit verification code
    # lives in the same column; matching it here reached
    # renew_activation_token()'s ValueError and answered 500, which told a
    # guesser the code was real.
    student = CustomUser.objects.filter(
        activation_token=token, is_active=False, user_type=UserTypes.STUDENT
    ).first()

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
