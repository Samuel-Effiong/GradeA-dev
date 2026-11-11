"""NFR-CMP-04 step 2: one student's data, as a JSON zip (D6).

What goes in is decided by `students.student_data_map` (which tables) and
the field lists below (which columns). Who may ask, and for how much, is
decided in one place, `EXPORT_CALLERS`. The rulings are in
docs/evidence/nfr-cmp-04-student-data/NOTES.md, "Step 2":

* a real super admin: any student, everything ("all");
* a school admin: a student of their own school, everything ("school").
  Never when either school is empty (R6). Everything includes rows from
  courses at other schools: the student's own school answers the
  student's data request (Q4);
* a teacher: the courses they own, the course-scoped tables only, and the
  student's name and email ("courses"). R5, owner 2026-11-08. A course has
  one teacher, its owner;
* nobody else, and never the student themselves (D2).

A grade the teacher has not released is not in the export in any form
(Q1, H-130): the same fields the student's own pages hide before release
are empty here, and what is shown goes through the student's own
whitelists. Sign-in and security data is never exported.

Every name in the zip, and the README, is the owner's approved wording
(2026-11-08, team/approved_wording_student_data_2026-11-08.md). The profile
picture keeps its stored file's extension (`_picture_name`).
"""

import io
import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from functools import partial
from types import SimpleNamespace
from typing import Callable, Optional

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from audit.enums import AuditAction
from classrooms.grade_service import released_course_grade
from classrooms.models import StudentCourse, reachable_courses
from students.feedback_projection import (
    student_safe_feedback,
    student_safe_formatted_grade,
)
from students.serializers import max_points_shown
from students.services import answer_document_for_student
from students.student_data_map import STUDENT_DATA_MAP, Match
from users.models import UserTypes

logger = logging.getLogger(__name__)

#: R5 (owner, 2026-11-08): teachers may export, their own courses only.
TEACHER_EXPORT_ALLOWED = True

#: The audit event for an export (SM: reuse the existing action).
EXPORT_AUDIT_ACTION = AuditAction.DATA_EXPORT

#: Owner-approved (2026-11-08). Every name a person sees in the zip: files,
#: nested keys and the manifest's keys. "profile_image" is the base name;
#: the stored file's extension is added (`_picture_name`).
EXPORT_NAMES_APPROVED = True
EXPORT_NAMES = {
    "download": "student-data.zip",
    "readme": "README.txt",
    "manifest": "manifest.json",
    "profile": "profile.json",
    "profile_image": "profile-picture",
    "classrooms.StudentCourse": "enrollments.json",
    "students.StudentSubmission": "submissions.json",
    "dashboard.StudentRiskAlertState": "at-risk-status.json",
    "students.BackgroundProcessingTask": "file-uploads.json",
    "ai_processor.ChatSession": "assistant-chats.json",
    "users.UserActivity": "activity.json",
    "users.Settings": "settings.json",
    "messages": "messages",
    "manifest_counts": "record_counts",
    "manifest_scope": "scope",
    "manifest_generated_at": "created_at",
    "manifest_missing": "missing_files",
}
README_TEXT = (
    "This download holds the data we keep about one student, as of the time"
    " in manifest.json.\n"
    "Each .json file lists one kind of record: enrollments, submissions,"
    " at-risk status, file uploads, assistant chats, activity and settings."
    " profile.json holds the student's account details, and profile-picture"
    " is their profile picture, if they set one.\n"
    "If a teacher made this download, it covers only that teacher's"
    " courses.\n"
    "A grade appears only after the teacher has released it.\n"
    "Sign-in and security details are never included.\n"
    "If a file is listed under missing_files in manifest.json, it couldn't be"
    " read when this download was made. Try again later.\n"
)

#: A real extension: a dot, then 1-10 letters or digits. The stored name is
#: "<email name>_<date>_<ext>", so with no extension a dot in the email name
#: would otherwise pass for one.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,10}")

_GRADING_RUN = "how grading ran (internal)"
_PROCESSING = "internal processing"
_SECURITY = "sign-in and security"

#: The columns exported as they are (after the value rules in `_value`).
EXPORT_FIELDS = {
    "users.CustomUser": (
        "id",
        "first_name",
        "middle_name",
        "last_name",
        "email",
        "user_type",
        "school",
        "bio",
        "date_joined",
        "last_login",
        "email_verified_at",
        "registration_method",
        "is_active",
        "profile_image",
        "profile_image_url",
    ),
    "classrooms.StudentCourse": (
        "id",
        "course",
        "created_at",
        "enrollment_status",
        "withdrawal_date",
        "final_grade",  # the released-only figure (Q1)
    ),
    "students.StudentSubmission": (
        "id",
        "assignment",
        "submission_date",
        "answers",
        "raw_input",  # the student's form of the document (H-130)
        "max_points",  # the student's form (H-133)
        "is_published",
    ),
    "dashboard.StudentRiskAlertState": (
        "is_at_risk",
        "average_score",
        "last_checked_at",
        "last_alerted_at",
    ),
    "students.BackgroundProcessingTask": (
        "id",
        "assignment",
        "submission",
        "task_type",
        "status",
        "file_name",
        "reason_code",
        "created_at",
        "finished_at",
    ),
    "ai_processor.ChatSession": (
        "id",
        "course",
        "assistant_type",
        "created_at",
        "updated_at",
    ),
    "ai_processor.ChatMessage": ("role", "content", "timestamp"),
    "users.UserActivity": ("timestamp",),
    "users.Settings": (
        "theme",
        "notify_student_submission",
        "notify_weekly_summary",
        "notify_assignment_due_reminder",
        "notify_grading_complete",
        "notify_assignment_edited",
        "notify_new_assignment_posted",
        "notify_teacher_activity_alerts",
        "notify_at_risk_student_alerts",
    ),
}

#: Q1 (SM, 2026-11-08): present only once the teacher has released the
#: grade; empty (null) before. Conditional on `is_published`.
EXPORT_CONDITIONAL = {
    "students.StudentSubmission": (
        "score",
        "score_percentage",
        "feedback",  # student_safe_feedback
        "formatted_grade",  # student_safe_formatted_grade
        "graded_at",
        "was_regraded",
        "regraded_at",
    ),
}

#: Every column left out, with the reason.
EXPORT_OMITTED = {
    "users.CustomUser": {
        "password": _SECURITY,
        "activation_token": _SECURITY,
        "activation_expires": _SECURITY,
        "failed_login_attempts": _SECURITY,
        "locked_until": _SECURITY,
        "admin_locked_at": _SECURITY,
        "token_epoch": _SECURITY,
        "must_change_password": _SECURITY,
        "is_superuser": "always false for a student (internal)",
        "is_staff": "always false for a student (internal)",
    },
    "classrooms.StudentCourse": {
        "student": "the student themselves",
        "auto_added": "internal",
        "ai_summary": "the teacher's AI summary; may reflect unreleased grades (Q5)",
        "ai_summary_generated_at": "goes with ai_summary (Q5)",
    },
    "students.StudentSubmission": {
        "student": "the student themselves",
        "ai_score": "the AI's draft grade before the teacher's review (Q1)",
        "ai_feedback": "the AI's draft feedback before the teacher's review (Q1)",
        "ai_graded_at": "goes with the AI's draft (Q1)",
        "grading_state": _GRADING_RUN,
        "grading_started_at": _GRADING_RUN,
        "grading_confidence": _GRADING_RUN,
        "extraction_confidence": _GRADING_RUN,
        "grading_prompt_version": _GRADING_RUN,
        "grading_config_version": _GRADING_RUN,
        "grading_strictness": _GRADING_RUN,
        "grading_model": _GRADING_RUN,
        "grading_fallback_used": _GRADING_RUN,
        "grading_release": _GRADING_RUN,
        "answers_model": _GRADING_RUN,
        "answers_prompt_version": _GRADING_RUN,
        "answers_config_version": _GRADING_RUN,
        "answers_release": _GRADING_RUN,
        "answers_recheck_model": _GRADING_RUN,
        "answers_recheck_result": _GRADING_RUN,
        "extraction_flags": _GRADING_RUN,
        "ai_grading_completed_at": _GRADING_RUN,
        "scheduled_grading_at": _GRADING_RUN,
        "grading_task_name": _GRADING_RUN,
        "attempt_count": _GRADING_RUN,
        "needs_review": "the teacher's review queue (Q2)",
        "review_reasons": "the teacher's review queue (Q2)",
        "review_severity": "the teacher's review queue (Q2)",
        "review_tier": "the teacher's review queue (Q2)",
        "pending_result": "a regrade not yet approved (Q1)",
        "pending_graded_at": "a regrade not yet approved (Q1)",
    },
    "dashboard.StudentRiskAlertState": {
        "id": "internal row id",
        "student": "the student themselves",
        "school": "internal",
        "alert_pending": "internal",
    },
    "students.BackgroundProcessingTask": {
        "requested_by": "the student themselves",
        "celery_task_id": _PROCESSING,
        "trace_id": _PROCESSING,
        "batch_session": "the teacher's batch upload",
        "meta": _PROCESSING,
        "error": _PROCESSING,
        "retry_count": _PROCESSING,
        "item_index": _PROCESSING,
        "cancel_requested_at": _PROCESSING,
        "started_at": _PROCESSING,
        "updated_at": _PROCESSING,
        # Epic B1a, SM R3 (2026-11-08): credit-hold bookkeeping.
        "job": _PROCESSING,
        "hold_state": _PROCESSING,
        "hold_credits": _PROCESSING,
        "hold_drawn": _PROCESSING,
        "released_credits": _PROCESSING,
    },
    "ai_processor.ChatSession": {"user": "the student themselves"},
    "ai_processor.ChatMessage": {
        "id": "internal row id",
        "session": "the message is nested under its session",
    },
    "users.UserActivity": {
        "id": "internal row id",
        "user": "the student themselves",
    },
    "users.Settings": {"id": "internal row id", "user": "the student themselves"},
}

#: What a teacher sees of the student's account (R5).
TEACHER_PROFILE_FIELDS = ("id", "first_name", "middle_name", "last_name", "email")

#: How a course-scoped table reaches its course (a teacher's scope).
COURSE_PATH = {
    "classrooms.StudentCourse": "course_id",
    "students.StudentSubmission": "assignment__course_id",
    "ai_processor.ChatSession": "course_id",
    "students.BackgroundProcessingTask": "assignment__course_id",  # E-Q2
}

#: Rows in a fixed order: time first, then id.
ORDERING = {
    "classrooms.StudentCourse": ("created_at", "id"),
    "students.StudentSubmission": ("submission_date", "id"),
    "students.BackgroundProcessingTask": ("created_at", "id"),
    "ai_processor.ChatSession": ("created_at", "id"),
    "ai_processor.ChatMessage": ("timestamp", "id"),
    "users.UserActivity": ("timestamp", "id"),
}

# A reader that the student-facing helpers treat as the student.
_AS_STUDENT = SimpleNamespace(user=SimpleNamespace(user_type=UserTypes.STUDENT))


@dataclass(frozen=True)
class Scope:
    kind: str  # "all", "school" or "courses" (the audit event says which)
    course_ids: Optional[frozenset] = None  # None: every course


def _super_admin(caller, student):
    return Scope("all") if caller.is_real_super_admin() else None


def _school_admin(caller, student):
    if caller.school_id is None or caller.school_id != student.school_id:
        return None
    return Scope("school")


def _teacher(caller, student):
    if not TEACHER_EXPORT_ALLOWED:
        return None
    # H-38: only the courses the teacher can still reach; a teacher removed
    # from the school loses its school-session courses.
    owned = frozenset(reachable_courses(caller).values_list("id", flat=True))
    if not StudentCourse.objects.filter(student=student, course_id__in=owned).exists():
        return None
    return Scope("courses", owned)


#: Who may export, and how much. One place (SM, R5).
EXPORT_CALLERS: dict[str, Callable] = {
    UserTypes.SUPER_ADMIN: _super_admin,
    UserTypes.SCHOOL_ADMIN: _school_admin,
    UserTypes.TEACHER: _teacher,
}


def export_scope(caller, student):
    """The caller's scope over `student`, or None (refused: "not found")."""
    if student.user_type != UserTypes.STUDENT or caller.pk == student.pk:
        return None
    decide = EXPORT_CALLERS.get(caller.user_type)
    return decide(caller, student) if decide else None


def _exported_entries(scope):
    for (label, field_name), entry in STUDENT_DATA_MAP.items():
        if not entry.export or entry.match != Match.USER:
            continue
        if scope.course_ids is not None and not entry.course_scoped:
            continue
        yield label, field_name


def _plain(obj, field_name):
    field = obj._meta.get_field(field_name)
    return getattr(obj, field.attname)


def _value(label, obj, field_name, released_grades):
    if label == "students.StudentSubmission":
        if field_name == "raw_input":
            return answer_document_for_student(obj)
        if field_name == "max_points":
            return max_points_shown(obj, _AS_STUDENT)
        if field_name in EXPORT_CONDITIONAL[label] and not obj.is_published:
            return None
        if field_name == "feedback":
            return student_safe_feedback(obj.feedback)
        if field_name == "formatted_grade":
            return student_safe_formatted_grade(obj.formatted_grade)
    if label == "classrooms.StudentCourse" and field_name == "final_grade":
        return released_grades(obj.course_id)
    return _plain(obj, field_name)


def _row(label, obj, released_grades=None):
    names = EXPORT_FIELDS[label] + EXPORT_CONDITIONAL.get(label, ())
    return {name: _value(label, obj, name, released_grades) for name in names}


def _rows(label, field_name, student, scope):
    model = apps.get_model(label)
    queryset = model.objects.filter(**{field_name: student})
    if scope.course_ids is not None:
        queryset = queryset.filter(**{f"{COURSE_PATH[label]}__in": scope.course_ids})
    queryset = queryset.order_by(*ORDERING.get(label, ("pk",)))
    if label == "students.StudentSubmission":
        queryset = queryset.select_related("assignment")
    released_grades: Optional[Callable] = None
    if label == "classrooms.StudentCourse":
        submissions = list(
            apps.get_model("students.StudentSubmission")
            .objects.filter(student=student)
            .select_related("assignment")
        )
        released_grades = partial(released_course_grade, submissions)

    rows = []
    for obj in queryset:
        row = _row(label, obj, released_grades)
        if label == "ai_processor.ChatSession":
            row[EXPORT_NAMES["messages"]] = [
                _row("ai_processor.ChatMessage", message)
                for message in apps.get_model("ai_processor.ChatMessage")
                .objects.filter(session=obj)
                .order_by(*ORDERING["ai_processor.ChatMessage"])
            ]
        rows.append(row)
    return rows


def _profile(student, scope):
    names = (
        TEACHER_PROFILE_FIELDS
        if scope.course_ids is not None
        else EXPORT_FIELDS["users.CustomUser"]
    )
    row = {name: _plain(student, name) for name in names}
    if "profile_image" in row:
        row["profile_image"] = student.profile_image.name or None
    return row


def _picture_name(student):
    """The profile picture's name in the zip: the approved base name plus
    the stored file's extension, when it has a real one."""
    _, extension = os.path.splitext(os.path.basename(student.profile_image.name))
    if not _EXTENSION.fullmatch(extension):
        extension = ""
    return EXPORT_NAMES["profile_image"] + extension


def _image(student):
    """The stored profile image's bytes, or None when it can't be read (Q3:
    the export goes on; the failure is logged and listed as missing)."""
    image = student.profile_image
    try:
        with image.storage.open(image.name, "rb") as handle:
            return handle.read()
    except Exception:
        logger.error(
            "student data export: profile image unreadable (student %s)",
            student.pk,
            exc_info=True,
        )
        return None


def _dump(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, indent=2)


def build_export(student, scope):
    """Return `(zip bytes, audit metadata)` for `student` within `scope`."""
    files, counts, missing = {}, {}, []
    for label, field_name in _exported_entries(scope):
        rows = _rows(label, field_name, student, scope)
        files[EXPORT_NAMES[label]] = _dump(rows).encode()
        counts[EXPORT_NAMES[label]] = len(rows)
    files[EXPORT_NAMES["profile"]] = _dump(_profile(student, scope)).encode()
    if scope.course_ids is None and student.profile_image:
        content = _image(student)
        if content is None:
            missing.append(_picture_name(student))
        else:
            files[_picture_name(student)] = content
    files[EXPORT_NAMES["readme"]] = README_TEXT.encode()
    manifest = {
        EXPORT_NAMES["manifest_counts"]: counts,
        EXPORT_NAMES["manifest_scope"]: scope.kind,
        EXPORT_NAMES["manifest_generated_at"]: timezone.now(),
        EXPORT_NAMES["manifest_missing"]: missing,
    }
    files[EXPORT_NAMES["manifest"]] = _dump(manifest).encode()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_name in sorted(files):
            archive.writestr(file_name, files[file_name])
    content = buffer.getvalue()
    metadata = {
        "file_count": len(files),
        "item_count": sum(counts.values()),
        "file_size_bytes": len(content),
        "scope": scope.kind,
    }
    return content, metadata
