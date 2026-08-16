"""
Run history for the grading benchmark — Tiers 1 and 2.

WHY

The benchmark could measure accuracy once, but not measure CHANGE. Every run
write-up in FINDINGS.md ends up saying some version of "a single run cannot
distinguish 'the fix didn't help' from 'noise'", because nothing kept the
numbers: baselines/baseline.json holds only the latest run, and the scheduled
Celery jobs built a full report every night and threw it away. Runs 1-4's data
no longer exists at all.

WHAT IS STORED

  Tier 1  runs.jsonl       one line per run: the headline metrics
  Tier 2  questions.jsonl  one line per question per run

Both are plain JSON Lines, committed to git. Plain text (not gzip) is
deliberate: git delta-compresses text well, and `grep`, `git diff` and code
review all keep working. At roughly 4 KB and 33 KB per run, a weekly run costs
about 2 MB a year, which is not worth optimising.

These files are an INDEX, not the source of truth. Each Tier 3 archive
(see archive.py) contains its own copy of these rows, so the files can be
rebuilt from the archives — which is what makes a run on the server, where
Celery cannot commit to git, recoverable.

COMPARABILITY

Every row records `code_sha`, `prompt_fingerprint` and `dataset_fingerprint`,
because comparing two runs is only valid when the inputs match. This is not
hypothetical: ground truth for maths/strong Q4 was corrected mid-way through
this benchmark's life, and runs either side of that are not comparable on that
question.

Two kinds of row must be kept out of variation statistics, and both are
recorded here so the analysis can exclude them:

  - `mode: "replay"` runs re-read fixed recorded responses, so their metrics are
    identical every time. Averaging 365 identical nightly rows would collapse
    the apparent spread to zero and then make every real run look like a huge
    anomaly. They are still worth keeping: same inputs + changed code = OUR
    regression, which is exactly what the nightly job is for.
  - partial runs (`--assignment maths`) grade a subset, so their rates are not
    comparable with full runs.

This module raises normally on failure. Callers in the grading path wrap it, so
that a bookkeeping bug can never destroy an expensive run — see the command's
`_record_history`.
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_processor.benchmark import scoring

BENCHMARK_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BENCHMARK_DIR / "history"
RUNS_PATH = HISTORY_DIR / "runs.jsonl"
QUESTIONS_PATH = HISTORY_DIR / "questions.jsonl"

PROMPT_PATH = BENCHMARK_DIR.parent / "GRADING_ASSIGNMENT_PROMPT_5.txt"

SOURCE_BENCHMARK = "benchmark"
SOURCE_FINDINGS = "findings_md"


# ── identity and fingerprints ─────────────────────────────────────────────


def make_run_id(now=None, sha=None):
    """
    Sortable, traceable run id: '20260814T091530Z-a6109ee'.

    Timestamp first so lexical order is chronological order; commit second so
    a row can always be tied back to the code that produced it.
    """
    now = now or datetime.now(timezone.utc)
    sha = sha if sha is not None else (code_sha() or "nogit")
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{sha}"


def code_sha():
    """Short git HEAD, or None when git is unavailable (never raises — this
    is provenance metadata, not something worth failing a run over)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BENCHMARK_DIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_fingerprint():
    """Identifies the grading prompt. Changing the prompt changes what the
    model is asked, so runs either side are not strictly comparable."""
    try:
        return _digest(PROMPT_PATH.read_text())
    except OSError:
        return None


def dataset_fingerprint():
    """
    Identifies the questions AND the expected answers together, so a
    correction to ground truth shows up as a different dataset even though
    the questions are unchanged.
    """
    from ai_processor.benchmark import submissions as submissions_module
    from ai_processor.benchmark.dataset import ASSIGNMENTS, STUDENTS

    parts = []
    for assignment in ASSIGNMENTS:
        parts.append(assignment.key)
        for question in assignment.questions:
            parts.append(json.dumps(question, sort_keys=True, default=str))
        for student in STUDENTS:
            for spec in submissions_module.answers_for(assignment.key, student.key):
                parts.append(
                    f"{student.key}:{spec.question_number}:"
                    f"{spec.expected_points}:{spec.exact}:{spec.answer_html}"
                )
    return _digest("\n".join(parts))


# ── building rows ─────────────────────────────────────────────────────────


def _nested_rates(block):
    """Keep only the trendable numbers from a by-subject / by-type block."""
    return {
        name: {
            "questions": values.get("questions"),
            "exact_rate": values.get("exact_rate"),
            "within_one_level_rate": values.get("within_one_level_rate"),
            "mean_level_error": values.get("mean_level_error"),
        }
        for name, values in (block or {}).items()
    }


def build_run_record(
    run,
    report,
    run_id=None,
    recorded_at=None,
    scope_assignments=None,
    scope_students=None,
    source=SOURCE_BENCHMARK,
):
    """Tier 1: one row summarising a whole run."""
    recorded_at = recorded_at or datetime.now(timezone.utc)
    overall = report.get("overall") or {}
    deterministic = report.get("deterministic") or {}
    evidence = report.get("evidence") or {}
    snapping = report.get("rubric_snapping") or {}
    second_opinion = report.get("second_opinion") or {}
    cost = report.get("cost") or {}
    consistency = report.get("consistency") or []

    results = run.get("results") or []
    failed = [r for r in results if r.get("error")]

    # "Full" means the whole dataset was requested. A partial run's rates are
    # not comparable with a full run's, so the analysis filters on this.
    is_full_run = not scope_assignments and not scope_students

    # check_consistency() returns a LIST of probe dicts, one per identical-
    # answer pair. A probe whose submissions failed has consistent=None
    # ("skipped"), which must not be read as "consistent".
    probes = consistency if isinstance(consistency, list) else []
    checked = [
        p for p in probes if isinstance(p, dict) and p.get("consistent") is not None
    ]

    return {
        "run_id": run_id or make_run_id(recorded_at),
        "recorded_at": recorded_at.isoformat(),
        "mode": run.get("mode"),
        "source": source,
        "code_sha": code_sha(),
        "prompt_fingerprint": prompt_fingerprint(),
        "dataset_fingerprint": dataset_fingerprint(),
        "is_full_run": is_full_run,
        "scope_assignments": sorted(scope_assignments) if scope_assignments else None,
        "scope_students": sorted(scope_students) if scope_students else None,
        "submissions": len(results),
        "submissions_failed": len(failed),
        "questions_graded": overall.get("questions"),
        "metrics": {
            "exact_rate": overall.get("exact_rate"),
            "within_one_level_rate": overall.get("within_one_level_rate"),
            "mean_level_error": overall.get("mean_level_error"),
            "deterministic_claimed": deterministic.get("claimed"),
            "deterministic_correct": deterministic.get("correct"),
            "deterministic_accuracy": deterministic.get("accuracy"),
            "evidence_checked": evidence.get("checked"),
            "evidence_verified": evidence.get("verified"),
            "evidence_verified_rate": evidence.get("verified_rate"),
            "snapped_scores": snapping.get("snapped_scores"),
            "snapped_rate": snapping.get("rate"),
            "second_opinion_compared": second_opinion.get("questions_compared"),
            "second_opinion_disagreed": second_opinion.get("questions_disagreed"),
            "second_opinion_disagreement_rate": second_opinion.get("disagreement_rate"),
            "consistency_probes": len(checked),
            "consistency_all_consistent": (
                all(p.get("consistent") for p in checked) if checked else None
            ),
            "total_tokens": cost.get("total_tokens"),
            "total_seconds": cost.get("total_seconds"),
            "model_calls": run.get("model_calls"),
        },
        "by_subject": _nested_rates(report.get("by_subject")),
        "by_question_type": _nested_rates(report.get("by_question_type")),
        "ranking_spearman": {
            key: (value or {}).get("spearman")
            for key, value in (report.get("ranking") or {}).items()
        },
        # Filled in later by the archive step; null means "not archived".
        "archive_url": None,
        "archive_error": None,
    }


def build_question_records(run, run_id):
    """Tier 2: one row per graded question, stamped with the run."""
    return [{"run_id": run_id, **row} for row in scoring.iter_question_outcomes(run)]


# ── reading and writing ───────────────────────────────────────────────────


def append_jsonl(path, records):
    """Append records as one JSON object per line."""
    records = list(records)
    if not records:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return len(records)


def load_jsonl(path):
    """
    Read a JSON Lines file, skipping blank lines.

    A corrupt line is skipped rather than raising: a half-written line from an
    interrupted run should not make the entire history unreadable.
    """
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_runs(path=None):
    return load_jsonl(path or RUNS_PATH)


def load_questions(path=None):
    return load_jsonl(path or QUESTIONS_PATH)


def dedupe_by_run_id(rows):
    """
    Last write wins for a repeated run_id.

    Rebuilding from archives, or re-importing, must be safe to do twice —
    otherwise a rebuild would silently double every metric's sample count and
    quietly corrupt the noise band.
    """
    merged = {}
    for row in rows:
        merged[row.get("run_id")] = row
    return list(merged.values())


def record_run(
    run,
    report,
    run_id=None,
    scope_assignments=None,
    scope_students=None,
    runs_path=None,
    questions_path=None,
):
    """
    Write Tier 1 + Tier 2 for one completed run.

    Returns (run_record, question_records) — the records themselves, not just
    a count, so the caller can hand them to the archive and the database
    mirror without rebuilding or re-reading them from disk.

    Raises on failure. The caller in the grading path is responsible for
    catching, so that recording can never break grading.
    """
    run_record = build_run_record(
        run,
        report,
        run_id=run_id,
        scope_assignments=scope_assignments,
        scope_students=scope_students,
    )
    question_records = build_question_records(run, run_record["run_id"])

    append_jsonl(runs_path or RUNS_PATH, [run_record])
    append_jsonl(questions_path or QUESTIONS_PATH, question_records)
    return run_record, question_records


def sync_to_database(run_records=None, question_records=None):
    """
    Mirror history rows into BenchmarkRun / BenchmarkQuestionOutcome.

    Idempotent: keyed on run_id, so re-syncing the same run updates it in
    place instead of adding a duplicate. That matters because a rebuild from
    archives, or simply running the sync twice, would otherwise double every
    metric's sample count and quietly corrupt the noise band.

    Returns (runs_written, questions_written).

    The files never depend on this succeeding — see the caller's guard. A
    machine with no database still gets a complete history on disk.
    """
    from django.db import transaction

    from ai_processor.models import BenchmarkQuestionOutcome, BenchmarkRun

    run_records = (
        list(run_records) if run_records is not None else dedupe_by_run_id(load_runs())
    )
    question_records = (
        list(question_records) if question_records is not None else load_questions()
    )

    by_run = {}
    for row in question_records:
        by_run.setdefault(row.get("run_id"), []).append(row)

    runs_written = 0
    questions_written = 0

    for record in run_records:
        run_id = record.get("run_id")
        if not run_id:
            continue
        metrics = record.get("metrics") or {}

        with transaction.atomic():
            run_obj, _ = BenchmarkRun.objects.update_or_create(
                run_id=run_id,
                defaults={
                    "recorded_at": record.get("recorded_at"),
                    "mode": record.get("mode") or "",
                    "source": record.get("source") or SOURCE_BENCHMARK,
                    "code_sha": record.get("code_sha"),
                    "prompt_fingerprint": record.get("prompt_fingerprint"),
                    "dataset_fingerprint": record.get("dataset_fingerprint"),
                    "is_full_run": bool(record.get("is_full_run")),
                    "submissions": record.get("submissions") or 0,
                    "submissions_failed": record.get("submissions_failed") or 0,
                    "questions_graded": record.get("questions_graded") or 0,
                    "exact_rate": metrics.get("exact_rate"),
                    "within_one_level_rate": metrics.get("within_one_level_rate"),
                    "mean_level_error": metrics.get("mean_level_error"),
                    "evidence_verified_rate": metrics.get("evidence_verified_rate"),
                    "deterministic_accuracy": metrics.get("deterministic_accuracy"),
                    "second_opinion_disagreement_rate": metrics.get(
                        "second_opinion_disagreement_rate"
                    ),
                    "total_tokens": metrics.get("total_tokens"),
                    "archive_url": record.get("archive_url"),
                    "payload": record,
                },
            )
            runs_written += 1

            rows = by_run.get(run_id)
            if rows is None:
                # No question rows supplied for this run (e.g. a backfilled
                # row from FINDINGS.md, which has headline numbers only).
                # Leave whatever is already stored alone rather than
                # deleting a previously-synced set.
                continue

            # Replace wholesale: simpler than diffing, and correct when a run
            # is re-imported from its archive.
            BenchmarkQuestionOutcome.objects.filter(run=run_obj).delete()
            BenchmarkQuestionOutcome.objects.bulk_create(
                [
                    BenchmarkQuestionOutcome(
                        run=run_obj,
                        assignment_key=row.get("assignment_key") or "",
                        student_key=row.get("student_key") or "",
                        question_number=row.get("question_number") or 0,
                        question_type=row.get("question_type") or "",
                        subject=row.get("subject") or "",
                        expected_points=row.get("expected_points"),
                        awarded_points=row.get("awarded_points"),
                        expected_level=row.get("expected_level"),
                        awarded_level=row.get("awarded_level"),
                        level_error=row.get("level_error"),
                        verdict=row.get("verdict") or "",
                        level_decision=row.get("level_decision"),
                        graded_by=row.get("graded_by"),
                        evidence_verified=row.get("evidence_verified"),
                        second_opinion_disagreed=row.get("second_opinion_disagreed"),
                        payload=row,
                    )
                    for row in rows
                ]
            )
            questions_written += len(rows)

    return runs_written, questions_written


def update_run_archive(run_id, archive_url=None, archive_error=None, runs_path=None):
    """
    Fill in the archive result on an already-written run row.

    The row is written before the upload is attempted (cheap and reliable
    first, slow and fragile last), so the outcome has to be patched in
    afterwards. Rewrites the file in place; it is small and this happens once
    per run.
    """
    path = Path(runs_path or RUNS_PATH)
    rows = load_jsonl(path)
    changed = False
    for row in rows:
        if row.get("run_id") == run_id:
            row["archive_url"] = archive_url
            row["archive_error"] = archive_error
            changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return True
