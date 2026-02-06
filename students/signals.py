from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from students.models import StudentSubmission


@receiver([post_save, post_delete], sender=StudentSubmission)
def clear_student_submission_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("studentsubmissions*")
        cache.delete_pattern("superadmin*")
        cache.delete_pattern("schooladmin*")
