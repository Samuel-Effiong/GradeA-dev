"""
Coverage for the benchmark run history (Tiers 1 and 2).

See ai_processor/benchmark/history.py for why this exists. The properties
locked here are the ones that would silently corrupt the trend analysis if
they broke:

- Tier 2 row count always equals the report's question count (the two code
  paths share one join and must never drift).
- Errored submissions are skipped by BOTH, consistently.
- Re-importing the same run does not double-count it.
- Replay runs and partial runs are marked, so the analysis can exclude them
  from variation statistics.
- A corrupt line does not make the whole history unreadable.

Run with:
    python manage.py test ai_processor.tests_benchmark_history
"""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from ai_processor.benchmark import history, runner, scoring
from ai_processor.benchmark import submissions as submissions_module
from ai_processor.benchmark.dataset import (
    ASSIGNMENTS,
    IDENTICAL_ANSWER_PROBES,
    STUDENTS,
)
from ai_processor.tests_grading_benchmark import HAS_RECORDINGS, BenchmarkFixtureMixin


def _synthetic_run(mode="replay", error=None, questions=2):
    """
    A minimal run in the shape execute_benchmark() returns, so the row
    builders can be tested without replaying the whole dataset.
    """
    assignment = ASSIGNMENTS[0]
    student = STUDENTS[0]
    specs = submissions_module.answers_for(assignment.key, student.key)[:questions]

    evaluations = [
        {
            "question_number": spec.question_number,
            "score_awarded": spec.expected_points,
            "level_achieved": "excellent",
            "level_decision": "clear",
            "graded_by": "test-model",
            "evidence_verified": True,
            "evidence_quotes": ["a quote"],
        }
        for spec in specs
    ]

    return {
        "mode": mode,
        "model_calls": 1,
        "total_tokens": 100,
        "responses": {},
        "results": [
            {
                "assignment_key": assignment.key,
                "student_key": student.key,
                "assignment": assignment,
                "specs": specs,
                "grading": {
                    "question_evaluations": evaluations,
                    "grading_confidence": 90,
                },
                "error": error,
                "elapsed_seconds": 1.0,
                "tokens": 100,
            }
        ],
    }


class RunIdAndFingerprintTest(SimpleTestCase):
    def test_run_id_is_chronologically_sortable(self):
        from datetime import datetime, timezone

        early = history.make_run_id(
            datetime(2026, 1, 1, tzinfo=timezone.utc), sha="aaa"
        )
        late = history.make_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc), sha="bbb")
        # Timestamp leads, so plain string sorting is time ordering.
        self.assertLess(early, late)
        self.assertTrue(early.startswith("20260101T"))
        self.assertTrue(early.endswith("-aaa"))

    def test_fingerprints_are_stable_across_calls(self):
        self.assertEqual(history.prompt_fingerprint(), history.prompt_fingerprint())
        self.assertEqual(history.dataset_fingerprint(), history.dataset_fingerprint())

    def test_dataset_fingerprint_covers_expected_answers_not_just_questions(self):
        # Ground truth for maths/strong Q4 was corrected once already. A
        # correction like that must produce a DIFFERENT fingerprint, or the
        # analysis would compare runs whose right answers disagree.
        original = history.dataset_fingerprint()
        assignment = ASSIGNMENTS[0]
        student = STUDENTS[0]
        specs = submissions_module.answers_for(assignment.key, student.key)
        spec = specs[0]
        bumped = spec.expected_points + 1

        real_answers_for = submissions_module.answers_for

        class _Patched:
            def __init__(self, spec, points):
                self._spec, self._points = spec, points

            def __getattr__(self, name):
                if name == "expected_points":
                    return self._points
                return getattr(self._spec, name)

        def fake_answers_for(akey, skey):
            rows = real_answers_for(akey, skey)
            if akey == assignment.key and skey == student.key and rows:
                return [_Patched(rows[0], bumped)] + list(rows[1:])
            return rows

        submissions_module.answers_for = fake_answers_for
        try:
            self.assertNotEqual(history.dataset_fingerprint(), original)
        finally:
            submissions_module.answers_for = real_answers_for

    def test_code_sha_never_raises(self):
        # Provenance metadata must not be able to fail a run.
        self.assertIn(type(history.code_sha()).__name__, ("str", "NoneType"))


class BuildRunRecordTest(SimpleTestCase):
    def _record(self, run, **kwargs):
        report = scoring.score_run(run)
        return history.build_run_record(run, report, **kwargs)

    def test_headline_fields_are_populated(self):
        record = self._record(_synthetic_run())
        self.assertEqual(record["mode"], "replay")
        self.assertEqual(record["source"], history.SOURCE_BENCHMARK)
        self.assertEqual(record["submissions"], 1)
        self.assertEqual(record["submissions_failed"], 0)
        self.assertEqual(record["questions_graded"], 2)
        self.assertEqual(record["metrics"]["exact_rate"], 1.0)
        self.assertIsNone(record["archive_url"])

    def test_full_run_flag_and_scope(self):
        full = self._record(_synthetic_run())
        self.assertTrue(full["is_full_run"])
        self.assertIsNone(full["scope_assignments"])

        # A partial run's rates are not comparable with a full run's, so it
        # must be marked for the analysis to exclude.
        partial = self._record(_synthetic_run(), scope_assignments=["maths"])
        self.assertFalse(partial["is_full_run"])
        self.assertEqual(partial["scope_assignments"], ["maths"])

    def test_failed_submission_is_counted_not_swallowed(self):
        record = self._record(_synthetic_run(error="boom"))
        self.assertEqual(record["submissions"], 1)
        self.assertEqual(record["submissions_failed"], 1)
        self.assertEqual(record["questions_graded"], 0)

    def test_record_is_json_serialisable(self):
        # It gets written as one JSON line; a stray dataclass would break
        # the file for every later read.
        record = self._record(_synthetic_run())
        json.dumps(record, default=str)


class QuestionRecordTest(SimpleTestCase):
    def test_rows_are_stamped_with_run_id_and_flattened(self):
        run = _synthetic_run()
        rows = history.build_question_records(run, "run-1")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["run_id"] == "run-1" for r in rows))
        self.assertEqual(rows[0]["verdict"], "exact")
        self.assertEqual(rows[0]["graded_by"], "test-model")
        self.assertEqual(rows[0]["level_decision"], "clear")

    def test_errored_submission_yields_no_rows(self):
        rows = history.build_question_records(_synthetic_run(error="boom"), "run-1")
        self.assertEqual(rows, [])

    def test_row_count_matches_report_question_count(self):
        # THE DRIFT LOCK. score_run() and iter_question_outcomes() share
        # iter_graded_questions(); if they ever stop agreeing, the stored
        # history would quietly disagree with the report printed from the
        # same run.
        for run in (_synthetic_run(), _synthetic_run(error="boom")):
            report = scoring.score_run(run)
            rows = history.build_question_records(run, "run-1")
            self.assertEqual(len(rows), report["overall"]["questions"])


class JsonlStorageTest(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "runs.jsonl"

    def test_append_and_load_round_trip(self):
        history.append_jsonl(self.path, [{"run_id": "a"}, {"run_id": "b"}])
        history.append_jsonl(self.path, [{"run_id": "c"}])
        self.assertEqual(
            [r["run_id"] for r in history.load_jsonl(self.path)], ["a", "b", "c"]
        )

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(history.load_jsonl(self.path / "nope.jsonl"), [])

    def test_appending_nothing_creates_nothing(self):
        self.assertEqual(history.append_jsonl(self.path, []), 0)
        self.assertFalse(self.path.exists())

    def test_corrupt_line_is_skipped_not_fatal(self):
        # An interrupted write must not make the whole history unreadable.
        history.append_jsonl(self.path, [{"run_id": "a"}])
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write('{"run_id": "hal\n')
        history.append_jsonl(self.path, [{"run_id": "b"}])
        self.assertEqual(
            [r["run_id"] for r in history.load_jsonl(self.path)], ["a", "b"]
        )

    def test_dedupe_keeps_one_row_per_run_id(self):
        # Rebuilding from archives must be safe to run twice — otherwise it
        # would double the sample count and corrupt the noise band.
        rows = [{"run_id": "a", "v": 1}, {"run_id": "b"}, {"run_id": "a", "v": 2}]
        deduped = history.dedupe_by_run_id(rows)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(
            next(r for r in deduped if r["run_id"] == "a")["v"], 2, "last write wins"
        )

    def test_update_run_archive_patches_only_the_matching_row(self):
        history.append_jsonl(
            self.path,
            [
                {"run_id": "a", "archive_url": None, "archive_error": None},
                {"run_id": "b", "archive_url": None, "archive_error": None},
            ],
        )
        self.assertTrue(
            history.update_run_archive("a", archive_url="u", runs_path=self.path)
        )
        rows = {r["run_id"]: r for r in history.load_jsonl(self.path)}
        self.assertEqual(rows["a"]["archive_url"], "u")
        self.assertIsNone(rows["b"]["archive_url"])

    def test_update_run_archive_records_failure_reason(self):
        history.append_jsonl(self.path, [{"run_id": "a"}])
        history.update_run_archive(
            "a", archive_error="Cloudinary unreachable", runs_path=self.path
        )
        row = history.load_jsonl(self.path)[0]
        self.assertIsNone(row["archive_url"])
        self.assertEqual(row["archive_error"], "Cloudinary unreachable")

    def test_update_run_archive_on_unknown_id_is_a_no_op(self):
        history.append_jsonl(self.path, [{"run_id": "a"}])
        self.assertFalse(
            history.update_run_archive("missing", archive_url="u", runs_path=self.path)
        )


class RecordRunTest(SimpleTestCase):
    def test_writes_both_tiers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runs_path = Path(tmp.name) / "runs.jsonl"
        questions_path = Path(tmp.name) / "questions.jsonl"

        run = _synthetic_run()
        report = scoring.score_run(run)
        record, question_records = history.record_run(
            run, report, runs_path=runs_path, questions_path=questions_path
        )

        # The records themselves come back, not just a count, so the caller
        # can hand them to the archive and the DB mirror without rebuilding.
        self.assertEqual(len(question_records), 2)
        self.assertEqual(len(history.load_jsonl(runs_path)), 1)
        self.assertEqual(len(history.load_jsonl(questions_path)), 2)
        self.assertTrue(
            all(
                r["run_id"] == record["run_id"]
                for r in history.load_jsonl(questions_path)
            )
        )


class DatabaseMirrorTest(TestCase):
    """
    The mirror exists so the data is queryable and so a Celery run on the
    server — which cannot commit to git — is still recorded durably.
    """

    def _rows(self, run_id="run-1", exact=0.9):
        run = _synthetic_run()
        report = scoring.score_run(run)
        record = history.build_run_record(run, report, run_id=run_id)
        record["metrics"]["exact_rate"] = exact
        return [record], history.build_question_records(run, run_id)

    def test_sync_creates_run_and_question_rows(self):
        from ai_processor.models import BenchmarkQuestionOutcome, BenchmarkRun

        runs, questions = self._rows()
        written = history.sync_to_database(runs, questions)

        self.assertEqual(written, (1, 2))
        self.assertEqual(BenchmarkRun.objects.count(), 1)
        self.assertEqual(BenchmarkQuestionOutcome.objects.count(), 2)

        stored = BenchmarkRun.objects.get(run_id="run-1")
        self.assertEqual(stored.exact_rate, 0.9)
        self.assertEqual(stored.questions_graded, 2)
        # The whole original row is kept, so a metric added later is still
        # recoverable from rows written before the column existed.
        self.assertEqual(stored.payload["run_id"], "run-1")

    def test_syncing_the_same_run_twice_does_not_duplicate(self):
        # A rebuild from archives must be safe to run repeatedly; duplicates
        # would inflate the sample count and corrupt the noise band.
        from ai_processor.models import BenchmarkQuestionOutcome, BenchmarkRun

        runs, questions = self._rows()
        history.sync_to_database(runs, questions)
        history.sync_to_database(runs, questions)

        self.assertEqual(BenchmarkRun.objects.count(), 1)
        self.assertEqual(BenchmarkQuestionOutcome.objects.count(), 2)

    def test_resync_updates_changed_metrics_in_place(self):
        from ai_processor.models import BenchmarkRun

        runs, questions = self._rows(exact=0.5)
        history.sync_to_database(runs, questions)
        self.assertEqual(BenchmarkRun.objects.get(run_id="run-1").exact_rate, 0.5)

        # e.g. the archive URL gets patched in after a successful upload.
        runs[0]["metrics"]["exact_rate"] = 0.75
        runs[0]["archive_url"] = "https://example.test/a.json.gz"
        history.sync_to_database(runs, questions)

        stored = BenchmarkRun.objects.get(run_id="run-1")
        self.assertEqual(stored.exact_rate, 0.75)
        self.assertEqual(stored.archive_url, "https://example.test/a.json.gz")
        self.assertEqual(BenchmarkRun.objects.count(), 1)

    def test_run_without_question_rows_still_syncs(self):
        # Backfilled rows from FINDINGS.md carry headline numbers only.
        from ai_processor.models import BenchmarkQuestionOutcome, BenchmarkRun

        runs, _ = self._rows()
        self.assertEqual(history.sync_to_database(runs, []), (1, 0))
        self.assertEqual(BenchmarkRun.objects.count(), 1)
        self.assertEqual(BenchmarkQuestionOutcome.objects.count(), 0)

    def test_deleting_a_run_removes_its_question_rows(self):
        from ai_processor.models import BenchmarkQuestionOutcome, BenchmarkRun

        runs, questions = self._rows()
        history.sync_to_database(runs, questions)
        BenchmarkRun.objects.get(run_id="run-1").delete()
        self.assertEqual(BenchmarkQuestionOutcome.objects.count(), 0)


@skipUnless(
    HAS_RECORDINGS,
    "No recorded model responses. Create them with "
    "`manage.py grading_benchmark --mode record` (makes real, billed calls).",
)
class CommandHistoryIntegrationTest(TestCase):
    """
    THE NON-BLOCKING PROMISE, end to end.

    A paid benchmark run costs real money and about an hour. Recording what
    it did is bookkeeping. These tests prove that no failure in the
    bookkeeping can damage the run: the report is byte-identical and the
    command still succeeds, whatever breaks.

    History paths are redirected to a temp directory so the tests never
    touch the tracked history files.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for attr, name in (
            ("RUNS_PATH", "runs.jsonl"),
            ("QUESTIONS_PATH", "questions.jsonl"),
        ):
            patcher = patch.object(history, attr, base / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runs_path = base / "runs.jsonl"
        self.questions_path = base / "questions.jsonl"

    def _run(self, *args):
        out = StringIO()
        call_command("grading_benchmark", "--mode", "replay", *args, stdout=out)
        return out.getvalue()

    def _metrics_only(self, output):
        """The report body, minus wall-clock and the history/archive lines
        that are expected to differ."""
        return [
            line
            for line in output.splitlines()
            if not line.startswith(("History:", "Archive:", "  grading "))
            and "wall=" not in line
        ]

    def test_history_is_written_and_report_is_unaffected(self):
        with_history = self._run()
        self.assertIn("History: recorded run", with_history)
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)
        self.assertEqual(len(history.load_jsonl(self.questions_path)), 133)

    def test_no_history_flag_writes_nothing(self):
        output = self._run("--no-history")
        self.assertNotIn("History:", output)
        self.assertFalse(self.runs_path.exists())

    def test_report_is_identical_with_and_without_history(self):
        without = self._metrics_only(self._run("--no-history"))
        with_ = self._metrics_only(self._run())
        self.assertEqual(without, with_)

    def test_history_write_failure_does_not_break_the_run(self):
        with patch.object(history, "record_run", side_effect=OSError("disk full")):
            output = self._run()
        # The run still reports its results in full...
        self.assertIn("ACCURACY vs ground truth", output)
        self.assertIn("133", output)
        # ...and says plainly that recording failed.
        self.assertIn("History: not recorded", output)

    def test_archive_preparation_failure_does_not_break_the_run(self):
        with patch(
            "ai_processor.benchmark.archive.prepare",
            side_effect=RuntimeError("boom"),
        ):
            output = self._run()
        self.assertIn("ACCURACY vs ground truth", output)
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)

    def test_database_mirror_failure_does_not_break_the_run(self):
        with patch.object(
            history, "sync_to_database", side_effect=RuntimeError("no db")
        ):
            output = self._run()
        self.assertIn("ACCURACY vs ground truth", output)
        # The files are the source of truth and must survive a DB problem.
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)

    def test_replay_run_is_recorded_but_not_archived(self):
        self._run()
        row = history.load_jsonl(self.runs_path)[0]
        self.assertEqual(row["mode"], "replay")
        self.assertIsNone(row["archive_url"])


@skipUnless(
    HAS_RECORDINGS,
    "No recorded model responses. Create them with "
    "`manage.py grading_benchmark --mode record` (makes real, billed calls).",
)
class RealRunHistoryTest(BenchmarkFixtureMixin, TestCase):
    """The drift lock against the real 133-question dataset, not a stub."""

    def test_full_replay_row_count_matches_report(self):
        teacher = self._make_teacher()
        run = runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        report = scoring.score_run(run)
        report["consistency"] = scoring.check_consistency(run, IDENTICAL_ANSWER_PROBES)

        rows = history.build_question_records(run, "run-1")
        self.assertEqual(len(rows), report["overall"]["questions"])

        record = history.build_run_record(run, report, run_id="run-1")
        self.assertEqual(record["questions_graded"], len(rows))
        self.assertEqual(record["submissions_failed"], 0)
        self.assertTrue(record["metrics"]["consistency_all_consistent"])
        self.assertEqual(record["metrics"]["deterministic_accuracy"], 1.0)
