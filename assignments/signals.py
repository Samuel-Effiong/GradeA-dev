from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from assignments.models import Assignment


def delete_cache_patterns(*patterns):
    if not hasattr(cache, "delete_pattern"):
        return

    for pattern in patterns:
        cache.delete_pattern(pattern)


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
