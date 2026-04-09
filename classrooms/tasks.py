from celery import shared_task
from django.utils import timezone

from ai_processor.services import ai_processor
from classrooms.models import Course, StudentCourse
from users.models import CustomUser


@shared_task(bind=True)
def student_summary_async(self, student_id, teacher_id, course_id):
    try:
        course = Course.objects.get(id=course_id)
        teacher = CustomUser.objects.get(id=teacher_id)
        student = CustomUser.objects.get(id=student_id)

        enrollment = (
            StudentCourse.objects.filter(course=course, student=student)
            .select_related("student", "course")
            .first()
        )

        summary = ai_processor.generate_student_summary(
            teacher=teacher,
            student=student,
            course=course,
        )

        enrollment.ai_summary = summary
        enrollment.ai_summary_generated_at = timezone.now()
        enrollment.save()

        return {
            "student_id": student_id,
            "student_name": enrollment.student.get_full_name(),
            "course": enrollment.course.name,
            "ai_summary": summary,
            "generated_at": enrollment.ai_summary_generated_at,
        }

    except Exception as exc:
        raise exc
