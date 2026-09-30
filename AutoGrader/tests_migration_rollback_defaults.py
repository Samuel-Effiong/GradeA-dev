"""H-56: a column added since production must not break a code-only rollback.

Code older than a migration does not know its column, so its INSERT omits
it. A NOT NULL column with only a Django-side `default` then fails that
INSERT with a NOT NULL violation: user creation, wallet `get_or_create`,
every Stripe webhook, assignment creation and the daily risk task all did,
for nine columns added after production (origin/main `9c21bee`). Each now
carries a database default (`db_default`), set by the
`*_db_defaults_for_rollback` migrations.

Two halves:

* **Guard** (static, over the migration graph). After production's heads,
  with an empty allow-list, it flags a NOT NULL column without a
  `db_default` (read from the final migration state, so a later AlterField
  that adds one counts) when either:

  (a) an AddField adds it, whatever the table's age; or
  (b) an AlterField turns it from nullable to NOT NULL. The previous
      nullability comes from the migration state before that migration,
      not from the models.

  Why this scope: a rollback's older code omits from its INSERT exactly the
  columns it does not know, and (a) covers every column added since
  production. An AlterField that turns a column NOT NULL is the other way a
  column can start refusing that INSERT, which is (b). An AlterField that
  leaves nullability alone (a choice, default or FK change) is skipped:
  older code already lists that column in its INSERT, or never inserts into
  its table. None of this depends on the cutoff tracking production, so it
  stays correct after the next release. CreateModel is out of scope (older
  code never inserts into a table it does not know), and so are its columns;
  any AddField after it on that table is in scope under (a). Decided by the
  SM, 2026-09-30.
* **Old-code INSERTs** (real PostgreSQL). For each of the nine columns a
  rollback could meet, a raw INSERT that lists every other column, as the
  older code's INSERT does, succeeds, and the row gets the default.
  Against the tree before the migrations (beta 463e222) each one fails with
  a NOT NULL violation; that run is recorded in the H-56 evidence.

When production moves, move PRODUCTION_HEADS to its new heads.
"""

import sys
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.migrations import Migration
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField, AlterField
from django.db.migrations.state import ModelState, ProjectState
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


def migrations_since_production(loader):
    """(app, name, migration) for every local migration production does not
    have."""
    in_production = set()
    for app, head in PRODUCTION_HEADS.items():
        in_production |= set(loader.graph.forwards_plan((app, head)))
    return [
        (app, name, migration)
        for (app, name), migration in loader.disk_migrations.items()
        if app in LOCAL_APPS and (app, name) not in in_production
    ]


def fields_touched_since_production(loader=None):
    """{(app, model, field): ("app.migration", ...)} for every AddField
    and AlterField in a migration production does not have."""
    loader = loader or MigrationLoader(None, ignore_no_migrations=True)
    touched = {}
    for app, name, migration in migrations_since_production(loader):
        for op in migration.operations:
            if isinstance(op, (AddField, AlterField)):
                touched.setdefault(
                    (app, op.model_name_lower, op.name_lower), []
                ).append(f"{app}.{name}")
    return {field: tuple(sorted(where)) for field, where in touched.items()}


def state_before(loader):
    """`state_before(app, name)`: the migration state just before that
    migration. Rule (b) reads a column's previous nullability here; the
    final state would already show it NOT NULL, and (b) would never fire."""

    def before(app, name):
        return loader.project_state((app, name), at_end=False)

    return before


def rollback_candidates(migrations, state_before):
    """{(app, model, field): ("app.migration", ...)}: the columns rule (a)
    or (b) covers. `state_before(app, name)` is the migration state just
    before that migration."""
    candidates = {}
    for app, name, migration in migrations:
        before = None
        for op in migration.operations:
            if not isinstance(op, (AddField, AlterField)):
                continue
            key = (app, op.model_name_lower, op.name_lower)
            if isinstance(op, AddField):
                candidates.setdefault(key, []).append(f"{app}.{name}")
            else:
                before = before or state_before(app, name)
                previous = before.models.get((app, op.model_name_lower))
                previous_field = (
                    previous.fields.get(op.name_lower) if previous else None
                )
                if previous_field is not None and (
                    previous_field.null and not op.field.null
                ):
                    candidates.setdefault(key, []).append(f"{app}.{name}")
    return {field: tuple(sorted(where)) for field, where in candidates.items()}


def broken_fields(candidates, final):
    """The candidates that are NOT NULL with no db_default in `final`, and
    not allow-listed, as {"app.model.field": ("app.migration", ...)}."""
    broken = {}
    for (app, model, name), where in candidates.items():
        model_state = final.models.get((app, model))
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
    """Rule (a) + (b) over the real migration graph, judged against the
    final migration state (or `state`, for the guard's own test)."""
    loader = loader or MigrationLoader(None, ignore_no_migrations=True)
    candidates = rollback_candidates(
        migrations_since_production(loader),
        state_before(loader),
    )
    return broken_fields(candidates, state or loader.project_state())


class RollbackDefaultsGuardTests(SimpleTestCase):
    def test_every_not_null_field_added_since_production_has_a_db_default(self):
        self.assertEqual(
            fields_a_rollback_would_break(),
            {},
            "NOT NULL fields added, or made NOT NULL, since production with "
            "no db_default: code older than their migration omits them from "
            "INSERT (or inserts NULL), so a code-only rollback fails every "
            "insert into the table. Give each a db_default (same value as "
            "`default`; db_default=Now() for a timestamp) and generate the "
            "AlterField.",
        )

    def test_the_guard_sees_the_nine_rollback_columns(self):
        """Guard on the guard: the fields H-56 found are rule (a)
        candidates, so an empty result means they are fixed, not unseen."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        candidates = rollback_candidates(
            migrations_since_production(loader),
            state_before(loader),
        )
        for field in ROLLBACK_COLUMNS:
            self.assertIn(field.key, candidates)

    def test_the_production_heads_exist(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        for app, head in PRODUCTION_HEADS.items():
            self.assertIn((app, head), loader.disk_migrations)

    def test_the_guard_flags_a_not_null_field_without_a_db_default(self):
        """Rule (a) on the real graph with a synthetic final state: drop
        one db_default and the guard reports that field alone."""
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

    def test_rule_b_fires_only_when_an_alter_makes_a_column_not_null(self):
        """Rule (b) on a synthetic migration: nullable to NOT NULL without a
        db_default is flagged; the same with a db_default, or an alter that
        keeps nullability, is not."""

        def state_with(**fields):
            state = ProjectState()
            state.add_model(
                ModelState(
                    "synthetic",
                    "thing",
                    [("id", models.AutoField(primary_key=True))] + list(fields.items()),
                )
            )
            return state

        before = state_with(
            tightened=models.CharField(max_length=5, null=True),
            defaulted=models.CharField(max_length=5, null=True),
            unchanged=models.CharField(max_length=5),
        )
        after = {
            "tightened": models.CharField(max_length=5),
            "defaulted": models.CharField(max_length=5, db_default="x"),
            "unchanged": models.CharField(max_length=5, default="y"),
        }
        operations = [AlterField("thing", n, f) for n, f in after.items()]
        migration = type("SyntheticAlter", (Migration,), {"operations": operations})(
            "0002_alter", "synthetic"
        )
        candidates = rollback_candidates(
            [("synthetic", "0002_alter", migration)], lambda app, name: before
        )
        self.assertEqual(
            set(candidates),
            {("synthetic", "thing", "tightened"), ("synthetic", "thing", "defaulted")},
        )
        self.assertEqual(
            set(broken_fields(candidates, state_with(**after))),
            {"synthetic.thing.tightened"},
        )

    def test_rule_b_reads_the_state_before_each_migration(self):
        """The production wiring (the Verification Engineer's H6 on
        b029f5c): before billing 0070 the wallet's deficit counter has no
        db_default; the final state has one. Reading the final state here
        would make rule (b) blind on every real migration."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        before = state_before(loader)("billing", "0070_db_defaults_for_rollback")
        wallet = before.models[("billing", "creditwallet")]
        self.assertIs(wallet.fields["dispute_deficit_credits"].db_default, NOT_PROVIDED)
        final = loader.project_state().models[("billing", "creditwallet")]
        self.assertIsNot(
            final.fields["dispute_deficit_credits"].db_default, NOT_PROVIDED
        )

    def test_the_guard_uses_the_state_before_each_migration(self):
        """fields_a_rollback_would_break wires rule (b) to state_before, not to
        any other state."""
        module = sys.modules[__name__]
        with patch.object(module, "state_before", wraps=state_before) as wired:
            fields_a_rollback_would_break()
        self.assertEqual(wired.call_count, 1)

    def test_a_nullability_preserving_alter_on_the_real_graph_is_skipped(self):
        """The eight AlterFields since production that change choices,
        defaults or FK details of a column that was already NOT NULL are
        not rule (b) candidates, and none is also added since production."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        candidates = rollback_candidates(
            migrations_since_production(loader),
            state_before(loader),
        )
        for key in [
            ("billing", "creditusagelog", "wallet"),
            ("billing", "creditledger", "ledger_type"),
            ("students", "backgroundprocessingtask", "task_type"),
        ]:
            self.assertNotIn(key, candidates)


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
