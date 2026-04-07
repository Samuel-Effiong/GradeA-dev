from django.core.cache import cache
from django.db.models import Avg
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from classrooms.models import Course, School, Session, StudentCourse, Topic
from students.models import StudentSubmission


def delete_cache_patterns(*patterns):
    if not hasattr(cache, "delete_pattern"):
        return

    for pattern in patterns:
        cache.delete_pattern(pattern)


@receiver([post_save, post_delete], sender=School)
def clear_school_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "schools:*",
        "courses:*",
        "sessions:*",
    )


@receiver([post_save, post_delete], sender=Session)
def clear_session_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*school*",
        "sessions:*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=Course)
def clear_course_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "sessions:*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
        "studentcourses:*",
        "topics:*",
    )


@receiver([post_save, post_delete], sender=StudentCourse)
def clear_student_course_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "sessions:*",
        "courses:*",
        "studentcourses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=Topic)
def clear_topic_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "topics:*",
        "courses:*",
        "assignments:*",
    )


@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):
    """
    When a submission is graded, recalculate the student's final grade
    using the average of all submission percentages.
    """

    student = instance.student
    course = instance.assignment.course

    try:
        enrollment = StudentCourse.objects.get(student=student, course=course)
    except StudentCourse.DoesNotExist:
        return

    avg_percentage = StudentSubmission.objects.filter(
        student=student, assignment__course=course, score_percentage__isnull=False
    ).aggregate(Avg("score_percentage"))["score_percentage__avg"]

    if avg_percentage is not None:
        enrollment.final_grade = round(avg_percentage, 2)
        enrollment.save(update_fields=["final_grade"])
