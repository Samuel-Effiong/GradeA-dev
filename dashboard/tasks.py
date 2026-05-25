import logging

from celery import shared_task
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor
from AutoGrader.tasks import send_email_task
from classrooms.models import Course
from dashboard.services import WeeklyCourseSummaryService
from users.models import ConcurrentUserSnapshot
from users.services import cleanup_expired_users, get_current_concurrent_users

logger = logging.getLogger(__name__)


@shared_task
def record_concurrent_users():
    expired_users = cleanup_expired_users()
    count = get_current_concurrent_users()

    ConcurrentUserSnapshot.objects.create(
        concurrent_users=count,
        timestamp=timezone.now(),
    )

    return f"Expired users: {expired_users}, Current users: {count}"


@shared_task(bind=True)
def send_weekly_course_summaries(self):
    service = WeeklyCourseSummaryService()
    as_of = timezone.now()

    eligible_courses = (
        Course.objects.filter(
            is_active=True,
            teacher__isnull=False,
            teacher__email__isnull=False,
            teacher__settings__notify_weekly_summary=True,
        )
        .select_related("teacher", "session")
        .distinct()
    )

    emails_queued = 0
    courses_processed = 0
    courses_skipped = 0

    for course in eligible_courses:
        if not course.teacher or not course.teacher.email:
            courses_skipped += 1
            continue

        try:
            summary = service.build_course_summary(course, as_of=as_of)
            ai_narrative = None
            try:
                ai_narrative = ai_processor.generate_weekly_course_summary_narrative(
                    course.teacher,
                    course,
                    summary,
                )
            except Exception:
                logger.exception(
                    "Failed to generate AI narration for weekly course summary",
                    extra={
                        "course_id": str(course.id),
                        "teacher_id": str(course.teacher_id),
                    },
                )

            if ai_narrative:
                summary["ai_narrative"] = ai_narrative

            context = {
                "teacher": course.teacher,
                "course": course,
                "summary": summary,
                "ai_narrative": summary.get("ai_narrative"),
                "at_risk_students": summary["at_risk_students"],
                "commonalities": summary["commonalities"],
                "trend_watch": summary["trend_watch"],
                "interventions": summary["interventions"],
            }

            html_message = render_to_string(
                "email/weekly_course_summary.html",
                context=context,
            )
            text_message = _build_plaintext_summary(course, summary)

            send_email_task.delay(
                subject=f"Weekly course summary: {course.name}",
                message=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[course.teacher.email],
                html_message=html_message,
            )
            emails_queued += 1
            courses_processed += 1
        except Exception:
            courses_skipped += 1
            logger.exception(
                "Failed to queue weekly course summary email",
                extra={
                    "course_id": str(course.id),
                    "teacher_id": str(course.teacher_id),
                },
            )

    return (
        f"Queued {emails_queued} weekly course summary email(s). "
        f"Processed {courses_processed} course(s), skipped {courses_skipped}."
    )


def _build_plaintext_summary(course, summary):
    overall = summary["overall"]
    at_risk_students = summary["at_risk_students"]
    trend_watch = summary["trend_watch"]
    ai_narrative = summary.get("ai_narrative") or {}

    lines = [
        f"Weekly course summary for {course.name}",
        "",
        ai_narrative.get("overall_narrative", summary["overall_summary"]),
        "",
        "Course metrics:",
        f"- Students enrolled: {overall['student_count']}",
        f"- Published assignments: {overall['published_assignment_count']}",
        f"- Submission rate: {overall['submission_rate']}%",
        (
            f"- Average grade: {overall['average_grade']}%"
            if overall["average_grade"] is not None
            else "- Average grade: No graded work yet"
        ),
        "",
    ]

    if at_risk_students:
        lines.append("At-risk students:")
        if ai_narrative.get("at_risk_narrative"):
            lines.append(ai_narrative["at_risk_narrative"])
        else:
            for student in at_risk_students:
                reason = (
                    student["risk_reasons"][0]
                    if student["risk_reasons"]
                    else "Needs review."
                )
                lines.append(f"- {student['student_name']}: {reason}")
        lines.append("")
    else:
        lines.append(
            ai_narrative.get(
                "at_risk_narrative",
                "At-risk students: None identified this week.",
            )
        )
        lines.append("")

    if summary["commonalities"]:
        lines.append("Common patterns:")
        if ai_narrative.get("commonality_narrative"):
            lines.append(ai_narrative["commonality_narrative"])
        else:
            for item in summary["commonalities"]:
                lines.append(f"- {item}")
        lines.append("")
    elif ai_narrative.get("commonality_narrative"):
        lines.append("Common patterns:")
        lines.append(ai_narrative["commonality_narrative"])
        lines.append("")

    if trend_watch["trending_up"] or trend_watch["trending_down"]:
        lines.append("Trend watch:")
        for student in trend_watch["trending_up"]:
            lines.append(f"- Trending up: {student['student_name']}")
        for student in trend_watch["trending_down"]:
            lines.append(f"- Trending down: {student['student_name']}")
        lines.append("")

    if summary["interventions"]:
        lines.append("Suggested interventions:")
        if ai_narrative.get("intervention_narrative"):
            lines.append(ai_narrative["intervention_narrative"])
        else:
            for intervention in summary["interventions"]:
                lines.append(
                    f"- {intervention['target']}: {intervention['recommendation']}"
                )
    elif ai_narrative.get("intervention_narrative"):
        lines.append("Suggested interventions:")
        lines.append(ai_narrative["intervention_narrative"])

    return "\n".join(lines)
