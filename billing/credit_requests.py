"""Epic B1c: credit requests (plan 08 §3.8, FR-B-11; STEP0_PLAN; owner go
2026-11-11).

A licensed teacher asks their license's admin for more credits. The admin
approves, which runs the normal overage purchase for that teacher keyed to
the request (`credit_request_id` on the purchase intent, in the checkout
metadata and on the offline request), or declines. The request is GRANTED
when the purchase is fulfilled, goes back to waiting when the checkout is
abandoned or the offline payment is rejected, and is CLOSED when the teacher
loses access to the license.

States::

    PENDING -approve-> APPROVED -fulfilled-> GRANTED
       |                  |- checkout expired / stale / unlinked / offline
       |                  |  rejected -> PENDING (note)
       |                  `- teacher left / license ended -> CLOSED
       |- decline -> DECLINED
       |- withdraw -> WITHDRAWN
       `- teacher left / license ended -> CLOSED

Every move is a conditional UPDATE on the current status, so a double-click
or a race gives one effect and one refusal.

The API and the Beat sweep are behind CREDIT_REQUESTS_ENABLED. The purchase
hooks below act only on rows the API made, so they run whatever the flag
says; with no request rows they do nothing.
"""

import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.context import current_request_actor
from audit.emitter import emit
from audit.enums import AuditAction

from .errors import BillingRequestError
from .models import (
    CREDIT_REQUEST_OPEN_STATUSES,
    CreditRequest,
    CreditRequestStatus,
    LicenseOverageOfflineRequest,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    SchoolCreditAllocation,
)

logger = logging.getLogger(__name__)

#: OWNER_SHEET §4, W1-W9 and W11-W13, approved word for word (SM relay,
#: 2026-11-11). W10 is CreditRequestStatus's labels. Any change needs the
#: owner.
CREDIT_REQUEST_TEXTS = {
    # W1, to the license admin.
    "admin_new_subject": "{teacher} asked for more credits",
    "admin_new_body": (
        "{teacher} asked for {n} more credit blocks for {school}. Their note: "
        '"{note}". Open your credit requests to approve or decline.'
    ),
    # W2, to the teacher.
    "granted_subject": "Your extra credits are ready",
    "granted_body": (
        "{admin} approved your request. {n} credit blocks have been added to "
        "your account."
    ),
    # W3, to the teacher.
    "declined_subject": "Your credit request was declined",
    "declined_body": (
        "{admin} declined your request for {n} credit blocks. Their reason: "
        '"{reason}".'
    ),
    # W4, to the teacher (owner Q1: yes).
    "closed_subject": "Your credit request was closed",
    "closed_body": (
        "Your request for {n} credit blocks was closed because you are no "
        "longer on {school}'s license."
    ),
    # W5, to the license admin.
    "refund_subject": "We refunded a duplicate payment",
    "refund_body": (
        "Two payments went through for the same credit request. We kept one "
        "and refunded the other ({amount}) to your card."
    ),
    # W6-W9, API errors.
    "error_open": (
        "You already have a credit request waiting. Withdraw it first if you "
        "want to change it."
    ),
    "error_no_license": "You need to be on a school license to ask for credits.",
    "error_answered": "This request has already been answered.",
    "error_withdraw": "You can only withdraw a request while it is waiting.",
    # W11-W13, notes shown to admins.
    "note_checkout_closed": (
        "The payment page closed before payment. The request is waiting again."
    ),
    "note_offline_rejected": (
        "Our team could not confirm the offline payment: {reason}. The "
        "request is waiting again."
    ),
    "note_teacher_left": "Closed: the teacher left the license.",
    "note_license_ended": "Closed: the license ended.",
}

#: W1 when the teacher left no note: the same text without its note sentence.
_NOTE_SENTENCE = ' Their note: "{note}".'

#: Plan 08 §3.8: an APPROVED request with no purchase linked after this long
#: goes back to waiting (review r2 M-e).
UNLINKED_AFTER = timedelta(minutes=15)
#: The stale-APPROVED sweep waits this long past the stored expiry, so the
#: checkout.session.expired webhook normally gets there first.
EXPIRY_MARGIN = timedelta(minutes=10)

TEACHER_LEFT = "teacher_left"
LICENSE_ENDED = "license_ended"
_CLOSE_NOTES = {
    TEACHER_LEFT: "note_teacher_left",
    LICENSE_ENDED: "note_license_ended",
}

OPEN = CREDIT_REQUEST_OPEN_STATUSES
PENDING = CreditRequestStatus.PENDING
APPROVED = CreditRequestStatus.APPROVED


class CreditRequestError(BillingRequestError):
    """A refusal with its HTTP status (400, 404 or 409)."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def enabled():
    return bool(getattr(settings, "CREDIT_REQUESTS_ENABLED", False))


def text(key, **fields):
    return CREDIT_REQUEST_TEXTS[key].format(**fields)


# --------------------------------------------------------------------------
# Who may do what
# --------------------------------------------------------------------------


def _is_super(user):
    return bool(
        user is not None and user.is_superuser and user.user_type == "SUPER_ADMIN"
    )


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.email


def _active_allocation(teacher_id, license_id=None):
    """The teacher's active, non-admin allocation on an active license."""
    rows = SchoolCreditAllocation.objects.filter(
        user_id=teacher_id,
        is_active=True,
        is_admin_allocation=False,
        license_subscription__is_active=True,
    )
    if license_id is not None:
        rows = rows.filter(license_subscription_id=license_id)
    return rows.select_related("license_subscription").first()


def can_answer(user, row):
    return _is_super(user) or (
        user is not None and row.license_subscription.admin_user_id == user.id
    )


def visible_to(user):
    """Teacher: their own requests. License admin: their licenses'. Super
    admin: every request (owner Q3)."""
    rows = CreditRequest.objects.select_related(
        "teacher", "license_subscription", "decided_by"
    )
    if _is_super(user):
        return rows
    return rows.filter(Q(teacher=user) | Q(license_subscription__admin_user=user))


def answerable_by(user):
    rows = CreditRequest.objects.all()
    if _is_super(user):
        return rows
    return rows.filter(license_subscription__admin_user=user)


def counts_for(user):
    rows = answerable_by(user)
    return {
        "pending": rows.filter(status=PENDING).count(),
        "answered": rows.exclude(status=PENDING).count(),
        "granted": rows.filter(status=CreditRequestStatus.GRANTED).count(),
        "declined": rows.filter(status=CreditRequestStatus.DECLINED).count(),
    }


# --------------------------------------------------------------------------
# Recording and telling people
# --------------------------------------------------------------------------


def _move(pk, from_statuses, **fields):
    """The conditional status UPDATE every transition goes through."""
    return CreditRequest.objects.filter(pk=pk, status__in=from_statuses).update(
        updated_at=timezone.now(), **fields
    )


def _audit(action, row, actor=None, **metadata):
    emit(
        action,
        actor=actor or current_request_actor(),
        target_type="CreditRequest",
        target_id=row.pk,
        school_id=row.license_subscription.school_id,
        metadata={
            "blocks": row.requested_blocks,
            "license_id": str(row.license_subscription_id),
            **metadata,
        },
    )


def _email(recipient, subject, body):
    if not recipient:
        return

    def _send():
        from AutoGrader.dispatch import safe_delay
        from AutoGrader.tasks import send_email_task

        safe_delay(
            send_email_task,
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
        )

    transaction.on_commit(_send, robust=True)


def _email_admin_new(row):
    admin = row.license_subscription.admin_user
    body_template = CREDIT_REQUEST_TEXTS["admin_new_body"]
    if not row.message:
        body_template = body_template.replace(_NOTE_SENTENCE, "")
    teacher = _name(row.teacher)
    _email(
        admin.email if admin else None,
        text("admin_new_subject", teacher=teacher),
        body_template.format(
            teacher=teacher,
            n=row.requested_blocks,
            school=row.license_subscription.school.name,
            note=row.message,
        ),
    )


def _email_teacher(row, kind, **fields):
    _email(
        row.teacher.email,
        text(f"{kind}_subject"),
        text(f"{kind}_body", n=row.requested_blocks, **fields),
    )


def _reload(pk):
    return CreditRequest.objects.select_related(
        "teacher", "license_subscription", "license_subscription__school", "decided_by"
    ).get(pk=pk)


# --------------------------------------------------------------------------
# Create, decline, withdraw
# --------------------------------------------------------------------------


def create_request(teacher, requested_blocks, message="", ai_job=None):
    allocation = _active_allocation(teacher.pk)
    if allocation is None:
        raise CreditRequestError(text("error_no_license"), 400)
    try:
        with transaction.atomic():
            row = CreditRequest.objects.create(
                teacher=teacher,
                license_subscription=allocation.license_subscription,
                requested_blocks=requested_blocks,
                message=message or "",
                ai_job=ai_job,
            )
    except IntegrityError as exc:
        # The partial unique index: one open request per teacher.
        raise CreditRequestError(text("error_open"), 409) from exc
    row = _reload(row.pk)
    _audit(AuditAction.CREDIT_REQUEST_CREATE, row, actor=teacher)
    _email_admin_new(row)
    return row


def _for_answer(request_id, actor):
    row = (
        CreditRequest.objects.select_related(
            "teacher", "license_subscription", "license_subscription__school"
        )
        .filter(pk=request_id)
        .first()
    )
    if row is None or not can_answer(actor, row):
        raise CreditRequestError("Not found.", 404)
    return row


def decline_request(request_id, actor, reason):
    row = _for_answer(request_id, actor)
    reason = (reason or "")[:500]
    moved = _move(
        row.pk,
        [PENDING],
        status=CreditRequestStatus.DECLINED,
        decided_by=actor,
        decided_at=timezone.now(),
        decline_reason=reason,
    )
    if not moved:
        raise CreditRequestError(text("error_answered"), 409)
    _audit(AuditAction.CREDIT_REQUEST_DECIDE, row, actor=actor, decision="decline")
    _email_teacher(row, "declined", admin=_name(actor), reason=reason)
    return _reload(row.pk)


def withdraw_request(request_id, teacher):
    row = (
        CreditRequest.objects.select_related("license_subscription")
        .filter(pk=request_id, teacher=teacher)
        .first()
    )
    if row is None:
        raise CreditRequestError("Not found.", 404)
    if not _move(row.pk, [PENDING], status=CreditRequestStatus.WITHDRAWN):
        raise CreditRequestError(text("error_withdraw"), 409)
    _audit(AuditAction.CREDIT_REQUEST_DECIDE, row, actor=teacher, decision="withdraw")
    return _reload(row.pk)


# --------------------------------------------------------------------------
# Approve (plan 08 §3.8, ordered; review r1 M6)
# --------------------------------------------------------------------------


def approve_request(
    request_id,
    actor,
    *,
    blocks=None,
    payment_method="stripe",
    success_url=None,
    cancel_url=None,
):
    """1. PENDING -> APPROVED, committed; 2. the purchase, outside any
    transaction (it calls Stripe); 3. on any error, back to PENDING with the
    error noted, and the error raised; 4. on success, link the purchase.
    A super admin's purchase grants at once, so the request is GRANTED by the
    grant hook inside step 2."""
    from .license_service import LicenseSubscriptionService

    row = _for_answer(request_id, actor)
    if row.status != PENDING:
        raise CreditRequestError(text("error_answered"), 409)
    if _active_allocation(row.teacher_id, row.license_subscription_id) is None:
        close_request(row.pk, _close_reason(row))
        raise CreditRequestError(text("error_answered"), 409)
    blocks = int(blocks or row.requested_blocks)
    moved = _move(
        row.pk,
        [PENDING],
        status=APPROVED,
        decided_by=actor,
        decided_at=timezone.now(),
        note="",
    )
    if not moved:
        raise CreditRequestError(text("error_answered"), 409)
    _audit(AuditAction.CREDIT_REQUEST_DECIDE, row, actor=actor, decision="approve")
    try:
        result = LicenseSubscriptionService.initiate_overage_purchase(
            row.license_subscription,
            requesting_user=actor,
            total_blocks=blocks,
            allocations={str(row.teacher_id): blocks},
            success_url=success_url,
            cancel_url=cancel_url,
            payment_method=payment_method,
            credit_request_id=row.pk,
        )
    except Exception as exc:
        _move(row.pk, [APPROVED], status=PENDING, note=str(exc)[:500])
        raise
    link = {}
    if result.get("action") == "checkout" and result.get("intent_id"):
        link["purchase_intent_id"] = result["intent_id"]
    elif result.get("action") == "offline_request_pending" and result.get("request_id"):
        link["offline_request_id"] = result["request_id"]
    if link:
        CreditRequest.objects.filter(pk=row.pk).update(**link)
    return result


# --------------------------------------------------------------------------
# Hooks in the purchase paths
# --------------------------------------------------------------------------


def on_purchase_fulfilled(
    credit_request_id,
    *,
    granted_teacher_ids,
    intent=None,
    offline_request=None,
    actor=None,
    close_reason=TEACHER_LEFT,
):
    """In the fulfilment's transaction (webhook, offline approval, super admin
    grant). Matched by credit_request_id, not only by the link, so a crash
    between approve's steps 1 and 4 still ends GRANTED (review r2 M-e);
    accepts PENDING or APPROVED. The teacher skipped at fulfilment: CLOSED,
    never PENDING. Every other open intent for the request is SUPERSEDED and
    its checkout expired after commit (review r3 r3-a)."""
    if not credit_request_id:
        return None
    row = (
        CreditRequest.objects.select_for_update(of=("self",))
        .select_related("teacher", "license_subscription", "decided_by")
        .filter(pk=credit_request_id)
        .first()
    )
    if row is None:
        return None
    link = {}
    if intent is not None:
        link["purchase_intent"] = intent
    if offline_request is not None:
        link["offline_request"] = offline_request
    granted = str(row.teacher_id) in {str(t) for t in granted_teacher_ids}
    if granted:
        moved = _move(row.pk, OPEN, status=CreditRequestStatus.GRANTED, note="", **link)
    else:
        moved = _move(
            row.pk,
            OPEN,
            status=CreditRequestStatus.CLOSED,
            note=text(_CLOSE_NOTES[close_reason]),
            **link,
        )
    _supersede_other_intents(row.pk, keep=intent)
    if not moved:
        return None
    if granted:
        _audit(AuditAction.CREDIT_REQUEST_DECIDE, row, actor=actor, decision="granted")
        _email_teacher(row, "granted", admin=_name(row.decided_by or actor))
        return CreditRequestStatus.GRANTED
    _audit(
        AuditAction.CREDIT_REQUEST_DECIDE,
        row,
        actor=actor,
        decision="close",
        reason_label=close_reason,
    )
    _email_teacher(row, "closed", school=row.license_subscription.school.name)
    return CreditRequestStatus.CLOSED


def _supersede_other_intents(request_pk, keep=None):
    others = LicenseOveragePurchaseIntent.objects.filter(
        credit_request_id=request_pk, status=LicenseOveragePurchaseStatus.PENDING
    )
    if keep is not None:
        others = others.exclude(pk=keep.pk)
    for other in others.select_for_update():
        LicenseOveragePurchaseIntent.objects.filter(
            pk=other.pk, status=LicenseOveragePurchaseStatus.PENDING
        ).update(
            status=LicenseOveragePurchaseStatus.SUPERSEDED,
            failure_reason=(
                "Superseded: another purchase for credit request "
                f"{request_pk} completed first."
            ),
            updated_at=timezone.now(),
        )
        if other.stripe_checkout_session_id:
            transaction.on_commit(
                partial(_expire_session, other.stripe_checkout_session_id),
                robust=True,
            )


def _expire_session(session_id):
    import stripe

    try:
        stripe.checkout.Session.expire(session_id)
    except stripe.error.StripeError as exc:
        # A paid-anyway session is refused and refunded by the completed
        # handler, so a failed expire costs nothing.
        logger.warning(
            "Could not expire superseded checkout session %s: %s",
            session_id,
            type(exc).__name__,
        )


def refuse_superseded_payment(intent, session):
    """The completed handler's branch for a SUPERSEDED intent with no grants
    (r4 m-3, m3-a): grant nothing, refund the whole payment after commit
    through the departed-share refund path, and tell the admin (W5)."""
    from .departed_share_refund import format_amount

    payment_intent = session.get("payment_intent")
    LicenseOveragePurchaseIntent.objects.filter(pk=intent.pk).update(
        stripe_payment_intent_id=payment_intent, updated_at=timezone.now()
    )
    if session.get("payment_status") != "paid" or not payment_intent:
        logger.warning(
            "Superseded overage intent %s: session %s completed unpaid; "
            "nothing granted, nothing to refund.",
            intent.id,
            session.get("id"),
        )
        return
    amount = int(session.get("amount_total") or intent.amount_cents)
    license_sub = intent.license_subscription
    admin = license_sub.admin_user or intent.initiated_by
    intent_id = str(intent.id)
    logger.error(
        "Superseded overage intent %s was paid (session %s): nothing granted; "
        "refunding %s cents.",
        intent_id,
        session.get("id"),
        amount,
    )

    def _refund():
        from AutoGrader.dispatch import safe_delay

        from .departed_share_refund import _manual
        from .tasks import refund_departed_teacher_share

        if safe_delay(refund_departed_teacher_share, intent_id, amount) is None:
            _manual(
                "the refund of superseded overage intent %s (%s cents) could "
                "not be queued: broker unavailable.",
                intent_id,
                amount,
            )

    transaction.on_commit(_refund, robust=True)
    _email(
        admin.email if admin else None,
        text("refund_subject"),
        text("refund_body", amount=format_amount(amount)),
    )


def on_checkout_expired(session):
    """checkout.session.expired for a credit request's checkout: the intent is
    SUPERSEDED (abandoned) and the request goes back to waiting (W11)."""
    metadata = session.get("metadata") or {}
    intent_id = metadata.get("intent_id")
    if metadata.get("flow") != "license_overage_purchase_checkout" or not intent_id:
        return None
    intent = (
        LicenseOveragePurchaseIntent.objects.select_for_update()
        .filter(pk=intent_id)
        .first()
    )
    if intent is None or not intent.credit_request_id:
        return None
    LicenseOveragePurchaseIntent.objects.filter(
        pk=intent.pk, status=LicenseOveragePurchaseStatus.PENDING
    ).update(
        status=LicenseOveragePurchaseStatus.SUPERSEDED,
        failure_reason="Checkout expired before payment.",
        updated_at=timezone.now(),
    )
    return _back_to_waiting(intent.credit_request_id, intent=intent)


def _back_to_waiting(request_pk, *, intent=None, offline_request=None, note=None):
    """APPROVED -> PENDING, only while this purchase is the request's own
    (linked to it, or nothing linked yet)."""
    rows = CreditRequest.objects.filter(pk=request_pk, status=APPROVED)
    if intent is not None:
        rows = rows.filter(
            Q(purchase_intent__isnull=True) | Q(purchase_intent=intent),
            offline_request__isnull=True,
        )
    if offline_request is not None:
        rows = rows.filter(
            Q(offline_request__isnull=True) | Q(offline_request=offline_request),
            purchase_intent__isnull=True,
        )
    return rows.update(
        status=PENDING,
        note=note or text("note_checkout_closed"),
        purchase_intent=None,
        offline_request=None,
        updated_at=timezone.now(),
    )


def on_offline_rejected(offline_request):
    """In reject_overage_offline_request's transaction (review r2 M-d)."""
    if not offline_request.credit_request_id:
        return 0
    return _back_to_waiting(
        offline_request.credit_request_id,
        offline_request=offline_request,
        note=text(
            "note_offline_rejected", reason=offline_request.rejection_reason or ""
        ),
    )


# --------------------------------------------------------------------------
# Closing on loss of access (STEP0_PLAN §3, A1-A7)
# --------------------------------------------------------------------------


def _close_reason(row):
    license_sub = row.license_subscription
    return LICENSE_ENDED if not license_sub.is_active else TEACHER_LEFT


def close_request(request_pk, reason):
    row = (
        CreditRequest.objects.select_related(
            "teacher", "license_subscription", "license_subscription__school"
        )
        .filter(pk=request_pk)
        .first()
    )
    if row is None:
        return False
    if not _move(
        row.pk,
        OPEN,
        status=CreditRequestStatus.CLOSED,
        note=text(_CLOSE_NOTES[reason]),
    ):
        return False
    _audit(
        AuditAction.CREDIT_REQUEST_DECIDE, row, decision="close", reason_label=reason
    )
    _email_teacher(row, "closed", school=row.license_subscription.school.name)
    return True


def close_open_requests(license_sub, *, teacher=None, reason=LICENSE_ENDED):
    """The one close helper the access-ending paths call (A1, A2, A4-A7),
    in their transaction. Returns how many requests it closed."""
    rows = CreditRequest.objects.filter(
        license_subscription_id=license_sub.pk, status__in=OPEN
    )
    if teacher is not None:
        rows = rows.filter(teacher_id=teacher.pk)
    return sum(
        1 for pk in list(rows.values_list("pk", flat=True)) if close_request(pk, reason)
    )


# --------------------------------------------------------------------------
# The sweep (Beat, every 5 minutes)
# --------------------------------------------------------------------------


def sweep(now=None):
    """Back to waiting: a checkout past its stored expiry, and an APPROVED
    request with nothing linked after 15 minutes (linked instead, when a
    purchase carries its id). Closed: any open request whose teacher lost
    access by a path that did not close it (the catch-all)."""
    now = now or timezone.now()
    counts = {"expired": 0, "linked": 0, "unlinked": 0, "closed": 0}

    stale = CreditRequest.objects.filter(
        status=APPROVED,
        purchase_intent__status=LicenseOveragePurchaseStatus.PENDING,
        purchase_intent__session_expires_at__lt=now - EXPIRY_MARGIN,
    ).values_list("pk", "purchase_intent_id")
    for request_pk, intent_pk in list(stale):
        with transaction.atomic():
            superseded = LicenseOveragePurchaseIntent.objects.filter(
                pk=intent_pk, status=LicenseOveragePurchaseStatus.PENDING
            ).update(
                status=LicenseOveragePurchaseStatus.SUPERSEDED,
                failure_reason="Checkout expired before payment.",
                updated_at=now,
            )
            if superseded and _back_to_waiting(
                request_pk,
                intent=LicenseOveragePurchaseIntent(pk=intent_pk),
            ):
                counts["expired"] += 1

    unlinked = CreditRequest.objects.filter(
        status=APPROVED,
        purchase_intent__isnull=True,
        offline_request__isnull=True,
        decided_at__lt=now - UNLINKED_AFTER,
    ).values_list("pk", flat=True)
    for request_pk in list(unlinked):
        intent = (
            LicenseOveragePurchaseIntent.objects.filter(credit_request_id=request_pk)
            .order_by("-created_at")
            .first()
        )
        offline = (
            LicenseOverageOfflineRequest.objects.filter(credit_request_id=request_pk)
            .order_by("-created_at")
            .first()
        )
        if intent is not None or offline is not None:
            link: dict = (
                {"purchase_intent": intent}
                if intent is not None
                else {"offline_request": offline}
            )
            counts["linked"] += _move(request_pk, [APPROVED], **link)
        elif _back_to_waiting(request_pk):
            counts["unlinked"] += 1

    for row in list(
        CreditRequest.objects.filter(status__in=OPEN).select_related(
            "license_subscription"
        )
    ):
        if _active_allocation(row.teacher_id, row.license_subscription_id) is None:
            with transaction.atomic():
                if close_request(row.pk, _close_reason(row)):
                    counts["closed"] += 1
    return counts
