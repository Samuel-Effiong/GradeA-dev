"""The vocabulary is part of the frozen schema: these pin it."""

from django.test import SimpleTestCase

from .enums import STUDENT_RECORD_ACTIONS, AuditAction

# FR-A-01, one verb per listed action. Success and failure are the outcome.
FR_A_01_VERBS = {
    "AUTH_LOGIN",
    "AUTH_LOGOUT",
    "GRADING_REQUESTED",
    "GRADING_COMPLETED",
    "GRADING_FAILED",
    "ASSIGNMENT_CREATE",
    "ASSIGNMENT_UPDATE",
    "ASSIGNMENT_DELETE",
    "ASSIGNMENT_COPY",
    "LESSON_CREATE",
    "LESSON_UPDATE",
    "LESSON_DELETE",
    "TAG_CREATE",
    "TAG_RENAME",
    "TAG_DELETE",
    "ROSTER_CHANGE",
    "SUBMISSION_UPLOAD",
    "CREDIT_TRANSACTION",
    "DEPARTMENT_CREATE",
    "DEPARTMENT_UPDATE",
    "DEPARTMENT_DELETE",
    "DEPARTMENT_MEMBER_ADD",
    "DEPARTMENT_MEMBER_REMOVE",
    "LIBRARY_ADD",
    "LIBRARY_EDIT",
    "LIBRARY_COPY",
    "ADMIN_ACTION",
    "DATA_EXPORT",
    "PERMISSION_CHANGE",
}


class ActionVocabularyTest(SimpleTestCase):
    def test_every_action_the_requirement_lists_has_a_verb(self):
        self.assertTrue(FR_A_01_VERBS <= set(AuditAction.values))

    def test_a_verb_is_stable_meaning_its_value_is_its_name(self):
        for name, value in zip(AuditAction.names, AuditAction.values, strict=True):
            with self.subTest(action=name):
                self.assertEqual(value, name)
                self.assertLessEqual(len(value), 64)

    def test_the_actions_kept_three_years_are_exactly_the_documented_ones(self):
        """Grading, roster changes, submissions and (Epic A S4, D6) grade
        changes touch a student's record."""
        self.assertEqual(
            STUDENT_RECORD_ACTIONS,
            {
                AuditAction.GRADING_REQUESTED,
                AuditAction.GRADING_COMPLETED,
                AuditAction.GRADING_FAILED,
                AuditAction.ROSTER_CHANGE,
                AuditAction.SUBMISSION_UPLOAD,
                AuditAction.GRADE_CHANGE,
            },
        )
