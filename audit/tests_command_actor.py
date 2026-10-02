"""H-69: `command_actor` names the super admin who ran a management command.

Outside a request every audit event is SYSTEM, so a command's writes did not
say who ran it. Inside `command_actor(user, command=...)` the history signals,
`record_bulk` and `emit` record that super admin as the actor, and every event
carries `metadata["command"]`.

The actor and the name are both checked before the block runs: an active
super admin, and a management command that exists.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.context import (
    command_actor,
    current_command,
    current_command_actor,
    request_audit_state,
)
from audit.emitter import emit
from audit.enums import ActorRole, AuditAction
from audit.history import record_bulk
from audit.models import AuditEvent
from users.models import UserTypes

User = get_user_model()
PW = "Command-actor-pw-1"  # pragma: allowlist secret
COMMAND = "resolve_licence_stripe_intent"


def make_user(email, user_type=UserTypes.TEACHER, **extra):
    extra.setdefault("is_active", True)
    return User.objects.create_user(
        email=email,
        password=PW,
        first_name="Command",
        last_name="Actor",
        user_type=user_type,
        **extra,
    )


def make_super_admin(email="operator@command.test", **extra):
    extra.setdefault("is_superuser", True)
    extra.setdefault("is_staff", True)
    return make_user(email, UserTypes.SUPER_ADMIN, **extra)


class CommandActorTestCase(TestCase):
    def setUp(self):
        self.operator = make_super_admin()
        self.teacher = make_user("teacher@command.test")
        # The fixtures' own "create" events are not what a test looks at.
        self.before_ids = set(AuditEvent.objects.values_list("id", flat=True))

    def new_events(self, action=None):
        found = AuditEvent.objects.exclude(id__in=self.before_ids)
        return found.filter(action=action) if action else found

    def the_event(self, action):
        found = self.new_events(action)
        self.assertEqual(found.count(), 1)
        return found.get()

    def deactivate_by_save(self):
        self.teacher.is_active = False
        self.teacher.save(update_fields=["is_active"])

    def assertByTheOperator(self, event):
        self.assertEqual(event.actor_id, self.operator.id)
        self.assertEqual(event.actor_role, ActorRole.SUPER_ADMIN)
        self.assertEqual(event.metadata["command"], COMMAND)

    def assertBySystem(self, event):
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertNotIn("command", event.metadata)


class WritesInsideACommandTests(CommandActorTestCase):
    def test_a_save_is_recorded_as_the_operator(self):
        with command_actor(self.operator, command=COMMAND):
            self.deactivate_by_save()
        event = self.the_event(AuditAction.PERMISSION_CHANGE)
        self.assertByTheOperator(event)
        self.assertEqual(event.target_id, self.teacher.id)
        # `source` still says how the row was written.
        self.assertEqual(event.metadata["source"], "save")
        self.assertEqual(event.metadata["changed_fields"], ["is_active"])

    def test_record_bulk_is_recorded_as_the_operator(self):
        with command_actor(self.operator, command=COMMAND):
            count = record_bulk(
                User.objects.filter(pk=self.teacher.pk), is_active=False
            )
        self.assertEqual(count, 1)
        event = self.the_event(AuditAction.PERMISSION_CHANGE)
        self.assertByTheOperator(event)
        self.assertEqual(event.metadata["source"], "bulk")

    def test_an_explicit_emit_with_no_actor_is_recorded_as_the_operator(self):
        with command_actor(self.operator, command=COMMAND):
            stored = emit(
                AuditAction.ADMIN_ACTION,
                target_type="CustomUser",
                target_id=self.teacher.id,
                metadata={"source": "TestCommand"},
                strict=True,
            )
        self.assertIsNotNone(stored)
        event = self.the_event(AuditAction.ADMIN_ACTION)
        self.assertByTheOperator(event)
        self.assertEqual(event.metadata, {"source": "TestCommand", "command": COMMAND})

    def test_an_explicit_actor_is_kept_and_the_command_is_still_named(self):
        with command_actor(self.operator, command=COMMAND):
            emit(
                AuditAction.ADMIN_ACTION,
                actor=self.teacher,
                target_type="CustomUser",
                target_id=self.teacher.id,
                strict=True,
            )
        event = self.the_event(AuditAction.ADMIN_ACTION)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.metadata, {"command": COMMAND})

    def test_a_requests_signed_in_user_outranks_the_command_in_history(self):
        """`command_actor` is for code with no request. If both exist, the
        user who authenticated is the one recorded as acting."""
        with request_audit_state(SimpleNamespace(user=self.teacher)):
            with command_actor(self.operator, command=COMMAND):
                self.deactivate_by_save()
        event = self.the_event(AuditAction.PERMISSION_CHANGE)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.metadata["command"], COMMAND)


class OutsideACommandTests(CommandActorTestCase):
    def test_a_save_outside_is_still_system_with_no_command(self):
        self.deactivate_by_save()
        self.assertBySystem(self.the_event(AuditAction.PERMISSION_CHANGE))

    def test_an_emit_outside_is_still_system_with_no_command(self):
        emit(
            AuditAction.ADMIN_ACTION,
            target_type="CustomUser",
            target_id=self.teacher.id,
            strict=True,
        )
        self.assertBySystem(self.the_event(AuditAction.ADMIN_ACTION))

    def test_a_call_site_cannot_supply_the_command_key(self):
        emit(
            AuditAction.ADMIN_ACTION,
            target_type="CustomUser",
            target_id=self.teacher.id,
            metadata={"source": "TestCommand", "command": COMMAND},
        )
        event = self.the_event(AuditAction.ADMIN_ACTION)
        self.assertEqual(event.metadata, {"source": "TestCommand"})

    def test_the_context_ends_with_the_block(self):
        with command_actor(self.operator, command=COMMAND):
            self.assertEqual(current_command_actor(), self.operator)
            self.assertEqual(current_command(), COMMAND)
        self.assertIsNone(current_command_actor())
        self.assertIsNone(current_command())
        self.deactivate_by_save()
        self.assertBySystem(self.the_event(AuditAction.PERMISSION_CHANGE))

    def test_the_context_ends_when_the_block_raises(self):
        with self.assertRaises(RuntimeError):
            with command_actor(self.operator, command=COMMAND):
                raise RuntimeError("the command failed")
        self.assertIsNone(current_command_actor())
        self.assertIsNone(current_command())


class WhoAndWhatMayBeNamedTests(CommandActorTestCase):
    def assertRefused(self, user, command=COMMAND):
        entered = []
        with self.assertRaises(ValueError):
            with command_actor(user, command=command):
                entered.append(True)
        self.assertEqual(entered, [])
        self.assertIsNone(current_command_actor())

    def test_no_user_is_refused(self):
        self.assertRefused(None)

    def test_a_teacher_is_refused(self):
        self.assertRefused(self.teacher)

    def test_a_school_admin_is_refused(self):
        self.assertRefused(make_user("admin@command.test", UserTypes.SCHOOL_ADMIN))

    def test_an_inactive_super_admin_is_refused(self):
        self.assertRefused(make_super_admin("gone@command.test", is_active=False))

    def test_a_super_admin_type_without_the_superuser_flag_is_refused(self):
        self.assertRefused(make_super_admin("half@command.test", is_superuser=False))

    def test_a_superuser_flag_without_the_super_admin_type_is_refused(self):
        self.assertRefused(
            make_user("flag@command.test", UserTypes.TEACHER, is_superuser=True)
        )

    def test_an_unsaved_user_is_refused(self):
        unsaved = User(
            email="unsaved@command.test",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_active=True,
        )
        self.assertRefused(unsaved)

    def test_a_command_that_does_not_exist_is_refused(self):
        self.assertRefused(self.operator, "no_such_command")

    def test_free_text_is_refused_as_a_command(self):
        for text in (
            "",
            None,
            "Resolve Licence",
            "resolve_licence_stripe_intent; note",
            "someone@example.com",
            42,
        ):
            with self.subTest(command=text):
                self.assertRefused(self.operator, text)

    def test_a_real_command_is_accepted(self):
        with command_actor(self.operator, command="migrate"):
            self.assertEqual(current_command(), "migrate")
