"""
Read the grading benchmark's run history.

    # has anything actually changed, or is it noise?
    manage.py grading_benchmark_history --trends

    # which questions does the grader mark inconsistently?
    manage.py grading_benchmark_history --unstable

    # one question's full grade history
    manage.py grading_benchmark_history --question maths/strong/4

    # a page you can open in a browser and send to someone
    manage.py grading_benchmark_history --html trends.html

    # mirror the files into the database (idempotent)
    manage.py grading_benchmark_history --sync-db

    # rebuild the files from downloaded run archives
    manage.py grading_benchmark_history --rebuild-from-archives ./archives

This command only READS the history that `grading_benchmark` writes. It makes
no model calls and costs nothing.
"""

import json as json_module
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_processor.benchmark import analysis, archive, history, report_html


def _fmt(value, kind="number"):
    if value is None:
        return "n/a"
    if kind == "rate":
        return f"{value * 100:.1f}%"
    if kind == "count":
        return f"{value:,.0f}"
    return f"{value:.4g}"


class Command(BaseCommand):
    help = "Report trends across recorded grading-benchmark runs."

    def add_arguments(self, parser):
        parser.add_argument("--trends", action="store_true")
        parser.add_argument("--unstable", action="store_true")
        parser.add_argument(
            "--question",
            help="One question's history, as assignment/student/number "
            "(e.g. maths/strong/4).",
        )
        parser.add_argument("--html", help="Write a self-contained HTML report here.")
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--sync-db",
            action="store_true",
            help="Mirror the history files into the database. Idempotent.",
        )
        parser.add_argument(
            "--rebuild-from-archives",
            metavar="DIR",
            help="Rebuild the history files from run archives in DIR. Each "
            "archive carries its own rows, so this restores history lost "
            "with the files — including runs made on the server.",
        )
        parser.add_argument(
            "--import-seed",
            metavar="FILE",
            help="Import hand-transcribed historical runs from a seed JSON "
            "file. Idempotent. Used once to recover Runs 1-5, whose raw data "
            "no longer exists, from the write-up in FINDINGS.md.",
        )
        parser.add_argument(
            "--include-replay",
            action="store_true",
            help="Include replay runs in the statistics. Off by default: "
            "replay re-reads fixed responses, so its numbers never vary and "
            "including them makes the normal range look far too narrow.",
        )
        parser.add_argument(
            "--include-partial",
            action="store_true",
            help="Include runs that graded only part of the dataset.",
        )

    def handle(self, *args, **options):
        if options.get("import_seed"):
            self._import_seed(options["import_seed"])

        if options.get("rebuild_from_archives"):
            self._rebuild(options["rebuild_from_archives"])

        runs = history.dedupe_by_run_id(history.load_runs())
        questions = history.load_questions()

        if options.get("sync_db"):
            written = history.sync_to_database(runs, questions)
            self.stdout.write(
                f"Synced {written[0]} run(s) and {written[1]} question row(s)."
            )

        if not runs:
            self.stdout.write(
                self.style.WARNING(
                    "No runs recorded yet. Run `manage.py grading_benchmark "
                    "--mode replay` to record one."
                )
            )
            return

        include_replay = options.get("include_replay", False)
        include_partial = options.get("include_partial", False)

        if options.get("html"):
            path = Path(options["html"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                report_html.render(runs, questions, include_replay, include_partial),
                encoding="utf-8",
            )
            self.stdout.write(f"HTML report written to {path}")

        if options.get("question"):
            self._render_question(questions, options["question"], options)
            return

        if options.get("unstable"):
            self._render_unstable(
                runs, questions, include_replay, include_partial, options
            )
            return

        # --trends is the default view when nothing else is asked for.
        if options.get("trends") or not options.get("html"):
            self._render_trends(runs, include_replay, include_partial, options)

    # ── views ─────────────────────────────────────────────────────────────

    def _render_trends(self, runs, include_replay, include_partial, options):
        report = analysis.trends(runs, include_replay, include_partial)

        if options["as_json"]:
            self.stdout.write(json_module.dumps(report, indent=2, default=str))
            return

        out = self.stdout
        out.write("=" * 72)
        out.write("GRADING BENCHMARK — TRENDS")
        out.write("=" * 72)
        out.write("")
        out.write(f"comparable runs : {report['runs_considered']}")
        if not include_replay:
            out.write("                  (replay runs excluded — see --include-replay)")
        if report["mixed_prompt_versions"]:
            out.write(
                self.style.WARNING(
                    "  ! these runs do not all use the same grading prompt, so "
                    "they are\n    not a like-for-like comparison"
                )
            )
        out.write("")

        if report["runs_considered"] < analysis.MIN_RUNS_FOR_BAND:
            out.write(
                self.style.WARNING(
                    f"Only {report['runs_considered']} comparable run(s). At least "
                    f"{analysis.MIN_RUNS_FOR_BAND} are needed before a normal range "
                    "means anything — reporting raw values only."
                )
            )
            out.write("")

        for metric, _label, kind in analysis.TRENDED_METRICS:
            entry = report["metrics"][metric]
            values = [value for _run_id, value in entry["series"]]
            if not values:
                continue

            out.write(f"{entry['label']}")
            out.write(
                "   values : " + ", ".join(_fmt(value, kind) for value in values[-8:])
            )

            band = entry["band"]
            if band:
                out.write(
                    f"   normal : {_fmt(band['normal_low'], kind)} to "
                    f"{_fmt(band['normal_high'], kind)}   "
                    f"(seen {_fmt(band['min'], kind)}–{_fmt(band['max'], kind)} "
                    f"over {band['runs']} runs)"
                )
            else:
                out.write("   normal : not enough runs yet")

            verdict = entry["assessment"]
            if verdict is None:
                out.write("   latest : no verdict yet")
            elif verdict["unusual"]:
                multiple = (
                    ""
                    if verdict["sigmas"] is None
                    else f" — {abs(verdict['sigmas']):.1f}x the usual spread"
                )
                out.write(
                    self.style.ERROR(
                        f"   latest : {_fmt(verdict['latest'], kind)} "
                        f"({verdict['direction']}) OUTSIDE the normal range"
                        f"{multiple}"
                    )
                )
            else:
                out.write(
                    self.style.SUCCESS(
                        f"   latest : {_fmt(verdict['latest'], kind)} "
                        f"({verdict['direction']}) — within normal variation"
                    )
                )
            out.write("")

    def _render_unstable(
        self, runs, questions, include_replay, include_partial, options
    ):
        comparable = analysis.comparable_runs(runs, include_replay, include_partial)
        findings = analysis.question_stability(
            questions, run_ids={r.get("run_id") for r in comparable}
        )

        if options["as_json"]:
            self.stdout.write(json_module.dumps(findings, indent=2, default=str))
            return

        out = self.stdout
        out.write("=" * 72)
        out.write("QUESTIONS THE GRADER MARKS INCONSISTENTLY")
        out.write("=" * 72)
        out.write("")

        if not findings:
            out.write(
                "No question has been graded in more than one comparable run "
                "yet,\nso consistency cannot be judged. Record more runs."
            )
            return

        unstable = [f for f in findings if f["unstable"]]
        out.write(
            f"{len(unstable)} of {len(findings)} tracked question(s) received "
            "different\ngrades on different runs (same answer, same rubric).\n"
        )

        for finding in findings[:30]:
            marker = "UNSTABLE" if finding["unstable"] else "  stable"
            levels = " -> ".join(
                "?" if level is None else str(level) for level in finding["levels"]
            )
            line = (
                f"  {marker}  "
                f"{finding['assignment_key']}/{finding['student_key']}"
                f" Q{finding['question_number']:<3} "
                f"runs={finding['runs']} grades=[{levels}]"
            )
            out.write(self.style.ERROR(line) if finding["unstable"] else line)

        if len(findings) > 30:
            out.write(f"  ... and {len(findings) - 30} more")

    def _render_question(self, questions, spec, options):
        parts = spec.split("/")
        if len(parts) != 3:
            raise CommandError(
                f"--question expects assignment/student/number, got {spec!r} "
                "(e.g. maths/strong/4)"
            )
        assignment_key, student_key, number = parts
        rows = analysis.question_history(questions, assignment_key, student_key, number)

        if options["as_json"]:
            self.stdout.write(json_module.dumps(rows, indent=2, default=str))
            return

        out = self.stdout
        out.write(f"History for {assignment_key}/{student_key} Q{number}")
        out.write("")
        if not rows:
            out.write("  no recorded grades for that question")
            return

        out.write(
            f"  expected: {rows[-1].get('expected_points')} "
            f"(level {rows[-1].get('expected_level')})"
        )
        out.write("")
        for row in rows:
            out.write(
                f"  {row.get('run_id'):<26} "
                f"awarded={str(row.get('awarded_points')):<6} "
                f"level={str(row.get('awarded_level')):<4} "
                f"{row.get('verdict', ''):<10} "
                f"evidence={row.get('evidence_verified')}"
            )

    # ── import / rebuild ──────────────────────────────────────────────────

    def _import_seed(self, path):
        """
        Add hand-transcribed historical runs.

        Idempotent: rows already present (matched on run_id) are left alone,
        so importing twice does not double the sample count and distort the
        normal range.
        """
        path = Path(path)
        if not path.exists():
            raise CommandError(f"No such seed file: {path}")

        try:
            seed = json_module.loads(path.read_text(encoding="utf-8"))
        except json_module.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc

        incoming = seed.get("runs") or []
        if not incoming:
            raise CommandError(f"{path} contains no 'runs'.")

        existing = {row.get("run_id") for row in history.load_runs()}
        fresh = [row for row in incoming if row.get("run_id") not in existing]

        if fresh:
            history.append_jsonl(history.RUNS_PATH, fresh)

        self.stdout.write(
            f"Seed import: {len(fresh)} run(s) added, "
            f"{len(incoming) - len(fresh)} already present."
        )

    # ── rebuild ───────────────────────────────────────────────────────────

    def _rebuild(self, directory):
        """
        Restore the history files from run archives.

        Each archive carries its own Tier 1 and Tier 2 rows, which is what
        makes the files regenerable — and therefore what makes a run executed
        on the server, where Celery cannot commit to git, recoverable.
        """
        directory = Path(directory)
        if not directory.exists():
            raise CommandError(f"No such directory: {directory}")

        bundles = sorted(directory.glob("*.json.gz"))
        if not bundles:
            raise CommandError(f"No *.json.gz archives found in {directory}")

        run_records, question_records = [], []
        failed = []
        for path in bundles:
            try:
                bundle = archive.load_bundle(path)
            except Exception as exc:
                failed.append(f"{path.name}: {exc}")
                continue
            if bundle.get("run_record"):
                run_records.append(bundle["run_record"])
            question_records.extend(bundle.get("question_records") or [])

        # Merge with whatever is already on disk, then de-duplicate, so a
        # rebuild is safe to run twice and never doubles a run.
        merged_runs = history.dedupe_by_run_id(history.load_runs() + run_records)
        seen = set()
        merged_questions = []
        for row in history.load_questions() + question_records:
            key = (
                row.get("run_id"),
                row.get("assignment_key"),
                row.get("student_key"),
                row.get("question_number"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged_questions.append(row)

        history.RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        history.RUNS_PATH.write_text(
            "".join(
                json_module.dumps(row, sort_keys=True, default=str) + "\n"
                for row in sorted(merged_runs, key=lambda r: r.get("run_id") or "")
            ),
            encoding="utf-8",
        )
        history.QUESTIONS_PATH.write_text(
            "".join(
                json_module.dumps(row, sort_keys=True, default=str) + "\n"
                for row in merged_questions
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            f"Rebuilt from {len(bundles)} archive(s): {len(merged_runs)} run(s), "
            f"{len(merged_questions)} question row(s)."
        )
        for problem in failed:
            self.stdout.write(self.style.WARNING(f"  skipped {problem}"))
