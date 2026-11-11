"""Epic B1b-7: the low-balance warning and `balance_status` (FR-B-05; plan
§3.9; 08b §1.5; NOTES §7.2 D7-a/D7-b, rulings §7.5 and §7.7, contract
§7.10).

The allocation (the "100%") is plan_used_credits() + plan_remaining_credits():
the live non-OVERAGE buckets, the same basis as the credits screen's "% of
plan consumed". Spendable is holds.spendable_credits. The cycle is the live
MONTHLY bucket with the latest created_at, else the live non-OVERAGE bucket
with a non-null expires_at and the latest expires_at; {cycle_end} is its
expires_at.

Levels: 0 (nothing spendable), 10 (<= 10%), 25 (<= 25%). One email per
level per cycle, claimed by one conditional UPDATE on the wallet's two
warning fields, so concurrent settlements send at most one. An allocation of
0 (a spent license budget hands the teacher an empty month) or no cycle
bucket sends nothing and writes nothing: the 0% text ("You've used all of
this cycle's credits") would not be true.

The emails are approved texts #11-#13 with closing #6/#7, and stay off
(LOW_BALANCE_EMAIL_APPROVED False) until B1b-8, as the queue emails do.
"""

import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import CONVERSION_FACTOR, CreditBucket, CreditBucketType, CreditWallet

logger = logging.getLogger(__name__)

# B1b-8 switches the texts on (08b §5 B1b-8 row).
LOW_BALANCE_EMAIL_APPROVED = False

# team/approved_wording_b1b_2026-11-09.md #11-#13, verbatim.
LOW_BALANCE_TEXTS = {
    25: (
        "You have 25% of your credits left",
        "You've used 75% of this cycle's credits. You have {remaining} credits "
        "left until {cycle_end}.",
    ),
    10: (
        "You have 10% of your credits left",
        "You've used 90% of this cycle's credits. You have {remaining} credits "
        "left until {cycle_end}.",
    ),
    0: (
        "You're out of credits",
        "You've used all of this cycle's credits. Grading stops until you add "
        "credits or your credits renew on {cycle_end}. Batches that run out "
        "will wait for {days} days.",
    ),
}
# #6 and #7, the same closing lines as the queue emails.
CLOSING_INDIVIDUAL = "Top up your credits to continue."
CLOSING_LICENSE = "Ask your school administrator for more credits to continue."

STATUS_OF_LEVEL = {25: "low", 10: "very_low", 0: "out"}


def _live(queryset):
    now = timezone.now()
    return queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def cycle_bucket(wallet):
    """The bucket the cycle is read from, or None (ruling §7.7 (ii))."""
    buckets = _live(CreditBucket.objects.filter(wallet=wallet))
    monthly = (
        buckets.filter(bucket_type=CreditBucketType.MONTHLY)
        .order_by("-created_at", "-pk")
        .first()
    )
    if monthly is not None:
        return monthly
    return (
        buckets.exclude(bucket_type=CreditBucketType.OVERAGE)
        .filter(expires_at__isnull=False)
        .order_by("-expires_at", "-pk")
        .first()
    )


def allocation(wallet):
    return wallet.plan_used_credits() + wallet.plan_remaining_credits()


def level_of(spendable, total):
    """0, 10, 25 or None. None whenever the allocation is <= 0."""
    if total <= 0:
        return None
    if spendable <= 0:
        return 0
    if spendable * 100 <= 10 * total:
        return 10
    if spendable * 100 <= 25 * total:
        return 25
    return None


def _spendable(wallet):
    from .holds import spendable_credits

    return spendable_credits(wallet)


def balance_status(wallet):
    """null / "low" / "very_low" / "out" (ruling §7.5 Q7-4, §7.7 (i))."""
    spendable = _spendable(wallet)
    total = allocation(wallet)
    if total <= 0 or cycle_bucket(wallet) is None:
        return "out" if spendable <= 0 else None
    return STATUS_OF_LEVEL.get(level_of(spendable, total))


def check_low_balance(wallet_id):
    """After a settlement commits: claim and send the level crossed, once
    per level per cycle. Returns the level claimed, or None."""
    wallet = CreditWallet.objects.filter(pk=wallet_id).first()
    if wallet is None:
        return None
    total = allocation(wallet)
    cycle = cycle_bucket(wallet)
    if total <= 0 or cycle is None:
        return None
    spendable = _spendable(wallet)
    level = level_of(spendable, total)
    if level is None:
        return None
    # The key is a DateField (no migration, ruling §7.9): two MONTHLY
    # buckets on one date only mean fewer emails, never extra ones.
    key = cycle.created_at.date()
    claimed = (
        CreditWallet.objects.filter(pk=wallet.pk)
        .filter(
            ~Q(low_balance_warned_cycle_start=key)
            | Q(low_balance_warned_cycle_start__isnull=True)
            | Q(low_balance_warned_level__isnull=True)
            | Q(low_balance_warned_level__gt=level)
        )
        .update(low_balance_warned_level=level, low_balance_warned_cycle_start=key)
    )
    if not claimed:
        return None
    notify(wallet, level, spendable, cycle)
    return level


def notify(wallet, level, spendable, cycle):
    if not LOW_BALANCE_EMAIL_APPROVED:
        logger.warning(
            "Wallet %s reached the %s%% low-balance level; the texts await "
            "owner approval, so no email was sent.",
            wallet.pk,
            level,
        )
        return
    _send(wallet, level, spendable, cycle)


def _send(wallet, level, spendable, cycle):
    from AutoGrader.tasks import send_email_task

    from .errors import is_individual_teacher

    teacher = wallet.user
    if teacher is None or not teacher.email:
        return
    subject, body = LOW_BALANCE_TEXTS[level]
    text = body.format(
        remaining=spendable // CONVERSION_FACTOR,
        cycle_end=cycle.expires_at.date().isoformat(),
        days=settings.QUEUE_LIFETIME_DAYS,
    )
    closing = CLOSING_INDIVIDUAL if is_individual_teacher(teacher) else CLOSING_LICENSE
    send_email_task.delay(
        subject=subject,
        message=f"{text}\n\n{closing}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[teacher.email],
    )
