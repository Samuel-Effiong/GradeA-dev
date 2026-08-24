import json
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django_celery_beat.models import ClockedSchedule, PeriodicTask

from assignments.models import Assignment, AssignmentGenerationSession, AssignmentStatus
from assignments.rigor import score_assignment
from assignments.services import _strip_html_from_title

ASSIGNMENT_DUE_REMINDER_OFFSETS = (24, 1)


def delete_cache_patterns(*patterns):
    if not hasattr(cache, "delete_pattern"):
        return

    for pattern in patterns:
        cache.delete_pattern(pattern)


def assignment_due_reminder_task_name(assignment_id, hours_before):
    return f"assignment-due-reminder-{assignment_id}-{hours_before}h"


def sync_assignment_due_reminder_tasks(instance):
    for hours_before in ASSIGNMENT_DUE_REMINDER_OFFSETS:
        task_name = assignment_due_reminder_task_name(instance.id, hours_before)

        if not instance.due_date or instance.status != AssignmentStatus.PUBLISHED:
            PeriodicTask.objects.filter(name=task_name).delete()
            continue

        reminder_time = instance.due_date - timedelta(hours=hours_before)

        if reminder_time <= timezone.now():
            PeriodicTask.objects.filter(name=task_name).delete()
            continue

        clocked_schedule, _ = ClockedSchedule.objects.get_or_create(
            clocked_time=reminder_time
        )

        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "task": "assignments.tasks.send_assignment_due_reminder",
                "clocked": clocked_schedule,
                "one_off": True,
                "enabled": True,
                "args": json.dumps([str(instance.id), hours_before]),
            },
        )


def queue_new_assignment_posted_notification(instance, created):
    previous_status = getattr(instance, "_previous_status", None)
    was_just_published = instance.status == AssignmentStatus.PUBLISHED and (
        created or previous_status != AssignmentStatus.PUBLISHED
    )

    if not was_just_published:
        return

    assignment_id = str(instance.id)

    def enqueue_notification():
        from assignments.tasks import send_new_assignment_posted_notification
        from AutoGrader.dispatch import safe_delay

        safe_delay(send_new_assignment_posted_notification, assignment_id)

    transaction.on_commit(enqueue_notification)


@receiver([post_save, post_delete], sender=Assignment)
def clear_assignment_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=AssignmentGenerationSession)
def clear_assignment_generation_session_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*assignmentgenerationsession*",
    )


@receiver(post_save, sender=Assignment)
def schedule_auto_grading(sender, instance, created, **kwargs):
    task_name = f"auto-grade-assignment-{instance.id}"
    sync_assignment_due_reminder_tasks(instance)
    queue_new_assignment_posted_notification(instance, created)

    if not instance.due_date or not instance.auto_grade_on_due_date:
        PeriodicTask.objects.filter(name=task_name).delete()
        return

    clocked_schedule, _ = ClockedSchedule.objects.get_or_create(
        clocked_time=instance.due_date
    )

    PeriodicTask.objects.update_or_create(
        name=task_name,
        defaults={
            "task": "assignments.tasks.auto_grade_due_assignment",
            "clocked": clocked_schedule,
            "one_off": True,
            "enabled": True,
            "args": json.dumps([str(instance.id)]),
        },
    )


@receiver(pre_save, sender=Assignment)
def sync_assignment_rigor(sender, instance, update_fields=None, **kwargs):
    """Keep the denormalized rigor columns in step with `questions`.

    Runs on every full save, so any write path -- the DRF serializers, the AI
    extraction tasks, the admin, a shell -- lands consistent values without
    having to remember to call anything.

    A partial save that does not touch `questions` is skipped: the recomputed
    values could not be persisted by that UPDATE anyway (Django writes only
    the named columns), so doing the work would just burn CPU. No Assignment
    save path currently passes `questions` in update_fields; if one is ever
    added it must include the three rigor_* columns alongside it.
    """
    if update_fields is not None and "questions" not in update_fields:
        return

    demand, standards, coverage = score_assignment(instance.questions)
    instance.rigor_demand = demand
    instance.rigor_standards = standards
    instance.rigor_blooms_coverage = coverage


@receiver(pre_save, sender=Assignment)
def sanitize_assignment_title(sender, instance, **kwargs):
    """Strip HTML tags out of `title` on every save.

    AI extraction wraps the title in heading/paragraph tags meant for the
    rich editor/PDF body rendering (see format_assignment_standard_html in
    assignments/services.py), but `title` itself is read verbatim in
    plain-text contexts - notification emails, PDF headers/filenames, list
    views - so raw markup must never reach it. Runs on every write path
    (DRF serializers, AI extraction tasks, admin, shell) the same way the
    other pre_save hooks in this module do, and is not gated on
    `update_fields` - unlike sync_assignment_rigor, a partial save that only
    touches `title` must still be sanitized.
    """
    if instance.title:
        instance.title = _strip_html_from_title(instance.title)


@receiver(pre_save, sender=Assignment)
def handle_due_date_removal(sender, instance, **kwargs):
    instance._previous_status = None

    if instance.id:
        try:
            old_instance = Assignment.objects.get(id=instance.id)
            instance._previous_status = old_instance.status
            if (
                old_instance.auto_grade_on_due_date
                and not instance.auto_grade_on_due_date
            ):
                PeriodicTask.objects.filter(
                    name=f"auto-grade-assignment-{instance.id}"
                ).delete()
            elif old_instance.due_date and not instance.due_date:
                PeriodicTask.objects.filter(
                    name=f"auto-grade-assignment-{instance.id}"
                ).delete()
        except Assignment.DoesNotExist:
            pass


@receiver(post_delete, sender=Assignment)
def delete_auto_grading_task(sender, instance, **kwargs):
    PeriodicTask.objects.filter(name=f"auto-grade-assignment-{instance.id}").delete()
    for hours_before in ASSIGNMENT_DUE_REMINDER_OFFSETS:
        PeriodicTask.objects.filter(
            name=assignment_due_reminder_task_name(instance.id, hours_before)
        ).delete()
