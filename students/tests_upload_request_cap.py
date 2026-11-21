"""Owner Q5 A (2026-11-19): one upload request is at most
UPLOAD_REQUEST_MAX_MB (env, default 500) on the three upload routes: the
teacher's answer batch, the teacher's assignment batch and the student's own
answer. A request whose Content-Length is over the cap is refused with
FILE_TOO_LARGE (413; dimension "bytes", limit the cap, actual the request's
length) before anything reads the body: no file parsed or spooled to disk,
none validated, stored, held, queued or sent to the AI, no audit event.
Under the cap every route is unchanged, the per-file cap included
(MAX_UPLOAD_FILE_SIZE_MB, default 10).

A chunked request (no Content-Length) is never read at all: gunicorn sets
CONTENT_LENGTH only from the header, and Django reads a request without one
as an empty body (LimitedStream(0)), so the route answers as it does today
for a request with no files. Pinned here, not changed."""

from unittest.mock import patch

from django.conf import settings
from django.http import multipartparser
from django.test import SimpleTestCase, override_settings
from django.test.client import (
    BOUNDARY,
    MULTIPART_CONTENT,
    FakePayload,
    encode_multipart,
)
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from audit.models import AuditEvent
from billing.models import AIJob
from billing.tests.test_holds_door import fixed_estimate, png_file
from billing.tests.test_holds_entry import classroom
from students import kept_files
from students.models import BackgroundProcessingTask, BatchUploadSession, KeptUpload

#: Owner-approved 2026-11-20 (Q8).
TEACHER_HINT = "Upload fewer files at a time, then try again."
#: The student route keeps FILE_TOO_LARGE's own remediation.
STUDENT_HINT = (
    "Split the file, remove blank pages or scan at a lower resolution, "
    "then upload again."
)


def body_of(data):
    return encode_multipart(BOUNDARY, data)


class TheDefaultCap(SimpleTestCase):
    def test_the_cap_is_500_mb_by_default(self):
        self.assertEqual(getattr(settings, "UPLOAD_REQUEST_MAX_MB", None), 500)
        self.assertEqual(
            getattr(settings, "UPLOAD_REQUEST_MAX_BYTES", None), 500 * 1024 * 1024
        )

    def test_the_per_file_cap_is_its_own_setting(self):
        from AutoGrader.uploads import max_upload_size_bytes

        self.assertEqual(max_upload_size_bytes(), 10 * 1024 * 1024)


class Routes(APITestCase):
    def setUp(self):
        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            _,
        ) = classroom("cap", 10**9)
        self.fixture_events = set(AuditEvent.objects.values_list("pk", flat=True))

    def answers_url(self):
        return reverse(
            "student-submission-batch-upload",
            kwargs={"assignment_id": self.assignment.pk},
        )

    def assignments_url(self):
        return reverse("assignment-upload-async")

    def student_url(self):
        return reverse(
            "student-submission-upload-async",
            kwargs={"assignment_id": self.assignment.pk},
        )

    def answers_body(self, n=2):
        return body_of({"answers": [png_file(f"a{i}.png") for i in range(n)]})

    def assignments_body(self, n=2):
        return body_of(
            {
                "course": str(self.course.pk),
                "assignments": [png_file(f"s{i}.png") for i in range(n)],
            }
        )

    def student_body(self):
        return body_of({"answer": png_file("mine.png")})

    def post(self, user, url, body, **extra):
        self.client.force_authenticate(user=user)
        with patch("students.views.launch_processing_task") as a, patch(
            "assignments.views.launch_processing_task"
        ) as b, fixed_estimate():
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.generic(
                    "POST", url, body, content_type=MULTIPART_CONTENT, **extra
                )
        self.launches = a.call_count + b.call_count
        return response

    def assert_refused(self, response, length, cap, hint=TEACHER_HINT):
        from AutoGrader.uploads import human_size

        self.assertEqual(response.status_code, 413, response.content)
        coded = response.json()["error"]["field_errors"]
        self.assertEqual(coded["reason_code"], "FILE_TOO_LARGE")
        self.assertEqual(
            coded["error"],
            f"This upload is {human_size(length)} and the limit is "
            f"{human_size(cap)} for one upload.",
        )
        self.assertEqual(coded["remediation"], hint)
        self.assertEqual(coded["params"]["dimension"], "bytes")
        self.assertEqual(coded["params"]["limit"], cap)
        self.assertEqual(coded["params"]["actual"], length)
        self.assertNotIn("file_name", coded["params"])

    def assert_nothing_happened(self):
        self.assertEqual(self.launches, 0)
        self.assertFalse(BatchUploadSession.objects.exists())
        self.assertFalse(BackgroundProcessingTask.objects.exists())
        self.assertFalse(KeptUpload.objects.exists())
        self.assertFalse(AIJob.objects.exists())
        # The classroom fixture writes its own events (subscription, roster).
        self.assertFalse(
            AuditEvent.objects.exclude(pk__in=self.fixture_events).exists()
        )


class OverTheCapIsRefusedBeforeTheBodyIsRead(Routes):
    def refused(self, user, url, body, hint=TEACHER_HINT):
        cap = len(body) - 1
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=cap), patch.object(
            multipartparser.MultiPartParser,
            "parse",
            side_effect=AssertionError("the body was parsed"),
        ), patch.object(
            kept_files, "store", side_effect=AssertionError("a file was stored")
        ):
            response = self.post(user, url, body)
        self.assert_refused(response, len(body), cap, hint)
        self.assert_nothing_happened()

    def test_teacher_answer_batch_one_byte_over(self):
        self.refused(self.teacher, self.answers_url(), self.answers_body())

    def test_teacher_assignment_batch_one_byte_over(self):
        self.refused(self.teacher, self.assignments_url(), self.assignments_body())

    def test_student_answer_one_byte_over(self):
        self.refused(
            self.student, self.student_url(), self.student_body(), STUDENT_HINT
        )

    def test_a_session_signed_in_teacher_is_refused_without_parsing(self):
        """A session-cookie user is authenticated with a CSRF check that
        parses a multipart body: the refusal must not authenticate them
        (the exception handler reads request.user)."""
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.teacher)
        body = self.answers_body()
        cap = len(body) - 1
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=cap), patch.object(
            multipartparser.MultiPartParser,
            "parse",
            side_effect=AssertionError("the body was parsed"),
        ), patch("students.views.launch_processing_task") as launch:
            response = self.client.generic(
                "POST", self.answers_url(), body, content_type=MULTIPART_CONTENT
            )
        self.launches = launch.call_count
        self.assert_refused(response, len(body), cap)
        self.assert_nothing_happened()


class AtOrUnderTheCapIsUnchanged(Routes):
    def test_teacher_answer_batch_at_the_cap_runs(self):
        body = self.answers_body()
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=len(body)):
            response = self.post(self.teacher, self.answers_url(), body)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(self.launches, 2)

    def test_teacher_assignment_batch_at_the_cap_runs(self):
        body = self.assignments_body()
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=len(body)):
            response = self.post(self.teacher, self.assignments_url(), body)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(self.launches, 2)

    def test_the_default_cap_answers_byte_for_byte_as_no_cap(self):
        """Under the cap the route's answer is the one it gave before:
        identical with the default cap and with the check switched off."""
        body = self.answers_body(1)
        first = self.post(self.teacher, self.answers_url(), body)
        BatchUploadSession.objects.all().delete()
        with override_settings(UPLOAD_REQUEST_MAX_BYTES=10**12):
            second = self.post(self.teacher, self.answers_url(), body)
        self.assertEqual(first.status_code, second.status_code)
        strip = {"session_id", "task_id", "item_id", "id"}

        def shape(response):
            def walk(value):
                if isinstance(value, dict):
                    return {k: walk(v) for k, v in value.items() if k not in strip}
                if isinstance(value, list):
                    return [walk(v) for v in value]
                return value

            return walk(response.json())

        self.assertEqual(shape(first), shape(second))

    def test_a_file_over_50_mb_under_the_request_cap_keeps_its_own_refusal(self):
        """The per-file cap still refuses that file as its own item (413
        FILE_TOO_LARGE with its file name), as before."""
        with override_settings(MAX_UPLOAD_FILE_SIZE_BYTES=10):
            response = self.post(self.teacher, self.answers_url(), self.answers_body(1))
        self.assertNotEqual(response.status_code, 413)
        entry = response.json()["data"]["tasks"][0]
        self.assertEqual(entry["reason_code"], "FILE_TOO_LARGE")


class AChunkedRequestIsNeverRead(Routes):
    """No Content-Length: Django reads nothing, so nothing can pass the cap
    and nothing is spooled. The route answers as for a request with no
    files (today's 400), unchanged."""

    def test_a_chunked_body_twice_the_cap_is_not_read(self):
        body = self.answers_body()
        read = []
        real_read = FakePayload.read

        def counting_read(payload, size=-1):
            data = real_read(payload, size)
            read.append(len(data))
            return data

        with override_settings(UPLOAD_REQUEST_MAX_BYTES=len(body) // 2), patch.object(
            FakePayload, "read", counting_read
        ), patch.object(
            kept_files, "store", side_effect=AssertionError("a file was stored")
        ):
            response = self.post(
                self.teacher,
                self.answers_url(),
                body,
                CONTENT_LENGTH="",
                HTTP_TRANSFER_ENCODING="chunked",
            )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(sum(read), 0)
        self.assertFalse(BatchUploadSession.objects.exists())
        self.assertFalse(KeptUpload.objects.exists())
