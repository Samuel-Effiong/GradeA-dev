"""AI-call record, slice 2: the six columns that say how a paper was read.

`StudentSubmission` gets six NOT NULL text columns (migration 0032, pure
AddField with a database default, so an old row and a row inserted by older
code after a code-only rollback read "unlabelled"), and the one check
constraint on the re-check's pair in two steps so that no scan of the table
runs under a blocking lock: migration 0033 adds it NOT VALID (a catalog change,
no row is read), migration 0034 validates it (a read of every row under a lock
that does not block reads or writes).

  * the columns: names, lengths, both defaults, no index, read-only in the
    admin, in no serializer;
  * the constraint: every pair the design note's table allows is saved, every
    other pair is refused by the DATABASE, a result word outside the five
    included; the Python function that builds the pairs
    (`step_label.pair_is_allowed`) and the constraint agree on every
    combination of words;
  * a row made before the migrations reads "unlabelled" after them, and the
    migrations reverse.
"""

import itertools

from django.contrib import admin
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase

from assignments.models import Assignment
from classrooms.models import Course, Session
from students import step_label
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

READING_LABEL_FIELDS = (
    "answers_model",
    "answers_prompt_version",
    "answers_config_version",
    "answers_release",
    "answers_recheck_model",
    "answers_recheck_result",
)
MAX_LENGTHS = {
    "answers_model": 255,
    "answers_prompt_version": 128,
    "answers_config_version": 128,
    "answers_release": 64,
    "answers_recheck_model": 255,
    "answers_recheck_result": 32,
}
UNLABELLED = "unlabelled"
TABLE = "students_studentsubmission"
CONSTRAINT = "answers_recheck_pair_allowed"
M0032 = "0032_submission_answers_label"
M0033 = "0033_submission_answers_recheck_pair_check"
M0034 = "0034_submission_answers_recheck_pair_validate"


def _column(name):
    field = StudentSubmission._meta.get_field(name)
    assert isinstance(field, models.CharField), field
    return field


class TheModuleOfNamesTest(SimpleTestCase):
    def test_there_is_one_list_of_the_six_in_the_order_of_the_model(self):
        from students.reading_label import READING_LABEL_FIELDS as listed

        self.assertEqual(listed, READING_LABEL_FIELDS)
        declared = [
            f.name
            for f in StudentSubmission._meta.get_fields()
            if f.name in READING_LABEL_FIELDS
        ]
        self.assertEqual(tuple(declared), READING_LABEL_FIELDS)


class TheSixColumnsTest(SimpleTestCase):
    def test_each_is_a_not_null_text_column_of_the_designed_length(self):
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                field = _column(name)
                self.assertFalse(field.null)
                self.assertEqual(field.max_length, MAX_LENGTHS[name])
                self.assertFalse(field.db_index)  # type: ignore[attr-defined]
                self.assertFalse(field.unique)

    def test_each_has_the_placeholder_as_both_defaults(self):
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                field = _column(name)
                self.assertEqual(field.default, UNLABELLED)
                self.assertTrue(field.has_db_default())  # type: ignore[attr-defined]
                self.assertEqual(field.db_default, UNLABELLED)

    def test_a_new_unsaved_submission_reads_the_placeholder(self):
        submission = StudentSubmission()
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertEqual(getattr(submission, name), UNLABELLED)

    def test_every_word_a_reading_can_write_fits_its_column(self):
        for word in (
            step_label.MODEL_UNKNOWN,
            step_label.NOT_RUN,
            step_label.FAILED,
            step_label.CONFIRMED_BLANK,
            step_label.FOUND_WRITING,
        ):
            self.assertLessEqual(len(word), MAX_LENGTHS["answers_recheck_result"])
        self.assertLessEqual(
            len(step_label.MODEL_UNKNOWN), MAX_LENGTHS["answers_model"]
        )

    def test_the_six_are_read_only_in_the_admin(self):
        model_admin = admin.site._registry[StudentSubmission]
        read_only = model_admin.get_readonly_fields(
            RequestFactory().get("/admin/"), obj=None
        )
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertIn(name, read_only)

    def test_the_constraint_is_declared_once_by_name(self):
        names = [c.name for c in StudentSubmission._meta.constraints]
        self.assertEqual(names.count(CONSTRAINT), 1)


class TheMigrationsTest(SimpleTestCase):
    def _migration(self, name):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        return loader.disk_migrations[("students", name)]

    def test_0032_is_six_add_fields_with_a_database_default_and_nothing_else(self):
        migration = self._migration(M0032)
        self.assertEqual(
            [type(op).__name__ for op in migration.operations], ["AddField"] * 6
        )
        self.assertEqual(
            [op.name for op in migration.operations], list(READING_LABEL_FIELDS)
        )
        for op in migration.operations:
            with self.subTest(name=op.name):
                self.assertEqual(op.field.db_default, UNLABELLED)
                self.assertEqual(op.field.default, UNLABELLED)
                self.assertFalse(op.field.null)

    def test_0033_adds_the_one_constraint_not_valid_and_nothing_else(self):
        migration = self._migration(M0033)
        self.assertEqual([type(op).__name__ for op in migration.operations], ["RunSQL"])
        operation = migration.operations[0]
        self.assertIn("NOT VALID", operation.sql)
        self.assertNotIn("VALIDATE", operation.sql.upper().replace("NOT VALID", ""))
        self.assertIn(CONSTRAINT, operation.sql)
        self.assertIn("DROP CONSTRAINT", operation.reverse_sql)
        self.assertEqual(
            [type(op).__name__ for op in operation.state_operations],
            ["AddConstraint"],
        )
        self.assertEqual(operation.state_operations[0].constraint.name, CONSTRAINT)
        self.assertIn(("students", M0032), migration.dependencies)

    def test_0034_validates_the_constraint_and_nothing_else(self):
        migration = self._migration(M0034)
        self.assertEqual([type(op).__name__ for op in migration.operations], ["RunSQL"])
        operation = migration.operations[0]
        self.assertIn("VALIDATE CONSTRAINT", operation.sql)
        self.assertIn(CONSTRAINT, operation.sql)
        self.assertNotIn("ADD CONSTRAINT", operation.sql)
        self.assertEqual(operation.state_operations, [])
        self.assertIn(("students", M0033), migration.dependencies)

    def test_0032_follows_the_grading_label_migration(self):
        migration = self._migration(M0032)
        self.assertIn(
            ("students", "0031_submission_grading_label"), migration.dependencies
        )

    def test_the_migrations_say_how_to_undo_them(self):
        from pathlib import Path

        import students

        folder = Path(students.__file__).parent / "migrations"
        for name in (M0032, M0033, M0034):
            with self.subTest(name=name):
                text = (folder / f"{name}.py").read_text(encoding="utf-8")
                self.assertIn("To undo", text)


class TheDatabaseHoldsTheDefaultTest(TestCase):
    def test_each_column_is_not_null_with_the_placeholder_as_its_default(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, is_nullable, column_default, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = ANY(%s)",
                [TABLE, list(READING_LABEL_FIELDS)],
            )
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
        self.assertEqual(set(rows), set(READING_LABEL_FIELDS))
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                nullable, default, length = rows[name]
                self.assertEqual(nullable, "NO")
                self.assertTrue(default and default.startswith(repr(UNLABELLED)))
                self.assertEqual(length, MAX_LENGTHS[name])

    def test_the_constraint_is_validated_once_every_migration_has_run(self):
        self.assertIs(_constraint_validated(), True)

    def test_no_index_was_added_for_them(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s", [TABLE]
            )
            definitions = " ".join(row[0] for row in cursor.fetchall())
        for name in READING_LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertNotIn(name, definitions)


def _submission():
    teacher = CustomUser.objects.create_user(
        email="pair-teacher@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
    )
    student = CustomUser.objects.create_user(
        email="pair-student@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
    )
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    assignment = Assignment.objects.create(title="A", course=course, questions=[])
    return StudentSubmission.objects.create(
        assignment=assignment, student=student, answers=[]
    )


WORDS = (
    "unlabelled",
    "not_run",
    "failed",
    "confirmed_blank",
    "found_writing",
    "unknown",
    "some/model",
    "maybe",
)


class TheConstraintOnThePairTest(TestCase):
    """The database refuses what the table of the design note forbids, and
    only that. Each combination is tried on a real row."""

    def _try(self, submission, result, model):
        try:
            with transaction.atomic():
                StudentSubmission.objects.filter(pk=submission.pk).update(  # type: ignore[misc]
                    answers_recheck_result=result, answers_recheck_model=model
                )
        except IntegrityError:
            return False
        return True

    def test_a_row_made_with_no_label_is_allowed(self):
        submission = _submission()
        submission.refresh_from_db()
        self.assertEqual(
            (submission.answers_recheck_result, submission.answers_recheck_model),
            (UNLABELLED, UNLABELLED),
        )

    def test_every_pair_the_table_allows_is_saved(self):
        submission = _submission()
        for result, model in (
            ("unlabelled", "unlabelled"),
            ("not_run", "not_run"),
            ("failed", "some/model"),
            ("failed", "unknown"),
            ("confirmed_blank", "some/model"),
            ("confirmed_blank", "unknown"),
            ("found_writing", "some/model"),
            ("found_writing", "unknown"),
        ):
            with self.subTest(pair=(result, model)):
                self.assertTrue(self._try(submission, result, model))

    def test_every_pair_the_table_forbids_is_refused_by_the_database(self):
        submission = _submission()
        for result, model in (
            ("not_run", "some/model"),
            ("not_run", "unknown"),
            ("not_run", "unlabelled"),
            ("unlabelled", "not_run"),
            ("unlabelled", "some/model"),
            ("unlabelled", "unknown"),
            ("failed", "not_run"),
            ("failed", "unlabelled"),
            ("confirmed_blank", "not_run"),
            ("confirmed_blank", "unlabelled"),
            ("found_writing", "not_run"),
            ("found_writing", "unlabelled"),
        ):
            with self.subTest(pair=(result, model)):
                self.assertFalse(self._try(submission, result, model))

    def test_the_python_function_and_the_database_agree_on_every_combination(self):
        """Held twice: the function that builds the pairs and the constraint."""
        submission = _submission()
        for result, model in itertools.product(WORDS, WORDS):
            with self.subTest(pair=(result, model)):
                self.assertEqual(
                    self._try(submission, result, model),
                    step_label.pair_is_allowed(result, model),
                )

    def test_a_result_word_outside_the_five_is_refused_beside_a_real_model(self):
        """The Python rule and the database both hold that the result column
        is one of the five words, whatever stands in the model column."""
        submission = _submission()
        for result in ("maybe", "unknown", "some/model", "", "FAILED"):
            with self.subTest(result=result):
                self.assertFalse(self._try(submission, result, "some/model"))
                self.assertFalse(step_label.pair_is_allowed(result, "some/model"))
        for result in step_label.RECHECK_RESULTS:
            if result != "not_run":
                with self.subTest(result=result):
                    self.assertTrue(self._try(submission, result, "some/model"))
                    self.assertTrue(step_label.pair_is_allowed(result, "some/model"))

    def test_a_new_row_with_a_forbidden_pair_is_refused_too(self):
        submission = _submission()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentSubmission.objects.create(
                assignment=submission.assignment,
                student=CustomUser.objects.create_user(
                    email="pair-other@example.com",
                    password="password123",  # pragma: allowlist secret
                    user_type=UserTypes.STUDENT,
                ),
                answers=[],
                answers_recheck_result="not_run",  # type: ignore[misc]
                answers_recheck_model="x/model",  # type: ignore[misc]
            )


AT_0031 = [("students", "0031_submission_grading_label")]
AT_0032 = [("students", M0032)]
AT_0033 = [("students", M0033)]
AT_0034 = [("students", M0034)]


def _targets(students_target):
    executor = MigrationExecutor(connection)
    others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "students"]
    return list(students_target) + others


def _columns_present():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = ANY(%s)",
            [TABLE, list(READING_LABEL_FIELDS)],
        )
        return cursor.fetchone()[0]


def _constraint_present():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = %s", [CONSTRAINT]
        )
        return cursor.fetchone()[0]


def _constraint_validated():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT convalidated FROM pg_constraint WHERE conname = %s", [CONSTRAINT]
        )
        row = cursor.fetchone()
    return None if row is None else row[0]


class ARowMadeBeforeTheColumnsExistedTest(TransactionTestCase):
    """Real migrations, backwards and forwards, on the test database; the
    schema is left at the latest state for the tests that follow."""

    def _migrate(self, students_target):
        targets = _targets(students_target)
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [n for n in executor.loader.graph.leaf_nodes() if n[0] == "students"]
        )
        super().tearDown()

    def test_a_row_made_before_0032_reads_the_placeholder_after_0033(self):
        old_apps = self._migrate(AT_0031)
        self.assertEqual(_columns_present(), 0)
        User = old_apps.get_model("users", "CustomUser")
        teacher = User.objects.create(
            email="mig-teacher@example.com",
            user_type=UserTypes.TEACHER,
            registration_method="EMAIL",
        )
        student = User.objects.create(
            email="mig-student@example.com",
            user_type=UserTypes.STUDENT,
            registration_method="EMAIL",
        )
        session = old_apps.get_model("classrooms", "Session").objects.create(
            name="S", teacher=teacher
        )
        course = old_apps.get_model("classrooms", "Course").objects.create(
            name="C", teacher=teacher, session=session
        )
        assignment = old_apps.get_model("assignments", "Assignment").objects.create(
            title="A", course=course, questions=[]
        )
        old = old_apps.get_model("students", "StudentSubmission").objects.create(
            assignment=assignment, student=student, answers=[{"question_number": 1}]
        )

        self._migrate(AT_0032)
        self.assertEqual(_columns_present(), 6)
        self.assertEqual(_constraint_present(), 0)
        self._migrate(AT_0033)
        self.assertEqual(_constraint_present(), 1)
        # Step one: present, not yet validated, and already refusing a bad
        # write while it reads no existing row.
        self.assertIs(_constraint_validated(), False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE %s SET answers_recheck_result = 'not_run', "
                    "answers_recheck_model = 'x/model' WHERE id = %%s" % TABLE,
                    [old.pk],
                )
        self._migrate(AT_0034)
        self.assertIs(_constraint_validated(), True)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s FROM %s WHERE id = %%s"
                % (", ".join(READING_LABEL_FIELDS), TABLE),
                [old.pk],
            )
            self.assertEqual(cursor.fetchone(), (UNLABELLED,) * 6)
        fresh = StudentSubmission.objects.get(pk=old.pk)
        self.assertEqual(fresh.answers, [{"question_number": 1}])

        # Reversing the validation does nothing; reversing 0033 drops the
        # constraint alone; then the columns.
        self._migrate(AT_0033)
        self.assertEqual(_constraint_present(), 1)
        self._migrate(AT_0032)
        self.assertEqual(_constraint_present(), 0)
        self.assertEqual(_columns_present(), 6)
        self._migrate(AT_0031)
        self.assertEqual(_columns_present(), 0)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM %s WHERE id = %%s" % TABLE, [old.pk])
            self.assertEqual(cursor.fetchone()[0], 1)
