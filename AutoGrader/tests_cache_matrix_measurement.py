"""H-1: per-mutation Redis invalidation, with the legacy wildcards removed.

The owner asked for evidence that broad clearing is eliminated rather than
moved somewhere else. Until H-1 step 4 this ran each of the twelve fixed
write paths twice, with the legacy wildcard receivers live and with them
patched out; the ON column (24-32 of 32 bystander entries deleted per
mutation) is recorded in docs/evidence/
H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md §5. Step 4 deleted the
wildcards, so the ON side can no longer run: this now measures the real
code once per path and asserts what the OFF column asserted. It measures:

* the Redis commands this process sent during the mutation (SCAN, DEL and
  UNLINK, and INCR/INCRBY - redis-py sends `incr` as INCRBY - which is
  one per generation bumped);
* bystander entries deleted: cached responses belonging to four other
  schools that no longer exist in Redis after the mutation;
* bystander reads gone cold: of those schools' reads, how many miss the
  cache when re-read. A miss is either a deleted entry or an entry
  orphaned by a generation bump, so a broad invalidation moved into
  generation counters would show up here even with zero deletes.

Cold reads are split in two. Seven of the eight bystander reads are keyed
only on the bystander user's own generation, and must never go cold from
another school's mutation. `course/my-courses` is also keyed on the
`global` generation, a Stage 2 design tracked as H-15, so any mutation that
bumps `global` makes it cold for every student. That column is reported,
not asserted to be zero.

Real Redis + real Postgres. Every mutation goes through the same real
endpoint, service or management command as its matrix test.
"""

from decimal import Decimal
from io import StringIO
from typing import Any

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db.models.signals import pre_save
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import (
    Assignment,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
    AssignmentStatus,
)
from assignments.signals import sanitize_assignment_title
from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process
from AutoGrader.tests_cache_matrix_support import (
    disallowed_scan_patterns,
    scan_patterns_sent_by_this_process,
)
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from students.models import StudentSubmission
from students.services import _claim_submission_for_grading
from users.models import UserTypes

User = get_user_model()

GLOBAL_KEYED_READ = "student my-courses"
BYSTANDER_SCHOOLS = 4


def question():
    return {
        "question_number": 1,
        "question_text": "Q1",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
        "blooms_level": "Apply",
    }


class InvalidationMeasurementTests(TransactionTestCase):
    reset_sequences = True

    def _user(self, email, user_type, first_name, **extra):
        return User.objects.create_user(
            email=email,
            password="password123",  # nosec  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            first_name=first_name,
            last_name=user_type.title(),
            **extra,
        )

    def _client(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        return client

    # --- fixtures -------------------------------------------------------

    def _bystander(self, mode, n):
        tag = f"{mode}-by{n}"
        school = School.objects.create(name=f"Bystander {tag}")
        admin = self._user(
            f"{tag}-admin@x.test", UserTypes.SCHOOL_ADMIN, f"{tag}A", school=school
        )
        teacher = self._user(
            f"{tag}-teacher@x.test", UserTypes.TEACHER, f"{tag}T", school=school
        )
        student = self._user(f"{tag}-student@x.test", UserTypes.STUDENT, f"{tag}S")
        Session.objects.create(
            name=f"{tag} school term",
            owner_type=SessionOwnerType.SCHOOL,
            school=school,
            created_by=admin,
        )
        term = Session.objects.create(name=f"{tag} term", teacher=teacher)
        course = Course.objects.create(
            name=f"{tag} course", teacher=teacher, session=term
        )
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        assignment = Assignment.objects.create(
            title=f"{tag} assignment",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "one"}],
        )
        return [
            ("teacher course list", teacher, reverse("course-list")),
            ("teacher assignment list", teacher, reverse("assignment-list")),
            ("student course list", student, reverse("course-list")),
            ("student submission list", student, reverse("student-submission-list")),
            (GLOBAL_KEYED_READ, student, reverse("course-my-courses")),
            (
                "student dashboard summary",
                student,
                reverse("student-summary", args=[course.pk]),
            ),
            ("admin session list", admin, reverse("session-list")),
            (
                "admin view of teacher",
                admin,
                reverse("user-detail", args=[teacher.pk]),
            ),
        ]

    def _subject(self, mode):
        s: dict[str, Any] = {}
        s["school"] = School.objects.create(name=f"Subject {mode}")
        s["other_school"] = School.objects.create(name=f"Subject {mode} move target")
        s["admin"] = self._user(
            f"{mode}-sub-admin@x.test",
            UserTypes.SCHOOL_ADMIN,
            f"{mode}SubA",
            school=s["school"],
        )
        s["teacher"] = self._user(
            f"{mode}-sub-teacher@x.test",
            UserTypes.TEACHER,
            f"{mode}SubT",
            school=s["school"],
        )
        s["mover"] = self._user(
            f"{mode}-sub-mover@x.test",
            UserTypes.TEACHER,
            f"{mode}SubM",
            school=s["school"],
        )
        s["superadmin"] = self._user(
            f"{mode}-sub-super@x.test",
            UserTypes.SUPER_ADMIN,
            f"{mode}SubSu",
            is_superuser=True,
        )
        s["students"] = [
            self._user(
                f"{mode}-sub-student{i}@x.test", UserTypes.STUDENT, f"{mode}SubS{i}"
            )
            for i in range(3)
        ]
        s["school_session"] = Session.objects.create(
            name=f"{mode} sub school term",
            owner_type=SessionOwnerType.SCHOOL,
            school=s["school"],
            created_by=s["admin"],
        )
        term = Session.objects.create(name=f"{mode} sub term", teacher=s["teacher"])
        s["course"] = Course.objects.create(
            name=f"{mode} sub course", teacher=s["teacher"], session=term
        )
        s["enrollments"] = [
            StudentCourse.objects.create(
                student=student,
                course=s["course"],
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            for student in s["students"]
        ]
        s["draft"] = Assignment.objects.create(
            title=f"{mode} sub draft",
            course=s["course"],
            status=AssignmentStatus.DRAFT,
            questions=[question()],
        )
        graded = Assignment.objects.create(
            title=f"{mode} sub graded",
            course=s["course"],
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        s["graded"] = graded
        for student in s["students"][:2]:
            StudentSubmission.objects.create(
                assignment=graded,
                student=student,
                answers=[{"question_number": 1, "answer_html": "one"}],
                graded_at=timezone.now(),
                score=Decimal("8.0"),
                is_published=False,
            )
        claimable = Assignment.objects.create(
            title=f"{mode} sub claimable",
            course=s["course"],
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        s["claimable_submission"] = StudentSubmission.objects.create(
            assignment=claimable,
            student=s["students"][2],
            answers=[{"question_number": 1, "answer_html": "one"}],
        )
        s["gen_for_message"] = AssignmentGenerationSession.objects.create(
            user=s["teacher"], course=s["course"], title="message target"
        )
        s["gen_for_delete"] = AssignmentGenerationSession.objects.create(
            user=s["teacher"], course=s["course"], title="delete target"
        )
        pre_save.disconnect(sanitize_assignment_title, sender=Assignment)
        try:
            Assignment.objects.create(
                title=f"<p>{mode} sub tagged</p>",
                course=s["course"],
                status=AssignmentStatus.PUBLISHED,
                questions=[question()],
            )
        finally:
            pre_save.connect(sanitize_assignment_title, sender=Assignment)
        s["wallet"] = CreditWallet.objects.get(user=s["teacher"])
        return s

    def _mutations(self, s):
        def ok(response):
            assert response.status_code in (200, 201, 204), response.content

        teacher = self._client(s["teacher"])
        return [
            (
                "G1 publish assignment",
                lambda: ok(
                    teacher.patch(
                        reverse("assignment-detail", args=[s["draft"].pk]),
                        {"status": "PUBLISHED"},
                        format="json",
                    )
                ),
            ),
            (
                "G2 withdraw student",
                lambda: ok(
                    teacher.patch(
                        reverse("student-course-detail", args=[s["enrollments"][0].pk]),
                        {"enrollment_status": "WITHDRAWN"},
                    )
                ),
            ),
            (
                "G3 publish all grades",
                lambda: ok(
                    teacher.post(
                        reverse("assignment-publish-all-grades", args=[s["graded"].pk])
                    )
                ),
            ),
            (
                "G4 move teacher school",
                lambda: ok(
                    self._client(s["superadmin"]).patch(
                        reverse("user-detail", args=[s["mover"].pk]),
                        {"school": str(s["other_school"].pk)},
                        format="json",
                    )
                ),
            ),
            (
                "G5 rename course",
                lambda: ok(
                    teacher.patch(
                        reverse("course-detail", args=[s["course"].pk]),
                        {"name": "renamed"},
                    )
                ),
            ),
            (
                "G6 rename school session",
                lambda: ok(
                    self._client(s["admin"]).patch(
                        reverse("session-detail", args=[s["school_session"].pk]),
                        {"name": "renamed school term"},
                    )
                ),
            ),
            (
                "G8 teacher renames self",
                lambda: ok(
                    teacher.patch(
                        reverse("user-detail", args=[s["teacher"].pk]),
                        {"first_name": "Renamed"},
                    )
                ),
            ),
            (
                "G9 delete generation session",
                lambda: ok(
                    teacher.delete(
                        reverse(
                            "assignment-generation-session-detail",
                            args=[s["gen_for_delete"].pk],
                        )
                    )
                ),
            ),
            (
                "P1 claim submission",
                lambda: _claim_submission_for_grading(s["claimable_submission"].pk),
            ),
            (
                "P3 add generation message",
                lambda: AssignmentGenerationMessage.objects.create(
                    session=s["gen_for_message"],
                    role=AssignmentGenerationRole.USER,
                    content="follow-up",
                ),
            ),
            (
                "P4 repair titles command",
                lambda: call_command(
                    "strip_html_from_assignment_titles", stdout=StringIO()
                ),
            ),
            (
                "P5 grant credits",
                lambda: CreditBucket.objects.create(
                    wallet=s["wallet"],
                    bucket_type=CreditBucketType.MANUAL_GRANT,
                    total_credits=100_000,
                    used_credits=0,
                ),
            ),
        ]

    # --- measurement ----------------------------------------------------

    @staticmethod
    def _response_keys():
        # Versioned response entries carry ":g."; generation counters and
        # unversioned bookkeeping keys (heartbeats, throttles) do not.
        return {
            key
            for key in cache.keys("*")  # type: ignore[attr-defined]  # django-redis
            if ":g." in key
        }

    def _read(self, user, url):
        response = self._client(user).get(url)
        self.assertEqual(response.status_code, 200, f"{url}: {response.content!r}")

    def _warm(self, reads):
        """Read every bystander endpoint and return {label#i: its key}."""
        owned = {}
        for index, (label, user, url) in enumerate(reads):
            before = self._response_keys()
            self._read(user, url)
            new = self._response_keys() - before
            existing = owned.get(f"{label}#{index}")
            if new:
                owned[f"{label}#{index}"] = new
            elif existing is None:
                owned[f"{label}#{index}"] = set()
        return owned

    def _measure(self, mode):
        s = self._subject(mode)
        reads = []
        for n in range(BYSTANDER_SCHOOLS):
            reads.extend(self._bystander(mode, n))

        rows = []
        for name, mutate in self._mutations(s):
            cache.clear()
            warmed = self._warm(reads)
            warmed_keys = set().union(*warmed.values())
            self.assertEqual(
                len(warmed_keys),
                len(reads),
                f"{mode}/{name}: expected one cached entry per bystander read",
            )

            with redis_commands_sent_by_this_process() as sent:
                with scan_patterns_sent_by_this_process() as scans:
                    mutate()

            deleted = len(warmed_keys - self._response_keys())

            cold_tenant = 0
            cold_global = 0
            for label, user, url in reads:
                before = self._response_keys()
                self._read(user, url)
                if self._response_keys() - before:
                    if label == GLOBAL_KEYED_READ:
                        cold_global += 1
                    else:
                        cold_tenant += 1

            rows.append(
                {
                    "mutation": name,
                    "scan": sent["SCAN"],
                    "disallowed_scans": disallowed_scan_patterns(scans),
                    "del": sent["DEL"] + sent["UNLINK"],
                    "incr": sent["INCR"] + sent["INCRBY"],
                    "deleted": deleted,
                    "cold_tenant": cold_tenant,
                    "cold_global": cold_global,
                }
            )
        return rows, len(reads)

    def test_every_fixed_write_path_invalidates_only_its_own_viewers(self):
        rows, total = self._measure("real")

        tenant_reads = total - BYSTANDER_SCHOOLS
        lines = [
            "",
            "[H-1 step 4 per-mutation invalidation measurement, wildcards removed]",
            f"bystanders: {BYSTANDER_SCHOOLS} other schools, {total} cached reads "
            f"({tenant_reads} tenant-keyed, {BYSTANDER_SCHOOLS} global-keyed "
            "my-courses)",
            "gone = bystander entries deleted; coldT = tenant-keyed bystander "
            "reads that missed; coldG = global-keyed my-courses reads that missed",
            f"{'mutation':<30} | {'SCAN':>4} {'DEL':>4} {'INCR':>4} {'gone':>5} "
            f"{'coldT':>6} {'coldG':>6}",
        ]
        for row in rows:
            lines.append(
                f"{row['mutation']:<30} | {row['scan']:>4} {row['del']:>4} "
                f"{row['incr']:>4} {row['deleted']:>5} {row['cold_tenant']:>6} "
                f"{row['cold_global']:>6}"
            )
        report = "\n".join(lines)
        print(report)

        for row in rows:
            name = row["mutation"]
            self.assertEqual(row["deleted"], 0, f"{name} deleted bystanders\n{report}")
            self.assertEqual(row["del"], 0, f"{name} sent DEL/UNLINK\n{report}")
            self.assertEqual(
                row["cold_tenant"],
                0,
                f"{name} made another school's tenant-keyed reads cold\n{report}",
            )
            # G1 saves an assignment, which clears that one assignment's
            # rendered PDFs by prefix (plan §2: kept, not a legacy wildcard).
            expected_scan = 1 if name.startswith("G1 ") else 0
            self.assertEqual(row["scan"], expected_scan, f"{name}\n{report}")
            self.assertEqual(row["disallowed_scans"], [], f"{name}\n{report}")
            # Every path still invalidates SOMETHING: removing the wildcards
            # must not have left a write path with no invalidation at all.
            self.assertGreater(row["incr"], 0, f"{name} bumped nothing\n{report}")
