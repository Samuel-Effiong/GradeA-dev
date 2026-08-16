"""
Run the ground-truth grading benchmark.

    # free, deterministic — what the nightly job runs
    manage.py grading_benchmark --mode replay

    # real model calls, real credits — the accuracy numbers
    manage.py grading_benchmark --mode live --json

    # one live run that also captures responses for future replays
    manage.py grading_benchmark --mode record

    # readable PDFs of every assignment and submission
    manage.py grading_benchmark --pdf --out ./benchmark_artifacts

See ai_processor/benchmark/ for the dataset and the metric definitions.
"""

import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai_processor.benchmark import archive, history, runner, scoring
from ai_processor.benchmark.dataset import IDENTICAL_ANSWER_PROBES, iter_all_errors

logger = logging.getLogger(__name__)


def _fmt(value, suffix=""):
    return "n/a" if value is None else f"{value}{suffix}"


def _pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


class Command(BaseCommand):
    help = "Run the ground-truth grading benchmark and report accuracy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=runner.MODES,
            default=runner.MODE_REPLAY,
            help="replay (free, default) | live (billed) | record (billed).",
        )
        parser.add_argument(
            "--pdf",
            action="store_true",
            help="Render assignment/submission PDFs and exit unless --mode given.",
        )
        parser.add_argument(
            "--out",
            default="./benchmark_artifacts",
            help="Directory for --pdf output.",
        )
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--assignment",
            action="append",
            dest="assignments",
            help="Limit to an assignment key (repeatable).",
        )
        parser.add_argument(
            "--student",
            action="append",
            dest="students",
            help="Limit to a student key (repeatable).",
        )
        parser.add_argument(
            "--teacher-email",
            help="Existing TEACHER to bill for live/record runs. Defaults to a "
            "throwaway benchmark teacher created on the spot.",
        )
        parser.add_argument(
            "--baseline",
            help="Path to a baseline JSON report to diff this run against.",
        )
        parser.add_argument(
            "--save-baseline",
            help="Write this run's metrics to the given path.",
        )
        parser.add_argument(
            "--no-history",
            action="store_true",
            help="Skip recording this run to the history files, the database "
            "and the archive. The escape hatch: with this flag the command "
            "behaves exactly as it did before run history existed.",
        )

    # ── entry point ───────────────────────────────────────────────────────

    def handle(self, *args, **options):
        problems = list(iter_all_errors())
        if problems:
            # Never spend money grading a dataset that cannot be scored.
            raise CommandError(
                "Dataset is internally inconsistent — refusing to run:\n  "
                + "\n  ".join(problems)
            )

        if options["pdf"]:
            self._render_pdfs(options["out"])

        mode = options["mode"]
        user = self._resolve_user(mode, options.get("teacher_email"))

        run = runner.execute_benchmark(
            user,
            mode=mode,
            assignment_keys=options.get("assignments"),
            student_keys=options.get("students"),
            progress=None if options["as_json"] else self._progress,
        )

        report = scoring.score_run(run)
        report["consistency"] = scoring.check_consistency(run, IDENTICAL_ANSWER_PROBES)
        report["meta"] = {
            "mode": mode,
            "generated_at": timezone.now().isoformat(),
            "model_calls": run["model_calls"],
            "second_opinion_models": list(
                getattr(settings, "GRADING_SECOND_OPINION_MODELS", [])
            ),
            "evidence_enforcement": getattr(
                settings, "GRADING_EVIDENCE_ENFORCEMENT", None
            ),
        }

        if options.get("save_baseline"):
            with open(options["save_baseline"], "w") as handle:
                json.dump(report, handle, indent=2, default=str)
            self.stdout.write(f"Baseline written to {options['save_baseline']}")

        diff = None
        if options.get("baseline"):
            with open(options["baseline"]) as handle:
                diff = compare_to_baseline(report, json.load(handle))

        # Cheapest and most reliable first: the history rows and the local
        # archive copy are written to disk BEFORE anything slow happens, so a
        # paid run that took an hour is already safe if the upload later
        # hangs. Every step is individually guarded — see _record_history.
        recorded = self._record_history(run, report, options)

        if options["as_json"]:
            payload = dict(report)
            if diff:
                payload["baseline_diff"] = diff
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            self._render_text(report, diff)

        # Slow and fragile last, after the operator has already seen the
        # results.
        self._finish_history(recorded)

    # ── run history (Tiers 1-3) ───────────────────────────────────────────
    #
    # EVERYTHING in this section is best-effort. A benchmark run is the
    # expensive thing; recording what it did is bookkeeping. Losing an hour
    # of paid grading to a bug in the bookkeeping would be far worse than
    # having no bookkeeping, so each step is caught individually and the
    # command's report, --json output and exit code are identical whether
    # any of it succeeds or fails.

    def _record_history(self, run, report, options):
        """Write Tier 1 + 2, then prepare (but do not upload) the archive."""
        if options.get("no_history"):
            return None

        recorded = {
            "run_record": None,
            "question_records": [],
            "blob": None,
            "local_path": None,
        }

        try:
            run_record, question_records = history.record_run(
                run,
                report,
                scope_assignments=options.get("assignments"),
                scope_students=options.get("students"),
            )
            recorded["run_record"] = run_record
            recorded["question_records"] = question_records
            if not options["as_json"]:
                self.stdout.write(
                    f"History: recorded run {run_record['run_id']} "
                    f"({len(question_records)} question rows)"
                )
        except Exception as exc:
            logger.warning("[Benchmark] Could not record run history: %s", exc)
            if not options["as_json"]:
                self.stdout.write(self.style.WARNING(f"History: not recorded ({exc})"))
            return recorded

        try:
            blob, local_path, error = archive.prepare(
                run, report, recorded["run_record"], recorded["question_records"]
            )
            recorded["blob"], recorded["local_path"] = blob, local_path
            if error:
                recorded["error"] = error
        except Exception as exc:
            logger.warning("[Benchmark] Could not prepare the run archive: %s", exc)

        return recorded

    def _finish_history(self, recorded):
        """Mirror into the database, then upload the archive."""
        if not recorded or not recorded.get("run_record"):
            return

        run_record = recorded["run_record"]

        # The files never depend on the database — a machine without one
        # still gets a complete history on disk.
        try:
            history.sync_to_database(
                [run_record], recorded.get("question_records") or []
            )
        except Exception as exc:
            logger.warning("[Benchmark] Could not mirror history to the DB: %s", exc)

        blob = recorded.get("blob")
        if blob is None:
            return

        url, error = archive.publish(
            run_record["run_id"], blob, local_path=recorded.get("local_path")
        )
        try:
            history.update_run_archive(
                run_record["run_id"], archive_url=url, archive_error=error
            )
            history.sync_to_database(
                [{**run_record, "archive_url": url, "archive_error": error}], []
            )
        except Exception as exc:
            logger.warning("[Benchmark] Could not record the archive result: %s", exc)

        if url:
            self.stdout.write(f"Archive: {url}")
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Archive: upload failed ({error}). Local copy kept at "
                    f"{recorded.get('local_path')}"
                )
            )

    # ── helpers ───────────────────────────────────────────────────────────

    def _progress(self, assignment, student):
        self.stdout.write(f"  grading {assignment.key}/{student.key} ...")

    def _render_pdfs(self, out_dir):
        from ai_processor.benchmark.render import render_all

        written = render_all(out_dir)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(written)} PDFs to {out_dir}"))

    def _resolve_user(self, mode, teacher_email):
        """
        The teacher billed for the run.

        Replay makes no billed calls, but execute_graded_task still walks
        the access-control and wallet path, so a valid teacher is needed
        in every mode.
        """
        from datetime import timedelta

        from billing.models import (
            BillingInterval,
            CreditBucket,
            CreditBucketType,
            CreditWallet,
            PlanCategory,
            PlanTier,
            SubscriptionPlan,
            UserSubscription,
        )
        from users.models import CustomUser, UserTypes

        if teacher_email:
            try:
                return CustomUser.objects.get(
                    email=teacher_email, user_type=UserTypes.TEACHER
                )
            except CustomUser.DoesNotExist as exc:
                raise CommandError(f"No TEACHER with email {teacher_email!r}") from exc

        email = "grading-benchmark@benchmark.local"
        user = CustomUser.objects.filter(email=email).first()
        if user is None:
            user = CustomUser.objects.create_user(
                email=email,
                password=(
                    CustomUser.objects.make_random_password()
                    if hasattr(CustomUser.objects, "make_random_password")
                    else "benchmark-not-a-login-account"
                ),
                user_type=UserTypes.TEACHER,
                is_active=True,
            )

        plan, _ = SubscriptionPlan.objects.get_or_create(
            name="Grading Benchmark Plan",
            defaults={
                "category": PlanCategory.INDIVIDUAL,
                "tier": PlanTier.PRO,
                "interval": BillingInterval.MONTHLY,
                "monthly_credits": 5_000_000,
                "carry_over_percent": 0,
                "is_active": True,
            },
        )
        now = timezone.now()
        if not UserSubscription.objects.filter(user=user, is_active=True).exists():
            UserSubscription.objects.create(
                user=user,
                plan=plan,
                is_active=True,
                billing_cycle_start=now,
                billing_cycle_end=now + timedelta(days=365),
                is_trial=False,
                auto_renew=False,
            )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        # estimate_total_token adds a flat +20,000 baseline to every
        # estimate, so a thin wallet fails before a single call is made.
        if wallet.total_remaining_credits() < 500_000:
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=5_000_000,
                used_credits=0,
                expires_at=now + timedelta(days=365),
            )
        return user

    # ── text report ───────────────────────────────────────────────────────

    def _render_text(self, report, diff):
        out = self.stdout
        overall = report["overall"]

        out.write("")
        out.write("=" * 72)
        out.write(f"GRADING BENCHMARK — mode={report['meta']['mode']}")
        out.write("=" * 72)

        out.write("")
        out.write("1. ACCURACY vs ground truth")
        out.write(f"   questions graded      : {overall['questions']}")
        out.write(
            f"   exact level match     : {overall['exact']} "
            f"({_pct(overall['exact_rate'])})"
        )
        out.write(
            f"   within one level      : {overall['within_one_level']} "
            f"({_pct(overall['within_one_level_rate'])})"
        )
        out.write(
            f"   mean level error      : {_fmt(overall['mean_level_error'])}"
            "   (+ = lenient, - = harsh)"
        )

        out.write("")
        out.write("2. BY QUESTION TYPE")
        for key, band in report["by_question_type"].items():
            out.write(
                f"   {key:<14} n={band['questions']:<3} "
                f"exact={_pct(band['exact_rate']):<7} "
                f"within1={_pct(band['within_one_level_rate']):<7} "
                f"bias={_fmt(band['mean_level_error'])}"
            )

        out.write("")
        out.write("3. BY SUBJECT")
        for key, band in report["by_subject"].items():
            out.write(
                f"   {key:<24} n={band['questions']:<3} "
                f"exact={_pct(band['exact_rate']):<7} "
                f"within1={_pct(band['within_one_level_rate']):<7} "
                f"bias={_fmt(band['mean_level_error'])}"
            )

        out.write("")
        out.write("4. STUDENT RANKING (does it order students correctly?)")
        for key, block in sorted(report["ranking"].items()):
            out.write(f"   {key:<12} spearman={_fmt(block['spearman'])}")
            out.write(f"      expected {block['expected_totals']}")
            out.write(f"      awarded  {block['awarded_totals']}")

        out.write("")
        out.write("5. DETERMINISTIC TIER 0 (must be 100%)")
        det = report["deterministic"]
        out.write(
            f"   claimed={det['claimed']} correct={det['correct']} "
            f"accuracy={_pct(det['accuracy'])}"
        )

        out.write("")
        out.write("6. CROSS-STUDENT CONSISTENCY (identical answers)")
        for probe in report["consistency"]:
            out.write(
                f"   {probe['assignment_key']} Q{probe['question_number']}: "
                f"strong={_fmt(probe['strong'])} twin={_fmt(probe['twin'])} "
                f"-> {probe['status']}"
            )

        out.write("")
        out.write("7. PIPELINE HEALTH")
        snap = report["rubric_snapping"]
        out.write(
            f"   scores snapped to a rubric level : {snap['snapped_scores']} "
            f"({_pct(snap['rate'])} of questions)"
        )
        ev = report["evidence"]
        out.write(
            f"   evidence verified                : {ev['verified']}/"
            f"{ev['checked']} ({_pct(ev['verified_rate'])})"
        )
        so = report["second_opinion"]
        out.write(f"   second opinion coverage          : {so['coverage']}")
        out.write(
            f"   disagreements                    : {so['questions_disagreed']}"
            f"/{so['questions_compared']} ({_pct(so['disagreement_rate'])}) "
            f"tiers={so['tiers']}"
        )

        out.write("")
        out.write("8. CONFIDENCE CALIBRATION (is low confidence predictive?)")
        for band_name, block in report["confidence_calibration"].items():
            out.write(
                f"   {band_name:<16} n={block['questions']:<3} "
                f"graded acceptably={_pct(block['rate'])}"
            )

        out.write("")
        out.write("9. LEVEL DECISION CALIBRATION (is 'borderline' predictive?)")
        decisions = report["level_decision_calibration"]
        for decision, block in decisions.items():
            out.write(
                f"   {decision:<16} n={block['questions']:<3} "
                f"exact={_pct(block['exact_rate'])} "
                f"within-1={_pct(block['within_one_level_rate'])}"
            )
        clear = decisions.get("clear", {}).get("exact_rate")
        borderline = decisions.get("borderline", {}).get("exact_rate")
        if clear is not None and borderline is not None:
            verdict = (
                "signal is useful — borderline is measurably worse"
                if borderline < clear
                else "NO SIGNAL — borderline grades are no worse than clear "
                "ones, so the second-opinion trigger is buying nothing"
            )
            out.write(f"   -> {verdict}")
        elif not decisions.get("borderline", {}).get("questions"):
            out.write(
                "   -> the grader never reported a borderline call; "
                "nothing to calibrate yet"
            )

        out.write("")
        out.write("10. COST")
        cost = report["cost"]
        out.write(
            f"   submissions={cost['submissions']} "
            f"tokens={cost['total_tokens']} "
            f"wall={cost['total_seconds']}s"
        )

        if report["failures"]:
            out.write("")
            out.write(f"10. OUT-OF-BAND FAILURES ({len(report['failures'])})")
            for failure in report["failures"]:
                out.write(
                    f"   {failure['assignment_key']}/{failure['student_key']} "
                    f"Q{failure['question_number']} "
                    f"({failure['question_type']}, {failure['points']} marks): "
                    f"expected {failure['expected']:g}, got "
                    f"{_fmt(failure['awarded'])}"
                )
                out.write(
                    f"       why it should be {failure['expected']:g}: "
                    f"{failure['note']}"
                )

        if report["errors"]:
            out.write("")
            out.write(f"11. RUN ERRORS ({len(report['errors'])})")
            for error in report["errors"]:
                out.write(
                    f"   {error['assignment_key']}/{error['student_key']}: "
                    f"{error['error']}"
                )

        if diff:
            out.write("")
            out.write("12. VS BASELINE")
            for line in diff["lines"]:
                out.write(f"   {line}")
            if diff["regressed"]:
                out.write(self.style.ERROR("   REGRESSION DETECTED"))
            else:
                out.write(self.style.SUCCESS("   no regression"))

        out.write("")


def compare_to_baseline(report, baseline, tolerance=0.05):
    """
    Diff a run against a stored baseline.

    Only a DROP matters: grading getting more accurate is not a
    regression. The tolerance exists because the model is not perfectly
    deterministic in live mode (OpenRouter fallback routing means
    temperature 0 does not guarantee identical output), so a
    single-question wobble must not page anyone.
    """
    lines = []
    regressed = False

    for metric in ("within_one_level_rate", "exact_rate"):
        now = report["overall"].get(metric)
        was = (baseline.get("overall") or {}).get(metric)
        if now is None or was is None:
            lines.append(f"{metric}: {_fmt(was)} -> {_fmt(now)} (incomparable)")
            continue
        delta = now - was
        lines.append(f"{metric}: {was:.3f} -> {now:.3f} ({delta:+.3f})")
        if delta < -tolerance:
            regressed = True

    now_failures = len(report.get("failures") or [])
    was_failures = len(baseline.get("failures") or [])
    lines.append(f"out-of-band failures: {was_failures} -> {now_failures}")

    now_det = (report.get("deterministic") or {}).get("accuracy")
    if now_det is not None and now_det < 1.0:
        lines.append(f"deterministic tier accuracy {now_det:.3f} < 1.0")
        regressed = True

    inconsistent = [p for p in report.get("consistency") or [] if not p["consistent"]]
    if inconsistent:
        lines.append(f"{len(inconsistent)} identical-answer pair(s) scored differently")
        regressed = True

    return {"lines": lines, "regressed": regressed}
