from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from users.models import CustomUser


@receiver([post_save, post_delete], sender=CustomUser)
def clear_user_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")
        cache.delete_pattern("*teacheradmin*")
        cache.delete_pattern("*studentadmin*")
        cache.delete_pattern("*user*")
        cache.delete_pattern("*school*")
        cache.delete_pattern("*course*")
        cache.delete_pattern("*studentcourse*")
