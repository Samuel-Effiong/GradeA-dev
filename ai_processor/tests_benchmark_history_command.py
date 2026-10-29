"""
Coverage for the `grading_benchmark_history` command and the HTML report.

The HTML tests matter more than they look: the report's whole purpose is to
be SENT to someone — opened on a machine that may have no access to this
network, or none at all. A stray CDN link or a var() inside an SVG attribute
would fail silently and produce a blank or unstyled page for the recipient,
with nothing in the terminal to indicate anything was wrong.

Run with:
    python manage.py test ai_processor.tests_benchmark_history_command
"""

import json
import re
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from ai_processor.benchmark import history, report_html


def _run_row(run_id, mode="record", exact=0.85, full=True, prompt="p1", **extra):
    # run_id leads with a compact UTC stamp (20260801T000000Z-...); turn that
    # back into a real timestamp for the DB mirror.
    stamp = run_id.split("-")[0]
    recorded_at = (
        f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T"
        f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}+00:00"
    )
    return {
        "run_id": run_id,
        "recorded_at": recorded_at,
        "mode": mode,
        "source": "benchmark",
        "code_sha": "abc1234",
        "prompt_fingerprint": prompt,
        "dataset_fingerprint": "d1",
        "is_full_run": full,
        "submissions": 21,
        "submissions_failed": 0,
        "questions_graded": 133,
        "metrics": {
            "exact_rate": exact,
            "within_one_level_rate": 1.0,
            "evidence_verified_rate": 0.99,
            "deterministic_accuracy": 1.0,
            "total_tokens": 500000,
        },
        "by_subject": {},
        "by_question_type": {},
        "ranking_spearman": {},
        "archive_url": None,
        "archive_error": None,
        **extra,
    }


def _question_row(run_id, level, qnum=4):
    return {
        "run_id": run_id,
        "assignment_key": "maths",
        "student_key": "strong",
        "question_number": qnum,
        "question_type": "SHORT-ANSWER",
        "subject": "Mathematics",
        "expected_points": 12,
        "awarded_points": 12 if level == 0 else 8,
        "expected_level": 0,
        "awarded_level": level,
        "verdict": "exact" if level == 0 else "adjacent",
        "evidence_verified": True,
    }


class HistoryCommandTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.runs_path = base / "runs.jsonl"
        self.questions_path = base / "questions.jsonl"
        for attr, path in (
            ("RUNS_PATH", self.runs_path),
            ("QUESTIONS_PATH", self.questions_path),
        ):
            patcher = patch.object(history, attr, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _call(self, *args):
        out = StringIO()
        call_command("grading_benchmark_history", *args, stdout=out)
        return out.getvalue()

    def _seed(self, rows, questions=()):
        history.append_jsonl(self.runs_path, rows)
        if questions:
            history.append_jsonl(self.questions_path, questions)

    def test_no_history_says_so_rather_than_crashing(self):
        self.assertIn("No runs recorded yet", self._call("--trends"))

    def test_trends_reports_values(self):
        self._seed(
            [
                _run_row("20260801T000000Z-a", exact=0.80),
                _run_row("20260802T000000Z-b", exact=0.85),
                _run_row("20260803T000000Z-c", exact=0.90),
            ]
        )
        output = self._call("--trends")
        self.assertIn("exact match", output)
        self.assertIn("comparable runs : 3", output)

    def test_too_few_runs_is_stated_plainly(self):
        self._seed([_run_row("20260801T000000Z-a")])
        self.assertIn("At least 3 are needed", self._call("--trends"))

    def test_replay_runs_are_excluded_from_the_count(self):
        self._seed(
            [
                _run_row("20260801T000000Z-a", mode="record"),
                _run_row("20260802T000000Z-b", mode="replay"),
            ]
        )
        self.assertIn("comparable runs : 1", self._call("--trends"))
        self.assertIn("comparable runs : 2", self._call("--trends", "--include-replay"))

    def test_json_output_is_machine_readable(self):
        self._seed(
            [
                _run_row(f"2026080{i}T000000Z-x", exact=0.8 + i / 100)
                for i in range(1, 4)
            ]
        )
        payload = json.loads(self._call("--trends", "--json"))
        self.assertEqual(payload["runs_considered"], 3)
        self.assertIn("exact_rate", payload["metrics"])

    def test_unstable_view_flags_a_moving_grade(self):
        self._seed(
            [_run_row("20260801T000000Z-a"), _run_row("20260802T000000Z-b")],
            [
                _question_row("20260801T000000Z-a", 0),
                _question_row("20260802T000000Z-b", 1),
            ],
        )
        output = self._call("--unstable")
        self.assertIn("UNSTABLE", output)
        self.assertIn("maths/strong", output)

    def test_question_view_shows_each_run(self):
        self._seed(
            [_run_row("20260801T000000Z-a"), _run_row("20260802T000000Z-b")],
            [
                _question_row("20260801T000000Z-a", 0),
                _question_row("20260802T000000Z-b", 1),
            ],
        )
        output = self._call("--question", "maths/strong/4")
        self.assertIn("20260801T000000Z-a", output)
        self.assertIn("20260802T000000Z-b", output)

    def test_malformed_question_argument_is_rejected_clearly(self):
        self._seed([_run_row("20260801T000000Z-a")])
        with self.assertRaises(CommandError) as ctx:
            self._call("--question", "maths")
        self.assertIn("assignment/student/number", str(ctx.exception))

    def test_seed_import_is_idempotent(self):
        seed = Path(self.tmp.name) / "seed.json"
        seed.write_text(json.dumps({"runs": [_run_row("20260801T000000Z-a")]}))

        self.assertIn("1 run(s) added", self._call("--import-seed", str(seed)))
        # Re-importing must not double the sample count, which would distort
        # every normal range computed from it.
        self.assertIn("0 run(s) added", self._call("--import-seed", str(seed)))
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)

    def test_missing_seed_file_is_reported(self):
        with self.assertRaises(CommandError):
            self._call("--import-seed", "/nonexistent/seed.json")

    def test_sync_db_writes_the_mirror(self):
        from ai_processor.models import BenchmarkRun

        self._seed([_run_row("20260801T000000Z-a")])
        self._call("--sync-db")
        self.assertEqual(BenchmarkRun.objects.count(), 1)

    def test_rebuild_from_archives_restores_rows(self):
        from ai_processor.benchmark import archive

        archives = Path(self.tmp.name) / "archives"
        archives.mkdir()
        bundle = {
            "run_id": "20260801T000000Z-a",
            "run_record": _run_row("20260801T000000Z-a"),
            "question_records": [_question_row("20260801T000000Z-a", 0)],
        }
        (archives / "20260801T000000Z-a.json.gz").write_bytes(archive.compress(bundle))

        output = self._call("--rebuild-from-archives", str(archives))
        self.assertIn("Rebuilt from 1 archive(s)", output)
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)
        self.assertEqual(len(history.load_jsonl(self.questions_path)), 1)

    def test_rebuild_twice_does_not_duplicate(self):
        from ai_processor.benchmark import archive

        archives = Path(self.tmp.name) / "archives"
        archives.mkdir()
        (archives / "a.json.gz").write_bytes(
            archive.compress(
                {
                    "run_record": _run_row("20260801T000000Z-a"),
                    "question_records": [_question_row("20260801T000000Z-a", 0)],
                }
            )
        )
        self._call("--rebuild-from-archives", str(archives))
        self._call("--rebuild-from-archives", str(archives))
        self.assertEqual(len(history.load_jsonl(self.runs_path)), 1)
        self.assertEqual(len(history.load_jsonl(self.questions_path)), 1)

    def test_rebuild_from_empty_directory_is_reported(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(CommandError):
            self._call("--rebuild-from-archives", str(empty))

    def test_html_report_is_written(self):
        self._seed(
            [
                _run_row(f"2026080{i}T000000Z-x", exact=0.8 + i / 100)
                for i in range(1, 4)
            ]
        )
        target = Path(self.tmp.name) / "out" / "report.html"
        self._call("--html", str(target))
        self.assertTrue(target.exists())
        self.assertIn("<!doctype html>", target.read_text())


class HtmlReportTest(SimpleTestCase):
    """
    The report is meant to be emailed and opened anywhere, so these check the
    things that would fail silently on someone else's machine.
    """

    def _render(self, runs=None, questions=None):
        runs = runs or [
            _run_row(f"2026080{i}T000000Z-x", exact=0.80 + i / 100) for i in range(1, 5)
        ]
        return report_html.render(runs, questions or [])

    def test_has_no_external_references(self):
        markup = self._render()
        self.assertEqual(
            re.findall(r'(?:src|href)="https?://', markup),
            [],
            "the report must not depend on the network",
        )
        self.assertNotIn("<script", markup)
        self.assertNotIn("@import", markup)

    def test_svg_colours_use_classes_not_var_in_attributes(self):
        # Browsers do NOT resolve var() inside SVG presentation attributes,
        # so fill="var(--accent)" renders as no colour at all.
        markup = self._render()
        self.assertEqual(re.findall(r'(?:fill|stroke|font-family)="var\(', markup), [])

    def test_every_colour_token_is_defined_in_the_bare_root_block(self):
        # A token defined only inside a media query or [data-theme] block is
        # undefined for viewers on the default "system" setting, which is the
        # classic unreadable-artifact bug.
        css = report_html._CSS
        bare = set(re.findall(r"(--[\w-]+)\s*:", css.split("@media")[0]))
        used = set(re.findall(r"var\((--[\w-]+)\)", css))
        self.assertEqual(used - bare, set())

    def test_body_paints_its_own_background(self):
        # Without this the page borrows the host's ground and can render one
        # theme's text on the other theme's background.
        self.assertRegex(report_html._CSS, r"body\s*\{[^}]*background:var\(")

    def test_both_theme_directions_are_covered(self):
        css = report_html._CSS
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn(':root:not([data-theme="light"])', css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_user_content_is_escaped(self):
        run = _run_row("20260801T000000Z-a")
        run["mode"] = "<script>alert(1)</script>"
        markup = report_html.render([run], [])
        self.assertNotIn("<script>alert(1)</script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_renders_with_no_runs_at_all(self):
        markup = report_html.render([], [])
        self.assertIn("<!doctype html>", markup)
        self.assertIn("No runs recorded yet", markup)

    def test_flat_metric_does_not_invent_an_axis_range(self):
        # tier 0 accuracy has been 100% on every run; labelling that chart
        # 50%-150% reads as a bug.
        runs = [_run_row(f"2026080{i}T000000Z-x") for i in range(1, 5)]
        markup = report_html.render(runs, [])
        self.assertIn("unchanged every run", markup)
        self.assertNotIn("150.0%", markup)

    def test_unstable_question_is_marked_in_the_table(self):
        runs = [_run_row("20260801T000000Z-a"), _run_row("20260802T000000Z-b")]
        questions = [
            _question_row("20260801T000000Z-a", 0),
            _question_row("20260802T000000Z-b", 1),
        ]
        markup = report_html.render(runs, questions)
        self.assertIn("flagged", markup)
