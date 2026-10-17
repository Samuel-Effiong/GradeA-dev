#!/usr/bin/env python
"""
Fails CI if a new Django migration file contains an operation that is not
safely additive (rename, drop, type change, non-nullable add-without-default)
and does not carry an explicit expand-contract acknowledgement.

A change to an existing column (AlterField) is judged against what the
column was BEFORE the migration, read from the migration files themselves
(no database): it is reported only when the column becomes NOT NULL with
nothing to fill the rows that are NULL, when its type changes, or when it
gets shorter. A change of choices, help text or a wider length on a column
is additive and passes (H-120).

Not judged: RunSQL and RunPython. Raw SQL and data migrations are for a
reviewer to read.

This enforces the house rule in docs/MIGRATIONS.md: additive-only migrations
auto-apply on deploy; anything else must be a reviewed, deliberate step of a
three-step expand -> migrate -> contract rollout, marked as such in the file.

Usage:
    python scripts/check_migration_safety.py [--base origin/main]

Exit codes:
    0   no new migrations, or every new migration is additive-only or
        carries a valid acknowledgement
    1   at least one new migration is non-additive and unacknowledged
    2   a new migration file could not be inspected (import error, etc.) -
        treated as a failure rather than silently skipped
"""
import argparse
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

from django.db.models.fields import NOT_PROVIDED

REPO_ROOT = Path(__file__).resolve().parent.parent

# Comment developers add to a migration file to say "yes, I know this isn't
# additive, this is a deliberate step of an expand/migrate/contract
# rollout" - see docs/MIGRATIONS.md for the full process each step implies.
ACK_MARKER = re.compile(
    r"#\s*expand-contract-step\s*:\s*(expand|migrate|contract)", re.IGNORECASE
)

# Operations that change or remove something old code/rows may still depend
# on, in a single step - i.e. not safely additive.
UNCONDITIONALLY_RISKY_OPS = {
    "RemoveField": (
        "drops a column - a worker still running the previous release "
        "will error the moment it reads or writes this field"
    ),
    "RenameField": (
        "renamed in a single step - old code referencing the previous "
        "name breaks immediately, and Django implements this as a rename "
        "at the DB level, not a copy, so it isn't reversible by re-adding "
        "a column of the old name"
    ),
    "RenameModel": (
        "renamed in a single step - old code/queries referencing the "
        "previous model or table name break immediately"
    ),
    "DeleteModel": ("drops a table - irreversible without a backup restore"),
    "AlterUniqueTogether": (
        "rebuilding this constraint can hold a lock for the duration on "
        "larger tables"
    ),
    "AlterIndexTogether": (
        "rebuilding this index can hold a lock for the duration on " "larger tables"
    ),
}


def changed_migration_files(base_ref):
    """New migration files this branch adds relative to base_ref."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print(
            f"error: `git diff` against {base_ref!r} failed:\n{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(2)
    return [
        line
        for line in result.stdout.splitlines()
        if re.search(r"/migrations/\d[^/]*\.py$", line)
    ]


def module_name_for(path_str):
    """'assignments/migrations/0036_x.py' -> 'assignments.migrations.0036_x'"""
    parts = Path(path_str).with_suffix("").parts
    idx = parts.index("migrations")
    return ".".join(parts[idx - 1 :])


def has_ack(path_str):
    return bool(ACK_MARKER.search(Path(REPO_ROOT / path_str).read_text()))


def has_a_default(field):
    """Python-side `default=` or database-side `db_default=`: either one
    gives existing rows a value without a human picking it."""
    if getattr(field, "has_default", lambda: False)():
        return True
    return getattr(field, "db_default", NOT_PROVIDED) is not NOT_PROVIDED


def field_is_safe_add(field):
    """A field being added is safe on an existing, populated table only if
    every existing row can get a value without a human picking one at
    migration time: nullable, or backed by a real default. A many-to-many
    field adds no column to the table at all (it makes a table of its own)."""
    if getattr(field, "many_to_many", False):
        return True
    return getattr(field, "null", False) or has_a_default(field)


#: Limits whose shrinking can refuse or cut values already stored.
SHRINKABLE = ("max_length", "max_digits", "decimal_places")


def alter_field_findings(old, new):
    """What an AlterField from `old` to `new` does that is not additive.
    `old` is the field as it stood before the migration, or None if it
    could not be found."""
    if old is None:
        return [
            "AlterField on a field whose previous state could not be found "
            "- it cannot be shown to be additive"
        ]
    findings = []
    if old.null and not new.null and not has_a_default(new):
        findings.append(
            "AlterField making a nullable column NOT NULL without a default "
            "- existing NULL rows will fail the migration outright, "
            "or silently need a backfill that isn't this operation"
        )
    old_type, new_type = old.get_internal_type(), new.get_internal_type()
    if old_type != new_type:
        findings.append(
            f"AlterField changing a column's type ({old_type} to {new_type}) "
            "- the table is rewritten or locked, and a worker on the "
            "previous release reads and writes the old type"
        )
    for limit in SHRINKABLE:
        before, after = getattr(old, limit, None), getattr(new, limit, None)
        if before is not None and after is not None and after < before:
            findings.append(
                f"AlterField shrinking {limit} from {before} to {after} "
                "- values already stored that no longer fit will fail the "
                "migration or be cut"
            )
    return findings


def field_before(state, app_label, op):
    try:
        return state.models[app_label, op.model_name_lower].fields[op.name]
    except (AttributeError, KeyError):
        return None


def operation_findings(op, state, app_label):
    op_name = type(op).__name__

    if op_name in UNCONDITIONALLY_RISKY_OPS:
        return [f"{op_name} - {UNCONDITIONALLY_RISKY_OPS[op_name]}"]

    if op_name == "AddField":
        field = getattr(op, "field", None)
        if field is not None and not field_is_safe_add(field):
            return [
                "AddField without null=True or a default - Django "
                "would have prompted for a one-off value interactively; "
                "that value does not become a reviewable backfill, and "
                "a worker on the previous release doesn't know this "
                "column exists"
            ]

    elif op_name == "AlterField":
        field = getattr(op, "field", None)
        if field is not None:
            return alter_field_findings(field_before(state, app_label, op), field)

    elif op_name == "SeparateDatabaseAndState":
        # What reaches the database is what matters to the rows and to a
        # worker on the previous release. Each database operation is judged
        # against the state as the ones before it left it.
        return findings_for(op.database_operations, state.clone(), app_label)

    return []


def findings_for(operations, state, app_label):
    """Human-readable risk descriptions for a migration's operations, empty
    if they are additive only. `state` is the project state just before the
    migration and `app_label` the migration's app; it is moved forward
    operation by operation, so each one is judged against what the ones
    before it left."""
    findings = []
    for op in operations:
        findings.extend(operation_findings(op, state, app_label))
        op.state_forwards(app_label, state)
    return findings


def classify(path_str):
    """Return a list of human-readable risk descriptions, empty if the
    migration is additive-only."""
    mod_name = module_name_for(path_str)
    module = importlib.import_module(mod_name)
    if getattr(module, "Migration", None) is None:
        raise ImportError(f"{mod_name} has no Migration class")

    # The state just before this migration, from the migration files alone:
    # a loader with no connection reads the disk and never a database.
    from django.db.migrations.loader import MigrationLoader

    app_label, _, name = mod_name.partition(".migrations.")
    app_label = app_label.split(".")[-1]
    loader = MigrationLoader(None)
    migration = loader.graph.nodes[(app_label, name)]
    state = loader.project_state(nodes=[(app_label, name)], at_end=False)
    return findings_for(migration.operations, state, app_label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="git ref to diff against for 'new' migration files (default: origin/main)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    # manage.py sets this via os.environ.setdefault before any Django
    # import; this script is a standalone entry point (not run through
    # manage.py) so it has to do the same thing itself. Without it,
    # django.setup() fails immediately with ImproperlyConfigured - this
    # was missing here AND from migration-safety.yml's env block, so the
    # check silently never worked in actual CI despite passing everywhere
    # it was tested locally (where DJANGO_SETTINGS_MODULE happened to
    # already be set in the shell).
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
    import django

    django.setup()

    files = changed_migration_files(args.base)
    if not files:
        print("No new migration files in this diff.")
        return 0

    failed = False
    for f in files:
        try:
            findings = classify(f)
        except Exception as exc:
            failed = True
            print(f"FAIL {f}: could not inspect this migration ({exc})")
            continue

        if not findings:
            print(f"OK   {f}: additive only")
            continue

        if has_ack(f):
            print(f"ACK  {f}: non-additive, marked as a reviewed expand-contract step")
            for msg in findings:
                print(f"       - {msg}")
            continue

        failed = True
        print(f"FAIL {f}: non-additive operation without acknowledgement")
        for msg in findings:
            print(f"       - {msg}")
        print(
            "       If this is a deliberate, reviewed step of an "
            "expand/migrate/contract rollout, add a comment "
            "`# expand-contract-step: expand|migrate|contract` to this "
            "file. See docs/MIGRATIONS.md."
        )

    if failed:
        print("\nOne or more migrations need review before this can merge.")
        print("See docs/MIGRATIONS.md for the house rule this check enforces.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
