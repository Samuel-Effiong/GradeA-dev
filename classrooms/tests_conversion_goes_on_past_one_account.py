"""
H-152, the second delta: one account never stops the conversion.

Found by Verifier 2 by reading (2026-10-07). A course need not have a
teacher (`Course.teacher` may be empty). The login email named the
inviting teacher with `course.teacher.get_full_name()`, so for a leftover
student whose pending place is in a course with no teacher the sender
raised. That is not a queue outage, nothing swallowed it, and the one-off
command `backfill_pending_student_invites` stopped there. The account's
conversion was undone with its transaction, so it still matched the
selection, and the command goes oldest first: every later run stopped at
the same account and the accounts after it were never converted. The
preview does not build an email and showed nothing of it.

Senior Manager's ruling:
  1. the SENDER does not need a teacher. With none, the email reads "You
     have been invited to join <course> on Grade A+." and the account is
     converted like any other;
  2. the COMMAND never lets one account stop the run: any error while
     converting one account undoes that account, prints
     "NOT converted (<the error's type>): student <id>", the type only and
     never the error's text (a text can hold an address), counts it in
     the summary and goes on. A second run tries them again.

  3. a DATABASE error is not "that account's": the email is queued before
     the account's transaction commits, so a failed commit has already
     sent a password that was not saved, and going on would repeat it.
     The run STOPS at the first one; its last line says so and names, by
     id, the student whose email may have been sent.

What is NOT swallowed: an interrupt from the keyboard still stops the run.
What a preview cannot know: whether a real run's conversion will fail.

A teacher's ordinary add cannot reach a course with no teacher: every
add route looks the course up among the caller's own courses. So the
first part changes nothing there, and one test holds its wording.

Run with:
    python manage.py test classrooms.tests_conversion_goes_on_past_one_account
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, InterfaceError
from django.utils import timezone

from classrooms.models import Course, EnrollmentStatusType, StudentCourse
from classrooms.services import notifications
from classrooms.tests_conversion_needs_a_queued_email import (
    CODE,
    NOT_CONVERTED,
    QueueBase,
    User,
)
from users.models import UserTypes

READY = (
    "\n\nYour account is ready - log in below with your email and the "
    "temporary password: "
)
NO_TEACHER = "You have been invited to join Orphan course on Grade A+."
WITH_TEACHER = "Grace Hopper has invited you to join H152d course on Grade A+."
FAULT_TEXT = "a fault that names"


class TeacherlessBase(QueueBase):
    def setUp(self):
        super().setUp()
        self.orphan_course = Course.objects.create(
            name="Orphan course", teacher=None, session=None
        )

    def leftover_in(self, course, email, *, joined_minutes_ago):
        student = User.objects.create_user(
            email=email,
            password=None,
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token=CODE,
            activation_expires=timezone.now() + timedelta(hours=23),
        )
        User.objects.filter(pk=student.pk).update(
            date_joined=timezone.now() - timedelta(minutes=joined_minutes_ago)
        )
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        return student


class TheSenderDoesNotNeedATeacherTest(TeacherlessBase):
    def wording(self, course, email):
        student = self.leftover_in(course, email, joined_minutes_ago=5)
        queued = notifications.send_student_login_invitation_email(
            student, course, "a-temporary-password"
        )
        self.assertIs(queued, True)
        return self.queue.sent[email]["merge_data"]["top_content"]

    def test_with_no_teacher_the_email_names_nobody(self):
        text = self.wording(self.orphan_course, "orphan@h152-delta.test")
        self.assertEqual(text, NO_TEACHER + READY + "a-temporary-password")

    def test_with_a_teacher_the_wording_is_as_it_was(self):
        text = self.wording(self.course, "taught@h152-delta.test")
        self.assertEqual(text, WITH_TEACHER + READY + "a-temporary-password")


class AStudentOfACourseWithNoTeacherIsConvertedTest(TeacherlessBase):
    def test_the_conversion_converts_them_and_goes_on(self):
        orphan = self.leftover_in(
            self.orphan_course, "orphan@h152-delta.test", joined_minutes_ago=30
        )
        later = self.leftover("later@h152-delta.test", joined_minutes_ago=10)

        text = self.run_command()

        for student in (orphan, later):
            row = User.objects.get(pk=student.pk)
            self.assertTrue(row.is_active, student.email)
            self.assertIsNone(row.activation_token, student.email)
            self.assertIn(f"convert: student {student.pk} ", text)
        self.assertEqual(self.queue.queued, [orphan.email, later.email])
        self.assertIn("Backfill complete: 2 converted,", text)
        self.assertNotIn("NOT converted (", text.split("Backfill complete")[0])
        self.assertIn("still holding a code: 0", text)


class OneAccountNeverStopsTheRunTest(QueueBase):
    """An error that is not a queue outage, while one account is being
    converted. Here the email task itself raises it; the error's text
    holds the student's address, as an error's text can."""

    def setUp(self):
        super().setUp()
        # Oldest first: the one that fails comes first.
        self.broken = self.leftover("bro.ken@h152-delta.test", joined_minutes_ago=30)
        self.fine = self.leftover("fine@h152-delta.test", joined_minutes_ago=10)
        self.queue.faulty = {
            self.broken.email: ValueError(f"{FAULT_TEXT} {self.broken.email}")
        }
        self.line = f"NOT converted (ValueError): student {self.broken.pk}"

    def test_the_account_is_left_as_it_was_and_listed_by_the_errors_type(self):
        before = self.snapshot(self.broken)

        text = self.run_command()

        self.assertEqual(self.snapshot(self.broken), before)
        self.assertIn(self.line, text)
        self.assertNotIn(f"convert: student {self.broken.pk} ", text)
        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.broken,
                course=self.course,
                enrollment_status=EnrollmentStatusType.PENDING,
            ).exists()
        )

    def test_the_run_goes_on_to_the_others(self):
        self.run_command()

        row = User.objects.get(pk=self.fine.pk)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.activation_token)
        self.assertEqual(self.queue.queued, [self.fine.email])

    def test_the_summary_counts_them_apart_from_the_unqueued(self):
        text = self.run_command()

        self.assertIn("Backfill complete: 1 converted,", text)
        self.assertIn("0 NOT converted (email could not be queued", text)
        self.assertIn("1 NOT converted (an error", text)
        self.assertIn("still holding a code: 1", text)

    def test_the_line_carries_the_errors_type_and_never_its_text(self):
        text = self.run_command()

        self.assertIn("(ValueError)", text)
        self.assertNotIn(FAULT_TEXT, text)
        self.assertNotIn("@", text)
        # It is its own kind of line, not the one for an unqueued email.
        self.assertNotIn(NOT_CONVERTED, text)

    def test_a_second_run_takes_them_up_again(self):
        self.run_command()
        self.queue.faulty = {}

        text = self.run_command()

        self.assertIn(f"convert: student {self.broken.pk} ", text)
        self.assertNotIn(f"student {self.fine.pk}", text)
        self.assertIn("Backfill complete: 1 converted,", text)
        self.assertIn("0 NOT converted (an error", text)
        self.assertIn("still holding a code: 0", text)
        self.assertTrue(User.objects.get(pk=self.broken.pk).is_active)
        self.assertEqual(
            sorted(self.queue.queued), sorted([self.fine.email, self.broken.email])
        )

    def test_an_interrupt_from_the_keyboard_still_stops_the_run(self):
        self.queue.faulty = {self.broken.email: KeyboardInterrupt()}
        before = (self.snapshot(self.broken), self.snapshot(self.fine))

        with self.assertRaises(KeyboardInterrupt):
            self.run_command()

        # Nothing was converted: the first account's conversion is undone,
        # the second was never reached.
        self.assertEqual((self.snapshot(self.broken), self.snapshot(self.fine)), before)
        self.assertEqual(self.queue.queued, [])


class ADatabaseErrorStopsTheRunTest(QueueBase):
    """The save of the first account fails in the database. (A failure at
    the commit, after the email is queued, cannot be staged inside a test's
    own transaction; the command treats both alike, and its line says the
    email MAY have been sent.)"""

    def setUp(self):
        super().setUp()
        self.broken = self.leftover("bro.ken@h152-delta.test", joined_minutes_ago=30)
        self.fine = self.leftover("fine@h152-delta.test", joined_minutes_ago=10)

    def run_with_a_failing_save(self, error):
        real_save = User.save
        broken_pk = self.broken.pk

        def save(instance, *args, **kwargs):
            if instance.pk == broken_pk:
                raise error
            return real_save(instance, *args, **kwargs)

        out = StringIO()
        with patch.object(User, "save", autospec=True, side_effect=save):
            with self.assertRaises(type(error)):
                call_command("backfill_pending_student_invites", stdout=out)
        return out.getvalue()

    def test_the_run_stops_and_says_which_account(self):
        for kind in (DatabaseError, InterfaceError):
            with self.subTest(kind=kind.__name__):
                before = (self.snapshot(self.broken), self.snapshot(self.fine))

                text = self.run_with_a_failing_save(
                    kind(f"{FAULT_TEXT} {self.broken.email}")
                )

                # Nothing converted, the second account never reached,
                # nobody mailed.
                self.assertEqual(
                    (self.snapshot(self.broken), self.snapshot(self.fine)), before
                )
                self.assertEqual(self.queue.attempts, [])
                last = text.strip().splitlines()[-1]
                self.assertIn(
                    f"STOPPED on a database error ({kind.__name__}) "
                    f"at student {self.broken.pk}.",
                    last,
                )
                self.assertIn("MAY have been sent", last)
                self.assertIn("Do not run again", last)
                # Ids only, no text of the error, and no summary: the run
                # did not finish.
                self.assertNotIn("@", text)
                self.assertNotIn(FAULT_TEXT, text)
                self.assertNotIn("Backfill complete", text)
