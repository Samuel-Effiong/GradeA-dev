from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from assignments.models import Assignment


@receiver([post_save, post_delete], sender=Assignment)
def clear_assignment_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*assignments*")
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")
