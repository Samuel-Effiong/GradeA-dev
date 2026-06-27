import logging

from celery import shared_task
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor
from AutoGrader.tasks import send_email_task
from classrooms.models import Course
from dashboard.services import StudentWeeklySummaryService, WeeklyCourseSummaryService
from users.models import ConcurrentUserSnapshot, CustomUser, UserTypes
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


@shared_task(bind=True)
def send_weekly_student_summaries(self):
    service = StudentWeeklySummaryService()
    as_of = timezone.now()

    eligible_students = (
        CustomUser.objects.filter(
            user_type=UserTypes.STUDENT,
            email__isnull=False,
            settings__notify_weekly_summary=True,
            enrollments__course__is_active=True,
            enrollments__enrollment_status="ENROLLED",
        )
        .exclude(email="")
        .exclude(email__iendswith="@student.local")
        .distinct()
    )

    emails_queued = 0
    students_processed = 0
    students_skipped = 0

    for student in eligible_students:
        try:
            summary = service.build_student_summary(student, as_of=as_of)
            context = {
                "student": student,
                "summary": summary,
                "overall": summary["overall"],
                "course_summaries": summary["course_summaries"],
                "recent_grades": summary["recent_grades"],
                "upcoming_deadlines": summary["upcoming_deadlines"],
                "overdue_assignments": summary["overdue_assignments"],
                "pending_without_due_dates": summary["pending_without_due_dates"],
                "new_assignments": summary["new_assignments"],
            }

            html_message = render_to_string(
                "email/weekly_student_summary.html",
                context=context,
            )
            text_message = _build_plaintext_student_summary(summary)

            send_email_task.delay(
                subject="Your weekly grade and deadline summary",
                message=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[student.email],
                html_message=html_message,
            )
            emails_queued += 1
            students_processed += 1
        except Exception:
            students_skipped += 1
            logger.exception(
                "Failed to queue weekly student summary email",
                extra={"student_id": str(student.id)},
            )

    return (
        f"Queued {emails_queued} weekly student summary email(s). "
        f"Processed {students_processed} student(s), skipped {students_skipped}."
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


def _build_plaintext_student_summary(summary):
    overall = summary["overall"]
    average_grade = (
        f"{overall['average_grade']}% ({overall['letter_grade']})"
        if overall["average_grade"] is not None
        else "No published grades yet"
    )

    lines = [
        f"Weekly summary for {summary['student']['name']}",
        "",
        "Overall progress:",
        f"- Active courses: {overall['course_count']}",
        f"- Published assignments: {overall['published_assignment_count']}",
        f"- Submitted assignments: {overall['submitted_assignment_count']}",
        f"- Graded assignments: {overall['graded_assignment_count']}",
        f"- Pending assignments: {overall['pending_assignment_count']}",
        f"- Overdue assignments: {overall['overdue_assignment_count']}",
        f"- Current average: {average_grade}",
        "",
        "Course breakdown:",
    ]

    for course in summary["course_summaries"]:
        course_average = (
            f"{course['average_grade']}% ({course['letter_grade']})"
            if course["average_grade"] is not None
            else "No published grades"
        )
        lines.append(
            f"- {course['course_name']}: {course['submitted_assignment_count']}/"
            f"{course['published_assignment_count']} submitted, "
            f"{course['pending_assignment_count']} pending, average {course_average}"
        )

    lines.extend(["", "Recent grades:"])
    if summary["recent_grades"]:
        for grade in summary["recent_grades"]:
            lines.append(
                f"- {grade['course_name']} - {grade['assignment_title']}: "
                f"{grade['score_percentage']}% ({grade['letter_grade']})"
            )
    else:
        lines.append("- No newly published grades this week.")

    lines.extend(["", "Upcoming deadlines:"])
    if summary["upcoming_deadlines"]:
        for assignment in summary["upcoming_deadlines"]:
            due_date = timezone.localtime(assignment["due_date"]).strftime(
                "%B %d, %Y at %I:%M %p"
            )
            lines.append(
                f"- {assignment['course_name']} - "
                f"{assignment['assignment_title'] or 'Untitled Assignment'}: {due_date}"
            )
    else:
        lines.append("- No upcoming deadlines in the next two weeks.")

    if summary["overdue_assignments"]:
        lines.extend(["", "Overdue assignments:"])
        for assignment in summary["overdue_assignments"]:
            due_date = timezone.localtime(assignment["due_date"]).strftime(
                "%B %d, %Y at %I:%M %p"
            )
            lines.append(
                f"- {assignment['course_name']} - "
                f"{assignment['assignment_title'] or 'Untitled Assignment'}: {due_date}"
            )

    if summary["pending_without_due_dates"]:
        lines.extend(["", "Pending assignments without due dates:"])
        for assignment in summary["pending_without_due_dates"]:
            lines.append(
                f"- {assignment['course_name']} - "
                f"{assignment['assignment_title'] or 'Untitled Assignment'}"
            )

    return "\n".join(lines)
