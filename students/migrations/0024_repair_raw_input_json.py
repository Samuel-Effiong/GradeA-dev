"""
Repair StudentSubmission.raw_input rows that hold a Python repr, not JSON.

raw_input is a TextField, but the submission pipeline used to assign the dict
returned by the HTML -> ProseMirror converter straight to it. Django coerced
that with str(), so the column ended up holding "{'type': 'doc', ...}" -
single-quoted, with None/True/False - which no JSON parser will read back. The
frontend editor and the answer-extraction prompt both consume this column.

Rows are classified individually and only rewritten when they are unambiguously
a Python literal that is not already valid JSON. Anything else - valid JSON,
free text like "Unprocessed uploaded text", NULL, blank - is left untouched.
"""

import ast
import json

from django.db import migrations

BATCH_SIZE = 500


def _repaired(value):
    """Return the JSON form of a repr-encoded value, or None to leave it alone."""
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    # Only object/array payloads are candidates. A cheap prefix check keeps the
    # expensive parsing off the overwhelming majority of rows.
    if not stripped.startswith(("{", "[")):
        return None

    try:
        json.loads(stripped)
    except (ValueError, TypeError):
        pass
    else:
        # Already valid JSON (the assignment lane always json.dumps'd).
        return None

    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        # Not a Python literal either - some other content we must not touch.
        return None

    if not isinstance(parsed, (dict, list)):
        return None

    try:
        return json.dumps(parsed)
    except (TypeError, ValueError):
        return None


def repair(apps, schema_editor):
    StudentSubmission = apps.get_model("students", "StudentSubmission")

    queryset = (
        StudentSubmission.objects.exclude(raw_input__isnull=True)
        .exclude(raw_input="")
        .only("id", "raw_input")
        .order_by("pk")
    )

    pending = []
    for submission in queryset.iterator(chunk_size=BATCH_SIZE):
        repaired = _repaired(submission.raw_input)
        if repaired is None:
            continue
        submission.raw_input = repaired
        pending.append(submission)
        if len(pending) >= BATCH_SIZE:
            StudentSubmission.objects.bulk_update(pending, ["raw_input"])
            pending.clear()

    if pending:
        StudentSubmission.objects.bulk_update(pending, ["raw_input"])


def unrepair(apps, schema_editor):
    """
    Deliberately a no-op rather than an error.

    The repaired rows are strictly more correct than what they replaced, and
    re-corrupting them on rollback would help nobody. Making this reversible
    keeps `migrate students 0023` working for anyone rolling back schema state.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0023_studentsubmission_review_tier_and_more"),
    ]

    operations = [
        migrations.RunPython(repair, unrepair),
    ]
