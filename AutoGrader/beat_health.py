"""
Watchdog over Celery Beat itself.

Generalizes the pattern already used for the Stripe webhook ledger (see
billing.tasks.sweep_stale_stripe_events): a scheduled job's own success or
failure is one thing, but nobody notices if Beat simply stops scheduling
it at all - a crashed or duplicated Beat process is silent otherwise.

django_celery_beat updates PeriodicTask.last_run_at every time Beat
dispatches a task, independent of whether the task itself succeeds,
fails, or is a deliberate no-op (e.g. nightly_stripe_live_qa when
ENABLE_STRIPE_LIVE_QA is unset). That single field is therefore a clean
signal for "did Beat actually schedule this," decoupled from whatever the
task's own business logic does once it runs.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _humanize(delta):
    """Render a timedelta as a short, human-readable string, e.g. '42 minutes'."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    return ", ".join(parts)


def find_overdue_tasks(now=None):
    """
    Compare every entry in settings.BEAT_HEALTH_EXPECTATIONS against the
    matching PeriodicTask row, returning a list of human-readable
    "<task> has not run for <gap>; expected every <interval>." strings for
    anything overdue. Pure/side-effect-free (no logging) so it can be
    tested and reused independently of the task wrapper below.
    """
    # Imported lazily, same reasoning as AutoGrader.error_messages: this
    # module is pulled in from AutoGrader/celery.py, which is imported by
    # AutoGrader/__init__.py - i.e. before django.setup() has necessarily
    # finished. A module-level `from django_celery_beat.models import
    # PeriodicTask` would raise AppRegistryNotReady at that point.
    from django.conf import settings
    from django.utils import timezone
    from django_celery_beat.models import PeriodicTask

    now = now or timezone.now()
    expectations = getattr(settings, "BEAT_HEALTH_EXPECTATIONS", {})
    if not expectations:
        return []

    periodic_tasks = {
        pt.name: pt for pt in PeriodicTask.objects.filter(name__in=expectations.keys())
    }

    overdue = []
    for task_name, (expected_interval, alert_threshold) in expectations.items():
        periodic_task = periodic_tasks.get(task_name)

        if periodic_task is None:
            overdue.append(
                f"{task_name} has no matching scheduled task (missing or "
                f"renamed); expected every {_humanize(expected_interval)}."
            )
            continue

        if not periodic_task.enabled:
            continue

        # Never fired yet: use the row's creation/last-edit time as the
        # reference point rather than skipping it outright, so a
        # newly-registered task that silently never starts firing still
        # gets caught once enough time has passed - not just a task that
        # stops firing after a healthy run.
        reference_time = periodic_task.last_run_at or periodic_task.date_changed
        gap = now - reference_time

        if gap > alert_threshold:
            overdue.append(
                f"{task_name} has not run for {_humanize(gap)}; "
                f"expected every {_humanize(expected_interval)}."
            )

    return overdue


@shared_task(name="AutoGrader.beat_health.check_beat_health")
def check_beat_health():
    """
    Log an ERROR naming every overdue task, so an alert says exactly what
    stopped rather than just "Celery is broken."

    This task cannot detect Beat being fully dead or duplicated by
    itself - if Beat doesn't run, this doesn't run either, so nothing
    here can page anyone on its own. AutoGrader.health.health's "beat"
    check reads this task's own last_run_at from a different process (the
    web process, not Beat), which is what actually closes that gap for an
    external uptime monitor polling /health.
    """
    overdue = find_overdue_tasks()

    if not overdue:
        return "All monitored Beat tasks are on schedule."

    message = "Beat schedule drift detected:\n" + "\n".join(
        f"- {line}" for line in overdue
    )
    logger.error(message)
    return message
