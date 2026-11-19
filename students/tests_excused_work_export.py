"""NFR-CMP-04 + Epic E2: a student's excused work is in their data export
(owner approved 2026-11-19).

excused-work.json (owner-approved name) lists each assignment a teacher
excused the student from: the assignment and its course as ids (the way
submissions.json and enrollments.json name them) and when it was excused.
The teacher who excused it is staff data and is left out. A teacher's
export holds only their own courses' rows. Erasure still deletes the rows.
"""

import json

from django.test import TestCase

from students import data_erase
from students.models import ExcusedWork
from students.student_data_map import STUDENT_DATA_MAP, Role

from .tests_data_erase import flag_on as erase_flag_on
from .tests_data_export import _ExportCase, flag_on, name

LABEL = "students.ExcusedWork"


class _ExcusedCase(_ExportCase):
    def setUp(self):
        super().setUp()
        w = self.w
        self.excused_a = ExcusedWork.objects.create(
            student=w.student, assignment=w.a2, excused_by=w.teacher
        )
        self.excused_b = ExcusedWork.objects.create(
            student=w.student, assignment=w.b1, excused_by=w.other_teacher
        )
        self.classmates = ExcusedWork.objects.create(
            student=w.classmate, assignment=w.a1, excused_by=w.teacher
        )

    def excused(self, caller=None, student=None):
        files = self.files(caller, student)
        return files, json.loads(files[name(LABEL)])


@flag_on
class ExcusedWorkExportTests(_ExcusedCase):
    def test_the_file_name_is_the_approved_one(self):
        from students import data_export

        self.assertEqual(data_export.EXPORT_NAMES[LABEL], "excused-work.json")

    def test_the_students_excused_work_is_listed(self):
        _, rows = self.excused()
        for row in rows:
            row["excused_at"] = row["excused_at"][:19]
        self.assertEqual(
            rows,
            [
                {
                    "id": str(row.pk),
                    "assignment": str(row.assignment_id),
                    "course": str(row.assignment.course_id),
                    "excused_at": row.excused_at.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                for row in (self.excused_a, self.excused_b)
            ],
        )

    def test_another_students_excusals_are_not_included(self):
        files, rows = self.excused()
        self.assertNotIn(str(self.classmates.pk), {row["id"] for row in rows})

    def test_a_teacher_gets_their_own_courses_only(self):
        _, rows = self.excused(self.w.teacher)
        self.assertEqual([row["id"] for row in rows], [str(self.excused_a.pk)])

    def test_the_teacher_who_excused_it_is_not_included(self):
        files, rows = self.excused()
        for row in rows:
            self.assertNotIn("excused_by", row)
        body = files[name(LABEL)].decode()
        for teacher in (self.w.teacher, self.w.other_teacher):
            with self.subTest(teacher=teacher.first_name):
                self.assertNotIn(str(teacher.pk), body)
                self.assertNotIn(teacher.email, body)

    def test_none_gives_an_empty_list_like_the_other_files(self):
        ExcusedWork.objects.filter(student=self.w.student).delete()
        files, rows = self.excused()
        self.assertEqual(rows, [])
        manifest = json.loads(files[name("manifest")])
        self.assertEqual(manifest[name("manifest_counts")][name(LABEL)], 0)

    def test_the_manifest_counts_it(self):
        files, _ = self.excused()
        manifest = json.loads(files[name("manifest")])
        self.assertEqual(manifest[name("manifest_counts")][name(LABEL)], 2)


class ExcusedWorkMapTests(TestCase):
    def test_the_map_exports_it_and_still_erases_it(self):
        entry = STUDENT_DATA_MAP[(LABEL, "student")]
        self.assertTrue(entry.export)
        self.assertTrue(entry.course_scoped)
        self.assertEqual(entry.role, Role.DELETE)


@erase_flag_on
class ExcusedWorkErasureTests(_ExcusedCase):
    def test_a_full_erasure_deletes_it(self):
        scope = data_erase.erase_scope(self.w.superadmin, self.w.student)
        data_erase.erase_student(self.w.student.pk, scope, by=self.w.superadmin)
        self.assertFalse(ExcusedWork.objects.filter(student=self.w.student).exists())
        self.assertTrue(ExcusedWork.objects.filter(pk=self.classmates.pk).exists())

    def test_a_teachers_erasure_deletes_their_courses_rows_only(self):
        scope = data_erase.erase_scope(self.w.teacher, self.w.student)
        data_erase.erase_student(self.w.student.pk, scope, by=self.w.teacher)
        self.assertEqual(
            list(
                ExcusedWork.objects.filter(student=self.w.student).values_list(
                    "pk", flat=True
                )
            ),
            [self.excused_b.pk],
        )
