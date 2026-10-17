"""
Run the assignment-extraction benchmark.

    # free, deterministic — what CI runs
    manage.py extraction_benchmark --mode replay

    # real model calls, real credits — the accuracy numbers
    manage.py extraction_benchmark --mode live --json

    # one live run that also captures responses for future replays
    manage.py extraction_benchmark --mode record

    # a single case, e.g. while iterating on the rubric prompt
    manage.py extraction_benchmark --mode live --case six_level_rubric

See ai_processor/benchmark/extraction_dataset.py for what each case
guards, and extraction_scoring.py for what the metrics mean.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ai_processor.benchmark import runner
from ai_processor.benchmark.extraction_dataset import (
    EXTRACTION_CASES_BY_KEY,
    iter_extraction_dataset_errors,
)
from ai_processor.benchmark.extraction_runner import (
    EXTRACTION_RECORDINGS_DIR,
    execute_extraction_benchmark,
)
from ai_processor.benchmark.extraction_scoring import METRICS


def _pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


class Command(BaseCommand):
    help = "Run the assignment-extraction benchmark and report accuracy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=runner.MODES,
            default=runner.MODE_REPLAY,
            help="replay (free, default) | live (billed) | record (billed).",
        )
        parser.add_argument(
            "--case",
            action="append",
            dest="cases",
            help="Limit to a case key (repeatable).",
        )
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--teacher-email",
            help=(
                "Teacher the run executes as. Required in EVERY mode — see "
                "_resolve_user for why replay needs one too."
            ),
        )

    def _resolve_user(self, mode, email):
        """
        Every mode needs a real teacher, including replay.

        The record/replay tape intercepts AIProcessor.__ai_model, which
        sits INSIDE execute_graded_task — so replay skips the network and
        the charge, but still runs the surrounding tier check, credit
        estimation and usage-log path, all of which read the user. That
        seam is deliberate and shared with the grading benchmark: moving
        the patch higher would make replay stop exercising the pipeline
        that wraps the model call, which is code that can break on its
        own.

        This is why the CI gate is the golden TEST
        (tests_extraction_benchmark_golden), not this command: a TestCase
        builds its own teacher fixture in a throwaway database, so the
        free regression check needs nothing set up by hand.
        """
        if not email:
            raise CommandError(
                f"--teacher-email is required for --mode {mode}. Replay "
                "makes no model calls and spends no credits, but it still "
                "runs the billing path around them, which needs a real "
                "teacher. For a fixture-free regression check run "
                "`manage.py test ai_processor.tests_extraction_benchmark_golden`."
            )
        from users.models import CustomUser, UserTypes

        user = CustomUser.objects.filter(
            email=email, user_type=UserTypes.TEACHER
        ).first()
        if user is None:
            raise CommandError(f"No teacher found with email {email!r}.")
        return user

    def handle(self, *args, **options):
        # A benchmark whose own ground truth is malformed reports failures
        # that belong to the fixture, and the natural response to those is
        # to relax the assertion — which is how an instrument quietly
        # stops measuring. Refuse to run at all instead.
        dataset_errors = list(iter_extraction_dataset_errors())
        if dataset_errors:
            raise CommandError(
                "Extraction dataset is invalid:\n  " + "\n  ".join(dataset_errors)
            )

        case_keys = options.get("cases")
        if case_keys:
            unknown = [k for k in case_keys if k not in EXTRACTION_CASES_BY_KEY]
            if unknown:
                raise CommandError(
                    f"Unknown case key(s): {', '.join(unknown)}. "
                    f"Known: {', '.join(sorted(EXTRACTION_CASES_BY_KEY))}"
                )

        mode = options["mode"]
        user = self._resolve_user(mode, options.get("teacher_email"))

        def progress(case):
            if not options["as_json"]:
                self.stdout.write(f"  extracting {case.key}...")

        run = execute_extraction_benchmark(
            user,
            mode=mode,
            case_keys=case_keys,
            recordings_dir=EXTRACTION_RECORDINGS_DIR,
            progress=progress,
        )

        if options["as_json"]:
            self.stdout.write(json.dumps(_serialisable(run), indent=2))
        else:
            self._report(run)

        if not run["passed"]:
            raise CommandError(
                f"{len(run['strict_failures'])} strict failure(s) — see above."
            )

    def _report(self, run):
        write = self.stdout.write
        write("")
        write(f"mode          : {run['mode']}")
        write(f"model calls   : {run['model_calls']}")
        write(f"total tokens  : {run['total_tokens']}")
        write("")
        write("METRIC                   RATE      COUNT")
        for metric in METRICS:
            count = run["counts"][metric]
            write(
                f"  {metric:<22} {_pct(run['rates'][metric]):>7}"
                f"   ({count['passed']}/{count['total']})"
            )
        write("")
        write(f"cases passed  : {run['cases_passed']}/{run['cases']}")
        write(f"overall       : {_pct(run['overall'])}")
        write(f"weakest       : {run['weakest_metric']}")

        for result in run["results"]:
            if result["passed"]:
                continue
            write("")
            write(self.style.ERROR(f"FAIL {result['case']}"))
            guards = EXTRACTION_CASES_BY_KEY[result["case"]].guards
            write(f"  guards: {guards}")
            for failure in result["strict_failures"]:
                write(f"    - {failure}")

        write("")
        if run["passed"]:
            write(self.style.SUCCESS("All cases passed."))


def _serialisable(run):
    """Drop the ExtractionCase objects and recorded responses so --json
    emits something a script can actually consume."""
    return {key: value for key, value in run.items() if key not in ("responses",)}
