"""Epic B1b-6: batch grading jobs and the queue (plan 08 §3.7; 08b §1.4, §2;
NOTES §6.2-§6.3).

With BILLING_HOLDS_ENABLED on, a batch of papers is one BATCH_GRADING job:

* start: the job holds whole papers that fit the teacher's spendable
  balance, ``held_per_item`` (the estimate's per-paper p90) each. Each held
  paper is claimed by one conditional UPDATE (``hold_state`` NULL -> HELD on
  a PENDING row; no new task status, D6-b) and launched after commit. The
  other papers wait, PENDING, with no hold and no Celery message.
* the last item (``decide_after_item``): when completed_items +
  failed_items reaches the number of claimed papers (D6-a), the job is
  QUEUED_PENDING_CREDITS if papers still wait, else SETTLED or FAILED. One
  conditional status UPDATE under the wallet lock decides it, once.
* promotion: a queued job is moved back to RESERVED, holds what now fits,
  and launches it after commit; if nothing fits it stays queued.
* expiry: at expires_at the job is EXPIRED and its waiting papers fail as
  INSUFFICIENT_CREDITS_MID_BATCH (retryable).
* the backstop: a job none of whose papers started or settled for
  BillingSettings.unstarted_backstop_hours fails its unstarted papers.

Every job-status write here is conditional on its from-status and taken
under the wallet lock first (NOTES §1.15, B1b-6 obligation 3).

With the flag off nothing here runs: the batch path is today's.
"""

import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from audit import metrics as audit_metrics
from students.models import BackgroundProcessingTask, BackgroundTaskStatus

from . import holds
from .models import (
    AIJob,
    AIJobStatus,
    AIJobType,
    BillingSettings,
    CreditWallet,
    HoldState,
    SettlementRecord,
)

logger = logging.getLogger(__name__)

#: 08b §2.1: the warning goes out this long before expiry (a constant).
QUEUE_EXPIRY_WARNING_HOURS = 48

#: The batch job's open, running states (RESERVED until its first paper starts).
RUNNING_STATUSES = (AIJobStatus.RESERVED, AIJobStatus.RUNNING)

#: Owner-approved texts #5, #8, #9, #10 and closings #6/#7
#: (team/approved_wording_b1b_2026-11-09.md), switched on at B1b-8.
QUEUE_EMAIL_APPROVED = False
QUEUE_EMAIL_TEXTS = {
    "queued": (
        "Your grading batch is waiting for credits",
        "Your credits ran out while grading {assignment}. {done} of {total} "
        "papers were graded and saved. The other {left} papers will start "
        "again on their own when you add credits. They will wait for {days} "
        "days, until {expires_on}.",
    ),
    "expiry_warning": (
        "Your waiting papers expire in 2 days",
        "{left} papers from {assignment} are still waiting for credits. If "
        "you don't add credits by {expires_on}, they will stop waiting. You "
        "can still grade them later by retrying them.",
    ),
    "restarted": (
        "Your grading batch has started again",
        "Credits arrived, so grading {assignment} has started again for "
        "{started} of the {left} waiting papers.",
    ),
    "expired": (
        "Your waiting papers have stopped waiting",
        "{left} papers from {assignment} waited {days} days for credits and "
        "have stopped waiting. Nothing was charged for them. You can grade "
        "them at any time by retrying them after you add credits.",
    ),
}
QUEUE_EMAIL_CLOSING_INDIVIDUAL = "Top up your credits to continue."
QUEUE_EMAIL_CLOSING_LICENSE = (
    "Ask your school administrator for more credits to continue."
)


class _NothingGranted(Exception):
    """Rolls a promotion back: nothing fits yet, the job stays queued."""


def wallet_of(teacher):
    """The wallet a teacher's batch bills, or None (then the batch runs as
    with the flag off: each paper's own unit refuses or bills)."""
    if teacher is None or teacher.pk is None:
        return None
    return CreditWallet.objects.filter(user_id=teacher.pk).first()


def _waiting(job_pk):
    """The job's papers that wait: PENDING, never held."""
    return BackgroundProcessingTask.objects.filter(
        job_id=job_pk,
        status=BackgroundTaskStatus.PENDING,
        hold_state__isnull=True,
    ).order_by("item_index", "pk")


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------


def open_batch_job(*, wallet, teacher, assignment, n, representative, meta=None):
    """A new ESTIMATED BATCH_GRADING job with the estimate written on it
    (B1b-4 Q5, obligation 5); held_per_item is the per-paper p90 (Q6)."""
    from .estimates import paper_hold_estimate

    estimate, p90_paper = paper_hold_estimate(
        assignment, n, teacher=teacher, representative=representative
    )
    job = holds.create_job(
        wallet=wallet,
        requested_by=teacher,
        job_type=AIJobType.BATCH_GRADING,
        total_items=n,
        held_per_item=p90_paper,
        assignment=assignment,
        course=getattr(assignment, "course", None),
        school_id=getattr(teacher, "school_id", None),
        meta=meta,
    )
    AIJob.objects.filter(pk=job.pk).update(
        estimate_p50=estimate["p50"],
        estimate_p90=estimate["p90"],
        estimate_source=estimate["source"],
    )
    job.refresh_from_db()
    return job


def start_batch(job, rows):
    """Hold and claim what fits; the job is RESERVED, or QUEUED when nothing
    fits (the teacher proceeded on the verdict: D-A8, Q7). Call inside the
    transaction that made the rows; launch the returned rows on commit."""
    with transaction.atomic():
        locked = holds._lock_wallet_of(job)
        if locked is None:
            return []
        job_row = AIJob.objects.select_for_update().get(pk=job.pk)
        claimed = holds.claim_batch_rows_locked(locked, job_row, rows)
        if claimed:
            AIJob.objects.filter(pk=job.pk, status=AIJobStatus.ESTIMATED).update(
                status=AIJobStatus.RESERVED
            )
        else:
            _queue_locked(job_row, from_statuses=(AIJobStatus.ESTIMATED,))
        return claimed


# --------------------------------------------------------------------------
# The last item (B1b-6 obligation 1)
# --------------------------------------------------------------------------


def _queue_locked(job_row, *, from_statuses=RUNNING_STATUSES):
    """Move the job to QUEUED_PENDING_CREDITS (the wallet is locked). The
    job's hold left must be 0; a drift is reported and returned (review r4
    m-6). Returns True if this call queued it."""
    first = _waiting(job_row.pk).values_list("item_index", flat=True).first()
    left = holds._left(job_row, None)
    if left:
        audit_metrics.count("credit_ledger_anomaly", tags={"kind": "hold_drift"})
        logger.error(
            "Batch job %s: %s credits still held at the move to QUEUED; " "returned.",
            job_row.pk,
            left,
        )
        if left > 0:
            holds._move_wallet(job_row.wallet_id, -left)
            AIJob.objects.filter(pk=job_row.pk).update(
                released_credits=F("released_credits") + left
            )
    now = timezone.now()
    moved = AIJob.objects.filter(pk=job_row.pk, status__in=from_statuses).update(
        status=AIJobStatus.QUEUED_PENDING_CREDITS,
        stopped_at_item=first,
        queued_at=now,
        expires_at=now + timedelta(days=settings.QUEUE_LIFETIME_DAYS),
        warned_expiry_at=None,
    )
    if moved:
        transaction.on_commit(partial(notify, job_row.pk, "queued"))
    return bool(moved)


def decide_after_item(job_pk):
    """After a claimed paper of a batch job finished (settled, failed, swept,
    released or deleted): move the job on exactly once when it was the last
    one (D6-a). Runs under the wallet lock; never in pre_delete."""
    status = AIJob.objects.filter(pk=job_pk).values_list("status", flat=True).first()
    if status not in RUNNING_STATUSES:
        return None
    savepoint = not transaction.get_connection().in_atomic_block
    with transaction.atomic(savepoint=savepoint):
        job = AIJob.objects.filter(pk=job_pk).first()
        if job is None:
            return None
        locked = holds._lock_wallet_of(job)
        if locked is None:
            return None
        job_row = AIJob.objects.select_for_update().get(pk=job_pk)
        if job_row.status not in RUNNING_STATUSES:
            return None
        rows = BackgroundProcessingTask.objects.filter(job_id=job_pk)
        claimed = rows.filter(hold_state__isnull=False).count()
        if job_row.completed_items + job_row.failed_items < claimed:
            return None
        if rows.filter(hold_state=HoldState.HELD).exists():
            return None
        if _waiting(job_pk).exists():
            return (
                AIJobStatus.QUEUED_PENDING_CREDITS if _queue_locked(job_row) else None
            )
        has_settlement = SettlementRecord.objects.filter(job_id=job_pk).exists()
        to_status = AIJobStatus.SETTLED if has_settlement else AIJobStatus.FAILED
        if holds.finish_job(job_row, to_status, from_statuses=RUNNING_STATUSES):
            return to_status
        return None


def after_batch_item(job_row):
    """The hook every finishing writer calls (settle, fail_item, the sweeps):
    nothing for any other job type."""
    if job_row is not None and job_row.job_type == AIJobType.BATCH_GRADING:
        decide_after_item(job_row.pk)


def mark_running(job):
    """RESERVED -> RUNNING at the first paper's start (plan §3.1), under the
    wallet lock; nothing when the job is already running."""
    if job is None or job.job_type != AIJobType.BATCH_GRADING:
        return
    if job.status != AIJobStatus.RESERVED:
        return
    with transaction.atomic():
        if holds._lock_wallet_of(job) is None:
            return
        AIJob.objects.filter(pk=job.pk, status=AIJobStatus.RESERVED).update(
            status=AIJobStatus.RUNNING
        )


def release_unused_item_hold(task):
    """D6-c: a held batch paper that ended before its metered unit (a refusal,
    the skip of a paper graded meanwhile, a cancel, the S7c stop, a launch
    failure) returns its hold once and counts as finished, so the sweep
    never overwrites its status and its job can finish. A paper whose unit
    already returned it (RETURNED) is left alone."""
    if task is None or task.job_id is None or task.hold_state != HoldState.HELD:
        return
    job = AIJob.objects.filter(pk=task.job_id).first()
    if job is None or job.job_type != AIJobType.BATCH_GRADING:
        return
    holds.fail_item(job, item=task)


# --------------------------------------------------------------------------
# Promotion
# --------------------------------------------------------------------------


def promote_wallet(wallet_pk):
    """Promote the wallet's queued batch jobs, oldest first, until one finds
    nothing fits. Returns the number of papers launched."""
    launched = 0
    jobs = AIJob.objects.filter(
        wallet_id=wallet_pk,
        job_type=AIJobType.BATCH_GRADING,
        status=AIJobStatus.QUEUED_PENDING_CREDITS,
    ).order_by("queued_at", "pk")
    for job in list(jobs):
        started = promote_job(job)
        if started is None:
            break
        launched += started
    return launched


def promote_job(job):
    """One atomic promotion (plan §3.7, review r1 M4): QUEUED -> RESERVED,
    hold what fits, claim, clear the session's credit stop (Q14), launch on
    commit. Returns the papers launched, 0 if another promoter or expiry won,
    None if nothing fits (rolled back: the job stays queued)."""
    try:
        with transaction.atomic():
            locked = holds._lock_wallet_of(job)
            if locked is None:
                return 0
            moved = AIJob.objects.filter(
                pk=job.pk, status=AIJobStatus.QUEUED_PENDING_CREDITS
            ).update(status=AIJobStatus.RESERVED)
            if not moved:
                return 0
            job_row = AIJob.objects.select_for_update().get(pk=job.pk)
            waiting = list(_waiting(job.pk))
            claimed = holds.claim_batch_rows_locked(locked, job_row, waiting)
            if not claimed:
                raise _NothingGranted()
            from students.task_tracking import clear_batch_credit_stop

            for session_id in {row.batch_session_id for row in claimed}:
                if session_id:
                    clear_batch_credit_stop(session_id)
            ids = [row.pk for row in claimed]
            transaction.on_commit(partial(launch_claimed, ids))
            transaction.on_commit(
                partial(
                    notify,
                    job.pk,
                    "restarted",
                    started=len(claimed),
                    left=len(waiting),
                )
            )
            return len(claimed)
    except _NothingGranted:
        return None


def promote_all():
    """The 5-minute sweep: every wallet with a queued batch job."""
    wallets = (
        AIJob.objects.filter(
            job_type=AIJobType.BATCH_GRADING,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            wallet__isnull=False,
        )
        .values_list("wallet_id", flat=True)
        .distinct()
    )
    return sum(promote_wallet(wallet_pk) for wallet_pk in list(wallets))


def launch_claimed(item_ids):
    """Send the claimed papers' messages (after commit). A broker failure
    marks the paper FAILURE, which returns its hold (D6-c)."""
    from assignments.tasks import launch_batch_items

    launch_batch_items(item_ids)


# --------------------------------------------------------------------------
# Expiry and the warning
# --------------------------------------------------------------------------


def expire_due(now=None):
    """Warn at expires_at - 48 h; expire at expires_at (conditional on
    QUEUED_PENDING_CREDITS, so promotion and expiry cannot both win)."""
    now = now or timezone.now()
    warned = 0
    due_warning = AIJob.objects.filter(
        job_type=AIJobType.BATCH_GRADING,
        status=AIJobStatus.QUEUED_PENDING_CREDITS,
        warned_expiry_at__isnull=True,
        expires_at__lte=now + timedelta(hours=QUEUE_EXPIRY_WARNING_HOURS),
        expires_at__gt=now,
    )
    for job in list(due_warning):
        if AIJob.objects.filter(
            pk=job.pk,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            warned_expiry_at__isnull=True,
        ).update(warned_expiry_at=now):
            warned += 1
            notify(job.pk, "expiry_warning")
    expired = 0
    due = AIJob.objects.filter(
        job_type=AIJobType.BATCH_GRADING,
        status=AIJobStatus.QUEUED_PENDING_CREDITS,
        expires_at__lte=now,
    )
    for job in list(due):
        if expire_job(job, now=now):
            expired += 1
    return {"warned": warned, "expired": expired}


def expire_job(job, now=None):
    """QUEUED -> EXPIRED; the waiting papers fail as
    INSUFFICIENT_CREDITS_MID_BATCH, retryable (M-B10: never deleted)."""
    from AutoGrader.reason_codes import ReasonCode
    from students.task_tracking import _mid_batch_error, describe_task_error

    now = now or timezone.now()
    with transaction.atomic():
        returned = holds.finish_job(
            job,
            AIJobStatus.EXPIRED,
            from_statuses=(AIJobStatus.QUEUED_PENDING_CREDITS,),
        )
        if returned is None:
            return False
        waiting = list(_waiting(job.pk))
        for row in waiting:
            error = _mid_batch_error(row.batch_session_id)
            BackgroundProcessingTask.objects.filter(
                pk=row.pk, status=BackgroundTaskStatus.PENDING
            ).update(
                status=BackgroundTaskStatus.FAILURE,
                reason_code=ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH.value,
                error=describe_task_error(error, None),
                finished_at=now,
                updated_at=now,
            )
        transaction.on_commit(partial(notify, job.pk, "expired", left=len(waiting)))
        return True


# --------------------------------------------------------------------------
# The backstop (plan §3.2, review r4 m-4)
# --------------------------------------------------------------------------


def backstop_unstarted(now=None):
    """A BATCH_GRADING job (RESERVED/RUNNING) or an UPLOAD door job
    (RESERVED) none of whose papers started or settled for
    unstarted_backstop_hours: its unstarted held papers fail (retryable) and
    return their holds. Returns the papers failed."""
    from AutoGrader.reason_codes import ReasonCode

    now = now or timezone.now()
    cutoff = now - timedelta(hours=BillingSettings.load().unstarted_backstop_hours)
    recent_start = BackgroundProcessingTask.objects.filter(
        job=OuterRef("pk"), started_at__gte=cutoff
    )
    recent_settle = SettlementRecord.objects.filter(
        job=OuterRef("pk"), settled_at__gte=cutoff
    )
    batch = AIJob.objects.filter(
        job_type=AIJobType.BATCH_GRADING, status__in=RUNNING_STATUSES
    )
    door = AIJob.objects.filter(job_type=AIJobType.UPLOAD, status=AIJobStatus.RESERVED)
    stalled = (
        (batch | door)
        .filter(reservation_claimed_at__lt=cutoff)
        .exclude(Exists(recent_start))
        .exclude(Exists(recent_settle))
    )
    failed = 0
    for job in list(stalled):
        rows = BackgroundProcessingTask.objects.filter(
            job_id=job.pk, hold_state=HoldState.HELD, started_at__isnull=True
        ).values_list("pk", flat=True)
        for row_pk in list(rows):
            if _backstop_item(job, row_pk, ReasonCode.PROVIDER_FAILURE.value):
                failed += 1
        after_batch_item(AIJob.objects.filter(pk=job.pk).first())
    return failed


def _backstop_item(job, row_pk, reason_code):
    with transaction.atomic():
        locked = holds._lock_wallet_of(job)
        if locked is None:
            return False
        job_row = AIJob.objects.select_for_update().get(pk=job.pk)
        took = BackgroundProcessingTask.objects.filter(
            pk=row_pk, hold_state=HoldState.HELD, started_at__isnull=True
        ).update(hold_state=HoldState.RETURNED)
        if not took:
            return False
        row = BackgroundProcessingTask.objects.select_for_update().get(pk=row_pk)
        amount = max(holds._left(job_row, row), 0)
        holds._move_wallet(locked.pk, -amount)
        now = timezone.now()
        BackgroundProcessingTask.objects.filter(pk=row_pk).update(
            released_credits=F("released_credits") + amount,
            status=BackgroundTaskStatus.FAILURE,
            reason_code=reason_code,
            finished_at=now,
            updated_at=now,
        )
        AIJob.objects.filter(pk=job.pk).update(
            released_credits=F("released_credits") + amount,
            failed_items=F("failed_items") + 1,
        )
        audit_metrics.count("credit_hold_backstop", tags={"job_type": job.job_type})
        return True


# --------------------------------------------------------------------------
# Notifications (texts #5, #8, #9, #10; switched on at B1b-8)
# --------------------------------------------------------------------------


def notify(job_pk, kind, **counts):
    """The teacher's email for a job state change, once the texts are
    approved. Until then nothing is sent (the late-door precedent)."""
    if not QUEUE_EMAIL_APPROVED:
        logger.warning(
            "Batch job %s is %s; the queue email texts await owner approval, "
            "so no email was sent.",
            job_pk,
            kind,
        )
        return
    _send(job_pk, kind, **counts)  # pragma: no cover (B1b-8)


def _send(job_pk, kind, **counts):  # pragma: no cover (B1b-8 switches it on)
    from AutoGrader.tasks import send_email_task

    from .errors import is_individual_teacher

    job = AIJob.objects.select_related("assignment", "requested_by").get(pk=job_pk)
    teacher = job.requested_by
    if teacher is None or not teacher.email:
        return
    subject, body = QUEUE_EMAIL_TEXTS[kind]
    waiting = _waiting(job_pk).count()
    values = {
        "assignment": job.assignment.title if job.assignment else "",
        "done": job.completed_items,
        "total": job.total_items,
        "left": counts.get("left", waiting),
        "started": counts.get("started", 0),
        "days": settings.QUEUE_LIFETIME_DAYS,
        "expires_on": job.expires_at.date().isoformat() if job.expires_at else "",
    }
    closing = (
        QUEUE_EMAIL_CLOSING_INDIVIDUAL
        if is_individual_teacher(teacher)
        else QUEUE_EMAIL_CLOSING_LICENSE
    )
    text = body.format(**values)
    if kind == "queued":
        text = f"{text}\n\n{closing}"
    send_email_task.delay(
        subject=subject,
        message=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[teacher.email],
    )
