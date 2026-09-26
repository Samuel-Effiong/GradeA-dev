"""The AuditEvent table matches the frozen data model (03a_data_model.md 2.1).

The schema is frozen first because every later call site depends on it, so
these tests pin it: the exact columns and their nullability, no foreign keys,
the indexes the query API and the retention sweep need, and the append-only
and student-data rules the table enforces on its own.
"""

import uuid

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from billing.immutable import ImmutableRecordError
from classrooms.models import School
from users.models import CustomUser, UserTypes

from .enums import ActorRole, AuditOutcome, ErrorClass, RetentionClass
from .models import AuditEvent

TABLE = "audit_auditevent"

# column -> nullable, exactly as the data model lists them
EXPECTED_COLUMNS = {
    "id": False,
    "occurred_at": False,
    "actor_id": True,
    "actor_role": False,
    "actor_email": True,
    "school_id": True,
    "department_id": True,
    "action": False,
    "target_type": False,
    "target_id": True,
    "outcome": False,
    "error_class": True,
    "reason_code": True,
    "trace_id": False,
    "client_correlation_id": True,
    "source_ip": True,
    "user_agent": True,
    "retention_class": False,
    "before": True,
    "after": True,
    "metadata": False,
}


def event_fields(**over):
    """A valid row, written directly (bypassing the emitter) to test the table."""
    fields = {
        "actor_id": uuid.uuid4(),
        "actor_role": ActorRole.TEACHER,
        "actor_email": "teacher@example.edu",
        "school_id": uuid.uuid4(),
        "action": "ASSIGNMENT_COPY",
        "target_type": "Assignment",
        "target_id": uuid.uuid4(),
        "outcome": AuditOutcome.SUCCESS,
        "trace_id": uuid.uuid4(),
        "retention_class": RetentionClass.GENERAL,
    }
    fields.update(over)
    return fields


def make_event(**over):
    return AuditEvent.objects.create(**event_fields(**over))


class ColumnsTest(TestCase):
    def columns(self):
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, TABLE)
        return {c.name: c.null_ok for c in description}

    def test_the_table_has_exactly_the_documented_columns_and_nullability(self):
        self.assertEqual(self.columns(), EXPECTED_COLUMNS)

    def test_the_table_is_named_audit_auditevent(self):
        self.assertEqual(AuditEvent._meta.db_table, TABLE)

    def test_the_primary_key_is_a_server_generated_uuid(self):
        event = make_event()
        self.assertIsInstance(event.id, uuid.UUID)
        self.assertNotEqual(make_event().id, event.id)

    def test_metadata_defaults_to_an_empty_object_and_occurred_at_to_now(self):
        before = timezone.now()
        event = make_event()
        event.refresh_from_db()
        self.assertEqual(event.metadata, {})
        self.assertGreaterEqual(event.occurred_at, before)


class NoForeignKeysTest(TestCase):
    """Identity is stored as values, so nothing an admin deletes can reach the
    trail (an audit log a deletion can erase is not an audit log)."""

    def test_the_table_has_no_foreign_key_constraint(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, TABLE)
        foreign = [n for n, c in constraints.items() if c["foreign_key"]]
        self.assertEqual(foreign, [])

    def test_the_model_has_no_relation_fields(self):
        relations = [f.name for f in AuditEvent._meta.get_fields() if f.is_relation]
        self.assertEqual(relations, [])

    def test_deleting_the_actor_leaves_the_event_and_its_captured_identity(self):
        actor = CustomUser.objects.create_user(
            email="gone@example.edu",
            password="pw-12345678",  # nosec  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Gone",
            last_name="Teacher",
        )
        actor_id = actor.id  # Django clears the pk of a deleted instance
        event = make_event(actor_id=actor_id, actor_email=actor.email)
        actor.delete()
        self.assertFalse(CustomUser.objects.filter(pk=actor_id).exists())
        event.refresh_from_db()
        self.assertEqual(event.actor_id, actor_id)
        self.assertEqual(event.actor_email, "gone@example.edu")

    def test_deleting_the_licence_leaves_the_event(self):
        school = School.objects.create(name="Closing School")
        school_id = school.id  # Django clears the pk of a deleted instance
        event = make_event(school_id=school_id)
        school.delete()
        self.assertFalse(School.objects.filter(pk=school_id).exists())
        self.assertTrue(
            AuditEvent.objects.filter(pk=event.pk, school_id=school_id).exists()
        )


class IndexesTest(TestCase):
    def indexes(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s",
                [TABLE],
            )
            return dict(cursor.fetchall())

    def test_each_documented_index_exists_on_the_documented_columns(self):
        defs = self.indexes()
        expected = {
            "audit_school_time_ix": "(school_id, occurred_at DESC)",
            "audit_actor_time_ix": "(actor_id, occurred_at DESC)",
            "audit_action_time_ix": "(action, occurred_at DESC)",
            "audit_reason_time_ix": "(reason_code, occurred_at DESC)",
            "audit_retention_ix": "(retention_class, occurred_at)",
            "audit_trace_ix": "(trace_id)",
        }
        for name, columns in expected.items():
            with self.subTest(index=name):
                self.assertIn(name, defs)
                self.assertIn(columns, defs[name])

    def test_the_reason_code_and_department_indexes_skip_null_rows(self):
        defs = self.indexes()
        self.assertIn("WHERE (reason_code IS NOT NULL)", defs["audit_reason_time_ix"])
        self.assertIn("WHERE (department_id IS NOT NULL)", defs["audit_dept_time_ix"])


class AppendOnlyTest(TestCase):
    def test_a_saved_event_cannot_be_edited(self):
        event = make_event()
        event.outcome = AuditOutcome.FAILURE
        with self.assertRaises(ImmutableRecordError):
            event.save()

    def test_a_queryset_update_of_a_recorded_field_is_refused(self):
        event = make_event()
        with self.assertRaises(ImmutableRecordError):
            AuditEvent.objects.filter(pk=event.pk).update(action="ADMIN_ACTION")
        event.refresh_from_db()
        self.assertEqual(event.action, "ASSIGNMENT_COPY")

    def test_an_event_cannot_be_deleted_one_at_a_time_or_in_bulk(self):
        event = make_event()
        with self.assertRaises(ImmutableRecordError):
            event.delete()
        with self.assertRaises(ImmutableRecordError):
            AuditEvent.objects.filter(pk=event.pk).delete()
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())

    def test_the_retention_sweep_may_blank_the_address_and_browser_only(self):
        """X-4: source_ip and user_agent are cleared on a short clock while the
        event itself keeps its full retention class."""
        event = make_event(source_ip="203.0.113.7", user_agent="Mozilla/5.0")
        AuditEvent.objects.filter(pk=event.pk).update(source_ip=None, user_agent=None)
        event.refresh_from_db()
        self.assertIsNone(event.source_ip)
        self.assertIsNone(event.user_agent)
        self.assertEqual(event.action, "ASSIGNMENT_COPY")

    def test_only_those_two_columns_are_writable_after_creation(self):
        self.assertEqual(
            AuditEvent.mutable_fields, frozenset({"source_ip", "user_agent"})
        )


class StudentDataMinimisationTest(TestCase):
    """The database itself refuses a student's email, address or browser."""

    def assert_refused(self, **over):
        fields = {"actor_role": ActorRole.STUDENT, "actor_email": None, **over}
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_event(**fields)

    def test_a_student_event_with_an_email_is_refused(self):
        self.assert_refused(actor_email="kid@example.com")

    def test_a_student_event_with_an_address_is_refused(self):
        self.assert_refused(source_ip="203.0.113.7")

    def test_a_student_event_with_a_browser_string_is_refused(self):
        self.assert_refused(user_agent="Mozilla/5.0")

    def test_a_student_event_without_any_of_them_is_accepted(self):
        event = make_event(actor_role=ActorRole.STUDENT, actor_email=None)
        self.assertIsNone(event.actor_email)

    def test_a_teacher_event_may_carry_all_three(self):
        event = make_event(source_ip="203.0.113.7", user_agent="Mozilla/5.0")
        self.assertEqual(event.source_ip, "203.0.113.7")


class ClosedVocabulariesTest(TestCase):
    def test_the_role_outcome_class_and_retention_values_match_the_data_model(self):
        self.assertEqual(
            set(ActorRole.values),
            {"STUDENT", "TEACHER", "SCHOOL_ADMIN", "SUPER_ADMIN", "SYSTEM"},
        )
        self.assertEqual(set(AuditOutcome.values), {"SUCCESS", "FAILURE", "DENIED"})
        self.assertEqual(
            set(ErrorClass.values),
            {"USER", "VALIDATION", "PROVIDER", "MODEL", "SYSTEM"},
        )
        self.assertEqual(set(RetentionClass.values), {"GENERAL", "STUDENT_RECORD"})

    def test_every_role_a_user_can_have_maps_to_an_actor_role(self):
        self.assertTrue(set(UserTypes.values) <= set(ActorRole.values))
