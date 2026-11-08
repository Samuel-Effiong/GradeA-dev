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
    The lock read is the check: no query is added while the wallet exists."""
    try:
        if job.wallet_id is not None:
            return lock_wallet_first(job.wallet)
    except CreditWallet.DoesNotExist:
        pass
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
    _audit(AuditAction.CREDIT_HOLD_RESERVED, job, item=item, credits=amount)
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
):
    """Charge `actual` once per settlement key (plan §3.2 settle).

    key: (item_id, retry_count). Default: the item's task row and its
    retry_count, or (job.id, 0) for a job with no task row. A duplicate key
    writes nothing and returns NOT_SETTLED.

    record_id: the usage id the run's calls already noted (NOTES §15i): the
    settlement record takes it, so the usage rows and the call rows share it
    without the call rows ever being updated.
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
        try:
            with transaction.atomic():
                record = SettlementRecord.objects.create(
                    job=job_row,
                    item_id=key[0],
                    retry_count=key[1],
                    **({"id": record_id} if record_id else {}),
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
                school=school,
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
        _audit(
            AuditAction.CREDIT_HOLD_SETTLED,
            job_row,
            item=item_row,
            credits=chargeable,
            credits_actual=actual,
            credits_released=leftover,
        )

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
        return amount


def finish_job(job, to_status):
    """Move an open job to a terminal status and return what is left of its
    hold (plan §3.2 release). Idempotent through the conditional status
    UPDATE: returns None if the job was not open."""
    if to_status not in TERMINAL_STATUSES:
        raise ValueError(f"{to_status} is not a terminal status")
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return None
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


def _sweep_item(item_pk, job_pk, cutoff):
    job = AIJob.objects.get(pk=job_pk)
    with transaction.atomic():
        locked = _lock_wallet_of(job)
        if locked is None:
            return None
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
        _audit(
            AuditAction.CREDIT_HOLD_SWEPT,
            job_row,
            item=item_row,
            credits=amount,
            reason_label="stale",
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

    def enter(self, processing_task_id):
        """The entry rule through the task row (plan §3.4; SM rulings
        2026-11-06). Raises the late-message error before any call."""
        if not processing_task_id:
            return
        row = (
            BackgroundProcessingTask.objects.select_related("job")
            .filter(pk=processing_task_id)
            .first()
        )
        if row is None:
            return
        if row.job_id is None:
            # E-1 style: the first call opens a SINGLE_ITEM job for this row.
            self.attach_item = row
            return
        if already_settled(row):
            raise _late(f"task {row.pk} already settled for retry {row.retry_count}")
        if row.hold_state == HoldState.HELD:
            if claim_item_start(row):
                self.job, self.item = row.job, BackgroundProcessingTask.objects.get(
                    pk=row.pk
                )
                return
            row = BackgroundProcessingTask.objects.select_related("job").get(pk=row.pk)
        old_job = row.job
        if old_job is None:  # its job was deleted since the row was read
            self.attach_item = row
            return
        if row.status in FINISHED_TASK_STATUSES:
            if old_job.job_type == AIJobType.UPLOAD:
                self._run_as_successor(open_late_door_job(old_job, row, 0), row)
                self.late_door_row = row
                return
            raise _late(f"task {row.pk}: its credit hold was returned")
        # A retry of the row (E-2): a new SINGLE_ITEM job keyed on the row.
        self._run_as_successor(open_retry_job(old_job, row, 0), row)

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
            # A shared batch or door job finishes in step 5, not here.
            finish_job(self.job, AIJobStatus.SETTLED)
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
    (Unit.enter) before its body; a nested unit joins its parent."""
    if not holds_enabled():
        from .refunds import billing_refund_scope

        with billing_refund_scope(reason=reason) as ids:
            yield ids
        return

    parent = _active_unit.get()
    unit = Unit(parent)
    if parent is None:
        unit.enter(processing_task_id)
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
    if estimate > 0 and not reserve(wallet, estimate, job):
        finish_job(job, AIJobStatus.FAILED)
        raise _refusal(CreditWallet.objects.get(pk=wallet.pk))
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
    )
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
        record_billing_task_id(billing_task_id)

    def abandon(self):
        abandon_single_call(self.job)


def hold_for_call(*, wallet, target_teacher, estimate):
    """Hold one provider call's estimate before the call, or raise
    InsufficientCreditsError. None with the flag off (today's balance check
    applies)."""
    if not holds_enabled():
        return None
    unit = current_unit()
    if unit is not None:
        unit.before_call(
            wallet=wallet, target_teacher=target_teacher, estimate=estimate
        )
        return _UnitCallHold(unit)
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

#: PENDING OWNER APPROVAL (SM 2026-11-06). Draft texts only: nothing is sent
#: while LATE_DOOR_EMAIL_APPROVED is False.
LATE_DOOR_EMAIL_TEXTS = {
    "subject": "We couldn't process {file_name}",
    "individual": (
        "We couldn't process {file_name} because your credits ran out while "
        "it was waiting. Nothing was charged for it. Top up your credits, "
        "then upload the file again."
    ),
    "license": (
        "We couldn't process {file_name} because your credits ran out while "
        "it was waiting. Nothing was charged for it. Ask your school "
        "administrator for more credits, then upload the file again."
    ),
}
LATE_DOOR_EMAIL_APPROVED = False
#: The student side reuses STUDENT_UPLOAD_NOT_PROCESSED unchanged. On: the
#: owner ruled the student is told (relayed by the SM 2026-11-08).
LATE_DOOR_STUDENT_NOTICE_APPROVED = True


def notify_late_door_refusal(row, job):
    """A late door file could not be held: the teacher is told which file,
    once the texts are approved. Never logs the file name."""
    from AutoGrader.tasks import send_email_task

    teacher = job.wallet.user
    name = row.file_name or "your file"
    if not LATE_DOOR_EMAIL_APPROVED:
        logger.warning(
            "Late door file refused (task %s); the notice texts await owner "
            "approval, so no email was sent.",
            row.pk,
        )
    else:
        variant = "individual" if is_individual_teacher(teacher) else "license"
        send_email_task.delay(
            subject=LATE_DOOR_EMAIL_TEXTS["subject"].format(file_name=name),
            message=LATE_DOOR_EMAIL_TEXTS[variant].format(file_name=name),
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
