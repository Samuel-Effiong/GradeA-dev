"""Fixtures for the B1b-4 estimate tests (NOTES §4): graded papers written
straight into the history the estimate reads (task rows and AICallRecord
rows), so each test sets exactly the history it needs."""

import uuid
from datetime import timedelta

from django.utils import timezone

from ai_processor.models import AICallRecord
from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)


def make_assignment(teacher, title="Estimate", questions=3, images=0):
    session = Session.objects.create(name=f"{title} session", teacher=teacher)
    course = Course.objects.create(name=title, teacher=teacher, session=session)
    rows = []
    for number in range(1, questions + 1):
        row = {
            "question_number": number,
            "question_text": f"Q{number}",
            "question_type": "OBJECTIVE",
            "points": 10,
        }
        if number <= images:
            row["question_image"] = f"https://img.example.com/{number}.png"
        rows.append(row)
    return Assignment.objects.create(
        title=title,
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=rows,
    )


def grading_session(teacher, assignment):
    return BatchUploadSession.objects.create(
        teacher=teacher,
        course=assignment.course,
        task_type="GRADE",
        total_files=0,
    )


def paper(
    teacher,
    assignment,
    cost,
    *,
    questions=3,
    images=0,
    status=BackgroundTaskStatus.SUCCESS,
    days_ago=1,
    outcome="charged",
    calls=1,
    batch=None,
):
    """One graded paper: a task row and `calls` grading calls whose charges
    sum to `cost`. Returns the task row."""
    when = timezone.now() - timedelta(days=days_ago)
    task = BackgroundProcessingTask.objects.create(
        requested_by=teacher,
        task_type=(
            BackgroundTaskType.BATCH_SUBMISSION_GRADING
            if batch is not None
            else BackgroundTaskType.SUBMISSION_GRADING
        ),
        batch_session=batch,
        assignment=assignment,
        status=status,
    )
    share, extra = divmod(cost, calls)
    for index in range(calls):
        record = AICallRecord.objects.create(
            task_id=str(uuid.uuid4()),
            task_type="grade_assignment",
            feature="Grading Assignment",
            outcome=outcome,
            charged_credits=share + (extra if index == 0 else 0),
            questions=questions,
            images=images,
            billed_user_id=teacher.pk,
            assignment_id=assignment.pk,
            processing_task_id=task.pk,
        )
        AICallRecord.objects.filter(pk=record.pk).update(created_at=when)
    return task


def papers(teacher, assignment, costs, **kwargs):
    return [paper(teacher, assignment, cost, **kwargs) for cost in costs]
