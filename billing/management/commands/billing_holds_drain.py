"""billing_holds_drain: the drain before credit holds are turned off (Epic
B1b-9, 08b §6.3; runbook docs/ops/billing_holds_drain.md).

The normal drain needs BILLING_HOLDS_ENABLED and BILLING_HOLDS_DRAINING both
on. Each round it expires every queued batch job (no email while draining),
then reports what is still open; it waits for running work to settle, fail
or be swept. It prints DRAINED (exit 0) when no wallet holds credits, no job
is open, no item is held and check_invariants() is empty; otherwise, at the
timeout, it exits 1 naming the wallets and jobs still open.

--flag-off-emergency needs the flag OFF and DRAINING on. It returns every
hold at once through the sweep with force=True (the only caller allowed to
pass it), finishes every open job, then verifies the same way.

Running it on staging or production is an owner go every time. It has no
Beat or cron entry.
"""

from datetime import timedelta
from time import monotonic, sleep

from django.core.management.base import BaseCommand, CommandError

from billing import holds
from billing.batch_queue import expire_job
from billing.models import AIJob, AIJobStatus, AIJobType, CreditWallet, HoldState
from students.models import BackgroundProcessingTask

DEFAULT_INTERVAL_SECONDS = 30
EXIT_NOT_DRAINED = 1
EXIT_REFUSED = 2
DRAINED = "DRAINED"


def default_timeout():
    """Q9-5: the item sweep window plus 10 minutes."""
    return holds.item_sweep_window() + timedelta(minutes=10)


def expire_queued_jobs():
    """Expire every queued batch job now; returns how many."""
    queued = AIJob.objects.filter(
        job_type=AIJobType.BATCH_GRADING,
        status=AIJobStatus.QUEUED_PENDING_CREDITS,
    )
    return sum(1 for job in list(queued) if expire_job(job))


def open_state():
    """What is still open, as plain ids and numbers."""
    return {
        "wallets": list(
            CreditWallet.objects.filter(reserved_credits__gt=0)
            .order_by("pk")
            .values_list("pk", "reserved_credits")
        ),
        "jobs": list(
            AIJob.objects.filter(status__in=holds.OPEN_STATUSES)
            .order_by("created_at")
            .values_list("pk", "job_type", "status", "wallet_id")
        ),
        "held_items": list(
            BackgroundProcessingTask.objects.filter(hold_state=HoldState.HELD)
            .order_by("pk")
            .values_list("pk", "job_id")
        ),
        "problems": holds.check_invariants(),
    }


def is_drained(state):
    return not any(state.values())


def describe(state):
    lines = [
        f"Open: wallets={len(state['wallets'])} jobs={len(state['jobs'])} "
        f"held_items={len(state['held_items'])} problems={len(state['problems'])}"
    ]
    lines += [f"  wallet {pk} reserved={amount}" for pk, amount in state["wallets"]]
    lines += [
        f"  job {pk} {job_type} {status} wallet={wallet}"
        for pk, job_type, status, wallet in state["jobs"]
    ]
    lines += [f"  held item {pk} job={job}" for pk, job in state["held_items"]]
    lines += [f"  problem {problem}" for problem in state["problems"]]
    return "\n".join(lines)


class Command(BaseCommand):
    help = (
        "Drain credit holds before BILLING_HOLDS_ENABLED is turned off "
        "(08b §6.3). Needs BILLING_HOLDS_DRAINING. An owner go every time on "
        "staging or production."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=DEFAULT_INTERVAL_SECONDS,
            help="Seconds between checks (default 30).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=None,
            help=(
                "Seconds to wait before exiting 1 (default: the item sweep "
                "window plus 10 minutes)."
            ),
        )
        parser.add_argument(
            "--flag-off-emergency",
            action="store_true",
            help=(
                "With the flag already OFF on an explicit order (recorded in "
                "NOTES): return every remaining hold now."
            ),
        )

    def handle(self, *args, **options):
        if options["flag_off_emergency"]:
            self.emergency()
            return
        if not (holds.holds_enabled() and holds.holds_draining()):
            raise CommandError(
                "Refused: billing_holds_drain needs BILLING_HOLDS_ENABLED and "
                "BILLING_HOLDS_DRAINING both on in the running settings.",
                returncode=EXIT_REFUSED,
            )
        timeout = options["timeout"]
        if timeout is None:
            timeout = default_timeout().total_seconds()
        deadline = monotonic() + timeout
        while True:
            expired = expire_queued_jobs()
            if expired:
                self.stdout.write(f"Expired {expired} queued batch job(s).")
            state = open_state()
            if is_drained(state):
                self.stdout.write(DRAINED)
                return
            self.stdout.write(describe(state))
            if monotonic() >= deadline:
                raise CommandError(
                    f"Not drained after {int(timeout)} s; the wallets and jobs "
                    "above are still open.",
                    returncode=EXIT_NOT_DRAINED,
                )
            sleep(options["interval"])

    def emergency(self):
        if holds.holds_enabled() or not holds.holds_draining():
            raise CommandError(
                "Refused: --flag-off-emergency needs BILLING_HOLDS_ENABLED off "
                "and BILLING_HOLDS_DRAINING on in the running settings.",
                returncode=EXIT_REFUSED,
            )
        self.stdout.write(
            "Flag-off emergency: record who ordered it, when and why in NOTES."
        )
        expired = expire_queued_jobs()
        swept = holds.sweep_stale_holds(force=True)
        finished = 0
        for job in list(AIJob.objects.filter(status__in=holds.OPEN_STATUSES)):
            if job.status == AIJobStatus.QUEUED_PENDING_CREDITS:
                ended = expire_job(job)
            else:
                ended = holds.finish_job(job, AIJobStatus.FAILED) is not None
            finished += int(bool(ended))
        self.stdout.write(
            f"Expired {expired} queued job(s); returned {swept['items_swept']} "
            f"item hold(s) and {swept['jobs_released']} job hold(s), "
            f"{swept['credits_returned']} credits; finished {finished} open "
            "job(s) FAILED."
        )
        state = open_state()
        if is_drained(state):
            self.stdout.write(DRAINED)
            return
        self.stdout.write(describe(state))
        raise CommandError(
            "Not drained after the emergency return; the wallets and jobs "
            "above are still open.",
            returncode=EXIT_NOT_DRAINED,
        )
