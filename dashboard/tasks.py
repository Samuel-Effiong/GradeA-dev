import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils import timezone

from ai_processor.services import ai_processor
from AutoGrader.tasks import send_email_task
from classrooms.models import Course, School
from dashboard.models import (
    SchoolAtRiskSnapshot,
    StudentRiskAlertState,
    TeacherInactivityAlertState,
)
from dashboard.services import (
    SchoolAdminWeeklySummaryService,
    StudentWeeklySummaryService,
    WeeklyCourseSummaryService,
)
from users.models import ConcurrentUserSnapshot, CustomUser, UserActivity, UserTypes
from users.services import (
    cleanup_expired_users,
    get_current_concurrent_users,
    get_opted_in_school_admins,
)

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


@shared_task(bind=True)
def send_weekly_school_admin_summaries(self):
    service = SchoolAdminWeeklySummaryService()
    as_of = timezone.now()

    eligible_admins = (
        CustomUser.objects.filter(
            user_type=UserTypes.SCHOOL_ADMIN,
            school__isnull=False,
            is_active=True,
            email__isnull=False,
            settings__notify_weekly_summary=True,
        )
        .exclude(email="")
        .select_related("school")
        .distinct()
    )

    emails_queued = 0
    admins_processed = 0
    admins_skipped = 0

    for admin in eligible_admins:
        if not admin.school or not admin.email:
            admins_skipped += 1
            continue

        try:
            summary = service.build_school_summary(admin.school, as_of=as_of)
            ai_narrative = None
            try:
                ai_narrative = (
                    ai_processor.generate_weekly_school_admin_summary_narrative(
                        admin,
                        admin.school,
                        summary,
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to generate AI narration for weekly school admin summary",
                    extra={
                        "school_id": str(admin.school_id),
                        "admin_id": str(admin.id),
                    },
                )

            if ai_narrative:
                summary["ai_narrative"] = ai_narrative

            context = {
                "admin": admin,
                "school": admin.school,
                "summary": summary,
                "ai_narrative": summary.get("ai_narrative"),
                "overall": summary["overall"],
                "at_risk_students": summary["at_risk_students"],
                "at_risk_student_count": summary["at_risk_student_count"],
                "teacher_activity": summary["teacher_activity"],
            }

            html_message = render_to_string(
                "email/weekly_school_admin_summary.html",
                context=context,
            )
            text_message = _build_plaintext_school_admin_summary(admin.school, summary)

            send_email_task.delay(
                subject=f"Weekly school summary: {admin.school.name}",
                message=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin.email],
                html_message=html_message,
            )
            emails_queued += 1
            admins_processed += 1
        except Exception:
            admins_skipped += 1
            logger.exception(
                "Failed to queue weekly school admin summary email",
                extra={
                    "school_id": str(admin.school_id),
                    "admin_id": str(admin.id),
                },
            )

    return (
        f"Queued {emails_queued} weekly school admin summary email(s). "
        f"Processed {admins_processed} admin(s), skipped {admins_skipped}."
    )


@shared_task(bind=True)
def send_at_risk_student_alerts(self):
    """
    Daily scan across every school with at least one active school admin:
    recompute the school-wide at-risk student set and record a
    SchoolAtRiskSnapshot (used by the at-risk trend chart) regardless of
    email opt-in, then — only for schools with at least one admin opted
    into the email alert — alert on students who newly crossed the
    threshold since the last run (never on students who remain at-risk
    from a previous run). Schools with zero opted-in admins still get a
    snapshot but no email/alert-state bookkeeping; if an admin opts in
    later, the next run treats the whole current at-risk set as "newly
    at-risk" and sends a one-time catch-up alert, which is intentional.
    """
    service = SchoolAdminWeeklySummaryService()

    schools = School.objects.filter(
        users__user_type=UserTypes.SCHOOL_ADMIN,
        users__is_active=True,
    ).distinct()

    schools_processed = 0
    schools_skipped = 0
    emails_queued = 0

    for school in schools:
        try:
            now = timezone.now()
            current_students = service._at_risk_students(school)

            SchoolAtRiskSnapshot.objects.update_or_create(
                school=school,
                snapshot_date=now.date(),
                defaults={"at_risk_count": len(current_students)},
            )

            admins = list(
                get_opted_in_school_admins(school, flag="notify_at_risk_student_alerts")
            )
            if not admins:
                schools_processed += 1
                continue

            current_ids = {student.id for student in current_students}
            existing_states = {
                state.student_id: state
                for state in StudentRiskAlertState.objects.filter(school=school)
            }

            newly_at_risk = []
            for student in current_students:
                state = existing_states.get(student.id)
                is_new = state is None or not state.is_at_risk

                obj, created = StudentRiskAlertState.objects.get_or_create(
                    student_id=student.id,
                    school=school,
                    defaults={
                        "is_at_risk": True,
                        "average_score": student.avg_score,
                        "last_alerted_at": now,
                    },
                )
                if not created:
                    obj.is_at_risk = True
                    obj.average_score = student.avg_score
                    update_fields = ["is_at_risk", "average_score"]
                    if is_new:
                        obj.last_alerted_at = now
                        update_fields.append("last_alerted_at")
                    obj.save(update_fields=update_fields)

                if is_new:
                    newly_at_risk.append(student)

            recovered_ids = [
                student_id
                for student_id, state in existing_states.items()
                if state.is_at_risk and student_id not in current_ids
            ]
            if recovered_ids:
                StudentRiskAlertState.objects.filter(
                    school=school, student_id__in=recovered_ids
                ).update(is_at_risk=False, average_score=None)

            if newly_at_risk:
                context = {
                    "school": school,
                    "newly_at_risk_students": [
                        {
                            "student_name": student.get_full_name(),
                            "average_score": (
                                round(student.avg_score, 1)
                                if student.avg_score is not None
                                else None
                            ),
                        }
                        for student in newly_at_risk
                    ],
                }
                html_message = render_to_string(
                    "email/school_admin_at_risk_alert.html", context=context
                )
                names = ", ".join(student.get_full_name() for student in newly_at_risk)
                message = (
                    f"{len(newly_at_risk)} student(s) newly flagged as at-risk "
                    f"at {school.name}: {names}"
                )
                for admin in admins:
                    try:
                        send_email_task.delay(
                            subject=f"New at-risk students at {school.name}",
                            message=message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[admin.email],
                            html_message=html_message,
                        )
                        emails_queued += 1
                    except Exception:
                        logger.exception(
                            "Failed to queue at-risk admin email",
                            extra={
                                "admin_id": str(admin.id),
                                "school_id": str(school.id),
                            },
                        )

            schools_processed += 1
        except Exception:
            schools_skipped += 1
            logger.exception(
                "Failed to process at-risk alerts for school",
                extra={"school_id": str(school.id)},
            )

    return (
        f"Queued {emails_queued} at-risk alert email(s). "
        f"Processed {schools_processed} school(s), skipped {schools_skipped}."
    )


@shared_task(bind=True)
def send_teacher_inactivity_alerts(self):
    """
    Daily scan: for each school with at least one opted-in admin, flags
    active teachers who have had no UserActivity for
    settings.TEACHER_INACTIVITY_THRESHOLD_DAYS and alerts once per
    inactivity episode. Teachers who join more recently than the threshold
    (and so haven't had a fair chance to log in yet) are skipped. A teacher
    becoming active again clears the flag so a future inactivity episode
    re-alerts.
    """
    threshold_days = settings.TEACHER_INACTIVITY_THRESHOLD_DAYS
    cutoff = timezone.now() - timedelta(days=threshold_days)

    schools = School.objects.filter(
        users__user_type=UserTypes.SCHOOL_ADMIN,
        users__is_active=True,
        users__settings__notify_teacher_activity_alerts=True,
    ).distinct()

    schools_processed = 0
    schools_skipped = 0
    emails_queued = 0

    for school in schools:
        admins = list(
            get_opted_in_school_admins(school, flag="notify_teacher_activity_alerts")
        )
        if not admins:
            continue

        try:
            newly_flagged = []
            teachers = CustomUser.objects.filter(
                school=school, user_type=UserTypes.TEACHER, is_active=True
            )
            for teacher in teachers:
                if teacher.date_joined > cutoff:
                    continue

                last_activity = UserActivity.objects.filter(user=teacher).aggregate(
                    Max("timestamp")
                )["timestamp__max"]
                currently_inactive = last_activity is None or last_activity < cutoff

                state, _ = TeacherInactivityAlertState.objects.get_or_create(
                    teacher=teacher
                )
                if currently_inactive:
                    if not state.is_flagged_inactive:
                        state.is_flagged_inactive = True
                        state.last_active_at = last_activity
                        state.last_alerted_at = timezone.now()
                        state.save(
                            update_fields=[
                                "is_flagged_inactive",
                                "last_active_at",
                                "last_alerted_at",
                            ]
                        )
                        newly_flagged.append((teacher, last_activity))
                elif state.is_flagged_inactive:
                    state.is_flagged_inactive = False
                    state.last_active_at = last_activity
                    state.save(update_fields=["is_flagged_inactive", "last_active_at"])

            if newly_flagged:
                context = {
                    "school": school,
                    "inactive_teachers": [
                        {"name": teacher.get_full_name(), "last_active_at": last_active}
                        for teacher, last_active in newly_flagged
                    ],
                    "threshold_days": threshold_days,
                }
                html_message = render_to_string(
                    "email/school_admin_teacher_activity_alert.html", context=context
                )
                names = ", ".join(
                    teacher.get_full_name() for teacher, _ in newly_flagged
                )
                message = (
                    f"{len(newly_flagged)} teacher(s) at {school.name} have had no "
                    f"activity for {threshold_days}+ days: {names}"
                )
                for admin in admins:
                    try:
                        send_email_task.delay(
                            subject=f"Teacher inactivity alert: {school.name}",
                            message=message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[admin.email],
                            html_message=html_message,
                        )
                        emails_queued += 1
                    except Exception:
                        logger.exception(
                            "Failed to queue teacher-inactivity admin email",
                            extra={
                                "admin_id": str(admin.id),
                                "school_id": str(school.id),
                            },
                        )

            schools_processed += 1
        except Exception:
            schools_skipped += 1
            logger.exception(
                "Failed to process teacher-inactivity alerts for school",
                extra={"school_id": str(school.id)},
            )

    return (
        f"Queued {emails_queued} teacher-inactivity alert email(s). "
        f"Processed {schools_processed} school(s), skipped {schools_skipped}."
    )


@shared_task(bind=True)
def send_teacher_first_course_milestone_alert(self, course_id):
    """
    Fired (via transaction.on_commit) from classrooms.signals when a
    teacher creates their first-ever course. Silently no-ops if the course
    no longer exists, the teacher has no school, or no admin is opted in --
    this is a best-effort milestone notification, not a critical path.
    """
    try:
        course = Course.objects.select_related("teacher", "teacher__school").get(
            pk=course_id
        )
    except Course.DoesNotExist:
        return "Course no longer exists; skipped."

    teacher = course.teacher
    school = getattr(teacher, "school", None)
    if not school:
        return "Teacher has no school; skipped."

    admins = list(
        get_opted_in_school_admins(school, flag="notify_teacher_activity_alerts")
    )
    if not admins:
        return "No opted-in admins; skipped."

    context = {
        "school": school,
        "milestone_teacher": teacher.get_full_name(),
        "milestone_course": course.name,
    }
    html_message = render_to_string(
        "email/school_admin_teacher_activity_alert.html", context=context
    )
    message = f"{teacher.get_full_name()} created their first course: {course.name}."

    emails_queued = 0
    for admin in admins:
        try:
            send_email_task.delay(
                subject=f"New teacher milestone at {school.name}",
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin.email],
                html_message=html_message,
            )
            emails_queued += 1
        except Exception:
            logger.exception(
                "Failed to queue teacher first-course milestone admin email",
                extra={"admin_id": str(admin.id), "course_id": str(course.id)},
            )

    return f"Queued {emails_queued} teacher milestone alert email(s)."


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


def _format_rigor_plaintext(teacher):
    """Render a teacher's rigor for the plain-text digest.

    Mirrors the HTML table's "3.4/5 (D 4.2 / E 2.1 / S 3.8)" shape so the two
    bodies of the same email cannot tell an admin different things. Components
    that have no data yet render as a dash rather than being dropped, so the
    absence is visible instead of silently narrowing the number.
    """
    breakdown = teacher.get("rigor_breakdown") or {}
    label = breakdown.get("label")

    if teacher.get("rigor") is None:
        return label or "rigor not yet scoreable"

    return f"rigor: {label} ({teacher['rigor']}/5)"


def _build_plaintext_school_admin_summary(school, summary):
    overall = summary["overall"]
    at_risk_students = summary["at_risk_students"]
    teacher_activity = summary["teacher_activity"]
    ai_narrative = summary.get("ai_narrative") or {}

    lines = [
        f"Weekly school summary for {school.name}",
        "",
        ai_narrative.get("overall_narrative", summary["overall_summary"]),
        "",
        "This week:",
        f"- Active teachers: {overall['active_teacher_count']}",
        f"- Active students: {overall['active_student_count']}",
        f"- Active courses: {overall['active_course_count']}",
        f"- Assignments created: {overall['assignments_created_this_week']}",
        f"- Assignments graded: {overall['assignments_graded_this_week']}",
        (
            f"- Average grading turnaround: {overall['avg_turnaround_days']} day(s)"
            if overall["avg_turnaround_days"] is not None
            else "- Average grading turnaround: No graded work this week"
        ),
        "",
    ]

    if at_risk_students:
        lines.append(f"At-risk students ({summary['at_risk_student_count']} total):")
        if ai_narrative.get("at_risk_narrative"):
            lines.append(ai_narrative["at_risk_narrative"])
        else:
            for student in at_risk_students:
                average_score = student["average_score"]
                score_text = (
                    f"{average_score}% average"
                    if average_score is not None
                    else "no graded work yet"
                )
                lines.append(f"- {student['student_name']}: {score_text}")
        lines.append("")
    else:
        lines.append(
            ai_narrative.get(
                "at_risk_narrative",
                "At-risk students: None identified this week.",
            )
        )
        lines.append("")

    lines.append("Teacher activity (overall, not just this week):")
    if teacher_activity:
        if ai_narrative.get("teacher_activity_narrative"):
            lines.append(ai_narrative["teacher_activity_narrative"])
        else:
            for teacher in teacher_activity:
                assignments_per_week = (
                    f"{teacher['assignments_per_week']}/wk"
                    if teacher["assignments_per_week"] is not None
                    else "no assignment history"
                )
                lines.append(
                    f"- {teacher['name']}: {teacher['courses']} course(s), "
                    f"{teacher['students']} student(s), {assignments_per_week}, "
                    f"{_format_rigor_plaintext(teacher)}"
                )
    else:
        lines.append("- No teachers found for this school.")

    return "\n".join(lines)
