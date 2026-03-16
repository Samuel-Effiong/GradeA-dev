from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from students.models import BatchUploadSession, StudentSubmission


def delete_cache_patterns(*patterns):
    if not hasattr(cache, "delete_pattern"):
        return

    for pattern in patterns:
        cache.delete_pattern(pattern)


@receiver([post_save, post_delete], sender=StudentSubmission)
def clear_student_submission_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=BatchUploadSession)
def clear_batch_upload_session_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "studentsubmissions:*",
        "assignments:*",
    )
