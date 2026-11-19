"""Epic E2: missing work counts as 0 by default (owner 2026-11-19, option 1).

Reverses the Q2 default: `Course.missing_counts_as_zero` is on for every
course, new and existing; a teacher can still switch it off per course.

The stored final grade is only rewritten by an event (a save, a toggle, or
the clocked due-date task, which exists only for due dates still ahead).
So on the day GRADE_CATEGORIES_ENABLED goes on, the course grades already
stored would not yet count work that was due earlier. The
`recalculate_missing_work` command brings them up to date at switch-on:
the same recompute as the toggle (audit only for grades that move, no
credits), and it makes the due-date tasks for due dates still ahead.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django_celery_beat.models import PeriodicTask

from assignments.models import AssignmentStatus
from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms import grade_categories, grade_service
from classrooms.models import Course
from classrooms.signals import compute_final_grade
from students.models import StudentSubmission

from .tests_e2_fixtures import (
    categories_off,
    categories_on,
    enroll,
    future,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
    past,
    stored_grade,
)

D = Decimal
BEFORE = [("classrooms", "0018_alter_session_is_active")]


class NewCourseCountsMissingWorkTest(TestCase):
    def test_a_new_course_counts_missing_work(self):
        course = make_course(make_teacher())
        self.assertTrue(course.missing_counts_as_zero)
        course.refresh_from_db()
        self.assertTrue(course.missing_counts_as_zero)

    def test_the_database_default_is_on_too(self):
        course = make_course(make_teacher())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'classrooms_course' "
                "AND column_name = 'missing_counts_as_zero'"
            )
            self.assertEqual(cursor.fetchone()[0], "true")
        self.assertTrue(
            Course.objects.filter(pk=course.pk)
            .values_list("missing_counts_as_zero", flat=True)
            .get()
        )


class ExistingCourseAfterTheMigrationTest(TransactionTestCase):
    """A course made before classrooms 0019 counts missing work after it:
    the real migration, run backwards and forwards on the test database."""

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_an_existing_course_counts_missing_work_after_0019(self):
        # The users tables are not touched by going back to 0018.
        teacher = make_teacher()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        executor.loader.build_graph()
        old = executor.loader.project_state(BEFORE).apps
        term = old.get_model("classrooms", "Session").objects.create(
            name="Old term", teacher_id=teacher.pk
        )
        course = old.get_model("classrooms", "Course").objects.create(
            name="Old course", teacher_id=teacher.pk, session_id=term.pk
        )

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertTrue(
            Course.objects.filter(pk=course.pk)
            .values_list("missing_counts_as_zero", flat=True)
            .get()
        )


@categories_on
class MissingWorkCountsByDefaultTest(TestCase):
    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.student = make_student()
        enroll(self.student, self.course)
        done = make_assignment(self.course, "Done", due_date=past())
        grade(self.student, done, 80)
        self.missed = make_assignment(self.course, "Missed", due_date=past())
        make_assignment(self.course, "Not due", due_date=future())

    def test_the_student_average_counts_missing_work_as_zero(self):
        # 80 / 200: "Missed" counts as 0 out of 100
        self.assertEqual(
            compute_final_grade(self.student.id, self.course.id),
            D("40.00"),
        )
        released = grade_service.released_course_grade(
            StudentSubmission.objects.filter(student=self.student).select_related(
                "assignment"
            ),
            self.course.id,
            student_id=self.student.id,
        )
        self.assertEqual(released, D("40.00"))
        breakdown = grade_service.grade_breakdown(self.student.id, self.course.id)
        self.assertEqual(breakdown["missing_count"], 1)
        self.assertTrue(breakdown["missing_counts_as_zero"])

    def test_excused_work_still_doesnt_count(self):
        grade_categories.excuse_work(self.missed, self.student, actor=self.teacher)
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    def test_switching_it_off_leaves_missing_work_out_again(self):
        self.course.missing_counts_as_zero = False
        self.course.save()
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))
        self.assertEqual(
            compute_final_grade(self.student.id, self.course.id),
            D("80.00"),
        )

    def test_handing_it_in_after_the_due_date_removes_the_zero(self):
        grade(self.student, self.missed, 50)
        # 80 and 50 out of 200
        self.assertEqual(stored_grade(self.student, self.course), D("65.00"))
        breakdown = grade_service.grade_breakdown(self.student.id, self.course.id)
        self.assertEqual(breakdown["missing_count"], 0)

    def test_a_new_due_date_ahead_gets_its_task(self):
        later = make_assignment(self.course, "Later", due_date=future(2))
        name = grade_categories.missing_work_task_name(later.id)
        self.assertTrue(PeriodicTask.objects.filter(name=name).exists())


class GoLiveTest(TestCase):
    """Switch-on: grades stored while the flag was off are brought up to
    date by `manage.py recalculate_missing_work --apply`."""

    def setUp(self):
        with categories_off:
            self.teacher = make_teacher()
            self.course = make_course(self.teacher)
            self.student = make_student()
            enroll(self.student, self.course)
            done = make_assignment(self.course, "Done", due_date=past())
            grade(self.student, done, 80)
            make_assignment(self.course, "Missed", due_date=past())
            self.later = make_assignment(self.course, "Later", due_date=future(2))
            self.off = make_course(self.teacher)
            Course.objects.filter(pk=self.off.pk).update(missing_counts_as_zero=False)
            enroll(self.student, self.off)
            graded = make_assignment(self.off, "Graded", due_date=past())
            grade(self.student, graded, 70)
            make_assignment(self.off, "Skipped", due_date=past())

    def run_command(self, *args):
        out = StringIO()
        call_command("recalculate_missing_work", *args, stdout=out)
        return out.getvalue()

    def changes(self):
        return AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE)

    def test_stored_grade_is_stale_until_the_command_runs(self):
        with categories_on:
            self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    @categories_on
    def test_apply_counts_earlier_due_dates_and_audits_only_changes(self):
        self.run_command("--apply")
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))
        self.assertEqual(stored_grade(self.student, self.off), D("70.00"))
        change = self.changes().get()
        self.assertEqual(change.metadata["course_id"], str(self.course.id))
        self.assertEqual(change.metadata["source"], "missing_work_go_live")

    @categories_on
    def test_apply_makes_the_due_date_tasks_still_ahead(self):
        self.run_command("--apply")
        name = grade_categories.missing_work_task_name(self.later.id)
        self.assertTrue(PeriodicTask.objects.filter(name=name).exists())

    @categories_on
    def test_a_second_run_changes_nothing(self):
        self.run_command("--apply")
        before = self.changes().count()
        self.run_command("--apply")
        self.assertEqual(self.changes().count(), before)
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))

    @categories_on
    def test_dry_run_writes_nothing(self):
        out = self.run_command()
        self.assertIn(str(self.course.id), out)
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))
        self.assertFalse(self.changes().exists())
        self.assertFalse(
            PeriodicTask.objects.filter(name__startswith="missing-work-").exists()
        )

    @categories_off
    def test_refuses_while_the_flag_is_off(self):
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    @categories_on
    def test_spends_no_credits(self):
        from billing.models import BillingTransaction, CreditBucket

        before = (
            BillingTransaction.objects.count(),
            list(CreditBucket.objects.values_list("used_credits", flat=True)),
        )
        self.run_command("--apply")
        self.assertEqual(
            (
                BillingTransaction.objects.count(),
                list(CreditBucket.objects.values_list("used_credits", flat=True)),
            ),
            before,
        )


class CreatingAQualifyingAssignmentTest(TestCase):
    """SM 2026-11-19: an assignment created already published, its due date
    past, in a course that counts missing work (flag on), recomputes the
    course's stored grades at once (audit only on change, no credits)."""

    def setUp(self):
        self.teacher = make_teacher()
        self.course = make_course(self.teacher)
        self.student = make_student()
        enroll(self.student, self.course)
        with categories_on:
            grade(self.student, make_assignment(self.course, due_date=past()), 80)

    def changes(self):
        return AuditEvent.objects.filter(action=AuditAction.GRADE_CHANGE)

    @categories_on
    def test_a_qualifying_create_updates_the_stored_grade(self):
        from billing.models import BillingTransaction

        before = self.changes().count()
        transactions = BillingTransaction.objects.count()
        make_assignment(self.course, "Backdated", due_date=past())
        self.assertEqual(stored_grade(self.student, self.course), D("40.00"))
        change = self.changes().order_by("-id").first()
        self.assertEqual(self.changes().count(), before + 1)
        self.assertEqual(change.metadata["source"], "assignment")
        self.assertEqual(BillingTransaction.objects.count(), transactions)

    @categories_on
    def test_a_draft_does_not_recompute(self):
        make_assignment(
            self.course, "Draft", due_date=past(), status=AssignmentStatus.DRAFT
        )
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    @categories_on
    def test_a_future_due_date_does_not_recompute(self):
        make_assignment(self.course, "Later", due_date=future())
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))

    @categories_on
    def test_a_course_that_switched_it_off_does_not_recompute(self):
        self.course.missing_counts_as_zero = False
        self.course.save()
        before = self.changes().count()
        make_assignment(self.course, "Backdated", due_date=past())
        self.assertEqual(stored_grade(self.student, self.course), D("80.00"))
        self.assertEqual(self.changes().count(), before)

    def test_the_flag_off_does_not_recompute(self):
        with categories_off:
            make_assignment(self.course, "Backdated", due_date=past())
        with categories_on:
            self.assertEqual(stored_grade(self.student, self.course), D("80.00"))
