"""
H-152, the delta: the conversion does not convert an account whose login
email could not be queued.

The one-off command `backfill_pending_student_invites` switches a leftover
old-scheme student on, sets a new temporary password and queues the email
that carries it. The sender swallowed a queue that could not be reached
("the enrollment happened but the notification didn't"), so such a student
came out switched on, with a password nobody holds, no email, and no line
of the output saying which account. "Forgot password" refuses an account
that was never verified, so they could not help themselves that way.

Senior Manager's ruling (2026-10-07): the sender says whether the email
was queued; the command undoes that ONE account's conversion when it was
not, prints "NOT converted (email could not be queued): student <id>",
counts it in the summary, and a second run picks up exactly those. Ids
only in the output, as before.

What must NOT change: a teacher's ordinary add by email. There the
enrolment still happens when the queue is down (the teacher's request
must not fail on an email), and the last class holds that.

What this cannot know: an email that WAS queued and is lost later (the
mail provider, a spam folder).

The queue is made unreachable the way it really fails: the task's
`.delay` raises a connection error and the real `safe_delay` handles it.

Run with:
    python manage.py test classrooms.tests_conversion_needs_a_queued_email
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from classrooms.services import notifications
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
TEACHER_PASSWORD = "Str0ng-h152-delta!"  # pragma: allowlist secret
CODE = "123456"
DISPATCH_LOGGER = "AutoGrader.dispatch"
NOT_CONVERTED = "NOT converted (email could not be queued): student "


class Queue:
    """Stands where the email task stands. `.delay` raises a connection
    error for the addresses in `down` (or for all, with `all_down`), as a
    broker that cannot be reached does; otherwise it records the call.

    For the tests of the second delta (classrooms/
    tests_conversion_goes_on_past_one_account.py): `faulty` maps an address
    to an exception to raise that is NOT an outage, and `sent` keeps what
    each queued email was built with."""

    name = "send_email_task"

    def __init__(self):
        self.down = set()
        self.all_down = False
        self.faulty = {}
        self.queued = []
        self.attempts = []
        self.sent = {}

    def delay(self, *args, **kwargs):
        (address,) = kwargs["recipient_list"]
        self.attempts.append(address)
        if address in self.faulty:
            raise self.faulty[address]
        if self.all_down or address in self.down:
            raise ConnectionError("broker unreachable (test)")
        self.queued.append(address)
        self.sent[address] = kwargs
        return object()


@override_settings(CACHES=LOCMEM)
class QueueBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.queue = Queue()
        task = patch.object(notifications, "send_email_task", self.queue)
        task.start()
        self.addCleanup(task.stop)

        self.teacher = User.objects.create_user(
            email="owner@h152-delta.test",
            password=TEACHER_PASSWORD,
            first_name="Grace",
            last_name="Hopper",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        session = Session.objects.create(name="H152d", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H152d course", teacher=self.teacher, session=session
        )

    def leftover(self, email, *, joined_minutes_ago, **names):
        """An invitation of the old scheme: switched off, holding a code,
        with a pending place in the course. The command goes through them
        oldest first."""
        student = User.objects.create_user(
            email=email,
            password=None,
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token=CODE,
            activation_expires=timezone.now() + timedelta(hours=23),
            **names,
        )
        User.objects.filter(pk=student.pk).update(
            date_joined=timezone.now() - timedelta(minutes=joined_minutes_ago)
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        return student

    def snapshot(self, student):
        row = User.objects.get(pk=student.pk)
        return (
            row.is_active,
            row.activation_token,
            row.activation_expires,
            row.password,
            row.must_change_password,
        )

    def run_command(self, *args, expect_queue_failure=False):
        out = StringIO()
        if expect_queue_failure:
            # The failure leaves its trace in the service's log, which is
            # what the runbook tells the person running it to look for.
            with self.assertLogs(DISPATCH_LOGGER, level="ERROR") as logs:
                call_command("backfill_pending_student_invites", *args, stdout=out)
            self.assertIn("broker unavailable", "\n".join(logs.output))
        else:
            call_command("backfill_pending_student_invites", *args, stdout=out)
        return out.getvalue()


class TheSenderSaysWhetherTheEmailWasQueuedTest(QueueBase):
    def invite(self):
        student = self.leftover("one@h152-delta.test", joined_minutes_ago=5)
        return notifications.send_student_login_invitation_email(
            student, self.course, "a-temporary-password"
        )

    def test_true_when_it_was_queued(self):
        self.assertIs(self.invite(), True)
        self.assertEqual(self.queue.queued, ["one@h152-delta.test"])

    def test_false_and_no_error_when_the_queue_could_not_be_reached(self):
        self.queue.all_down = True
        with self.assertLogs(DISPATCH_LOGGER, level="ERROR"):
            result = self.invite()
        self.assertIs(result, False)
        self.assertEqual(self.queue.queued, [])


class TheConversionNeedsAQueuedEmailTest(QueueBase):
    def setUp(self):
        super().setUp()
        # Oldest first: the one whose email cannot be queued comes first,
        # so what happens to the second shows the run goes on.
        self.unlucky = self.leftover("un.lucky@h152-delta.test", joined_minutes_ago=30)
        self.lucky = self.leftover(
            "lucky@h152-delta.test",
            joined_minutes_ago=10,
            first_name="Ada",
            last_name="Lovelace",
        )
        self.queue.down = {self.unlucky.email}

    def test_the_account_is_left_exactly_as_it_was(self):
        before = self.snapshot(self.unlucky)

        text = self.run_command(expect_queue_failure=True)

        self.assertEqual(self.snapshot(self.unlucky), before)
        self.assertFalse(User.objects.get(pk=self.unlucky.pk).is_active)
        self.assertIn(f"{NOT_CONVERTED}{self.unlucky.pk}", text)
        self.assertNotIn(f"convert: student {self.unlucky.pk} ", text)
        # Its place in the course is untouched.
        self.assertTrue(
            StudentCourse.objects.filter(
                student=self.unlucky,
                course=self.course,
                enrollment_status=EnrollmentStatusType.PENDING,
            ).exists()
        )

    def test_the_run_goes_on_and_converts_the_others(self):
        self.run_command(expect_queue_failure=True)

        row = User.objects.get(pk=self.lucky.pk)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.activation_token)
        self.assertTrue(row.has_usable_password())
        self.assertEqual(self.queue.queued, [self.lucky.email])

    def test_the_summary_counts_them(self):
        text = self.run_command(expect_queue_failure=True)

        self.assertIn("Backfill complete: 1 converted,", text)
        self.assertIn("1 NOT converted (email could not be queued", text)
        # The unconverted one is nameless; it is not counted among "the
        # converted" with no name.
        self.assertIn("0 of the converted have no name", text)
        self.assertIn("still holding a code: 1", text)

    def test_a_second_run_picks_up_exactly_those(self):
        self.run_command(expect_queue_failure=True)
        self.queue.down = set()

        text = self.run_command()

        self.assertIn(f"convert: student {self.unlucky.pk} ", text)
        self.assertNotIn(f"student {self.lucky.pk}", text)
        self.assertIn("Backfill complete: 1 converted,", text)
        self.assertIn("0 NOT converted (email could not be queued", text)
        self.assertIn("still holding a code: 0", text)
        row = User.objects.get(pk=self.unlucky.pk)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.activation_token)
        # One email each, over the two runs: the lucky one is not mailed
        # twice.
        self.assertEqual(
            sorted(self.queue.queued), sorted([self.lucky.email, self.unlucky.email])
        )

    def test_the_output_names_nobody_when_an_email_could_not_be_queued(self):
        text = self.run_command(expect_queue_failure=True)

        self.assertIn(NOT_CONVERTED, text)
        self.assertNotIn("@", text)
        self.assertNotIn("Lovelace", text)

    def test_a_run_with_the_queue_up_says_none_was_left(self):
        self.queue.down = set()

        text = self.run_command()

        self.assertIn("Backfill complete: 2 converted,", text)
        self.assertIn("0 NOT converted (email could not be queued", text)
        self.assertNotIn(NOT_CONVERTED, text)
        self.assertIn("still holding a code: 0", text)

    def test_the_preview_queues_nothing_and_does_not_guess(self):
        """A dry run cannot know whether the queue will be up; it tries
        nothing and says what it would do."""
        self.queue.all_down = True
        before = (self.snapshot(self.unlucky), self.snapshot(self.lucky))

        text = self.run_command("--dry-run")

        self.assertEqual(self.queue.attempts, [])
        self.assertEqual(
            (self.snapshot(self.unlucky), self.snapshot(self.lucky)), before
        )
        self.assertIn(f"[dry-run] would convert: student {self.unlucky.pk} ", text)
        self.assertIn("Backfill (dry run) complete: 2 converted,", text)
        self.assertNotIn(NOT_CONVERTED, text)


class AnOrdinaryAddByEmailIsUnchangedTest(QueueBase):
    """H-148's flow uses the same sender. A teacher's add must not fail,
    or be undone, because an email could not be queued."""

    def add(self, email):
        cache.clear()
        self.client.force_authenticate(self.teacher)
        with self.assertLogs(DISPATCH_LOGGER, level="ERROR"):
            return self.client.post(
                reverse("course-students", kwargs={"pk": self.course.id}),
                {"email": email, "first_name": "Ada", "last_name": "Lovelace"},
                format="json",
            )

    def test_a_new_student_is_still_added_when_the_queue_is_down(self):
        self.queue.all_down = True

        response = self.add("new.student@h152-delta.test")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student = User.objects.get(email="new.student@h152-delta.test")
        self.assertTrue(student.is_active)
        self.assertEqual((student.first_name, student.last_name), ("Ada", "Lovelace"))
        self.assertTrue(
            StudentCourse.objects.filter(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.PENDING,
            ).exists()
        )
        self.assertEqual(self.queue.attempts, ["new.student@h152-delta.test"])

    def test_a_student_who_never_signed_in_is_still_added_when_the_queue_is_down(
        self,
    ):
        never = User.objects.create_user(
            email="never@h152-delta.test",
            password=TEACHER_PASSWORD,
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.queue.all_down = True

        response = self.add(never.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(
            StudentCourse.objects.filter(
                student=never,
                course=self.course,
                enrollment_status=EnrollmentStatusType.PENDING,
            ).exists()
        )
        self.assertEqual(self.queue.attempts, [never.email])
