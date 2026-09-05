from celery import shared_task
from django.utils import timezone

from ai_processor.services import ai_processor
from classrooms.models import Course, StudentCourse
from students.exceptions import TaskCancelledError
from students.task_tracking import (
    cancellable_final_save,
    ensure_task_not_cancelled,
    mark_processing_task_cancelled,
    mark_processing_task_failure,
    mark_processing_task_started,
    mark_processing_task_success,
)
from users.models import CustomUser


@shared_task(bind=True)
def student_summary_async(
    self, student_id, teacher_id, course_id, processing_task_id=None
):
    """Generate and store a student's AI course summary.

    `processing_task_id` is optional because this task has two callers with
    genuinely different needs:

      * classrooms.views.CourseViewSet.student_summary dispatches it through
        launch_processing_task, so the teacher who asked for the summary can
        poll and cancel it. That caller passes an id.
      * students.services dispatches it fire-and-forget after a grade lands,
        purely to refresh a now-stale cached summary. Nobody polls that one,
        so it creates no tracking row and passes nothing.

    Every tracking helper below no-ops on a None id, so the second caller
    runs exactly as it did before this was tracked at all.
    """
    try:
        ensure_task_not_cancelled(processing_task_id)
        mark_processing_task_started(
            processing_task_id, meta={"step": "Generating student summary"}
        )

        course = Course.objects.get(id=course_id)
        teacher = CustomUser.objects.get(id=teacher_id)
        student = CustomUser.objects.get(id=student_id)

        enrollment = (
            StudentCourse.objects.filter(course=course, student=student)
            .select_related("student", "course")
            .first()
        )

        # Checked BEFORE the model call, not after. generate_student_summary
        # charges the teacher's wallet, so reaching it with nowhere to store
        # the result meant paying for a summary and then dying on
        # `None.ai_summary` - billed, discarded, and reported as a generic
        # failure.
        if enrollment is None:
            raise ValueError(
                "This student is no longer enrolled in this course, so there "
                "is nothing to summarise."
            )

        summary = ai_processor.generate_student_summary(
            teacher=teacher,
            student=student,
            course=course,
        )

        enrollment.ai_summary = summary
        enrollment.ai_summary_generated_at = timezone.now()

        # Same lock-and-check the other cancellable tasks use: without it a
        # cancellation observed after the last check could still be raced by
        # this write.
        with cancellable_final_save(processing_task_id):
            enrollment.save(update_fields=["ai_summary", "ai_summary_generated_at"])

        result = {
            "student_id": str(student_id),
            "student_name": enrollment.student.get_full_name(),
            "course": enrollment.course.name,
            "ai_summary": summary,
            "generated_at": enrollment.ai_summary_generated_at,
        }

        # The success meta deliberately mirrors the returned dict. Before
        # this task was tracked, the frontend polled it through
        # TaskViewSet.task_status's AsyncResult fallback and read the summary
        # out of `meta` (which was str(task.info), i.e. this dict). Tracked
        # tasks report `meta` from the tracking row instead, so carrying the
        # same keys here keeps that polled response equivalent.
        mark_processing_task_success(
            processing_task_id,
            meta={
                "step": "Student summary generated",
                **{
                    key: (value.isoformat() if key == "generated_at" else value)
                    for key, value in result.items()
                },
            },
        )

        return result

    except TaskCancelledError:
        mark_processing_task_cancelled(
            processing_task_id, meta={"step": "Student summary cancelled"}
        )
        raise
    except Exception as exc:
        mark_processing_task_failure(
            processing_task_id,
            exc,
            meta={"step": "Student summary generation failed"},
            fallback_message=(
                "We couldn't generate this student's summary. Please try "
                "again, or contact support if this continues."
            ),
        )
        raise
