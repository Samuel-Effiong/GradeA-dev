"""
Repair failed Stripe webhook events by re-running their stored payload.

WHEN YOU NEED THIS
-------------------
Stripe retries a failed webhook delivery for ~3 days and then gives up
forever. `billing.tasks.sweep_stale_stripe_events` logs an ERROR when a
FAILED event crosses that line — at that point the customer may have paid
without receiving anything, and only a human can fix it. This is that fix.

WHY IT IS NOT AUTOMATIC
------------------------
Some handlers move real money (stripe.Refund.create,
Subscription.modify). If a handler failed AFTER issuing a refund but
BEFORE finishing its database work, re-running it issues that refund a
second time — and no database rollback can undo money that already left
Stripe. So this is deliberately a command a person runs after looking at
the event, not a loop that runs unattended.

USAGE
------
    # See what would be replayed (this is the default — nothing runs):
    manage.py replay_stripe_events

    # Narrow it down:
    manage.py replay_stripe_events --event-type invoice.payment_succeeded
    manage.py replay_stripe_events --since 2026-08-01

    # Actually replay, once you have checked the event in Stripe:
    manage.py replay_stripe_events --event-id evt_123 --apply

Only FAILED events are ever eligible. SUCCEEDED events are never touched —
replaying one would double-grant credits.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.models import StripeEvent, StripeEventStatus
from billing.webhooks import _EVENT_HANDLERS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Replay FAILED Stripe webhook events from their stored payload. "
        "Defaults to a dry run; pass --apply to actually re-run handlers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-id",
            help="Replay exactly this Stripe event id (e.g. evt_123).",
        )
        parser.add_argument(
            "--event-type",
            help="Only replay events of this type (e.g. invoice.payment_succeeded).",
        )
        parser.add_argument(
            "--since",
            help=(
                "Only replay events first seen at/after this ISO-8601 "
                "datetime (e.g. 2026-08-01 or 2026-08-01T00:00:00Z)."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of events to process (default 25).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Actually re-run the handlers. Without this nothing is "
                "executed and nothing is written — the command only reports "
                "what it would do."
            ),
        )

    def handle(self, *args, **options):
        events = self._select_events(options)

        if not events:
            self.stdout.write(
                self.style.SUCCESS("No FAILED Stripe events match those filters.")
            )
            return

        apply_changes = options["apply"]
        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — nothing will be executed. Re-run with --apply "
                    "once you have confirmed each event is safe to replay "
                    "(check in Stripe whether a refund or charge already went "
                    "through)."
                )
            )

        replayed = 0
        failed = 0

        for event in events:
            handler = _EVENT_HANDLERS.get(event.event_type)
            label = (
                f"{event.stripe_event_id} ({event.event_type}, "
                f"attempts={event.attempts}, first seen {event.processed_at:%Y-%m-%d %H:%M})"
            )

            if handler is None:
                self.stdout.write(
                    f"  SKIP {label}: no handler registered for this event type."
                )
                continue

            if not event.payload:
                self.stdout.write(f"  SKIP {label}: no stored payload to replay.")
                continue

            if not apply_changes:
                self.stdout.write(f"  WOULD REPLAY {label}")
                if event.last_error:
                    self.stdout.write(f"      last error: {event.last_error[:200]}")
                continue

            try:
                handler(event.payload["object"])
            except Exception as exc:
                failed += 1
                StripeEvent.objects.filter(pk=event.pk).update(
                    status=StripeEventStatus.FAILED,
                    completed_at=timezone.now(),
                    last_error=repr(exc)[:2000],
                )
                self.stderr.write(self.style.ERROR(f"  FAILED AGAIN {label}: {exc}"))
                logger.exception(
                    "Manual replay of Stripe event %s failed.", event.stripe_event_id
                )
                continue

            StripeEvent.objects.filter(pk=event.pk).update(
                status=StripeEventStatus.SUCCEEDED,
                completed_at=timezone.now(),
                last_error="",
            )
            replayed += 1
            self.stdout.write(self.style.SUCCESS(f"  REPLAYED {label}"))
            logger.warning(
                "Stripe event %s was manually replayed via "
                "replay_stripe_events by an operator.",
                event.stripe_event_id,
            )

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Replay complete: {replayed} succeeded, {failed} failed again."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(events)} event(s) would be replayed. "
                    f"Re-run with --apply to execute."
                )
            )

    def _select_events(self, options):
        # FAILED only, always. A SUCCEEDED event has already been applied;
        # replaying one would double-grant credits or re-charge a customer.
        queryset = StripeEvent.objects.filter(status=StripeEventStatus.FAILED)

        if options["event_id"]:
            queryset = queryset.filter(stripe_event_id=options["event_id"])
        if options["event_type"]:
            queryset = queryset.filter(event_type=options["event_type"])
        if options["since"]:
            since = parse_datetime(options["since"]) or parse_datetime(
                f"{options['since']}T00:00:00+00:00"
            )
            if since is None:
                raise CommandError(
                    f"Could not parse --since value {options['since']!r}. "
                    f"Use an ISO-8601 date or datetime."
                )
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
            queryset = queryset.filter(processed_at__gte=since)

        return list(queryset.order_by("processed_at")[: options["limit"]])
