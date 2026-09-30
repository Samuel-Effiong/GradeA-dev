"""H-56: a column added since production must not break a code-only rollback.

Code older than a migration does not know its column, so its INSERT omits
it. A NOT NULL column with only a Django-side `default` then fails that
INSERT with a NOT NULL violation: user creation, wallet `get_or_create`,
every Stripe webhook, assignment creation and the daily risk task all did,
for nine columns added after production (origin/main `9c21bee`). Each now
carries a database default (`db_default`), set by the
`*_db_defaults_for_rollback` migrations.

Two halves:

* **Guard** (static, over the migration graph). Every field that an
  AddField or AlterField after production's head touches must, in the final
  migration state, be nullable or have a `db_default`. The allow-list is
  empty on purpose. CreateModel is out of scope: code that predates a table
  never inserts into it. The same rule catches an AlterField that makes an
  existing column NOT NULL.
* **Old-code INSERTs** (real PostgreSQL). For each of the nine columns a
  rollback could meet, a raw INSERT that lists every other column, as the
  older code's INSERT does, succeeds, and the row gets the default.
  Against the tree before the migrations (beta 463e222) each one fails with
  a NOT NULL violation; that run is recorded in the H-56 evidence.

When production moves, move PRODUCTION_HEADS to its new heads.
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField, AlterField
from django.db.models import NOT_PROVIDED
from django.test import SimpleTestCase, TransactionTestCase

from assignments.models import Assignment
from billing.models import CreditWallet, StripeEvent
from classrooms.models import Course, School, Session
from dashboard.models import StudentRiskAlertState
from users.models import UserTypes

User = get_user_model()

#: The latest migration per app at origin/main 9c21bee (production). An app
#: missing here has no migration in production, so all of it is newer.
PRODUCTION_HEADS = {
    "users": "0035_settings_notify_assignment_edited",
    "billing": "0058_alter_licensebillingrecord_record_type",
    "assignments": "0037_assignment_course_title_index",
    "classrooms": "0016_school_is_active",
    "students": "0025_student_submission_hot_path_indexes",
    "dashboard": "0002_schoolatrisksnapshot",
    "ai_processor": "0005_benchmarkrun_benchmarkquestionoutcome",
}

#: (app, model, field) -> why it may stay NOT NULL without a db_default.
#: Empty on purpose (H-56): a db_default costs nothing, a failed rollback
#: costs an outage.
ALLOWED = {}


def fields_touched_since_production(loader=None):
    """{(app, model, field): ("app.migration", ...)} for every AddField
    and AlterField in a migration production does not have."""
    loader = loader or MigrationLoader(None, ignore_no_migrations=True)
    in_production = set()
    for app, head in PRODUCTION_HEADS.items():
        in_production |= set(loader.graph.forwards_plan((app, head)))
    touched = {}
    for key, migration in loader.disk_migrations.items():
        if key[0] not in LOCAL_APPS or key in in_production:
            continue
        for op in migration.operations:
            if isinstance(op, (AddField, AlterField)):
                touched.setdefault(
                    (key[0], op.model_name_lower, op.name_lower), []
                ).append(f"{key[0]}.{key[1]}")
    return {field: tuple(sorted(where)) for field, where in touched.items()}


LOCAL_APPS = {
    "users",
    "billing",
    "assignments",
    "classrooms",
    "students",
    "dashboard",
    "ai_processor",
}


def fields_a_rollback_would_break(loader=None, state=None):
    """The touched fields that are NOT NULL with no db_default in the final
    migration state (or `state`, for the guard's own test), and not
    allow-listed."""
    loader = loader or MigrationLoader(None, ignore_no_migrations=True)
    state = state or loader.project_state()
    broken = {}
    for (app, model, name), where in fields_touched_since_production(loader).items():
        model_state = state.models.get((app, model))
        if model_state is None or name not in model_state.fields:
            continue  # removed again later
        field = model_state.fields[name]
        if field.many_to_many or field.null or field.primary_key:
            continue
        if field.db_default is not NOT_PROVIDED:
            continue
        if (app, model, name) not in ALLOWED:
            broken[f"{app}.{model}.{name}"] = where
    return broken


class RollbackDefaultsGuardTests(SimpleTestCase):
    def test_every_not_null_field_added_since_production_has_a_db_default(self):
        self.assertEqual(
            fields_a_rollback_would_break(),
            {},
            "NOT NULL fields added or altered since production with no "
            "db_default: code older than their migration omits them from "
            "INSERT, so a code-only rollback fails every insert into the "
            "table. Give each a db_default (same value as `default`; "
            "db_default=Now() for a timestamp) and generate the AlterField.",
        )

    def test_the_guard_sees_the_nine_rollback_columns(self):
        """Guard on the guard: the fields H-56 found are in its scope, so an
        empty result means they are fixed, not unseen."""
        touched = fields_touched_since_production()
        for field in ROLLBACK_COLUMNS:
            self.assertIn(field.key, touched)

    def test_the_production_heads_exist(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        for app, head in PRODUCTION_HEADS.items():
            self.assertIn((app, head), loader.disk_migrations)

    def test_the_guard_flags_a_not_null_field_without_a_db_default(self):
        """On a synthetic final state: drop one db_default and the guard
        reports that field."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        state = loader.project_state()
        field = state.models[("users", "customuser")].fields["token_epoch"]
        original = field.db_default
        try:
            field.db_default = NOT_PROVIDED
            self.assertEqual(
                set(fields_a_rollback_would_break(loader, state)),
                {"users.customuser.token_epoch"},
            )
        finally:
            field.db_default = original


class Column:
    def __init__(self, app, model, name, table, column, expected):
        self.key = (app, model, name)
        self.table = table
        self.column = column
        self.expected = expected


#: The nine columns a rollback to 9c21bee's code would meet, and the value
#: an insert that omits each should get.
ROLLBACK_COLUMNS = [
    Column(
        "users",
        "customuser",
        "failed_login_attempts",
        "users_customuser",
        "failed_login_attempts",
        0,
    ),
    Column(
        "users",
        "customuser",
        "must_change_password",
        "users_customuser",
        "must_change_password",
        False,
    ),
    Column("users", "customuser", "token_epoch", "users_customuser", "token_epoch", 0),
    Column(
        "billing",
        "creditwallet",
        "dispute_deficit_credits",
        "billing_creditwallet",
        "dispute_deficit_credits",
        0,
    ),
    Column(
        "billing",
        "creditwallet",
        "is_consumption_blocked",
        "billing_creditwallet",
        "is_consumption_blocked",
        False,
    ),
    Column(
        "billing",
        "creditwallet",
        "refund_deficit_credits",
        "billing_creditwallet",
        "refund_deficit_credits",
        0,
    ),
    Column(
        "billing",
        "stripeevent",
        "recovery_attempts",
        "billing_stripeevent",
        "recovery_attempts",
        0,
    ),
    Column(
        "assignments",
        "assignment",
        "updated_at",
        "assignments_assignment",
        "updated_at",
        "a timestamp",
    ),
    Column(
        "dashboard",
        "studentriskalertstate",
        "alert_pending",
        "dashboard_studentriskalertstate",
        "alert_pending",
        False,
    ),
]


def insert_omitting(obj, column):
    """INSERT `obj`'s row as code that does not know `column` would: every
    other concrete column, with the values Django would save. Returns the
    row's primary key."""
    meta = type(obj)._meta
    fields = [
        f
        for f in meta.concrete_fields
        if f.column != column and not (f.primary_key and f.db_returning)
    ]
    values = [f.get_db_prep_save(f.pre_save(obj, add=True), connection) for f in fields]
    quote = connection.ops.quote_name
    sql = (
        f"INSERT INTO {quote(meta.db_table)} "
        f"({', '.join(quote(f.column) for f in fields)}) "
        f"VALUES ({', '.join(['%s'] * len(fields))}) "
        f"RETURNING {quote(meta.pk.column)}"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, values)
        return cursor.fetchone()[0]


class OldCodeInsertTests(TransactionTestCase):
    """Real PostgreSQL. One subtest per column; each insert runs in its own
    savepoint, so a failure (the pre-fix behaviour) reports that column and
    lets the others run."""

    def setUp(self):
        self.school = School.objects.create(name="H-56 school")
        self.teacher = User.objects.create_user(
            email="h56-teacher@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        self.student = User.objects.create_user(
            email="h56-student@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        self.course = Course.objects.create(
            name="H-56 course",
            teacher=self.teacher,
            session=Session.objects.create(name="H-56 term", teacher=self.teacher),
        )

    def unsaved_row(self, column):
        """A valid, unsaved instance of the column's model."""
        tag = uuid.uuid4().hex[:8]
        if column.table == "users_customuser":
            user = User(
                email=f"h56-{tag}@x.test",
                user_type=UserTypes.STUDENT,
            )
            user.set_password("password123")  # pragma: allowlist secret
            return user
        if column.table == "billing_creditwallet":
            owner = User.objects.create_user(
                email=f"h56-wallet-{tag}@x.test",
                password="password123",  # nosec  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
            )
            CreditWallet.objects.filter(user=owner).delete()
            return CreditWallet(user=owner)
        if column.table == "billing_stripeevent":
            return StripeEvent(stripe_event_id=f"evt_h56_{tag}", event_type="h56.test")
        if column.table == "assignments_assignment":
            return Assignment(course=self.course, title=f"H-56 {tag}")
        if column.table == "dashboard_studentriskalertstate":
            return StudentRiskAlertState(student=self.student, school=self.school)
        raise AssertionError(column.table)

    def test_an_insert_that_omits_the_column_succeeds_with_the_default(self):
        quote = connection.ops.quote_name
        for column in ROLLBACK_COLUMNS:
            with self.subTest(column=f"{column.table}.{column.column}"):
                obj = self.unsaved_row(column)
                with transaction.atomic():
                    pk = insert_omitting(obj, column.column)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {quote(column.column)} FROM {quote(column.table)} "
                        f"WHERE {quote(type(obj)._meta.pk.column)} = %s",
                        [pk],
                    )
                    [value] = cursor.fetchone()
                if column.expected == "a timestamp":
                    self.assertIsNotNone(value)
                else:
                    self.assertEqual(value, column.expected)
