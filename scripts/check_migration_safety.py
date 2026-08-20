#!/usr/bin/env python
"""
Fails CI if a new Django migration file contains an operation that is not
safely additive (rename, drop, type change, non-nullable add-without-default)
and does not carry an explicit expand-contract acknowledgement.

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
import re
import subprocess
import sys
from pathlib import Path

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


def field_is_safe_add(field):
    """A field being added is safe on an existing, populated table only if
    every existing row can get a value without a human picking one at
    migration time: nullable, or backed by a real default."""
    null = getattr(field, "null", False)
    has_default = getattr(field, "has_default", lambda: False)()
    return null or has_default


def classify(path_str):
    """Return a list of human-readable risk descriptions, empty if the
    migration is additive-only."""
    mod_name = module_name_for(path_str)
    module = importlib.import_module(mod_name)
    migration = getattr(module, "Migration", None)
    if migration is None:
        raise ImportError(f"{mod_name} has no Migration class")

    findings = []
    for op in migration.operations:
        op_name = type(op).__name__

        if op_name in UNCONDITIONALLY_RISKY_OPS:
            findings.append(f"{op_name} - {UNCONDITIONALLY_RISKY_OPS[op_name]}")

        elif op_name == "AddField":
            field = getattr(op, "field", None)
            if field is not None and not field_is_safe_add(field):
                findings.append(
                    "AddField without null=True or a default - Django "
                    "would have prompted for a one-off value interactively; "
                    "that value does not become a reviewable backfill, and "
                    "a worker on the previous release doesn't know this "
                    "column exists"
                )

        elif op_name == "AlterField":
            field = getattr(op, "field", None)
            if field is not None and not field_is_safe_add(field):
                findings.append(
                    "AlterField making a column NOT NULL without a default "
                    "- existing NULL rows will fail the migration outright, "
                    "or silently need a backfill that isn't this operation"
                )

    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="git ref to diff against for 'new' migration files (default: origin/main)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
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
