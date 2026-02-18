from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from classrooms.models import Course, School, Session, StudentCourse


@receiver([post_save, post_delete], sender=School)
def clear_school_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*schools*")
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")


@receiver([post_save, post_delete], sender=Session)
def clear_session_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*sessions*")
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")


@receiver([post_save, post_delete], sender=Course)
def clear_course_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*course*")
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")


@receiver([post_save, post_delete], sender=StudentCourse)
def clear_student_course_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*studentcourses*")
        cache.delete_pattern("*course*")
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")
