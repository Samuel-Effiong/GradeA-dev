"""H-274 (e.1a): the other two student answer doors keep their pre-(e.1) rules.

SM ruling (A), extended 2026-11-02: upload-async mirrors the synchronous
upload (S6: an unsupported type is refused first, then a 0-byte answer is
SUBMISSION_EMPTY), and batch-upload keeps S7a (every file is an item; the
whole batch is never refused, nor left half-queued).
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from students.models import BackgroundProcessingTask, BackgroundTaskStatus
from students.tests_batch_item_results import BatchFixture, png_bytes


def _empty(name, content_type):
    return SimpleUploadedFile(name, b"", content_type=content_type)


class TheAsyncStudentUploadKeepsS6(BatchFixture, TestCase):
    def setUp(self):
        self.build(students=1)
        self.api.force_authenticate(user=self.students[0])

    def upload(self, uploaded):
        return self.api.post(
            reverse(
                "student-submission-upload-async",
                kwargs={"assignment_id": str(self.assignment.id)},
            ),
            {"answer": uploaded},
            format="multipart",
        )

    def assertTaskFailedWith(self, response, code):
        self.assertEqual(response.status_code, 200, response.content[:400])
        task = BackgroundProcessingTask.objects.get()
        self.assertEqual(task.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(task.reason_code, code)

    def test_a_zero_byte_answer_is_submission_empty(self):
        self.assertTaskFailedWith(
            self.upload(_empty("blank.png", "image/png")), "SUBMISSION_EMPTY"
        )

    def test_an_empty_file_of_an_unsupported_type_is_the_type_first(self):
        self.assertTaskFailedWith(
            self.upload(_empty("blank.txt", "text/plain")), "FILE_TYPE_UNSUPPORTED"
        )


class AnEmptyFileInABatchFailsAsItsItem(BatchFixture, TestCase):
    def setUp(self):
        self.build(students=2)

    def run_batch(self, empty, code):
        files = [
            SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"),
            empty,
            SimpleUploadedFile("c.png", png_bytes(), content_type="image/png"),
        ]
        with self.extraction(["Pupil00 Enrolled", "Pupil01 Enrolled"]):
            response = self.batch_upload(files)

        self.assertEqual(response.status_code, 202, response.content[:400])
        tasks = response.json()["data"]["tasks"]
        self.assertEqual(
            [t["file_name"] for t in tasks], ["a.png", empty.name, "c.png"]
        )
        data = self.session_results(response.json()["data"]["session_id"])
        self.assertEqual((data["success_count"], data["failure_count"]), (2, 1))
        [bad] = data["failure_list"]
        self.assertEqual(bad["file_name"], empty.name)
        self.assertEqual(bad["item_index"], 2)
        self.assertEqual(bad["reason_code"], code)

    def test_a_zero_byte_answer_is_submission_empty_and_the_rest_run(self):
        self.run_batch(_empty("blank.png", "image/png"), "SUBMISSION_EMPTY")

    def test_an_empty_unsupported_file_is_the_type_first_and_the_rest_run(self):
        self.run_batch(_empty("blank.txt", "text/plain"), "FILE_TYPE_UNSUPPORTED")
