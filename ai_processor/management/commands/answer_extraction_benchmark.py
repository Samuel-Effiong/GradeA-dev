"""
Run the deterministic answer-extraction benchmark and write its report.

    # free, deterministic - what CI runs
    manage.py answer_extraction_benchmark

    # machine-readable summary on stdout as well
    manage.py answer_extraction_benchmark --json

Mutation and live-provider results are not produced here - they are slow
(mutations) or billed (live) - but their reports are folded in when they
exist. See ai_processor/benchmark/answers/README.md for how to produce them.

Exits non-zero if any scenario fails, or if a scenario recorded as a known
gap has started passing (its note is then wrong and must be removed).
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_processor.benchmark.answers.report import REPORT_DIR, build_report, write_report


class Command(BaseCommand):
    help = "Run the answer-extraction benchmark and write its report."

    def add_arguments(self, parser):
        parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
        parser.add_argument(
            "--mutations", type=Path, default=REPORT_DIR / "mutations.json"
        )
        parser.add_argument("--live", type=Path, default=REPORT_DIR / "live_run.json")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        report = build_report(options["mutations"], options["live"])
        json_path, markdown_path = write_report(report, options["report_dir"])
        summary = report["summary"]

        if options["as_json"]:
            self.stdout.write(json.dumps(summary, indent=2))
        else:
            for key, value in summary.items():
                self.stdout.write(f"{key:<28} {value}")
        self.stdout.write(f"\nreport: {markdown_path}\ndata:   {json_path}")

        if summary["failed"] or summary["gaps_closed"]:
            raise CommandError(
                f"{summary['failed']} scenario(s) failed and "
                f"{summary['gaps_closed']} known gap(s) now pass - see {markdown_path}"
            )
