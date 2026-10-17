"""
Isolate the grading-prompt edit's effect from run-to-run variance.

THE QUESTION

The committed baseline (0.8452) was recorded weeks ago with the OLD
prompt. The accepted run (0.8274) used the NEW prompt, today. Comparing
those two confounds two different things:

    prompt effect  +  run-to-run variance

LLM grading is not bit-reproducible even at temperature 0 (OpenRouter
routes across providers, and providers vary internally), so some of that
3-question gap is noise no matter what.

THE DESIGN

Run BOTH arms today, repeatedly, ALTERNATING:

    B, C, B, C, B, C        B = OLD prompt, C = NEW prompt

Alternating is not cosmetic. Running all-B then all-C would let any
provider drift across a 12-hour window land entirely on one arm and
masquerade as the prompt's effect. Interleaving makes drift hit both
arms roughly equally.

With n runs per arm we get WITHIN-arm spread (how much a single
unchanged prompt wanders between runs) and BETWEEN-arm difference. If
the within-arm spread is comparable to the between-arm difference, the
prompt edit is not distinguishable from noise.

THE MEASUREMENT THAT ACTUALLY ANSWERS IT

Aggregate exact_rate cannot tell "the same 3 questions flip every time"
(a real, reproducible prompt effect) from "3 different questions flip
each run" (noise averaging to the same rate). So every run also persists
PER-QUESTION outcomes, keyed by (assignment, student, question). The
flip analysis over those rows is the real result; the percentages are a
summary of it.

WHAT WENT WRONG LAST TIME, AND WHAT CHANGED

The previous attempt swapped the prompt FILE on disk and restored it in
a `finally` block. An external kill (session teardown) bypasses
`finally` entirely, so the repo was left holding the OLD prompt - the
edit silently reverted, with no git diff to reveal it. It also lost every
completed submission, because the tape only persists at the very end.

This version fixes all three:

  * NO DISK MUTATION. `ai_processor.services.GRADING_ASSIGNMENT_PROMPT`
    is a module-level global that the grading methods read at CALL time,
    so the arm is selected by rebinding that attribute in memory. A crash
    at any instant cannot leave the repo altered, because the repo is
    never altered.
  * MODE_LIVE, not MODE_RECORD. Live runs write no recordings at all, so
    the committed recordings and golden snapshot cannot be touched.
  * INCREMENTAL PERSISTENCE. Each run's full report + per-question rows
    are written to results.jsonl the moment that run finishes. A kill
    costs at most the run in flight, and everything before it is usable.
    Re-running skips whatever is already in the file.

Run detached (setsid) so a session teardown cannot kill it.
"""

import json
import os
import sys
import traceback
from datetime import timedelta
from pathlib import Path

PROJ = "/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus"
SCRATCH = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")

RESULTS = SCRATCH / "isolation_results.jsonl"
QUESTIONS = SCRATCH / "isolation_questions.jsonl"
OLD_PROMPT = (SCRATCH / "GRADING_PROMPT_old.txt").read_text()
NEW_PROMPT = (SCRATCH / "GRADING_PROMPT_new.txt").read_text()

#: Runs per arm. Alternated B,C,B,C,... so drift hits both arms equally.
RUNS_PER_ARM = int(os.environ.get("ISOLATION_RUNS_PER_ARM", "3"))

import django  # noqa: E402

django.setup()

from django.test.utils import setup_databases, teardown_databases  # noqa: E402
from django.utils import timezone  # noqa: E402


def pin_client_timeout():
    """
    Give the OpenAI client an explicit timeout and retry cap.

    ai_processor/services.py constructs it with NEITHER:

        self.client = OpenAI(base_url=..., api_key=OPENROUTER_API_KEY)

    The first attempt at this experiment hung for over TWO HOURS on a
    single submission - process alive, 0.1% CPU, blocked in
    poll_schedule_timeout on a socket that never answered - and burned
    the night for nothing. Without a bound, one half-open connection
    stalls indefinitely.

    Pinned here rather than in services.py because this is an experiment
    harness and changing production behaviour mid-experiment would
    confound the very comparison being measured. The underlying gap is
    real and belongs in the recommendations, not in a patch snuck in
    under an unrelated run.

    300s is ~7x the slowest observed grading batch (~40s), so it cannot
    truncate a legitimately slow call; it only bounds a dead one.
    """
    from openai import OpenAI

    from ai_processor import services as ai_services

    ai_services.ai_processor.client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=300.0,
        max_retries=3,
    )
    print("Client timeout pinned: 300s, max_retries=3.", flush=True)


def make_teacher():
    """Billable teacher fixture. Mirrors BenchmarkFixtureMixin."""
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

    teacher = CustomUser.objects.create_user(
        email=f"isolation-{timezone.now().timestamp()}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        name=f"isolation-{teacher.id}",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=200_000_000,
        carry_over_percent=0,
        is_active=True,
    )
    now = timezone.now()
    UserSubscription.objects.create(
        user=teacher,
        plan=plan,
        is_active=True,
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        is_trial=False,
        auto_renew=False,
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=200_000_000,
        used_credits=0,
        expires_at=now + timedelta(days=30),
    )
    return teacher


def completed_run_ids():
    """Run ids already persisted, so a restart resumes rather than repeats."""
    if not RESULTS.exists():
        return set()
    done = set()
    for line in RESULTS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            done.add(json.loads(line)["run_id"])
        except Exception:
            continue
    return done


def append_jsonl(path, records):
    with open(path, "a") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def execute_one(arm, index, teacher):
    """One full 21-submission benchmark pass under the given arm."""
    from unittest.mock import patch

    from ai_processor.benchmark import scoring
    from ai_processor.benchmark.runner import MODE_LIVE, execute_benchmark

    prompt = OLD_PROMPT if arm == "B_old_prompt" else NEW_PROMPT
    run_id = f"{arm}#{index}"

    started = timezone.now()
    # THE ARM SWITCH. Rebinding the module global - never the file - so a
    # crash cannot leave the repository holding the wrong prompt.
    with patch("ai_processor.services.GRADING_ASSIGNMENT_PROMPT", prompt):
        from ai_processor.services import GRADING_ASSIGNMENT_PROMPT as active

        expected = "answer_status" in prompt
        if ("answer_status" in active) != expected:
            raise AssertionError(
                f"{run_id}: patched prompt did not take effect - refusing to "
                "record a mislabelled arm."
            )
        print(f"  [{run_id}] arm confirmed active, grading...", flush=True)
        run = execute_benchmark(
            teacher,
            mode=MODE_LIVE,
            recordings_dir=SCRATCH / "isolation_scratch_recordings",
            progress=lambda a, s: print(f"    [{run_id}] {a.key}/{s.key}", flush=True),
        )

    report = scoring.score_run(run)
    overall = report.get("overall", {})
    errors = [r for r in run["results"] if r.get("error")]

    record = {
        "run_id": run_id,
        "arm": arm,
        "index": index,
        "started_at": started.isoformat(),
        "finished_at": timezone.now().isoformat(),
        "model_calls": run.get("model_calls"),
        "total_tokens": run.get("total_tokens"),
        "submissions_errored": len(errors),
        "exact_rate": overall.get("exact_rate"),
        "exact": overall.get("exact"),
        "questions": overall.get("questions"),
        "within_one_level_rate": overall.get("within_one_level_rate"),
        "mean_level_error": overall.get("mean_level_error"),
        "by_question_type": report.get("by_question_type"),
        "by_subject": report.get("by_subject"),
        "deterministic": report.get("deterministic"),
        "evidence": report.get("evidence"),
        "second_opinion": report.get("second_opinion"),
        "consistency": report.get("consistency"),
    }
    append_jsonl(RESULTS, [record])

    # Per-question rows: the only data that can tell a reproducible
    # prompt effect from noise that averages to the same rate.
    rows = []
    for row in scoring.iter_question_outcomes(run):
        row = dict(row)
        row["run_id"] = run_id
        row["arm"] = arm
        rows.append(row)
    append_jsonl(QUESTIONS, rows)

    print(
        f"  [{run_id}] DONE exact_rate={record['exact_rate']} "
        f"({record['exact']}/{record['questions']}) "
        f"within_one={record['within_one_level_rate']} "
        f"tokens={record['total_tokens']:,} errors={len(errors)}",
        flush=True,
    )
    return record


def main():
    # Alternate the arms: B, C, B, C, ... so provider drift over the run
    # window cannot land entirely on one arm.
    schedule = []
    for index in range(1, RUNS_PER_ARM + 1):
        schedule.append(("B_old_prompt", index))
        schedule.append(("C_new_prompt", index))

    done = completed_run_ids()
    todo = [(a, i) for a, i in schedule if f"{a}#{i}" not in done]

    print(f"Isolation v2 | runs/arm={RUNS_PER_ARM} | schedule={len(schedule)}")
    print(f"already complete: {sorted(done) or 'none'}")
    print(f"to run: {[f'{a}#{i}' for a, i in todo]}\n", flush=True)

    if not todo:
        print("Nothing to do.")
        return

    config = setup_databases(verbosity=0, interactive=False)
    try:
        pin_client_timeout()
        teacher = make_teacher()
        for arm, index in todo:
            try:
                execute_one(arm, index, teacher)
            except Exception:
                # One failed run must not abandon the others - every
                # completed run is already durably on disk.
                print(f"  [{arm}#{index}] FAILED:", flush=True)
                traceback.print_exc()
    finally:
        teardown_databases(config, verbosity=0)
    print("\nAll scheduled runs attempted.", flush=True)


if __name__ == "__main__":
    main()
