"""
One-off cutover: converts every student left in the OLD pending-invite
scheme (is_active=False + an activation_token, from before
enroll_student_by_email started activating new invites immediately - see
classrooms/services/enrollment.py) to the new one - a real, working
temporary password, is_active=True, must_change_password=True - and sends
each one the new login-credentials email
(notifications.send_student_login_invitation_email) in place of the old
activation-link one.

Idempotent by construction, not by a separate marker: the selection query
is is_active=False AND activation_token is set, and every row this command
touches comes out with is_active=True. A row already converted (by this
command, or by the student registering the old way before this ran) no
longer matches the query, so a second run finds nothing left to do for it.
A student who HAS since logged in (must_change_password reset to False by
their own password change) is likewise never revisited, because they too
are already is_active=True and out of scope.

Sends real email and mutates real user state - run --dry-run first and
read the output before running for real.

Usage:
    python manage.py backfill_pending_student_invites [--dry-run]
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from classrooms.models import EnrollmentStatusType, StudentCourse
from classrooms.services import notifications
from users.models import CustomUser, UserTypes
from users.services import generate_temporary_password

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Convert legacy is_active=False pending student invites to the "
        "active-immediately scheme: real password, is_active=True, "
        "must_change_password=True, and a resent login-credentials email."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        students = CustomUser.objects.filter(
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token__isnull=False,
        ).exclude(activation_token="")

        count = 0
        skipped = 0
        for student in students:
            course = self._pending_course_for(student)
            if course is None:
                # No pending enrollment left to notify about (e.g. the
                # enrollment was deleted after the invite went out). Nothing
                # sane to email, so leave the account alone rather than
                # activating it with no course context.
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"[dry-run] would convert {student.email} (pending: {course.name})"
                )
                count += 1
                continue

            self._convert(student, course)
            self.stdout.write(f"Converted {student.email} (pending: {course.name})")
            count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete{' (dry run)' if dry_run else ''}: "
                f"{count} student(s) converted, {skipped} skipped (no pending enrollment)."
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
        student.save(update_fields=["password", "is_active", "must_change_password"])
        notifications.send_student_login_invitation_email(
            student, course, generated_password
        )
