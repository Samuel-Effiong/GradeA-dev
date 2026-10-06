"""H-120: the migration safety check judges a changed column by what it was.

`scripts/check_migration_safety.py` is the check behind the "Migration
safety" workflow: a new migration that is not additive must carry an
acknowledgement. It had no tests.

It judged an `AlterField` by the field AFTER the change alone: NOT NULL and
no default meant "making a column NOT NULL", even for a column that was NOT
NULL already and only gained a choice or some length. So safe migrations
were reported, and each needed a marker that said nothing true about it.

Now an `AlterField` is judged against the field as it stood BEFORE the
migration, read from the migration files themselves (no database). It is
reported when the column becomes NOT NULL with nothing to fill its NULL
rows, when its type changes, or when it gets shorter.

Two kinds of test: small made-up tables for each rule, and the real
migrations the check fails on today (the table at the bottom, which is
written to be read by someone deciding what to acknowledge).
"""

import importlib.util

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.functions import Now
from django.test import SimpleTestCase

SCRIPT = settings.BASE_DIR / "scripts" / "check_migration_safety.py"


def load_the_check():
    spec = importlib.util.spec_from_file_location("check_migration_safety", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load_the_check()

NOT_NULL = "AlterField making a nullable column NOT NULL without a default"
UNKNOWN = "AlterField on a field whose previous state could not be found"
ADDED = "AddField without null=True or a default"


def kinds(findings):
    """Each finding is "what - why"; the what is enough to tell them apart."""
    return [finding.split(" - ")[0] for finding in findings]


def a_shop(**fields):
    """The state of a made-up app: a Tag, and an Item with these fields."""
    state = ProjectState()
    state.add_model(
        ModelState("shop", "Tag", [("id", models.AutoField(primary_key=True))])
    )
    state.add_model(
        ModelState(
            "shop",
            "Item",
            [("id", models.AutoField(primary_key=True)), *fields.items()],
        )
    )
    return state


def judged(operations, **fields):
    return kinds(check.findings_for(operations, a_shop(**fields), "shop"))


def altered(before, after):
    return judged([migrations.AlterField("Item", "thing", after)], thing=before)


def type_change(old, new):
    return f"AlterField changing a column's type ({old} to {new})"


class AChangedColumnIsJudgedByWhatItWas(SimpleTestCase):
    def test_changes_that_are_additive_pass(self):
        char = models.CharField
        passes = {
            "a choice added to a column that was NOT NULL already": (
                char(max_length=20),
                char(max_length=20, choices=[("a", "A"), ("b", "B")]),
            ),
            "help text on a column that was NOT NULL already": (
                char(max_length=20),
                char(max_length=20, help_text="what it is"),
            ),
            "a column made longer": (char(max_length=20), char(max_length=100)),
            "a nullable column that stays nullable": (
                char(max_length=20, null=True),
                char(max_length=30, null=True),
            ),
            "a NOT NULL column made nullable": (
                char(max_length=20),
                char(max_length=20, null=True),
            ),
            "a database default given to a column that was NOT NULL already": (
                models.DateTimeField(auto_now=True),
                models.DateTimeField(auto_now=True, db_default=Now()),
            ),
            "a nullable column made NOT NULL, with a default to fill it": (
                char(max_length=20, null=True),
                char(max_length=20, default="none"),
            ),
            "a nullable column made NOT NULL, with a database default": (
                models.IntegerField(null=True),
                models.IntegerField(db_default=0),
            ),
            "more digits and more decimal places": (
                models.DecimalField(max_digits=5, decimal_places=2),
                models.DecimalField(max_digits=8, decimal_places=3),
            ),
        }
        for name, (before, after) in passes.items():
            with self.subTest(change=name):
                self.assertEqual(altered(before, after), [])

    def test_a_nullable_column_made_not_null_with_nothing_to_fill_it_is_reported(
        self,
    ):
        self.assertEqual(
            altered(
                models.CharField(max_length=20, null=True),
                models.CharField(max_length=20),
            ),
            [NOT_NULL],
        )

    def test_a_type_change_is_reported_and_named(self):
        changes = {
            "text to a number": (
                models.CharField(max_length=20),
                models.IntegerField(default=0),
                type_change("CharField", "IntegerField"),
            ),
            "a short text to a long text": (
                models.CharField(max_length=20),
                models.TextField(),
                type_change("CharField", "TextField"),
            ),
            "a number to a bigger number": (
                models.IntegerField(),
                models.BigIntegerField(),
                type_change("IntegerField", "BigIntegerField"),
            ),
        }
        for name, (before, after, expected) in changes.items():
            with self.subTest(change=name):
                self.assertEqual(altered(before, after), [expected])

    def test_a_column_made_shorter_is_reported_with_both_numbers(self):
        shrinks = {
            "fewer characters": (
                models.CharField(max_length=100),
                models.CharField(max_length=20),
                "AlterField shrinking max_length from 100 to 20",
            ),
            "fewer digits": (
                models.DecimalField(max_digits=6, decimal_places=2),
                models.DecimalField(max_digits=5, decimal_places=2),
                "AlterField shrinking max_digits from 6 to 5",
            ),
            "fewer decimal places": (
                models.DecimalField(max_digits=6, decimal_places=2),
                models.DecimalField(max_digits=6, decimal_places=1),
                "AlterField shrinking decimal_places from 2 to 1",
            ),
        }
        for name, (before, after, expected) in shrinks.items():
            with self.subTest(change=name):
                self.assertEqual(altered(before, after), [expected])

    def test_each_thing_wrong_with_one_change_is_reported(self):
        self.assertEqual(
            altered(
                models.CharField(max_length=100, null=True),
                models.IntegerField(),
            ),
            [NOT_NULL, type_change("CharField", "IntegerField")],
        )

    def test_a_field_with_no_known_past_cannot_be_called_additive(self):
        self.assertEqual(
            judged(
                [migrations.AlterField("Item", "ghost", models.IntegerField())],
                thing=models.IntegerField(),
            ),
            [UNKNOWN],
        )


class AnAddedColumn(SimpleTestCase):
    def added(self, field):
        return judged([migrations.AddField("Item", "extra", field)])

    def test_columns_every_existing_row_can_get_a_value_for_pass(self):
        passes = {
            "nullable": models.IntegerField(null=True),
            "with a default": models.IntegerField(default=0),
            "with a database default": models.IntegerField(db_default=0),
            "a many-to-many, which adds a table and no column": (
                models.ManyToManyField("shop.Tag")
            ),
        }
        for name, field in passes.items():
            with self.subTest(column=name):
                self.assertEqual(self.added(field), [])

    def test_a_not_null_column_with_no_default_is_reported(self):
        self.assertEqual(self.added(models.IntegerField()), [ADDED])


class TheOperationsOfOneMigration(SimpleTestCase):
    def test_each_is_judged_against_what_the_ones_before_it_left(self):
        """Added nullable, then made NOT NULL, in one file: the second
        operation's "before" is the first one's result."""
        self.assertEqual(
            judged(
                [
                    migrations.AddField(
                        "Item", "note", models.CharField(max_length=20, null=True)
                    ),
                    migrations.AlterField(
                        "Item", "note", models.CharField(max_length=20)
                    ),
                ]
            ),
            [NOT_NULL],
        )

    def test_what_a_separate_database_and_state_does_to_the_database_is_judged(
        self,
    ):
        to_not_null = migrations.AlterField("Item", "thing", models.IntegerField())
        cases = {
            "the change reaches the database": (
                migrations.SeparateDatabaseAndState(database_operations=[to_not_null]),
                [NOT_NULL],
            ),
            "the change is to Django's own record only": (
                migrations.SeparateDatabaseAndState(state_operations=[to_not_null]),
                [],
            ),
        }
        for name, (operation, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    judged([operation], thing=models.IntegerField(null=True)),
                    expected,
                )

    def test_a_record_only_change_is_still_the_next_operations_past(self):
        """Django's record says the column is NOT NULL after the first
        operation, so the second changes nothing about that."""
        self.assertEqual(
            judged(
                [
                    migrations.SeparateDatabaseAndState(
                        state_operations=[
                            migrations.AlterField(
                                "Item", "thing", models.IntegerField()
                            )
                        ]
                    ),
                    migrations.AlterField(
                        "Item", "thing", models.IntegerField(help_text="a count")
                    ),
                ],
                thing=models.IntegerField(null=True),
            ),
            [],
        )

    def test_the_operations_that_were_always_reported_still_are(self):
        always = {
            "RemoveField": migrations.RemoveField("Item", "thing"),
            "RenameField": migrations.RenameField("Item", "thing", "other"),
            "RenameModel": migrations.RenameModel("Item", "Article"),
            "DeleteModel": migrations.DeleteModel("Tag"),
            "AlterUniqueTogether": migrations.AlterUniqueTogether(
                "Item", {("id", "thing")}
            ),
            "AlterIndexTogether": migrations.AlterIndexTogether(
                "Item", {("id", "thing")}
            ),
        }
        for name, operation in always.items():
            with self.subTest(operation=name):
                self.assertEqual(
                    judged([operation], thing=models.IntegerField()), [name]
                )

    def test_raw_sql_and_data_migrations_are_not_judged(self):
        """A stated limit, pinned: the check reads neither. They are for a
        reviewer."""
        self.assertEqual(
            judged(
                [
                    migrations.RunSQL("DROP TABLE shop_item", migrations.RunSQL.noop),
                    migrations.RunPython(migrations.RunPython.noop),
                ]
            ),
            [],
        )


# The real migrations the check fails on today (pull request 2), and one
# that carries a marker only because of this defect. For each: what the
# file does, in plain words, and what the check says of it now.
#
#   PASSES            additive: no acknowledgement is needed
#   NEEDS A DECISION  the check is right; it stays red until a person
#                     acknowledges it in the file. Nobody has: that is the
#                     founder's decision, file by file.
#
# All of them are already applied. "Needs a decision" says what the rule
# makes of the file, not that something is broken today.
#
# Each line was checked against the file itself, read to its last
# operation (2026-10-06). An earlier version of the billing/0059 line said
# its work was raw SQL; it has none.
PASSES = []
REMOVED, RENAMED = "RemoveField", "RenameField"
THE_REAL_MIGRATIONS = {
    "assignments/migrations/0038_assignment_updated_at.py": (
        "Adds Assignment.updated_at as a NOT NULL column with no default. "
        "Going forward it is safe; a rollback is not, because the previous "
        "release cannot insert a row. 0040 repairs that.",
        [ADDED],
    ),
    "assignments/migrations/0040_db_defaults_for_rollback.py": (
        "Gives that same column a database default. It was NOT NULL already.",
        PASSES,
    ),
    "billing/migrations/0009_betaprofile_uuid_and_more.py": (
        "One more choice on CreditBucket.bucket_type (NOT NULL already), a "
        "new column that has a default, and two more columns altered with "
        "nothing about NULL or type changed.",
        PASSES,
    ),
    "billing/migrations/0011_remove_betaprofile_uuid_betaprofile_uid_and_more.py": (
        "Drops BetaProfile.uuid, adds a nullable uid, and changes "
        "BetaProfile.id from text to an automatic number.",
        [REMOVED, type_change("CharField", "BigAutoField")],
    ),
    "billing/migrations/0013_remove_betaprofile_id_alter_betaprofile_uid.py": (
        "Drops BetaProfile.id; uid becomes NOT NULL and the primary key (it "
        "has a default, and 0012 fills it first, so that part is not "
        "reported).",
        [REMOVED],
    ),
    "billing/migrations/0014_rename_uid_betaprofile_id.py": (
        "Renames BetaProfile.uid to id in one step.",
        [RENAMED],
    ),
    "billing/migrations/0016_planfeature_alter_subscriptionplan_options_and_more.py": (
        "Twenty-one operations: drops SubscriptionPlan.overage_block_price; "
        "adds ten columns, one of them (tagline) NOT NULL with no default; "
        "alters six without narrowing any; makes two new tables; adds the "
        "features many-to-many (which is no longer reported).",
        [REMOVED, ADDED],
    ),
    "billing/migrations/0017_remove_subscriptionplan_overage_credit_price_cents_and_more.py": (
        "Drops SubscriptionPlan.overage_credit_price_cents, and alters one "
        "column with nothing about it changed.",
        [REMOVED],
    ),
    "billing/migrations/0021_alter_creditbucket_bucket_type_and_more.py": (
        "One more choice each on four columns that were NOT NULL already "
        "(three of them had no default, and were the ones reported).",
        PASSES,
    ),
    "billing/migrations/0022_usersubscription_is_trial_usersubscription_trial_end_and_more.py": (
        "Two new columns that are safe to add, and CreditBucket.bucket_type "
        "altered with nothing about it changed.",
        PASSES,
    ),
    "billing/migrations/0023_alter_subscriptionplan_name.py": (
        "SubscriptionPlan.name: five choices become eight. NOT NULL already.",
        PASSES,
    ),
    "billing/migrations/0025_alter_subscriptionplan_name.py": (
        "SubscriptionPlan.name: more choices, and 20 characters become 100.",
        PASSES,
    ),
    "billing/migrations/0059_append_only_audit_tables.py": (
        "Drops the database's foreign-key constraints on six relations of "
        "the two financial audit tables and lets one of them be NULL; "
        "turns CreditLedger.user from a relation into a plain id column in "
        "Django's own record only (same column, no data touched); adds "
        "four nullable columns. Nothing is removed, renamed or narrowed. "
        "The check has no rule about a dropped foreign-key constraint, so "
        "a person should still look at this file although it passes.",
        PASSES,
    ),
    "students/migrations/0026_alter_backgroundprocessingtask_task_type.py": (
        "One more choice on a column that was NOT NULL already. It carries "
        "an acknowledgement (H-119) that it would no longer need; the "
        "marker stays.",
        PASSES,
    ),
}


class TheRealMigrationsTheCheckFailsOnToday(SimpleTestCase):
    def test_each_one(self):
        for path, (_what_it_does, expected) in THE_REAL_MIGRATIONS.items():
            with self.subTest(migration=path):
                self.assertEqual(kinds(check.classify(path)), expected)

    def test_seven_of_the_thirteen_pass_and_six_need_a_decision(self):
        """students/0026 is the fourteenth: acknowledged already."""
        thirteen = {
            path: expected
            for path, (_what, expected) in THE_REAL_MIGRATIONS.items()
            if not path.startswith("students/")
        }
        self.assertEqual(len(thirteen), 13)
        self.assertEqual(sum(1 for expected in thirteen.values() if not expected), 7)

    def test_an_acknowledgement_is_still_read(self):
        self.assertTrue(
            check.has_ack(
                "students/migrations/0026_alter_backgroundprocessingtask_task_type.py"
            )
        )
        self.assertFalse(
            check.has_ack("billing/migrations/0023_alter_subscriptionplan_name.py")
        )
