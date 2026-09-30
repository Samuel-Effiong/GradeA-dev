"""Epic A S8 harness (plan 08 §8.1): what one busy school day writes to the
audit trail. Test tooling, not app code: nothing imports it, and it is not
in the suite (the file name doesn't match the test pattern). Run it by label
on the test database:

    python manage.py test audit.bench_volume --settings=settings_worktree

It drives the real routes and tasks - with Celery dispatch and the AI
provider patched out, as the suite's own tests do - and prints the events
each flow wrote, per action and per retention class, plus bytes per row.
Those numbers are copied into `audit/volume.py`, which the
`audit_volume_report` command projects from.
"""

import json
from collections import Counter
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import grade_engine_async
from audit import history
from audit.models import AuditEvent
from audit.tasks import sweep_audit_pii_short_retention, sweep_audit_retention
from audit.tests_history import ESSAY, _grader
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()
PW = "Volume-harness-pw-1"  # pragma: allowlist secret
PDF = b"%PDF-1.4\n%%EOF\n"
CLASS_SIZE = 30
EDITS = 3
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _tally(since):
    """{action: n} and {retention_class: n} for events after `since` (a set
    of event pks already seen)."""
    new = AuditEvent.objects.exclude(pk__in=since)
    return (
        Counter(new.values_list("action", flat=True)),
        Counter(new.values_list("retention_class", flat=True)),
    )


@override_settings(CACHES=LOCMEM_CACHE, GRADING_SECOND_OPINION_ENABLED=False)
class BusySchoolDay(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ("users.views.sync_user_to_mailerlite",):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def flow(self, name, results, run):
        seen = set(AuditEvent.objects.values_list("pk", flat=True))
        run()
        actions, classes = _tally(seen)
        results[name] = {"actions": dict(actions), "classes": dict(classes)}

    def test_print_one_busy_day(self):
        school = School.objects.create(name="Volume School")
        teacher = User.objects.create_user(
            email="volume.teacher@example.com",
            password=PW,
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        session = Session.objects.create(name="Term", teacher=teacher)
        course = Course.objects.create(name="Volume", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title="Essay",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=ESSAY,
        )
        self.client.force_authenticate(user=teacher)
        teacher_day, student_day, system_day = {}, {}, {}

        def roster_import():
            rows = "".join(
                f"Vol\tStudent{i}\tvolume.student.{i}@example.com\n"
                for i in range(CLASS_SIZE)
            )
            response = self.client.post(
                reverse("course-bulk-add-students", kwargs={"pk": course.pk}),
                {"raw_data": "First Name\tLast Name\tEmail\n" + rows},
            )
            self.assertEqual(response.status_code, 200, response.content[:300])

        self.flow("roster import of 30", teacher_day, roster_import)
        students = list(
            User.objects.filter(email__startswith="volume.student.").order_by("email")
        )
        self.assertEqual(len(students), CLASS_SIZE)

        def batch_upload():
            with patch("students.views.launch_processing_task") as launch:
                launch.return_value = MagicMock(id="volume-celery-task-id")
                response = self.client.post(
                    reverse(
                        "student-submission-batch-upload",
                        kwargs={"assignment_id": assignment.id},
                    ),
                    {
                        "answers": [
                            SimpleUploadedFile(
                                f"a{i}.pdf", PDF, content_type="application/pdf"
                            )
                            for i in range(CLASS_SIZE)
                        ]
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 202, response.content[:300])

        self.flow("batch upload of 30", teacher_day, batch_upload)
        # The upload task (patched out above) is what creates the rows; a
        # new submission writes no history of its own (SUBMISSION_UPLOAD is
        # the batch's event), so creating them directly adds nothing.
        submissions = [
            StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "<p>An essay.</p>"}],
            )
            for student in students
        ]

        def grade_all():
            with patch("assignments.views.grade_engine_async") as task:
                task.delay.return_value.id = "volume-celery-task-id"
                response = self.client.post(
                    reverse("assignment-grade-all", kwargs={"pk": assignment.pk})
                )
            self.assertIn(response.status_code, (200, 202), response.content[:300])

        self.flow("grade-all of 30 (the request)", teacher_day, grade_all)

        def grading_runs():
            with patch.object(AIProcessor, "execute_graded_task", side_effect=_grader):
                for submission in submissions:
                    outcome = grade_engine_async.apply(
                        args=(str(teacher.id), str(submission.id))
                    )
                    self.assertTrue(outcome.successful(), outcome.result)

        self.flow("grading runs of 30 (Celery)", teacher_day, grading_runs)

        def publish_all():
            with patch("students.services.notify_student_of_graded_submission"):
                response = self.client.post(
                    reverse(
                        "assignment-publish-all-grades", kwargs={"pk": assignment.pk}
                    )
                )
            self.assertEqual(response.status_code, 200, response.content[:300])

        self.flow("publish-all of 30", teacher_day, publish_all)

        def edits():
            for submission in submissions[:EDITS]:
                response = self.client.patch(
                    reverse(
                        "student-submission-update-grade",
                        kwargs={"pk": submission.pk},
                    ),
                    data={"score": 10},
                    format="json",
                )
                self.assertEqual(response.status_code, 200, response.content[:300])

        self.flow(f"{EDITS} grade edits", teacher_day, edits)

        # A student's day: one sign-in (an already-enrolled student, so no
        # PENDING activation); viewing grades writes nothing.
        student = students[0]
        student.set_password(PW)
        student.is_active = True
        student.email_verified_at = timezone.now()
        student.save()
        # Setup, before the flow's snapshot: record_bulk, as production code
        # must (the S4 guard scans this file too).
        history.record_bulk(
            StudentCourse.all_objects.filter(student=student),
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        def sign_in():
            self.client.force_authenticate(user=None)
            response = self.client.post(
                reverse("login"),
                {"email": student.email, "password": PW},
                format="json",
                REMOTE_ADDR="10.8.0.1",
            )
            self.assertEqual(response.status_code, 200, response.content[:300])

        self.flow("student sign-in", student_day, sign_in)

        def beat():
            sweep_audit_retention.apply()
            sweep_audit_pii_short_retention.apply()

        self.flow("Beat: the two audit sweeps", system_day, beat)

        table = AuditEvent._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT avg(pg_column_size(t.*)) FROM "  # nosec B608
                f"{connection.ops.quote_name(table)} t"
            )
            avg_bytes = float(cursor.fetchone()[0] or 0)
            cursor.execute(
                "SELECT pg_relation_size(%s), pg_indexes_size(%s)", [table, table]
            )
            heap, indexes = cursor.fetchone()

        def totals(day):
            total = Counter()
            for flow in day.values():
                total.update(flow["actions"])
            return dict(sorted(total.items()))

        report = {
            "teacher_day_flows": teacher_day,
            "student_day_flows": student_day,
            "system_day_flows": system_day,
            "PER_TEACHER_DAY": totals(teacher_day),
            "PER_STUDENT_DAY": totals(student_day),
            "SYSTEM_PER_DAY": totals(system_day),
            "bytes_per_row": round(avg_bytes, 1),
            "heap_bytes": heap,
            "index_bytes": indexes,
            "rows": AuditEvent.objects.count(),
        }
        print("\nS8_VOLUME " + json.dumps(report, sort_keys=True))
