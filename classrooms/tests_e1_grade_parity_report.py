"""Epic E1a: the `grade_parity_report` command (plan 09 §4.2).

Run on a populated test database: the snapshot fixture plus seeded corpora.
It must report every figure as matching, must find a planted difference,
and must write nothing.
"""

import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from classrooms.models import StudentCourse
from classrooms.tests_e1_grade_parity import _DbCorpusMixin
from classrooms.tests_e1_snapshots import Fixture
from students.models import StudentSubmission


class GradeParityReportTest(_DbCorpusMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        Fixture().build()

    def setUp(self):
        for seed in range(8):
            self.build_db_corpus(seed)

    def run_report(self, *args):
        out = StringIO()
        code: str | int | None = 0
        try:
            call_command("grade_parity_report", *args, stdout=out)
        except SystemExit as exc:
            code = exc.code
        # E1_PARITY_SAMPLE_DIR: keep each report's text, for the evidence.
        sample_dir = os.environ.get("E1_PARITY_SAMPLE_DIR")
        if sample_dir:
            name = self._testMethodName.removeprefix("test_")
            with open(os.path.join(sample_dir, f"{name}.txt"), "w") as fh:
                fh.write(out.getvalue())
        return code, out.getvalue()

    def test_every_figure_matches_on_a_populated_database(self):
        code, out = self.run_report()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: every figure matches (0 differences)", out)
        for figure in ("R1", "R2", "R3", "R4", "RR", "RS", "PM", "FG"):
            line = next(row for row in out.splitlines() if row.startswith(figure))
            self.assertRegex(line, r": [1-9][0-9]* compared, 0 differences$")

    def test_a_planted_difference_is_reported_and_fails(self):
        from classrooms import grade_service

        real = grade_service.course_grade

        def off_by_one(student_id, course_id):
            value = real(student_id, course_id)
            return None if value is None else value + 1

        with mock.patch.object(grade_service, "course_grade", off_by_one):
            code, out = self.run_report()
        self.assertEqual(code, 1, out)
        self.assertIn("RESULT: DIFFERENCES FOUND", out)
        self.assertRegex(out, r"R1 .*: [1-9][0-9]* compared, [1-9][0-9]* differences")
        self.assertIn("first 10:", out)

    def test_writes_nothing(self):
        before = (
            list(
                StudentCourse.all_objects.order_by("id").values_list(
                    "id", "final_grade"
                )
            ),
            StudentSubmission.objects.count(),
        )
        self.run_report()
        after = (
            list(
                StudentCourse.all_objects.order_by("id").values_list(
                    "id", "final_grade"
                )
            ),
            StudentSubmission.objects.count(),
        )
        self.assertEqual(before, after)
