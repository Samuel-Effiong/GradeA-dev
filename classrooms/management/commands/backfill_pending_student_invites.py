"""
One-off cutover: converts every student left in the OLD pending-invite
scheme (is_active=False + an activation_token, from before
enroll_student_by_email started activating new invites immediately - see
classrooms/services/enrollment.py) to the new one - a real, working
temporary password, is_active=True, must_change_password=True - and sends
each one the new login-credentials email
(notifications.send_student_login_invitation_email) in place of the old
activation-link one.

It is the precondition for removing the code-based student sign-up
(POST /auth/register/student and /course/student/renew-student-token): once
it has run, no student row carries an activation code, so nothing is left
for those endpoints to complete.

  * A student with a pending enrollment is converted as above, and their
    old code is cleared.
  * A student with NO pending enrollment (e.g. the enrollment was deleted
    after the invite went out) has nothing to be emailed about, so the
    account is left inactive - but its dead code is still cleared, so it
    can't be completed through the old door either. Adding them to a course
    later goes through enroll_student_by_email, which activates them then.
  * A student on a placeholder @student.local address is, per the founder,
    intentionally inaccessible to the student: the code is cleared, and it
    is neither activated nor emailed (the email is undeliverable, and
    activating would give the row a usable password nobody holds, unlike
    direct add's unusable one).
  * A student that was ever verified or signed in is inactive because
    someone deactivated it (enrollment.was_never_activated): the code is
    cleared, and it is neither re-enabled nor emailed.

Idempotent by construction, not by a separate marker: the selection query
is is_active=False AND activation_token set, and every row this command
touches comes out with its code cleared. A second run finds nothing.

Output carries internal ids only - never an email address, name or
credential - so it is safe in Railway's logs. Sends real email and
mutates real user state: run --dry-run first and read the output.

H-152: the summary also counts the converted accounts that have no name.
An old-scheme account was created nameless and a student does not name
themselves, so each of those must then be named by their teacher. Runbook:
GAP-planning/H-152-conversion-runbook.md (outside the repository).

H-152 (delta): an account whose login email could not be handed to the
queue is NOT converted. Its conversion is undone (each one is its own
transaction), the output says "NOT converted (email could not be queued):
student <id>", the summary counts it, and it still matches the selection,
so a second run picks up exactly those. Before this, such a student came
out switched on with a password nobody held and no email, and nothing
said which account. A queued email lost later is beyond this command.

Usage:
    python manage.py backfill_pending_student_invites --dry-run
    python manage.py backfill_pending_student_invites
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from classrooms.models import EnrollmentStatusType, StudentCourse
from classrooms.services import notifications
from classrooms.services.enrollment import was_never_activated
from users.models import CustomUser, UserTypes
from users.services import generate_temporary_password

logger = logging.getLogger(__name__)

PLACEHOLDER_DOMAIN = "@student.local"


class EmailNotQueued(Exception):
    """Raised inside one account's transaction to undo its conversion."""


def pending_students():
    return CustomUser.objects.filter(
        user_type=UserTypes.STUDENT,
        is_active=False,
        activation_token__isnull=False,
    ).exclude(activation_token="")


class Command(BaseCommand):
    help = (
        "Convert legacy is_active=False pending student invites to the "
        "active-immediately scheme (real password, is_active=True, "
        "must_change_password=True, a resent login-credentials email) and "
        "clear every student activation code."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        prefix = "[dry-run] would " if dry_run else ""

        converted = cleared_only = placeholder = deactivated = 0
        not_queued = 0
        # H-152: an old-scheme account was created without a name (the
        # student typed it at the door, which is now closed), and a student
        # does not name themselves. Each converted account with no name
        # must be named by a teacher afterwards, so the run says how many.
        converted_without_a_name = 0
        for student in pending_students().order_by("date_joined"):
            if student.email.endswith(PLACEHOLDER_DOMAIN):
                # Founder: @student.local students are intentionally
                # inaccessible to the student. No email (undeliverable), no
                # activation (it would hand the row a usable password that
                # nobody holds, unlike direct add's unusable one) - only the
                # dead code goes.
                self.stdout.write(
                    f"{prefix}clear code only (placeholder address): "
                    f"student {student.pk}"
                )
                if not dry_run:
                    self._clear_code(student)
                placeholder += 1
                continue

            if not was_never_activated(student):
                # Verified or signed in at some point, so it is inactive
                # because someone deactivated it: never re-enabled or
                # emailed (SM product rule 2026-09-29), only the dead code
                # goes.
                self.stdout.write(
                    f"{prefix}clear code only (deactivated account): "
                    f"student {student.pk}"
                )
                if not dry_run:
                    self._clear_code(student)
                deactivated += 1
                continue

            course = self._pending_course_for(student)
            if course is None:
                self.stdout.write(
                    f"{prefix}clear code only (no pending enrollment): "
                    f"student {student.pk}"
                )
                if not dry_run:
                    self._clear_code(student)
                cleared_only += 1
                continue

            if not dry_run:
                try:
                    self._convert(student, course)
                except EmailNotQueued:
                    # Undone by the rollback; the row still matches the
                    # selection, so the next run takes it up again.
                    self.stdout.write(
                        "NOT converted (email could not be queued): "
                        f"student {student.pk}"
                    )
                    not_queued += 1
                    continue
            self.stdout.write(
                f"{prefix}convert: student {student.pk} (pending course "
                f"{course.pk})"
            )
            converted += 1
            if (
                not (student.first_name or "").strip()
                and not (student.last_name or "").strip()
            ):
                converted_without_a_name += 1

        remaining = pending_students().count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill {'(dry run) ' if dry_run else ''}complete: "
                f"{converted} converted, {cleared_only} code-only cleared "
                f"(no pending enrollment), {placeholder} code-only cleared "
                f"(placeholder address, left inactive, not emailed), "
                f"{deactivated} code-only cleared (deactivated account, left "
                f"inactive, not emailed). "
                f"{not_queued} NOT converted (email could not be queued; "
                f"run again when the email queue is up). "
                f"{converted_without_a_name} of the converted have no name "
                f"(a teacher must name each of them). "
                f"Inactive students still holding a code: {remaining}."
            )
        )

    @staticmethod
    def _pending_course_for(student):
        enrollment = (
            StudentCourse.objects.filter(
                student=student, enrollment_status=EnrollmentStatusType.PENDING
            )
            .select_related("course")
            .order_by("created_at")
            .first()
        )
        return enrollment.course if enrollment else None

    @staticmethod
    @transaction.atomic
    def _convert(student, course):
        generated_password = generate_temporary_password(student)
        student.set_password(generated_password)
        student.is_active = True
        student.must_change_password = True
        student.activation_token = None
        student.activation_expires = None
        student.save(
            update_fields=[
                "password",
                "is_active",
                "must_change_password",
                "activation_token",
                "activation_expires",
            ]
        )
        queued = notifications.send_student_login_invitation_email(
            student, course, generated_password
        )
        if not queued:
            raise EmailNotQueued

    @staticmethod
    def _clear_code(student):
        student.activation_token = None
        student.activation_expires = None
        student.save(update_fields=["activation_token", "activation_expires"])
