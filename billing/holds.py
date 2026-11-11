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

import contextlib
import contextvars
import logging
import uuid
from collections import namedtuple
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q, Sum
from django.utils import timezone

from ai_processor.exceptions import NOT_CHARGED, ProviderFailureError
from audit import metrics as audit_metrics
from audit.context import current_request_actor
from audit.emitter import emit
from audit.enums import AuditAction, ReasonCode
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
    "settled chargeable drawn overrun_charged unbilled leftover record_id",
)
NOT_SETTLED = SettlementOutcome(False, 0, 0, 0, 0, 0, None)


def holds_enabled():
    return bool(getattr(settings, "BILLING_HOLDS_ENABLED", False))


def holds_draining():
    """B1b-9 (08b §6.3): BILLING_HOLDS_DRAINING, whatever the flag."""
    return bool(getattr(settings, "BILLING_HOLDS_DRAINING", False))


def holds_for_new_work():
    """Whether NEW work takes holds: the flag on and not draining. What is
    already held keeps the holds path while draining (holds_enabled)."""
    return holds_enabled() and not holds_draining()


def call_runs_without_hold():
    """Whether a provider call outside or inside a unit runs the flag-off
    path: the flag off, or draining with no held unit open (B1b-9 Q9-1)."""
    if not holds_enabled():
        return True
    return holds_draining() and current_unit() is None


def flag_off_balance(wallet):
    """What the flag-off balance check compares with. B1b-9 Q9-2 (SM ruling,
    a departure from 08b's "exactly as flag off"): while draining with the
    flag on, holds are still open, so spendable; otherwise the total."""
    if holds_enabled():
        return spendable_credits(wallet)
    return wallet.total_remaining_credits()


def warn_if_draining_with_holds_off():
    """B1b-9 Q9-6: DRAINING with the flag off is the emergency state only;
    said once at start-up (BillingConfig.ready)."""
    if holds_draining() and not holds_enabled():
        logger.warning(
            "BILLING_HOLDS_DRAINING is on with BILLING_HOLDS_ENABLED off: the "
            "flag-off emergency state. Run billing_holds_drain "
            "--flag-off-emergency, then unset BILLING_HOLDS_DRAINING."
        )


def _audit(action, job, *, item=None, **metadata):
    """One FINANCIAL audit row about `job` (SM ruling 2026-11-06). Ids and
    amounts only; never raises into the caller (emit's own contract)."""
    fields = {"job_type": str(job.job_type), **metadata}
    if item is not None:
        fields["item_id"] = str(item.pk)
    emit(
        action,
        actor=current_request_actor(),
        target_type="AIJob",
        target_id=job.pk,
        school_id=job.school_id,
        metadata=fields,
    )


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


def _lock_wallet_of(job):
    """lock_wallet_first for `job`'s wallet, or None when that wallet was
    deleted (NOTES §21n): a stale instance's wallet is gone from the
    database, a fresh one's wallet_id is NULL. Only a terminal job has no
    wallet (release_open_holds_on_delete), so None means nothing may move.
    The lock read is the check: no query is added while the wallet exists.

    B1b-3 P2 (NOTES §3.3): locked by job.wallet_id, so a job whose wallet is
    not cached costs no extra read of it, and with its user, which the
    consume path's cache signal reads; of=("self",) locks the wallet row
    only. The same row is locked as lock_wallet_first(job.wallet) did."""
    if job.wallet_id is not None:
        locked = (
            CreditWallet.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(pk=job.wallet_id)
            .first()
        )
        if locked is not None:
            return locked
    logger.warning("Credit hold job %s: its wallet was deleted; nothing moved.", job.pk)
    return None


def _refuse_walletless(job):
    """No provider call and no successor on a job whose wallet was deleted:
    the existing late-message error, which names no wallet (NOTES §21n)."""
    return _late(f"job {job.pk}: its wallet was deleted")


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


def _reserve_locked(locked_wallet, amount, job, item, status=None):
    """The conditional UPDATE (plan §3.2 step 3); the wallet row is locked.

    status (B1b-3 P5): begin_single_call's RUNNING for the new job it just
    made, written in the same job UPDATE instead of one of its own."""
    total = locked_wallet.total_remaining_credits()
    granted = CreditWallet.objects.filter(
        pk=locked_wallet.pk, reserved_credits__lte=total - amount
    ).update(reserved_credits=F("reserved_credits") + amount)
    if not granted:
        return False
    AIJob.objects.filter(pk=job.pk).update(
        status=status if status is not None else F("status"),
        reserved_credits=F("reserved_credits") + amount,
        # A timestamp only, never a fencing token (review r1 M2).
        reservation_claimed_at=timezone.now(),
    )
    if item is not None:
        BackgroundProcessingTask.objects.filter(pk=item.pk).update(
            hold_credits=F("hold_credits") + amount, hold_state=HoldState.HELD
        )
    _audit(AuditAction.CREDIT_HOLD_RESERVED, job, item=item, credits=amount)
    return True


def claim_batch_rows_locked(locked_wallet, job_row, rows):
    """B1b-6 (plan §3.7, D6-b): hold and claim as many of `rows` (in order)
    as the spendable balance fits whole, held_per_item each; each claim is a
    conditional UPDATE hold_state NULL -> HELD on a PENDING row. The wallet
    is locked. Returns the claimed rows."""
    per = job_row.held_per_item
    fit = _free(locked_wallet) // per if per > 0 else len(rows)
    k = min(len(rows), fit)
    if k <= 0:
        return []
    if not _reserve_locked(locked_wallet, k * per, job_row, None):
        return []
    claimed = []
    for row in rows[:k]:
        took = BackgroundProcessingTask.objects.filter(
            pk=row.pk,
            job_id=job_row.pk,
            status=BackgroundTaskStatus.PENDING,
            hold_state__isnull=True,
        ).update(hold_state=HoldState.HELD, hold_credits=per)
        if took:
            claimed.append(row)
    unused = (k - len(claimed)) * per
    if unused:
        _move_wallet(locked_wallet.pk, -unused)
        AIJob.objects.filter(pk=job_row.pk).update(
            reserved_credits=F("reserved_credits") - unused
        )
    return claimed


def reserve(wallet, amount, job, item=None, status=None):
    """Hold `amount` for `job` (and on `item`, when given). True if granted.
    One short transaction, no network I/O."""
    assert_not_zero_credit(CREDIT_RESERVE)
    if amount <= 0:
        raise ValueError("a hold must be a positive number of credits")
    with transaction.atomic():
        locked = lock_wallet_first(wallet)
        return _reserve_locked(locked, amount, job, item, status=status)


def ensure_call_fits(job, estimate, *, item=None, tally=0):
    """The pre-call fit check (plan §3.4): against the holder's own hold less
    what this unit already used. Reserves any shortfall; raises
    InsufficientCreditsError before the call if it cannot. Returns the
    shortfall reserved (0 when the hold covers it)."""
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            raise _refuse_walletless(job)
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
    school=None,
    record_id=None,
    close_status=None,
):
    """Charge `actual` once per settlement key (plan §3.2 settle).

    key: (item_id, retry_count). Default: the item's task row and its
    retry_count, or (job.id, 0) for a job with no task row. A duplicate key
    writes nothing and returns NOT_SETTLED.

    record_id: the usage id the run's calls already noted (NOTES §15i): the
    settlement record takes it, so the usage rows and the call rows share it
    without the call rows ever being updated.

    close_status (B1b-3 P1, NOTES §3.3a): finish_single_call's SETTLED. The
    job-counter UPDATE also closes the job, when the job row read under its
    lock is open: finish_job's conditional status write, for a SINGLE_CALL
    job whose remainder this settle has already returned in full.
    """
    if actual < 0:
        raise ValueError("actual cost cannot be negative")
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return NOT_SETTLED
        job_row, item_row = _lock_rows(job, item)
        if key is None:
            key = (
                (item_row.id, item_row.retry_count)
                if item_row is not None
                else (job_row.id, 0)
            )
        # An item the sweep already returned and counted (plan R4-1).
        already_counted = (
            item_row is not None and item_row.hold_state == HoldState.RETURNED
        )
        left = max(_left(job_row, item_row), 0)
        chargeable = min(actual, left + _free(locked))
        # B1b-3 P4: the record is written with what it charges; it is still
        # the fence, and nothing is consumed before it exists.
        try:
            with transaction.atomic():
                record = SettlementRecord.objects.create(
                    job=job_row,
                    item_id=key[0],
                    retry_count=key[1],
                    credits_charged=chargeable,
                    **({"id": record_id} if record_id else {}),
                )
        except IntegrityError:
            return NOT_SETTLED

        if chargeable > 0:
            locked.consume_credits(
                chargeable,
                feature=feature,
                task_type=task_type,
                task_id=str(record.id),
                course=course,
                school=school,
                _wallet_locked=True,  # B1b-3 P3: locked and read above
            )
        drawn = min(left, chargeable)
        leftover = left - drawn
        overrun_charged = chargeable - drawn
        unbilled = actual - chargeable

        _move_wallet(locked.pk, -(drawn + leftover))
        closing = close_status is not None and job_row.status in OPEN_STATUSES
        close = close_status if closing else F("status")
        if closing and close_status == AIJobStatus.SETTLED:
            transaction.on_commit(partial(emit_estimate_gap, job_row.pk))
        if chargeable > 0:
            # B1b-7 (plan §3.9): the low-balance check, after the charge
            # commits.
            transaction.on_commit(partial(_check_low_balance, locked.pk))
        AIJob.objects.filter(pk=job_row.pk).update(
            status=close,
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
        _audit(
            AuditAction.CREDIT_HOLD_SETTLED,
            job_row,
            item=item_row,
            credits=chargeable,
            credits_actual=actual,
            credits_released=leftover,
        )

    if item_row is not None and not already_counted:
        _after_batch_item(job_row)
    tags = {"job_type": job_row.job_type}
    if overrun_charged:
        audit_metrics.count("credit_overrun_charged", overrun_charged, tags=tags)
    if unbilled:
        audit_metrics.count("credit_overrun", unbilled, tags=tags)
    return SettlementOutcome(
        True, chargeable, drawn, overrun_charged, unbilled, leftover, record.id
    )


def fail_item(job, *, item=None):
    """No charge; return the holder's whole remaining hold and count the
    failure (plan §3.4 on an exception). Returns the credits returned."""
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return 0
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
        if amount > 0:
            _audit(
                AuditAction.CREDIT_HOLD_RELEASED,
                job_row,
                item=item_row,
                credits=amount,
                reason_label="failed",
            )
    if item_row is not None and count_it:
        _after_batch_item(job_row)
    return amount


def _check_low_balance(wallet_pk):
    from .low_balance import check_low_balance

    check_low_balance(wallet_pk)


def gap_bucket(actual, p50):
    """B1b-7 (NOTES §7.10, SM Q7-5): actual / p50, bucketed. Integer
    compares, so the edges are exact: <0.5, [0.5, 0.8), [0.8, 1.2],
    (1.2, 2], >2."""
    if actual * 10 < 5 * p50:
        return "<0.5"
    if actual * 10 < 8 * p50:
        return "0.5-0.8"
    if actual * 10 <= 12 * p50:
        return "0.8-1.2"
    if actual <= 2 * p50:
        return "1.2-2"
    return ">2"


def emit_estimate_gap(job_pk):
    """B1b-7 (plan §3.10, NFR-OBS-03): once per job that settles with an
    estimate. The actual cost is consumed + unbilled credits. Internal
    metrics; no alert in B1b."""
    job_row = AIJob.objects.filter(pk=job_pk).first()
    if job_row is None or not job_row.estimate_p50 or job_row.estimate_p50 <= 0:
        return
    actual = job_row.consumed_credits + job_row.unbilled_credits
    audit_metrics.count(
        "credit_estimate_gap", tags={"bucket": gap_bucket(actual, job_row.estimate_p50)}
    )
    hit = job_row.estimate_p90 is not None and actual <= job_row.estimate_p90
    audit_metrics.count(
        "credit_estimate_band_hit", tags={"hit": "true" if hit else "false"}
    )


def _note_settled(job_pk):
    """B1b-7 D7-e: the request's settled-job record (billing.job_ref)."""
    from .job_ref import note_settled

    note_settled(job_pk)


def _after_batch_item(job_row):
    """B1b-6: the last-item helper, for a BATCH_GRADING job and (door-close
    fix) an UPLOAD door job; no query for any other job type."""
    if job_row.job_type in (AIJobType.BATCH_GRADING, AIJobType.UPLOAD):
        from .batch_queue import decide_after_item

        decide_after_item(job_row.pk)


def finish_job(job, to_status, from_statuses=OPEN_STATUSES):
    """Move an open job to a terminal status and return what is left of its
    hold (plan §3.2 release). Idempotent through the conditional status
    UPDATE: returns None if the job was not open.

    from_statuses (B1b-6): the statuses the move is conditional on; expiry
    passes QUEUED_PENDING_CREDITS only, so it never ends a promoted job."""
    if to_status not in TERMINAL_STATUSES:
        raise ValueError(f"{to_status} is not a terminal status")
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return None
        moved = AIJob.objects.filter(pk=job.pk, status__in=from_statuses).update(
            status=to_status
        )
        if not moved:
            return None
        if to_status == AIJobStatus.SETTLED:
            # B1b-7 (plan §3.10): this caller won the move, so the gap metric
            # is emitted once per settled job.
            transaction.on_commit(partial(emit_estimate_gap, job.pk))
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
        if returned > 0:
            _audit(
                AuditAction.CREDIT_HOLD_RELEASED,
                job_row,
                credits=returned,
                reason_label=str(to_status).lower(),
            )
        return returned


# --------------------------------------------------------------------------
# Starting, late messages
# --------------------------------------------------------------------------


def detach_item(item):
    """B1b-6 (Q10): take a finished, unheld row out of its job so a new batch
    job can adopt it (the H-215a retry). The old job keeps the row's numbers
    in deleted_items_*, as for a deleted row, so its invariant still adds
    up; the row's hold columns start again at zero. Returns the row."""
    with transaction.atomic():
        if item.job_id is not None:
            job = AIJob.objects.filter(pk=item.job_id).first()
            if job is not None and _lock_wallet_of(job) is not None:
                fresh = BackgroundProcessingTask.objects.select_for_update().get(
                    pk=item.pk
                )
                if fresh.hold_state == HoldState.HELD:
                    raise ValueError("a held row cannot leave its job")
                AIJob.objects.filter(pk=job.pk).update(
                    deleted_items_held=F("deleted_items_held") + fresh.hold_credits,
                    deleted_items_drawn=F("deleted_items_drawn") + fresh.hold_drawn,
                    deleted_items_released=F("deleted_items_released")
                    + fresh.released_credits,
                )
        BackgroundProcessingTask.objects.filter(pk=item.pk).update(
            job=None,
            hold_state=None,
            hold_credits=0,
            hold_drawn=0,
            released_credits=0,
        )
    item.refresh_from_db()
    return item


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
    key=(item.id, item.retry_count). A job whose wallet was deleted gets no
    successor (NOTES §21n)."""
    if (
        old_job.wallet_id is None
        or not CreditWallet.objects.filter(pk=old_job.wallet_id).exists()
    ):
        logger.warning(
            "Credit hold job %s: its wallet was deleted; no successor.", old_job.pk
        )
        raise _refuse_walletless(old_job)
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
    # amount 0: no up-front hold; each call's pre-call check reserves its
    # estimate (SM ruling Q1, B1a).
    if amount <= 0 or reserve(old_job.wallet, amount, new_job):
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


def _sweep_item(item_pk, job_pk, cutoff, force=False):
    job = AIJob.objects.get(pk=job_pk)
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return None
        job_row = AIJob.objects.select_for_update().get(pk=job_pk)
        # Conditional: a run that re-claimed the item since it was read is
        # not swept (review r4 R4-1).
        held = BackgroundProcessingTask.objects.filter(
            pk=item_pk, hold_state=HoldState.HELD
        )
        if not force:
            held = held.filter(started_at__lt=cutoff)
        claimed = held.update(hold_state=HoldState.RETURNED)
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
        _audit(
            AuditAction.CREDIT_HOLD_SWEPT,
            job_row,
            item=item_row,
            credits=amount,
            reason_label="stale",
        )
    _after_batch_item(job_row)
    return amount


def sweep_stale_holds(now=None, *, force=False):
    """Return holds whose run can no longer be in progress.

    Items: only STARTED items past item_sweep_window() with no settlement;
    launched-but-unstarted items are never swept (a broker backlog is
    normal). Jobs with no task row: past single_sweep_window() from creation
    with no settlement, finished FAILED.

    force (B1b-9 Q9-3): every HELD item, started or not, and every such job,
    with no window. Passed ONLY by billing_holds_drain --flag-off-emergency
    (a test guards that)."""
    now = now or timezone.now()
    cutoff = now - item_sweep_window()
    settled = SettlementRecord.objects.filter(
        item_id=OuterRef("pk"), retry_count=OuterRef("retry_count")
    )
    held = BackgroundProcessingTask.objects.filter(
        hold_state=HoldState.HELD, job__isnull=False
    )
    if not force:
        held = held.filter(started_at__isnull=False, started_at__lt=cutoff)
    candidates = list(held.exclude(Exists(settled)).values_list("pk", "job_id"))
    items_swept = credits_returned = 0
    for item_pk, job_pk in candidates:
        amount = _sweep_item(item_pk, job_pk, cutoff, force=force)
        if amount is not None:
            items_swept += 1
            credits_returned += amount

    jobs_released = 0
    open_jobs = AIJob.objects.filter(
        status__in=(AIJobStatus.RESERVED, AIJobStatus.RUNNING),
        job_type__in=JOB_LEVEL_TYPES,
    )
    if not force:
        open_jobs = open_jobs.filter(created_at__lt=now - single_sweep_window())
    stale_jobs = open_jobs.exclude(
        Exists(BackgroundProcessingTask.objects.filter(job=OuterRef("pk")))
    ).exclude(Exists(SettlementRecord.objects.filter(job=OuterRef("pk"))))
    for job in list(stale_jobs):
        returned = finish_job(job, AIJobStatus.FAILED)
        if returned is not None:
            jobs_released += 1
            credits_returned += returned

    # B1b-6 (plan §3.2, review r4 m-4): jobs none of whose papers started
    # or settled for unstarted_backstop_hours.
    from .batch_queue import backstop_unstarted

    backstopped = backstop_unstarted(now)

    return {
        "items_swept": items_swept,
        "credits_returned": credits_returned,
        "jobs_released": jobs_released,
        "items_backstopped": backstopped,
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
            # B1b FB-1: a deleted row's numbers live on in deleted_items_*.
            if (job.reserved_credits, job.hold_drawn, job.released_credits) != (
                (item_sums["held"] or 0) + job.deleted_items_held,
                (item_sums["drawn"] or 0) + job.deleted_items_drawn,
                (item_sums["released"] or 0) + job.deleted_items_released,
            ):
                problems.append(f"item_sum job={job.pk}")
            negative = items.filter(
                hold_credits__lt=F("hold_drawn") + F("released_credits")
            )
            for item_pk in negative.values_list("pk", flat=True):
                problems.append(f"item_negative item={item_pk}")
    return problems


# --------------------------------------------------------------------------
# The metered unit (plan 08 §3.3, §3.4), replacing billing_refund_scope
# --------------------------------------------------------------------------

_active_unit: contextvars.ContextVar = contextvars.ContextVar(
    "billing_metered_unit", default=None
)


def current_unit():
    """The innermost open metered unit, or None."""
    return _active_unit.get()


class CreditHoldReturnedError(ProviderFailureError):
    """A late message exits before any call (plan §3.2, R3-3b): its credit
    hold was returned, or its row already settled. PROVIDER_FAILURE is the
    code the sweep already set; its text says no credits were charged."""


def _late(detail):
    return CreditHoldReturnedError(params={"credit_clause": NOT_CHARGED}, detail=detail)


#: Task-row statuses update_processing_task never moves back to STARTED.
FINISHED_TASK_STATUSES = (
    BackgroundTaskStatus.FAILURE,
    BackgroundTaskStatus.SUCCESS,
    BackgroundTaskStatus.CANCELLED,
)


class Unit:
    """One logical AI operation. The ROOT unit owns the holder; nested units
    keep their own tally and hand it to their parent on success (the refund
    scope's nesting rule). Nothing is charged until the root exits."""

    def __init__(self, parent):
        self.parent = parent
        self.tally = 0
        # FR-B-09 (NOTES §15i): the root's settlement id, fixed before any
        # call so every call row notes it; nested units use the root's.
        self.usage_id = uuid.uuid4() if parent is None else None
        # Root only.
        self.job = None
        self.item = None
        self.key = None
        self.owns_job = False
        self.attach_item = None
        self.late_door_row = None
        self.settle_kwargs = None
        self.billed = None

    def enter(self, processing_task_id, *, new_jobs=True):
        """The entry rule through the task row (plan §3.4; SM rulings
        2026-11-06). Raises the late-message error before any call.

        new_jobs=False while draining (B1b-9 Q9-1): returns False wherever
        the unit would open a NEW job (no row, a row with no job, a late
        door, a retry); the caller then runs it flag-off. True otherwise."""
        if not processing_task_id:
            return new_jobs
        row = (
            BackgroundProcessingTask.objects.select_related("job")
            .filter(pk=processing_task_id)
            .first()
        )
        if row is None:
            return new_jobs
        if row.job_id is None:
            if not new_jobs:
                return False
            # E-1 style: the first call opens a SINGLE_ITEM job for this row.
            self.attach_item = row
            return True
        if already_settled(row):
            raise _late(f"task {row.pk} already settled for retry {row.retry_count}")
        if row.hold_state == HoldState.HELD:
            if claim_item_start(row):
                self.job, self.item = row.job, BackgroundProcessingTask.objects.get(
                    pk=row.pk
                )
                if row.job is not None and row.job.job_type == AIJobType.BATCH_GRADING:
                    from .batch_queue import mark_running

                    mark_running(row.job)
                return True
            row = BackgroundProcessingTask.objects.select_related("job").get(pk=row.pk)
        old_job = row.job
        if old_job is None:  # its job was deleted since the row was read
            if not new_jobs:
                return False
            self.attach_item = row
            return True
        if row.status in FINISHED_TASK_STATUSES:
            if old_job.job_type == AIJobType.UPLOAD:
                if not new_jobs:
                    return False
                self._run_as_successor(open_late_door_job(old_job, row, 0), row)
                self.late_door_row = row
                return True
            raise _late(f"task {row.pk}: its credit hold was returned")
        if not new_jobs:
            return False
        # A retry of the row (E-2): a new SINGLE_ITEM job keyed on the row.
        self._run_as_successor(open_retry_job(old_job, row, 0), row)
        return True

    def _run_as_successor(self, job, row):
        self.job = job
        self.key = (row.pk, row.retry_count)
        self.owns_job = True

    def root(self):
        unit = self
        while unit.parent is not None:
            unit = unit.parent
        return unit

    def open_tally(self):
        """What this unit and its open ancestors have used so far."""
        total, unit = 0, self
        while unit is not None:
            total += unit.tally
            unit = unit.parent
        return total

    def before_call(self, *, wallet, target_teacher, estimate):
        """The pre-call fit check. Opens the root's job at the first call
        (SINGLE_ITEM, no up-front amount: SM ruling Q1) and reserves this
        call's estimate beyond what the hold still covers, or raises
        InsufficientCreditsError before the provider is reached."""
        root = self.root()
        if root.job is None:
            job = create_job(
                wallet=wallet,
                requested_by=target_teacher,
                job_type=AIJobType.SINGLE_ITEM,
                total_items=1,
                school_id=getattr(target_teacher, "school_id", None),
            )
            AIJob.objects.filter(pk=job.pk).update(status=AIJobStatus.RUNNING)
            root.job = AIJob.objects.get(pk=job.pk)
            root.owns_job = True
            if root.attach_item is not None:
                BackgroundProcessingTask.objects.filter(pk=root.attach_item.pk).update(
                    job=root.job
                )
                root.item = BackgroundProcessingTask.objects.get(pk=root.attach_item.pk)
        elif root.job.wallet_id != wallet.pk:
            raise ValueError("a metered unit bills one wallet")
        if root.billed is None:
            root.billed = target_teacher
        ensure_call_fits(root.job, estimate, item=root.item, tally=self.open_tally())

    def after_call(self, actual, *, feature, task_type, course, school):
        self.tally += actual
        root = self.root()
        if root.settle_kwargs is None:
            root.settle_kwargs = {
                "feature": feature,
                "task_type": task_type,
                "course": course,
                "school": school,
            }

    def absorb(self, child):
        self.tally += child.tally

    def settle_root(self):
        if self.job is None:
            return None
        outcome = settle(
            self.job,
            self.tally,
            item=self.item,
            key=self.key,
            record_id=self.usage_id,
            **(self.settle_kwargs or {}),
        )
        if self.owns_job:
            # A shared batch or door job ends at its last item
            # (batch_queue.decide_after_item), not here.
            finish_job(self.job, AIJobStatus.SETTLED)
        _note_settled(self.job.pk)
        if outcome.settled and outcome.chargeable and self.billed is not None:
            _record_analytics(
                self.billed,
                outcome.chargeable,
                (self.settle_kwargs or {}).get("feature"),
            )
        return outcome

    def fail_root(self):
        if self.job is None:
            return
        fail_item(self.job, item=self.item)
        if self.owns_job:
            finish_job(self.job, AIJobStatus.FAILED)


def _record_analytics(teacher, amount, feature):
    # As execute_graded_task did at each charge (Q4: now at settlement).
    from .services import AnalyticsService

    AnalyticsService.record_consumption(user=teacher, amount=amount, feature=feature)
    AnalyticsService.track_activity(user=teacher)


@contextlib.contextmanager
def metered_unit(*, processing_task_id=None, reason="task failed"):
    """A billed multi-call operation (decision 2: a failed AI call is not
    charged). With the flag off it is billing_refund_scope, unchanged.

    A root unit opened with a processing_task_id runs the entry rule
    (Unit.enter) before its body; a nested unit joins its parent.

    While draining (B1b-9 Q9-1) a root keeps the holds path only for a HELD
    task row; any other root runs flag-off, and a unit nested in a flag-off
    root stays flag-off."""
    from .refunds import billing_refund_scope, refund_scope_open

    parent = _active_unit.get()
    unit = Unit(parent)
    flag_off = not holds_enabled()
    if not flag_off and parent is None:
        if holds_draining():
            flag_off = refund_scope_open() or not unit.enter(
                processing_task_id, new_jobs=False
            )
        else:
            unit.enter(processing_task_id)
    if flag_off:
        with billing_refund_scope(reason=reason) as ids:
            yield ids
        return

    token = _active_unit.set(unit)
    try:
        yield unit
    except BaseException as exc:
        _active_unit.reset(token)
        if parent is None:
            unit.fail_root()
            if unit.late_door_row is not None and isinstance(
                exc, InsufficientCreditsError
            ):
                notify_late_door_refusal(unit.late_door_row, unit.job)
        # A nested unit's calls are dropped: never charged (plan §3.4).
        raise
    _active_unit.reset(token)
    if parent is not None:
        parent.absorb(unit)
    else:
        unit.settle_root()


# --------------------------------------------------------------------------
# A single call outside any unit (plan 08 §3.3)
# --------------------------------------------------------------------------


def begin_single_call(*, wallet, target_teacher, estimate):
    """Open a SINGLE_CALL job and hold `estimate`, or leave it FAILED and
    raise InsufficientCreditsError BEFORE the provider call."""
    job = create_job(
        wallet=wallet,
        requested_by=target_teacher,
        job_type=AIJobType.SINGLE_CALL,
        total_items=1,
        school_id=getattr(target_teacher, "school_id", None),
    )
    if estimate > 0:
        # B1b-3 P5: RUNNING is written by the reserve's own job UPDATE.
        if not reserve(wallet, estimate, job, status=AIJobStatus.RUNNING):
            finish_job(job, AIJobStatus.FAILED)
            raise _refusal(CreditWallet.objects.get(pk=wallet.pk))
    else:
        AIJob.objects.filter(pk=job.pk).update(status=AIJobStatus.RUNNING)
    return AIJob.objects.get(pk=job.pk)


def finish_single_call(
    job, actual, *, feature, task_type, course, school, record_id=None
):
    """Settle the call's actual usage, return the rest, close the job.
    Returns the billing task id (the settlement's id)."""
    outcome = settle(
        job,
        actual,
        feature=feature,
        task_type=task_type,
        course=course,
        school=school,
        record_id=record_id,
        close_status=AIJobStatus.SETTLED,
    )
    if not outcome.settled:
        # Not settled (a deleted wallet, a duplicate key): settle returned
        # before its job UPDATE, so close the job as before (NOTES §3.3a).
        finish_job(job, AIJobStatus.SETTLED)
    return str(outcome.record_id) if outcome.record_id else None


def abandon_single_call(job):
    """The call failed or was cancelled: nothing charged, hold returned."""
    fail_item(job)
    finish_job(job, AIJobStatus.FAILED)


# --------------------------------------------------------------------------
# The hold for one provider call in execute_graded_task (plan 08 §3.3;
# NOTES §15i: one provider call site, one set of call_record notes)
# --------------------------------------------------------------------------


class _UnitCallHold:
    """A call inside a metered unit: the unit holds and settles it."""

    def __init__(self, unit):
        self.unit = unit
        self.usage_id = str(unit.root().usage_id)

    def finish(self, actual, *, feature, task_type, course, school, before_charge):
        self.unit.after_call(
            actual, feature=feature, task_type=task_type, course=course, school=school
        )

    def abandon(self):
        """The unit's own exit returns the hold (fail_root)."""


class _SingleCallHold:
    """A call outside any unit: its own SINGLE_CALL job."""

    def __init__(self, job, billed):
        self.job = job
        self.billed = billed
        self.usage_id = str(uuid.uuid4())

    def finish(self, actual, *, feature, task_type, course, school, before_charge):
        from .refunds import record_billing_task_id

        try:
            with transaction.atomic():
                before_charge()
                billing_task_id = finish_single_call(
                    self.job,
                    actual,
                    feature=feature,
                    task_type=task_type,
                    course=course,
                    school=school,
                    record_id=self.usage_id,
                )
                _record_analytics(self.billed, actual, feature)
        except BaseException:
            self.abandon()
            raise
        _note_settled(self.job.pk)
        record_billing_task_id(billing_task_id)

    def abandon(self):
        abandon_single_call(self.job)


def hold_for_call(*, wallet, target_teacher, estimate):
    """Hold one provider call's estimate before the call, or raise
    InsufficientCreditsError. None with the flag off (today's balance check
    applies). While draining, None too unless a held unit is open (B1b-9)."""
    if not holds_enabled():
        return None
    unit = current_unit()
    if unit is not None:
        unit.before_call(
            wallet=wallet, target_teacher=target_teacher, estimate=estimate
        )
        return _UnitCallHold(unit)
    if not holds_for_new_work():
        return None
    job = begin_single_call(
        wallet=wallet, target_teacher=target_teacher, estimate=estimate
    )
    return _SingleCallHold(job, target_teacher)


def abandon_call(hold):
    """The provider call raised: return a single call's hold. A no-op with
    the flag off (None) and inside a unit (its exit returns the hold)."""
    if hold is not None:
        hold.abandon()


# --------------------------------------------------------------------------
# The upload door (plan 08 §3.2, E-7)
# --------------------------------------------------------------------------


def open_door_job(*, billed_user, requested_by, total_items, assignment=None):
    """One UPLOAD job per door request; each queued file holds on its row."""
    job = create_job(
        wallet=billed_user.credit_wallet,
        requested_by=requested_by,
        job_type=AIJobType.UPLOAD,
        total_items=total_items,
        assignment=assignment,
        school_id=getattr(billed_user, "school_id", None),
    )
    AIJob.objects.filter(pk=job.pk).update(status=AIJobStatus.RESERVED)
    return AIJob.objects.get(pk=job.pk)


def hold_door_item(job, item, estimate):
    """Attach `item` to the door job and hold its estimate on it. False (and
    the row detached again) when the hold does not fit. No estimate (the
    door could not read the file): nothing is attached, and the task's
    per-item rule applies."""
    if not estimate:
        return True
    BackgroundProcessingTask.objects.filter(pk=item.pk).update(job=job)
    if reserve(job.wallet, estimate, job, item=item):
        return True
    BackgroundProcessingTask.objects.filter(pk=item.pk).update(job=None)
    return False


# --------------------------------------------------------------------------
# The late-door notice (plan 08 §3.5; owner ruling 2026-10-30)
# --------------------------------------------------------------------------

#: Owner-approved text 3 (team/approved_wording_2026-11-08.md), with {days}
#: from QUEUE_LIFETIME_DAYS; on at B1b-8. Sent for batch rows only (SM Q8-3):
#: a student's single upload has no retry route, so "retry it" would not be
#: true there; that case stays logged.
LATE_DOOR_EMAIL_TEXTS = {
    "subject": "We couldn't process {file_name}",
    "individual": (
        "We couldn't process {file_name} because your credits ran out while "
        "it was waiting. Nothing was charged for it. Your file is kept for "
        "{days} days. Top up your credits, then retry it. You won't need to "
        "upload it again."
    ),
    "license": (
        "We couldn't process {file_name} because your credits ran out while "
        "it was waiting. Nothing was charged for it. Your file is kept for "
        "{days} days. Ask your school administrator for more credits, then "
        "retry it. You won't need to upload it again."
    ),
}
LATE_DOOR_EMAIL_APPROVED = True
#: The student side reuses STUDENT_UPLOAD_NOT_PROCESSED unchanged. On: the
#: owner ruled the student is told (relayed by the SM 2026-11-08).
LATE_DOOR_STUDENT_NOTICE_APPROVED = True


def notify_late_door_refusal(row, job):
    """A late door file could not be held: the teacher is told which file,
    once the texts are approved. Never logs the file name."""
    from AutoGrader.tasks import send_email_task
    from students.item_retry import UPLOAD_ITEM_TYPES

    teacher = job.wallet.user
    name = row.file_name or "your file"
    if not LATE_DOOR_EMAIL_APPROVED or row.task_type not in UPLOAD_ITEM_TYPES:
        logger.warning(
            "Late door file refused (task %s); the notice texts await owner "
            "approval, so no email was sent.",
            row.pk,
        )
    else:
        variant = "individual" if is_individual_teacher(teacher) else "license"
        send_email_task.delay(
            subject=LATE_DOOR_EMAIL_TEXTS["subject"].format(file_name=name),
            message=LATE_DOOR_EMAIL_TEXTS[variant].format(
                file_name=name, days=settings.QUEUE_LIFETIME_DAYS
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[teacher.email],
        )
    student = row.requested_by
    if LATE_DOOR_STUDENT_NOTICE_APPROVED and student.user_type == "STUDENT":
        from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED

        send_email_task.delay(
            subject=LATE_DOOR_EMAIL_TEXTS["subject"].format(file_name=name),
            message=str(STUDENT_UPLOAD_NOT_PROCESSED),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
        )


# --------------------------------------------------------------------------
# A wallet that is deleted (NOTES §21n)
# --------------------------------------------------------------------------


def release_open_holds_on_delete(sender, instance, **kwargs):
    """pre_delete on CreditWallet (connected in BillingConfig.ready): every
    open job of the wallet is CANCELLED and its holds returned before the
    collector sets AIJob.wallet NULL, so no open job is ever left without a
    wallet. Whatever the flag says: jobs made while it was on outlive it."""
    for job in AIJob.objects.filter(wallet=instance, status__in=OPEN_STATUSES):
        finish_job(job, AIJobStatus.CANCELLED)


def _job_has_rows_to_run(job_pk):
    """A live row of the job that is HELD, or was never held and has not
    finished (SM ruling C4: such a job stays open; B1b-6's last-item helper
    decides it). Read in post_delete, after the collector's batch delete, so
    rows the same delete is removing are already gone (NOTES §1.20)."""
    return (
        BackgroundProcessingTask.objects.filter(job_id=job_pk)
        .filter(
            Q(hold_state=HoldState.HELD)
            | (Q(hold_state__isnull=True) & ~Q(status__in=FINISHED_TASK_STATUSES))
        )
        .exists()
    )


def finish_job_left_empty_on_item_delete(sender, instance, **kwargs):
    """post_delete on BackgroundProcessingTask (connected in
    BillingConfig.ready; NOTES §1.20-§1.21). SM ruling C4: an open job with
    no live row left to run ends SETTLED if it has a SettlementRecord,
    otherwise FAILED; never QUEUED. B1b-6 replaces this rule with its
    last-item helper, which must also run after the rows are gone.

    It runs after Collector.delete has deleted every row of the batch, so
    the rows-to-run read is accurate whatever order the collector visited
    them in (in pre_delete it was not: §1.20).

    The first read is a plain status read without a lock: a terminal job
    never reverts (§1.21 (a)), so a terminal status can be trusted, and in
    a cascade every row after the first one that finishes the job costs
    one query. An open status is read again under the wallet lock."""
    if instance.job_id is None:
        return
    status = (
        AIJob.objects.filter(pk=instance.job_id)
        .values_list("status", flat=True)
        .first()
    )
    if status is None or status not in OPEN_STATUSES:
        return  # no such job (§1.21 (b)), or terminal: it never reverts
    savepoint = not transaction.get_connection().in_atomic_block
    with transaction.atomic(savepoint=savepoint):
        locked = (
            CreditWallet.objects.select_for_update(of=("self",))
            .filter(ai_jobs__pk=instance.job_id)
            .first()
        )
        if locked is None:  # a wallet-less job (§21n)
            return
        job = AIJob.objects.filter(pk=instance.job_id).first()
        if job is None or job.status not in OPEN_STATUSES:
            return
        if job.job_type == AIJobType.BATCH_GRADING:
            # B1b-6 (obligation 1): the last-item helper decides a batch
            # job, after the rows are gone (here, in post_delete).
            from .batch_queue import decide_after_item

            decide_after_item(job.pk)
            return
        if _job_has_rows_to_run(job.pk):
            return
        has_settlement = SettlementRecord.objects.filter(job_id=job.pk).exists()
        finish_job(job, AIJobStatus.SETTLED if has_settlement else AIJobStatus.FAILED)


def return_hold_on_item_delete(sender, instance, **kwargs):
    """pre_delete on BackgroundProcessingTask (connected in BillingConfig.ready;
    B1b FB-1, NOTES §1.1). Whatever the flag says, as release_open_holds_on_delete.

    A HELD row's hold is returned the way fail_item returns it; the row's
    numbers are added to its job's deleted_items_*, so check_invariants'
    item_sum still adds up. A job left with nothing to run is finished
    after the delete, by finish_job_left_empty_on_item_delete (post_delete):
    here the siblings the same delete removes are still in the table.

    Lock order (NOTES §1.15): the wallet first, always. Every writer of a
    row's hold columns and of a job's status holds that wallet lock, so once
    it is held a plain read of the row is stable; the instance is never
    trusted (a RETURNED row can be HELD again: the sweep returns a started
    row, and its run's next call reserves on it). Pre_delete always runs
    inside Collector.delete's transaction, hence no savepoint per row."""
    if instance.job_id is None:
        return
    # SM condition (2): no savepoint per row only inside an open transaction
    # (Collector.delete's). Called outside one, it runs in a full atomic of
    # its own, so it can never half-apply.
    savepoint = not transaction.get_connection().in_atomic_block
    with transaction.atomic(savepoint=savepoint):
        locked = (
            CreditWallet.objects.select_for_update(of=("self",))
            .filter(ai_jobs__pk=instance.job_id)
            .first()
        )
        if locked is None:  # no such job, or a wallet-less one (§21n)
            return
        fresh = (
            BackgroundProcessingTask.objects.filter(
                pk=instance.pk, job_id=instance.job_id
            )
            .values(
                "hold_state",
                "hold_credits",
                "hold_drawn",
                "released_credits",
            )
            .first()
        )
        if fresh is None:
            return
        if fresh["hold_state"] != HoldState.HELD:
            # Nothing to return: carry the row's numbers to its job.
            AIJob.objects.filter(pk=instance.job_id).update(
                deleted_items_held=F("deleted_items_held") + fresh["hold_credits"],
                deleted_items_drawn=F("deleted_items_drawn") + fresh["hold_drawn"],
                deleted_items_released=F("deleted_items_released")
                + fresh["released_credits"],
            )
            return
        job_row = AIJob.objects.select_for_update().get(pk=instance.job_id)
        item_row = BackgroundProcessingTask.objects.select_for_update().get(
            pk=instance.pk
        )
        returned = max(_left(job_row, item_row), 0)
        _move_wallet(locked.pk, -returned)
        BackgroundProcessingTask.objects.filter(pk=item_row.pk).update(
            released_credits=F("released_credits") + returned,
            hold_state=HoldState.RETURNED,
        )
        AIJob.objects.filter(pk=job_row.pk).update(
            released_credits=F("released_credits") + returned,
            failed_items=F("failed_items") + 1,
            deleted_items_held=F("deleted_items_held") + item_row.hold_credits,
            deleted_items_drawn=F("deleted_items_drawn") + item_row.hold_drawn,
            deleted_items_released=F("deleted_items_released")
            + item_row.released_credits
            + returned,
        )
        if returned > 0:
            # C5: the existing label; a new one would be new admin wording.
            _audit(
                AuditAction.CREDIT_HOLD_RELEASED,
                job_row,
                item=item_row,
                credits=returned,
                reason_label="failed",
            )
