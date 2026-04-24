import json
from datetime import timedelta

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django_celery_beat.models import ClockedSchedule, PeriodicTask

from assignments.models import Assignment

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

        if not instance.due_date:
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


@receiver(post_save, sender=Assignment)
def schedule_auto_grading(sender, instance, created, **kwargs):
    task_name = f"auto-grade-assignment-{instance.id}"
    sync_assignment_due_reminder_tasks(instance)

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
def handle_due_date_removal(sender, instance, **kwargs):
    if instance.id:
        try:
            old_instance = Assignment.objects.get(id=instance.id)
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
