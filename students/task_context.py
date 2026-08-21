# students/task_context.py
from typing import Any, Dict  # Optional

from .models import (
    BackgroundProcessingTask,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)

# from django.db import models


def get_task_context(processing_task: BackgroundProcessingTask) -> Dict[str, Any]:
    """
    Derive contextual information from a BackgroundProcessingTask.

    Returns a dictionary with keys:
        - resource_type: str ("assignment" | "submission" | "grading" | "batch" | "unknown")
        - resource_id: str | None (UUID of the primary resource)
        - action: str (e.g., "extracted", "submitted", "graded", "updated", "formatted")
        - additional_ids: dict (extra IDs like assignment_id, course_id, student_id, etc.)

    The function uses the task's related objects and task_type to populate the fields.
    """
    context: Dict[str, Any] = {
        "resource_type": None,
        "resource_id": None,
        "action": None,
        "additional_ids": {},
    }

    task_type = processing_task.task_type
    assignment = processing_task.assignment
    submission = processing_task.submission
    batch_session = processing_task.batch_session

    # --- Determine resource_type, resource_id, action ---
    if task_type in [
        BackgroundTaskType.ASSIGNMENT_EXTRACTION,
        BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
        BackgroundTaskType.ASSIGNMENT_REEXTRACTION,
    ]:
        context["resource_type"] = "assignment"
        if assignment:
            context["resource_id"] = str(assignment.id)
        # Action based on specific type
        if task_type == BackgroundTaskType.ASSIGNMENT_EXTRACTION:
            context["action"] = "extracted"
        elif task_type == BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD:
            context["action"] = "batch_uploaded"
        elif task_type == BackgroundTaskType.ASSIGNMENT_REEXTRACTION:
            context["action"] = "updated"

    elif task_type in [
        BackgroundTaskType.ANSWER_EXTRACTION,
        BackgroundTaskType.BATCH_ANSWER_UPLOAD,
    ]:
        context["resource_type"] = "submission"
        if submission:
            context["resource_id"] = str(submission.id)
        context["action"] = (
            "submitted"
            if task_type == BackgroundTaskType.ANSWER_EXTRACTION
            else "batch_submitted"
        )

    elif task_type in [
        BackgroundTaskType.SUBMISSION_GRADING,
        BackgroundTaskType.BATCH_SUBMISSION_GRADING,
        BackgroundTaskType.FORMATTED_GRADE,
    ]:
        context["resource_type"] = "grading"
        if submission:
            context["resource_id"] = str(submission.id)
        if task_type == BackgroundTaskType.SUBMISSION_GRADING:
            context["action"] = "graded"
        elif task_type == BackgroundTaskType.BATCH_SUBMISSION_GRADING:
            context["action"] = "batch_graded"
        elif task_type == BackgroundTaskType.FORMATTED_GRADE:
            context["action"] = "formatted"

    # Fallback: if still None, try to infer from the presence of objects
    if context["resource_type"] is None:
        if assignment:
            context["resource_type"] = "assignment"
            context["resource_id"] = str(assignment.id)
            context["action"] = "related"
        elif submission:
            context["resource_type"] = "submission"
            context["resource_id"] = str(submission.id)
            context["action"] = "related"
        elif batch_session:
            context["resource_type"] = "batch"
            context["resource_id"] = str(batch_session.id)
            context["action"] = "related"
        else:
            context["resource_type"] = "unknown"

    # --- Build additional_ids ---
    additional = {}

    if assignment:
        additional["assignment_id"] = str(assignment.id)
        if assignment.course_id:
            additional["course_id"] = str(assignment.course_id)
        if assignment.topic_id:
            additional["topic_id"] = str(assignment.topic_id)

    if submission:
        additional["submission_id"] = str(submission.id)
        if submission.student_id:
            additional["student_id"] = str(submission.student_id)
        # Ensure assignment_id is also present if not already
        if submission.assignment_id and "assignment_id" not in additional:
            additional["assignment_id"] = str(submission.assignment_id)
        # Add course_id via assignment if available
        if (
            not additional.get("course_id")
            and hasattr(submission, "assignment")
            and submission.assignment
        ):
            if submission.assignment.course_id:
                additional["course_id"] = str(submission.assignment.course_id)

    if batch_session:
        additional["session_id"] = str(batch_session.id)
        # Add assignment_id from session if present and not already added
        if batch_session.assignment_id and "assignment_id" not in additional:
            additional["assignment_id"] = str(batch_session.assignment_id)
        # Add course_id from session if present
        if batch_session.course_id and "course_id" not in additional:
            additional["course_id"] = str(batch_session.course_id)

    context["additional_ids"] = additional
    return context


def get_session_context(batch_session: BatchUploadSession) -> Dict[str, Any]:
    """
    Derive top-level context for a BatchUploadSession.

    Returns:
        - resource_type: str ("assignment" | "submission" | "grading" | "batch")
        - resource_id: str | None (usually assignment_id if applicable)
        - action: str (e.g., "batch_uploaded", "batch_submitted", "batch_graded")
        - additional_ids: dict (course_id, assignment_id, etc.)
    """
    context: Dict[str, Any] = {
        "resource_type": None,
        "resource_id": None,
        "action": None,
        "additional_ids": {},
    }

    task_type = batch_session.task_type
    assignment = batch_session.assignment
    course = batch_session.course

    if task_type == BatchUploadType.ASSIGNMENT:
        context["resource_type"] = "assignment"
        context["action"] = "batch_uploaded"
        if assignment:
            context["resource_id"] = str(assignment.id)
    elif task_type == BatchUploadType.SUBMISSION:
        context["resource_type"] = "submission"
        context["action"] = "batch_submitted"
        if assignment:
            context["resource_id"] = str(assignment.id)
        elif course:
            # Fallback to course if no assignment (should not happen, but safe)
            context["resource_id"] = str(course.id)
    elif task_type == BatchUploadType.GRADE:
        context["resource_type"] = "grading"
        context["action"] = "batch_graded"
        if assignment:
            context["resource_id"] = str(assignment.id)
    else:
        context["resource_type"] = "batch"
        context["action"] = "batch_processed"

    # Additional IDs
    additional = {}
    if assignment:
        additional["assignment_id"] = str(assignment.id)
        if assignment.course_id:
            additional["course_id"] = str(assignment.course_id)
    if course and "course_id" not in additional:
        additional["course_id"] = str(course.id)
    if batch_session.id:
        additional["session_id"] = str(batch_session.id)

    context["additional_ids"] = additional
    return context
