"""Credit holds (Epic B1a, plan 08 §3.2, §3.4, §3.5).

A hold reserves credits on the wallet (`CreditWallet.reserved_credits`)
before an AI call, so free balance is never shared by two jobs. A charge
draws on its own hold first (`hold_drawn`), may take free balance beyond it
(`overrun_charged`), and absorbs what cannot be billed (`unbilled_credits`);
whatever is left of the hold goes back in the same transaction.

The holder of a hold is a BackgroundProcessingTask (an item) when the work
has a task row, else the job itself (E-4, E-5, SINGLE_CALL, a late door job):
    item_hold_left = hold_credits - hold_drawn - released_credits
    job_hold_left  = reserved_credits - hold_drawn - released_credits

Invariant: wallet.reserved_credits == sum over the wallet's jobs of
(reserved_credits - hold_drawn - released_credits); for a job with items,
its three amounts equal the sums over its items.

Lock order in every transaction: wallet (lock_wallet_first), then the job
row, then the item row. No network I/O happens inside these transactions.
Nothing here is behind BILLING_HOLDS_ENABLED: the callers are (step 3).
"""

import logging
import uuid
from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q, Sum
from django.utils import timezone

from audit import metrics as audit_metrics
from audit.enums import ReasonCode
from students.models import BackgroundProcessingTask, BackgroundTaskStatus

from .errors import InsufficientCreditsError, is_individual_teacher
from .locks import lock_wallet_first
from .models import (
    AIJob,
    AIJobStatus,
    AIJobType,
    BillingSettings,
    CreditWallet,
    HoldState,
    SettlementRecord,
)
from .zero_credit import CREDIT_RESERVE, assert_not_zero_credit

logger = logging.getLogger(__name__)

OPEN_STATUSES = (
    AIJobStatus.ESTIMATED,
    AIJobStatus.RESERVED,
    AIJobStatus.RUNNING,
    AIJobStatus.QUEUED_PENDING_CREDITS,
)
TERMINAL_STATUSES = (
    AIJobStatus.SETTLED,
    AIJobStatus.FAILED,
    AIJobStatus.EXPIRED,
    AIJobStatus.CANCELLED,
)
#: Job types whose hold may have no task row (the job is its own item).
JOB_LEVEL_TYPES = (AIJobType.SINGLE_CALL, AIJobType.SINGLE_ITEM, AIJobType.UPLOAD)
#: The existing refusal text (ai_processor/services.py balance check).
REFUSAL_MESSAGE = "Refill your wallet to continue"

SettlementOutcome = namedtuple(
    "SettlementOutcome",
    "settled chargeable drawn overrun_charged unbilled leftover",
)
NOT_SETTLED = SettlementOutcome(False, 0, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def spendable_credits(wallet):
    """total_remaining_credits() - reserved_credits, floored at 0."""
    current = CreditWallet.objects.get(pk=wallet.pk)
    return _free(current)


def _free(locked_wallet):
    return max(
        0, locked_wallet.total_remaining_credits() - locked_wallet.reserved_credits
    )


def _left(job_row, item_row):
    if item_row is not None:
        return item_row.hold_credits - item_row.hold_drawn - item_row.released_credits
    return job_row.reserved_credits - job_row.hold_drawn - job_row.released_credits


def item_hold_left(job, item=None):
    job_row = AIJob.objects.get(pk=job.pk)
    item_row = (
        BackgroundProcessingTask.objects.get(pk=item.pk) if item is not None else None
    )
    return _left(job_row, item_row)


def _lock_rows(job, item):
    job_row = AIJob.objects.select_for_update().get(pk=job.pk)
    item_row = (
        BackgroundProcessingTask.objects.select_for_update().get(pk=item.pk)
        if item is not None
        else None
    )
    return job_row, item_row


def _refusal(wallet):
    return InsufficientCreditsError(
        REFUSAL_MESSAGE, individual=is_individual_teacher(wallet.user)
    )


def _move_wallet(wallet_pk, delta):
    if delta:
        CreditWallet.objects.filter(pk=wallet_pk).update(
            reserved_credits=F("reserved_credits") + delta
        )


# --------------------------------------------------------------------------
# Jobs and reserving
# --------------------------------------------------------------------------


def create_job(
    *,
    wallet,
    requested_by,
    job_type,
    total_items=1,
    held_per_item=0,
    assignment=None,
    course=None,
    school_id=None,
    trace_id=None,
    meta=None,
):
    """A new ESTIMATED job. Refused inside a zero-credit scope (plan §3.11)."""
    assert_not_zero_credit(CREDIT_RESERVE)
    return AIJob.objects.create(
        wallet=wallet,
        requested_by=requested_by,
        job_type=job_type,
        total_items=total_items,
        held_per_item=held_per_item,
        assignment=assignment,
        course=course,
        school_id=school_id,
        trace_id=trace_id or uuid.uuid4(),
        meta=meta or {},
    )


def _reserve_locked(locked_wallet, amount, job, item):
    """The conditional UPDATE (plan §3.2 step 3); the wallet row is locked."""
    total = locked_wallet.total_remaining_credits()
    granted = CreditWallet.objects.filter(
        pk=locked_wallet.pk, reserved_credits__lte=total - amount
    ).update(reserved_credits=F("reserved_credits") + amount)
    if not granted:
        return False
    AIJob.objects.filter(pk=job.pk).update(
        reserved_credits=F("reserved_credits") + amount,
        # A timestamp only, never a fencing token (review r1 M2).
        reservation_claimed_at=timezone.now(),
    )
    if item is not None:
        BackgroundProcessingTask.objects.filter(pk=item.pk).update(
            hold_credits=F("hold_credits") + amount, hold_state=HoldState.HELD
        )
    return True


def reserve(wallet, amount, job, item=None):
    """Hold `amount` for `job` (and on `item`, when given). True if granted.
    One short transaction, no network I/O."""
    assert_not_zero_credit(CREDIT_RESERVE)
    if amount <= 0:
        raise ValueError("a hold must be a positive number of credits")
    with transaction.atomic():
        locked = lock_wallet_first(wallet)
        return _reserve_locked(locked, amount, job, item)


def ensure_call_fits(job, estimate, *, item=None, tally=0):
    """The pre-call fit check (plan §3.4): against the holder's own hold less
    what this unit already used. Reserves any shortfall; raises
    InsufficientCreditsError before the call if it cannot. Returns the
    shortfall reserved (0 when the hold covers it)."""
    with transaction.atomic():
        locked = lock_wallet_first(job.wallet)
        job_row, item_row = _lock_rows(job, item)
        available = _left(job_row, item_row) - tally
        if estimate <= available:
            return 0
        shortfall = estimate - max(available, 0)
        assert_not_zero_credit(CREDIT_RESERVE)
        if not _reserve_locked(locked, shortfall, job_row, item_row):
            raise _refusal(locked)
        return shortfall


# --------------------------------------------------------------------------
# Settling, failing, finishing
# --------------------------------------------------------------------------


def settle(
    job,
    actual,
    *,
    item=None,
    key=None,
    feature=None,
    task_type=None,
    course=None,
):
    """Charge `actual` once per settlement key (plan §3.2 settle).

    key: (item_id, retry_count). Default: the item's task row and its
    retry_count, or (job.id, 0) for a job with no task row. A duplicate key
    writes nothing and returns NOT_SETTLED.
    """
    if actual < 0:
        raise ValueError("actual cost cannot be negative")
    with transaction.atomic():
        locked = lock_wallet_first(job.wallet)
        job_row, item_row = _lock_rows(job, item)
        if key is None:
            key = (
                (item_row.id, item_row.retry_count)
                if item_row is not None
                else (job_row.id, 0)
            )
        try:
            with transaction.atomic():
                record = SettlementRecord.objects.create(
                    job=job_row, item_id=key[0], retry_count=key[1]
                )
        except IntegrityError:
            return NOT_SETTLED

        # An item the sweep already returned and counted (plan R4-1).
        already_counted = (
            item_row is not None and item_row.hold_state == HoldState.RETURNED
        )
        left = max(_left(job_row, item_row), 0)
        chargeable = min(actual, left + _free(locked))
        if chargeable > 0:
            locked.consume_credits(
                chargeable,
                feature=feature,
                task_type=task_type,
                task_id=str(record.id),
                course=course,
            )
        drawn = min(left, chargeable)
        leftover = left - drawn
        overrun_charged = chargeable - drawn
        unbilled = actual - chargeable

        SettlementRecord.objects.filter(pk=record.pk).update(credits_charged=chargeable)
        _move_wallet(locked.pk, -(drawn + leftover))
        AIJob.objects.filter(pk=job_row.pk).update(
            consumed_credits=F("consumed_credits") + chargeable,
            hold_drawn=F("hold_drawn") + drawn,
            released_credits=F("released_credits") + leftover,
            overrun_charged=F("overrun_charged") + overrun_charged,
            unbilled_credits=F("unbilled_credits") + unbilled,
            completed_items=F("completed_items") + (0 if already_counted else 1),
        )
        if item_row is not None:
            BackgroundProcessingTask.objects.filter(pk=item_row.pk).update(
                hold_drawn=F("hold_drawn") + drawn,
                released_credits=F("released_credits") + leftover,
                hold_state=HoldState.RETURNED,
            )

    tags = {"job_type": job_row.job_type}
    if overrun_charged:
        audit_metrics.count("credit_overrun_charged", overrun_charged, tags=tags)
    if unbilled:
        audit_metrics.count("credit_overrun", unbilled, tags=tags)
    return SettlementOutcome(
        True, chargeable, drawn, overrun_charged, unbilled, leftover
    )


def fail_item(job, *, item=None):
    """No charge; return the holder's whole remaining hold and count the
    failure (plan §3.4 on an exception). Returns the credits returned."""
    with transaction.atomic():
        locked = lock_wallet_first(job.wallet)
        job_row, item_row = _lock_rows(job, item)
        count_it = item_row is None or item_row.hold_state == HoldState.HELD
        amount = max(_left(job_row, item_row), 0)
        _move_wallet(locked.pk, -amount)
        AIJob.objects.filter(pk=job_row.pk).update(
            released_credits=F("released_credits") + amount,
            failed_items=F("failed_items") + (1 if count_it else 0),
        )
        if item_row is not None:
            BackgroundProcessingTask.objects.filter(pk=item_row.pk).update(
                released_credits=F("released_credits") + amount,
                hold_state=HoldState.RETURNED,
            )
        return amount


def finish_job(job, to_status):
    """Move an open job to a terminal status and return what is left of its
    hold (plan §3.2 release). Idempotent through the conditional status
    UPDATE: returns None if the job was not open."""
    if to_status not in TERMINAL_STATUSES:
        raise ValueError(f"{to_status} is not a terminal status")
    with transaction.atomic():
        locked = lock_wallet_first(job.wallet)
        moved = AIJob.objects.filter(pk=job.pk, status__in=OPEN_STATUSES).update(
            status=to_status
        )
        if not moved:
            return None
        job_row = AIJob.objects.select_for_update().get(pk=job.pk)
        items = BackgroundProcessingTask.objects.select_for_update().filter(job=job_row)
        if items.exists():
            # Each held item returns its own remainder (per-item invariant).
            returned = 0
            for item_row in items.filter(hold_state=HoldState.HELD):
                amount = max(_left(job_row, item_row), 0)
                BackgroundProcessingTask.objects.filter(pk=item_row.pk).update(
                    released_credits=F("released_credits") + amount,
                    hold_state=HoldState.RETURNED,
                )
                returned += amount
        else:
            returned = max(_left(job_row, None), 0)
        _move_wallet(locked.pk, -returned)
        AIJob.objects.filter(pk=job_row.pk).update(
            released_credits=F("released_credits") + returned
        )
        return returned


# --------------------------------------------------------------------------
# Starting, late messages
# --------------------------------------------------------------------------


def claim_item_start(item):
    """The start claim at unit entry (review r4 R4-1): one conditional UPDATE.
    False means the hold was returned: the run exits as a late message."""
    return (
        BackgroundProcessingTask.objects.filter(
            pk=item.pk, hold_state=HoldState.HELD
        ).update(started_at=timezone.now())
        == 1
    )


def already_settled(item):
    """A late door item whose file already settled exits (review r4 m-5)."""
    return SettlementRecord.objects.filter(
        item_id=item.id, retry_count=item.retry_count
    ).exists()


def _open_successor_job(old_job, item, amount, job_type, link_key):
    """A new job-level job for `item`'s work, linked to `old_job` by its
    trace_id (03a: shared with AuditEvent rows and every item) and by ids in
    meta. It holds `amount` or is left FAILED and InsufficientCreditsError is
    raised. The old job and the task row are untouched; settle it with
    key=(item.id, item.retry_count)."""
    new_job = create_job(
        wallet=old_job.wallet,
        requested_by=old_job.requested_by,
        job_type=job_type,
        total_items=1,
        assignment=old_job.assignment,
        course=old_job.course,
        school_id=old_job.school_id,
        trace_id=old_job.trace_id,
        meta={link_key: str(old_job.id), "task_id": str(item.id)},
    )
    if reserve(old_job.wallet, amount, new_job):
        AIJob.objects.filter(pk=new_job.pk).update(status=AIJobStatus.RESERVED)
        return AIJob.objects.get(pk=new_job.pk)
    AIJob.objects.filter(pk=new_job.pk).update(status=AIJobStatus.FAILED)
    raise _refusal(CreditWallet.objects.get(pk=old_job.wallet_id))


def open_late_door_job(old_job, item, amount):
    """A door item whose hold was returned runs under a NEW UPLOAD job with
    its own hold (plan §3.2); refused if it cannot hold (owner ruling
    2026-10-30)."""
    return _open_successor_job(old_job, item, amount, AIJobType.UPLOAD, "late_door_of")


def open_retry_job(old_job, item, amount):
    """An item retry (E-2) runs under a NEW SINGLE_ITEM job keyed on the same
    task row with its new retry_count (SM ruling 2026-11-06: plan §3.4 E-2
    over §3.2); the old job's counts and the task's hold columns are
    untouched."""
    return _open_successor_job(old_job, item, amount, AIJobType.SINGLE_ITEM, "retry_of")


# --------------------------------------------------------------------------
# The stale-hold sweep
# --------------------------------------------------------------------------


def _time_limit_seconds():
    from students.services import GRADING_TASK_TIME_LIMIT_SECONDS

    return GRADING_TASK_TIME_LIMIT_SECONDS


def _sweep_margin():
    """Plan §3.2 "plus a margin"; editable (SM ruling 2026-11-06)."""
    return timedelta(minutes=BillingSettings.load().hold_sweep_margin_minutes)


def item_sweep_window():
    """visibility_timeout + the grading time limit + the margin: before that
    Celery may still redeliver a started item (plan §3.2, review r3 R3-3a)."""
    visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS.get(
        "visibility_timeout", 3600
    )
    return timedelta(seconds=int(visibility) + _time_limit_seconds()) + _sweep_margin()


def single_sweep_window():
    """A job with no task row has no broker message: the time limit plus the
    margin from its creation (plan §3.2)."""
    return timedelta(seconds=_time_limit_seconds()) + _sweep_margin()


def _sweep_item(item_pk, job_pk, cutoff):
    job = AIJob.objects.get(pk=job_pk)
    with transaction.atomic():
        locked = lock_wallet_first(job.wallet)
        job_row = AIJob.objects.select_for_update().get(pk=job_pk)
        # Conditional: a run that re-claimed the item since it was read is
        # not swept (review r4 R4-1).
        claimed = BackgroundProcessingTask.objects.filter(
            pk=item_pk, hold_state=HoldState.HELD, started_at__lt=cutoff
        ).update(hold_state=HoldState.RETURNED)
        if not claimed:
            return None
        item_row = BackgroundProcessingTask.objects.select_for_update().get(pk=item_pk)
        amount = max(_left(job_row, item_row), 0)
        _move_wallet(locked.pk, -amount)
        BackgroundProcessingTask.objects.filter(pk=item_pk).update(
            released_credits=F("released_credits") + amount,
            status=BackgroundTaskStatus.FAILURE,
            # Retryable; its existing text says no credits were charged.
            reason_code=ReasonCode.PROVIDER_FAILURE,
            finished_at=timezone.now(),
        )
        AIJob.objects.filter(pk=job_pk).update(
            released_credits=F("released_credits") + amount,
            failed_items=F("failed_items") + 1,
        )
        return amount


def sweep_stale_holds(now=None):
    """Return holds whose run can no longer be in progress.

    Items: only STARTED items past item_sweep_window() with no settlement;
    launched-but-unstarted items are never swept (a broker backlog is
    normal). Jobs with no task row: past single_sweep_window() from creation
    with no settlement, finished FAILED."""
    now = now or timezone.now()
    cutoff = now - item_sweep_window()
    settled = SettlementRecord.objects.filter(
        item_id=OuterRef("pk"), retry_count=OuterRef("retry_count")
    )
    candidates = list(
        BackgroundProcessingTask.objects.filter(
            hold_state=HoldState.HELD,
            started_at__isnull=False,
            started_at__lt=cutoff,
            job__isnull=False,
        )
        .exclude(Exists(settled))
        .values_list("pk", "job_id")
    )
    items_swept = credits_returned = 0
    for item_pk, job_pk in candidates:
        amount = _sweep_item(item_pk, job_pk, cutoff)
        if amount is not None:
            items_swept += 1
            credits_returned += amount

    jobs_released = 0
    stale_jobs = (
        AIJob.objects.filter(
            status__in=(AIJobStatus.RESERVED, AIJobStatus.RUNNING),
            job_type__in=JOB_LEVEL_TYPES,
            created_at__lt=now - single_sweep_window(),
        )
        .exclude(Exists(BackgroundProcessingTask.objects.filter(job=OuterRef("pk"))))
        .exclude(Exists(SettlementRecord.objects.filter(job=OuterRef("pk"))))
    )
    for job in list(stale_jobs):
        returned = finish_job(job, AIJobStatus.FAILED)
        if returned is not None:
            jobs_released += 1
            credits_returned += returned

    return {
        "items_swept": items_swept,
        "credits_returned": credits_returned,
        "jobs_released": jobs_released,
    }


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


def check_invariants(wallet=None):
    """Problems as strings (empty when the books agree). Reports only, never
    repairs (plan §3.2)."""
    if wallet is not None:
        wallets = CreditWallet.objects.filter(pk=wallet.pk)
    else:
        wallets = CreditWallet.objects.filter(
            Q(reserved_credits__gt=0) | Q(ai_jobs__isnull=False)
        ).distinct()
    problems = []
    for current in wallets:
        sums = AIJob.objects.filter(wallet=current).aggregate(
            reserved=Sum("reserved_credits"),
            drawn=Sum("hold_drawn"),
            released=Sum("released_credits"),
        )
        expected = (
            (sums["reserved"] or 0) - (sums["drawn"] or 0) - (sums["released"] or 0)
        )
        if current.reserved_credits != expected:
            problems.append(
                f"hold_drift wallet={current.pk} "
                f"reserved={current.reserved_credits} expected={expected}"
            )
        jobs_with_items = AIJob.objects.filter(wallet=current).filter(
            Exists(BackgroundProcessingTask.objects.filter(job=OuterRef("pk")))
        )
        for job in jobs_with_items:
            items = BackgroundProcessingTask.objects.filter(job=job)
            item_sums = items.aggregate(
                held=Sum("hold_credits"),
                drawn=Sum("hold_drawn"),
                released=Sum("released_credits"),
            )
            if (job.reserved_credits, job.hold_drawn, job.released_credits) != (
                item_sums["held"] or 0,
                item_sums["drawn"] or 0,
                item_sums["released"] or 0,
            ):
                problems.append(f"item_sum job={job.pk}")
            negative = items.filter(
                hold_credits__lt=F("hold_drawn") + F("released_credits")
            )
            for item_pk in negative.values_list("pk", flat=True):
                problems.append(f"item_negative item={item_pk}")
    return problems
