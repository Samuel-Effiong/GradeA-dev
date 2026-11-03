# H-279 EVIDENCE: diffs to existing tests

These are the existing tests whose contract H-279 changes, as the SM asked
("List each diff in EVIDENCE"). Each diff runs from the branch base
e11e56536 to 2e56c49dd. Every amendment uses the approved held-first,
then-approved form, and keeps the old assertion as the pre-approval half.
The new H-279 modules (students/tests_h279_regrade_pending_approval.py,
students/tests_h279_regrade_pending_concurrency.py) are not listed: they
are entirely new.

The exact patch is logs/existing_test_diffs_e11e5653_2e56c49d.diff.gz (raw
sha256 94ccb17e3a4bfd72564eb51e7c4ba78cc83192cc778a3a9e2310cc95444bc861, gz sha256 25d9f00bd81f6d25ed0734e631ccc49a1d178251b228e890af84cfa9ac5c421e). Below, the blank
context lines lose their single leading space, because the repo's
whitespace hook trims it; the .gz is byte-exact.

| Pin | File | Commit(s) | Why |
|---|---|---|---|
| X1 | classrooms/tests_student_final_grade_released_only.py | cca16b03c | a re-grade of a released row is held; the final grade moves at approval |
| X2–X7 | students/tests_regrade_formatted_grade.py | cca16b03c, 495d74ea1 (X7: the JSON params matched "score"; now `'"score" ='`) | the re-grade's formatted grade is written to the held result and released with it |
| X8–X10 | students/tests_formatting_task_superseded.py | cca16b03c | the superseded check reads the held stamp for a held result |
| H-141 | students/tests_student_upload_answer.py | 339da92ee (SM ruling) | KEYS goes from 30 to 32 (pending_result, pending_graded_at appended); both are always None on the upload answer; 2 new tests |

## classrooms/tests_student_final_grade_released_only.py

```diff
diff --git a/classrooms/tests_student_final_grade_released_only.py b/classrooms/tests_student_final_grade_released_only.py
index 9f9948d17..f87fd88ce 100644
--- a/classrooms/tests_student_final_grade_released_only.py
+++ b/classrooms/tests_student_final_grade_released_only.py
@@ -174,14 +174,21 @@ class ReleaseIsWhatMovesTheStudentsNumber(StudentFinalGradeBase):
         self.assert_grade(self.detail(self.student, fresh=False), "50.00", "F")
         self.assert_grade(self.listed(self.student, fresh=False), "50.00", "F")

-    def test_a_regrade_after_release_moves_the_number_at_once(self):
-        """Intended: the work is released, so its new score is too."""
+    def test_a_regrade_after_release_moves_the_number_once_approved(self):
+        """H-279 (owner, 2026-11-02): an AI re-grade of released work is held
+        for the teacher, and the number moves when the teacher approves it
+        (the publish route). It was "at once" before H-279."""
         self.grade_by_ai(self.first, 10)
         self.release(self.first)
         self.assert_grade(self.detail(self.student), "100.00", "A+")

         self.grade_by_ai(self.first, 0)

+        # held: the released number stands
+        self.assert_grade(self.detail(self.student, fresh=False), "100.00", "A+")
+
+        self.release(self.first)
+
         self.assert_grade(self.detail(self.student, fresh=False), "0.00", "F")

     def test_a_released_zero_is_a_grade(self):
```

## students/tests_regrade_formatted_grade.py

```diff
diff --git a/students/tests_regrade_formatted_grade.py b/students/tests_regrade_formatted_grade.py
index fd87c4a00..4d3b2d40e 100644
--- a/students/tests_regrade_formatted_grade.py
+++ b/students/tests_regrade_formatted_grade.py
@@ -23,21 +23,35 @@ from django.test.utils import CaptureQueriesContext

 from assignments.tasks import formatted_grade_async
 from students.models import StudentSubmission
+from students.services import held_result_stamp
 from students.tests_answer_document_before_release import AnswerDocumentBase
 from students.tests_formatting_task_superseded import FORMATTER, SupersededBase
 from students.tests_manual_grade_formatted_grade import OLD_STATEMENT


 class ARegradeClearsTheOldWording(SupersededBase):
-    """A released paper graded 7, with the wording of that result stored."""
+    """A released paper graded 7, with the wording of that result stored.
+
+    H-279 (owner, 2026-11-02): an AI re-grade of a released paper is held for
+    the teacher. Until the teacher approves it (here: the publish route) the
+    released score and its wording stand; the approval releases the new
+    score, and with it no old wording (H-146's rule, at the approval)."""
+
+    def held_stamp(self):
+        return held_result_stamp(self.row())

     def test_before_the_regrade_the_student_reads_the_old_statement(self):
         """The control: the fixture's text does reach the student."""
         self.assertIn(OLD_STATEMENT, str(self.student_reads()["formatted_grade"]))

-    def test_the_regrade_clears_the_stored_formatted_grade(self):
+    def test_the_regrade_clears_the_stored_formatted_grade_once_approved(self):
         self.grade_by_ai(self.submission, 3)

+        # held: the released wording stands
+        self.assertIn(OLD_STATEMENT, self.stored_formatted())
+
+        self.release()
+
         self.assertIsNone(self.stored_formatted())

     def test_between_the_save_and_the_task_the_student_reads_no_old_wording(self):
@@ -45,6 +59,13 @@ class ARegradeClearsTheOldWording(SupersededBase):

         self.grade_by_ai(self.submission, 3)

+        # held: the student reads the released score and its wording
+        held = self.student_reads()
+        self.assertEqual(Decimal(str(held["score"])), Decimal("7"))
+        self.assertIn(OLD_STATEMENT, str(held["formatted_grade"]))
+
+        self.release()
+
         after = self.student_reads()
         self.assertEqual(Decimal(str(after["score"])), Decimal("3"))
         self.assertIsNone(after["formatted_grade"])
@@ -52,7 +73,12 @@ class ARegradeClearsTheOldWording(SupersededBase):
     def test_the_regrades_own_task_fills_it(self):
         self.grade_by_ai(self.submission, 3)

-        self.run_task(self.stamp_now())
+        self.run_task(self.held_stamp())
+
+        # held: the task worded the held result; the student reads the old
+        self.assertIn(OLD_STATEMENT, str(self.student_reads()["formatted_grade"]))
+
+        self.release()

         text = str(self.student_reads()["formatted_grade"])
         self.assertIn("You scored 3 out of 10 points", text)
@@ -66,9 +92,14 @@ class ARegradeClearsTheOldWording(SupersededBase):
             self.assertRaises(RuntimeError),
         ):
             formatted_grade_async(
-                str(self.submission.pk), "a prompt", result_stamp=self.stamp_now()
+                str(self.submission.pk), "a prompt", result_stamp=self.held_stamp()
             )

+        # held: the released wording stands
+        self.assertIn(OLD_STATEMENT, self.stored_formatted())
+
+        self.release()
+
         self.assertIsNone(self.stored_formatted())
         self.assertIsNone(self.student_reads()["formatted_grade"])

@@ -76,17 +107,41 @@ class ARegradeClearsTheOldWording(SupersededBase):
         first = self.stamp_now()
         self.grade_by_ai(self.submission, 3)

+        # held: the first grading's result is still the released one, so
+        # its task still words it
+        self.run_task(first)
+        self.assertIn("You scored 7 out of 10 points", self.stored_formatted())
+
+        self.release()
         self.run_task(first)

         self.assertIsNone(self.stored_formatted())

     def test_it_is_cleared_by_the_same_update_as_the_score(self):
         """Not a second write after the grade's: one UPDATE, so no moment
-        has the new score and the old wording."""
-        with CaptureQueriesContext(connection) as queries:
+        has the new score and the old wording. H-279: that UPDATE is the
+        approval's; the held save writes neither."""
+        this_table = 'UPDATE "' + StudentSubmission._meta.db_table + '"'
+
+        with CaptureQueriesContext(connection) as held:
             self.grade_by_ai(self.submission, 3)
+        self.assertEqual(
+            [
+                query["sql"]
+                for query in held
+                if query["sql"].startswith(this_table)
+                # a column written, not a key inside the held result's
+                # JSON (the captured SQL carries its parameters)
+                and (
+                    '"formatted_grade" =' in query["sql"] or '"score" =' in query["sql"]
+                )
+            ],
+            [],
+        )
+
+        with CaptureQueriesContext(connection) as queries:
+            self.release()

-        this_table = 'UPDATE "' + StudentSubmission._meta.db_table + '"'
         clearing = [
             query["sql"]
             for query in queries
```

## students/tests_formatting_task_superseded.py

```diff
diff --git a/students/tests_formatting_task_superseded.py b/students/tests_formatting_task_superseded.py
index 85623d830..ff71927ac 100644
--- a/students/tests_formatting_task_superseded.py
+++ b/students/tests_formatting_task_superseded.py
@@ -39,7 +39,7 @@ from students.models import (
     BackgroundTaskType,
     StudentSubmission,
 )
-from students.services import grade_engine, grading_result_stamp
+from students.services import grade_engine, grading_result_stamp, held_result_stamp
 from students.task_tracking import create_processing_task
 from students.tests_formatter_input import grading_result as a_grading_result
 from students.tests_formatter_input import make_people_and_submission
@@ -137,19 +137,35 @@ class AnOlderTaskDoesNotWriteOverAManualGrade(SupersededBase):


 class ARegradeSupersedesTheFirstGradingsTask(SupersededBase):
+    """H-279 (owner, 2026-11-02): the paper is released, so the AI re-grade
+    is held for the teacher; it is the released result once approved (here:
+    the publish route)."""
+
     def test_the_first_gradings_task_writes_nothing_after_a_regrade(self):
         first = self.stamp_now()
         self.grade_by_ai(self.submission, 3)

+        # held: the first grading's result is still the released one, and
+        # its task still words it
+        self.run_task(first)
+        self.assertIn(WORDED_BY_THIS_RUN, self.stored_formatted())
+        self.assertIn("You scored 7 out of 10 points", self.stored_formatted())
+
+        self.release()
         self.run_task(first)

-        # the regrade cleared the text (H-146); the older task wrote nothing
+        # the approval cleared the text (H-146); the older task wrote nothing
         self.assertIsNone(self.stored_formatted())

     def test_the_regrades_own_task_writes(self):
         self.grade_by_ai(self.submission, 3)

-        self.run_task(self.stamp_now())
+        self.run_task(held_result_stamp(self.row()))
+
+        # held: its wording is kept with the held result, not released
+        self.assertNotIn("You scored 3 out of 10 points", self.stored_formatted())
+
+        self.release()

         self.assertIn("You scored 3 out of 10 points", self.stored_formatted())

@@ -229,8 +245,13 @@ class GradingPassesTheStamp(TransactionTestCase):

 class TheStamp(SupersededBase):
     def test_it_changes_with_a_regrade_and_with_a_manual_grade(self):
+        """H-279: the paper is released, so the re-grade is held, and the
+        stamp changes when the teacher approves it."""
         first = self.stamp_now()
         self.grade_by_ai(self.submission, 7)
+        # held: the released result, and its stamp, are as they were
+        self.assertEqual(self.stamp_now(), first)
+        self.release()
         second = self.stamp_now()
         self.override(7)
         third = self.stamp_now()
```

## students/tests_student_upload_answer.py

```diff
diff --git a/students/tests_student_upload_answer.py b/students/tests_student_upload_answer.py
index ec5a6ad32..d91186a28 100644
--- a/students/tests_student_upload_answer.py
+++ b/students/tests_student_upload_answer.py
@@ -38,11 +38,12 @@ from django.core.cache import cache
 from django.urls import reverse
 from django.utils import timezone
 from rest_framework import status
-from rest_framework.test import APITestCase
+from rest_framework.test import APIRequestFactory, APITestCase

 from ai_processor.extraction_flags import answers_with_flags
 from assignments.models import Assignment, AssignmentStatus
 from students.models import GradingState, StudentSubmission
+from students.serializers import StudentUploadAnswerSerializer
 from students.services import (
     GRADING_CLAIM_STALE_AFTER,
     _mark_grading_claim_failed,
@@ -94,6 +95,10 @@ KEYS = [
     # own facts, not a staff constant: a student is shown them.
     "upload_mismatch",
     "flagged_for_review",
+    # H-279 (SM ruling, 2026-11-03): a re-grade held for the teacher. On the
+    # teacher's page, the held result; on an upload answer, always null.
+    "pending_result",
+    "pending_graded_at",
 ]

 #: What a student is sent in place of every staff field, whatever the row
@@ -115,6 +120,9 @@ CONSTANTS = {
     "scheduled_grading_at": None,
     "grading_task_name": None,
     "is_grading_scheduled": False,
+    # H-279: whoever uploads, whatever the row holds.
+    "pending_result": None,
+    "pending_graded_at": None,
 }

 #: What may differ between two of the student's upload answers: the paper,
@@ -389,6 +397,43 @@ class OnlyTheAnswerProtectsTest(UploadAnswerBase):
         self.assertEqual(list(answer), KEYS)
         self.assert_constants(answer)

+    def held(self):
+        """The graded paper, released, then re-graded by the AI: the new
+        result is held for the teacher (H-279)."""
+        self.grade()
+        StudentSubmission.objects.filter(pk=self.submission.pk).update(
+            is_published=True
+        )
+        _populate_and_save_grade(self.row(self.submission), VALID_GRADE, None)
+        row = self.row(self.submission)
+        self.assertIsNotNone(row.pending_result)
+        self.assertIsNotNone(row.pending_graded_at)
+        return row
+
+    def test_a_student_upload_over_a_held_result_says_nothing_of_it(self):
+        """H-279 (SM ruling): the held result's keys are null on the upload
+        answer."""
+        answer = self.answer_for(self.held())
+
+        self.assertEqual(list(answer), KEYS)
+        self.assertIsNone(answer["pending_result"])
+        self.assertIsNone(answer["pending_graded_at"])
+
+    def test_built_for_a_teacher_over_a_held_result_it_says_nothing_of_it(self):
+        """H-279 (SM ruling): the same for a teacher's request. The upload
+        route is a student's only (a teacher is refused it), so this builds
+        the route's serializer with a teacher's request: it reads no column
+        of the held result, whoever asks."""
+        row = self.held()
+        request = APIRequestFactory().get("/")
+        request.user = self.teacher
+
+        data = StudentUploadAnswerSerializer(row, context={"request": request}).data
+
+        self.assertEqual(list(data), KEYS)
+        self.assertIsNone(data["pending_result"])
+        self.assertIsNone(data["pending_graded_at"])
+
     def test_a_graded_paper_shows_the_assignments_total_not_the_graders(self):
         self.assertEqual(self.answer_for(self.grade())["max_points"], 20)

```
