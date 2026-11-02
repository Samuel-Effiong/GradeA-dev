"""H-278: one session can be marked THE active session (owner-approved rules).

a. Session.is_active, default False; existing rows stay inactive.
b. At most one active session per school (SCHOOL) and per teacher
   (INDIVIDUAL), enforced by the database.
c. Set on create and on PATCH by exactly whoever may already edit the
   session (CanManageSession); a licensed teacher stays read-only.
d. Marking one active clears the scope's previous active session in the
   same transaction, under a row lock; concurrent switches end with exactly
   one active and no 500.
e. Clearing leaves none active; deleting the active session leaves none.
f. Every session shows is_active; the list filters on ?is_active=; a
   licensed teacher sees the school's active session.
g. Every change of active session is one SESSION_ACTIVE_CHANGE event,
   which replaces the generic STATE_CHANGE row for that request.
"""

import threading
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.enums import AuditAction
from audit.models import AuditEvent
from AutoGrader.testing.concurrency import run_concurrently
from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School, Session, SessionOwnerType
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
#: A string, not the enum member, so this module imports before the fix
#: (each test then fails on its own) and the stored value is pinned.
CHANGE = "SESSION_ACTIVE_CHANGE"


def make_user(email, user_type, school=None, **fields):
    user = CustomUser.objects.create_user(
        email=email, password="password123"  # pragma: allowlist secret
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


def school_session(school, name, **fields):
    return Session.objects.create(
        name=name,
        owner_type=SessionOwnerType.SCHOOL,
        school=school,
        teacher=None,
        **fields,
    )


def own_session(teacher, name, **fields):
    return Session.objects.create(
        name=name, owner_type=SessionOwnerType.INDIVIDUAL, teacher=teacher, **fields
    )


class World:
    """Two schools, their admins, a licensed teacher of school A, an
    individual teacher and a super admin."""

    def build_world(self):
        cache.clear()
        self.school_a = School.objects.create(name="H278 School A")
        self.school_b = School.objects.create(name="H278 School B")
        self.admin_a = make_user(
            "admin.a@h278.test", UserTypes.SCHOOL_ADMIN, self.school_a
        )
        self.admin_b = make_user(
            "admin.b@h278.test", UserTypes.SCHOOL_ADMIN, self.school_b
        )
        self.licensed = make_user(
            "licensed@h278.test", UserTypes.TEACHER, self.school_a
        )
        self.solo = make_user("solo@h278.test", UserTypes.TEACHER)
        self.root = make_user(
            "root@h278.test", UserTypes.SUPER_ADMIN, is_superuser=True, is_staff=True
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="H278 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.STANDARD,
            monthly_credits=100_000,
            is_active=True,
        )
        licence = LicenseSubscription.objects.create(
            school=self.school_a,
            admin_user=self.admin_a,
            plan=plan,
            contract_months=12,
            max_seats=10,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=365),
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.licensed,
            monthly_allocation=25_000,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )
        assert self.licensed.is_under_license()
        self.fall = school_session(self.school_a, "Fall")
        self.spring = school_session(self.school_a, "Spring")
        self.other = school_session(self.school_b, "Other school term")
        self.mine = own_session(self.solo, "My fall")
        self.mine2 = own_session(self.solo, "My spring")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def patch(self, user, session, **data):
        return self.as_(user).patch(
            reverse("session-detail", kwargs={"pk": str(session.pk)}),
            data,
            format="json",
        )

    def active(self, **scope):
        return set(
            Session.objects.filter(is_active=True, **scope).values_list("pk", flat=True)
        )


def _new_events(before_ids):
    return list(AuditEvent.objects.exclude(pk__in=before_ids).order_by("occurred_at"))


# ---------------------------------------------------------------------------
# a, b: the field and the database rule
# ---------------------------------------------------------------------------


class TheFieldAndTheDatabaseRule(World, TestCase):
    def setUp(self):
        self.build_world()

    def test_a_session_is_inactive_by_default(self):
        self.assertIs(Session.objects.get(pk=self.fall.pk).is_active, False)
        self.assertEqual(self.active(), set())

    def test_two_active_sessions_in_one_school_are_refused(self):
        school_session(self.school_a, "Summer", is_active=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            school_session(self.school_a, "Winter", is_active=True)

    def test_two_active_sessions_of_one_teacher_are_refused(self):
        own_session(self.solo, "Mine A", is_active=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            own_session(self.solo, "Mine B", is_active=True)

    def test_each_scope_has_its_own_active_session(self):
        school_session(self.school_a, "A active", is_active=True)
        school_session(self.school_b, "B active", is_active=True)
        own_session(self.solo, "Solo active", is_active=True)
        own_session(self.licensed, "Licensed own active", is_active=True)
        self.assertEqual(len(self.active()), 4)

    def test_many_inactive_sessions_are_fine(self):
        for i in range(3):
            school_session(self.school_a, f"Old {i}", is_active=False)
            own_session(self.solo, f"Old mine {i}", is_active=False)


# ---------------------------------------------------------------------------
# c, d, e: who may set it, and the switch
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class TheSchoolAdminMarksTheActiveSession(World, TestCase):
    def setUp(self):
        self.build_world()

    def test_create_as_active(self):
        response = self.as_(self.admin_a).post(
            reverse("session-list"),
            {"name": "Summer", "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIs(response.data["is_active"], True)
        self.assertEqual(
            self.active(school=self.school_a), {Session.objects.get(name="Summer").pk}
        )

    def test_creating_an_active_session_clears_the_previous_one(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        response = self.as_(self.admin_a).post(
            reverse("session-list"),
            {"name": "Summer", "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            self.active(school=self.school_a), {Session.objects.get(name="Summer").pk}
        )

    def test_a_patch_switches_the_active_session(self):
        self.assertEqual(
            self.patch(self.admin_a, self.fall, is_active=True).status_code, 200
        )
        response = self.patch(self.admin_a, self.spring, is_active=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIs(response.data["is_active"], True)
        self.assertEqual(self.active(school=self.school_a), {self.spring.pk})

    def test_clearing_leaves_no_active_session(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        response = self.patch(self.admin_a, self.fall, is_active=False)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.active(school=self.school_a), set())

    def test_deleting_the_active_session_leaves_none(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        response = self.as_(self.admin_a).delete(
            reverse("session-detail", kwargs={"pk": str(self.fall.pk)})
        )
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(self.active(school=self.school_a), set())

    def test_another_schools_active_session_is_untouched(self):
        school_session(self.school_b, "B active", is_active=True)
        self.patch(self.admin_a, self.fall, is_active=True)
        self.assertEqual(len(self.active(school=self.school_b)), 1)

    def test_an_admin_cannot_mark_another_schools_session(self):
        response = self.patch(self.admin_a, self.other, is_active=True)
        self.assertIn(response.status_code, (403, 404), response.content)
        self.assertEqual(self.active(), set())

    def test_a_super_admin_may_mark_a_school_session(self):
        response = self.patch(self.root, self.fall, is_active=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.active(school=self.school_a), {self.fall.pk})

    def test_a_name_edit_leaves_the_flag_alone(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        response = self.patch(self.admin_a, self.fall, name="Fall term")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.active(school=self.school_a), {self.fall.pk})


@override_settings(CACHES=LOCMEM)
class TheIndividualTeacherMarksTheirOwn(World, TestCase):
    def setUp(self):
        self.build_world()

    def test_switch_between_their_own_sessions(self):
        self.assertEqual(
            self.patch(self.solo, self.mine, is_active=True).status_code, 200
        )
        self.assertEqual(
            self.patch(self.solo, self.mine2, is_active=True).status_code, 200
        )
        self.assertEqual(self.active(teacher=self.solo), {self.mine2.pk})

    def test_create_as_active_clears_their_previous_one(self):
        self.patch(self.solo, self.mine, is_active=True)
        response = self.as_(self.solo).post(
            reverse("session-list"),
            {"name": "My summer", "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            self.active(teacher=self.solo), {Session.objects.get(name="My summer").pk}
        )

    def test_their_switch_does_not_touch_a_school(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        self.patch(self.solo, self.mine, is_active=True)
        self.assertEqual(self.active(school=self.school_a), {self.fall.pk})


@override_settings(CACHES=LOCMEM)
class TheLicensedTeacherReadsIt(World, TestCase):
    def setUp(self):
        self.build_world()

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data
        rows = data.get("results", data) if isinstance(data, dict) else data
        return {str(row["id"]): row["is_active"] for row in rows}

    def test_a_licensed_teacher_cannot_set_it(self):
        response = self.patch(self.licensed, self.fall, is_active=True)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(self.active(), set())

    def test_a_licensed_teacher_cannot_create_an_active_session(self):
        response = self.as_(self.licensed).post(
            reverse("session-list"),
            {"name": "Sneaky", "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(self.active(), set())

    def test_every_session_shows_the_flag_and_the_switch_is_seen(self):
        client = self.as_(self.licensed)
        before = self.ids(client.get(reverse("session-list")))
        self.assertEqual(before, {str(self.fall.pk): False, str(self.spring.pk): False})

        self.patch(self.admin_a, self.spring, is_active=True)

        after = self.ids(client.get(reverse("session-list")))
        self.assertEqual(after, {str(self.fall.pk): False, str(self.spring.pk): True})

    def test_the_list_filters_on_is_active(self):
        self.patch(self.admin_a, self.spring, is_active=True)
        client = self.as_(self.licensed)
        self.assertEqual(
            self.ids(client.get(reverse("session-list"), {"is_active": "true"})),
            {str(self.spring.pk): True},
        )
        self.assertEqual(
            self.ids(client.get(reverse("session-list"), {"is_active": "false"})),
            {str(self.fall.pk): False},
        )

    def test_retrieve_shows_the_flag(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        response = self.as_(self.licensed).get(
            reverse("session-detail", kwargs={"pk": str(self.fall.pk)})
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIs(response.data["is_active"], True)


# ---------------------------------------------------------------------------
# g: the audit event
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class EveryChangeIsOneAuditEvent(World, TestCase):
    def setUp(self):
        self.build_world()

    def only_event(self, before):
        events = _new_events(before)
        self.assertEqual(len(events), 1, [(e.action, e.metadata) for e in events])
        return events[0]

    def snapshot(self):
        return list(AuditEvent.objects.values_list("pk", flat=True))

    def assertChange(self, event, target, is_active, previous):
        self.assertEqual(event.action, CHANGE)
        self.assertEqual(event.target_type, "Session")
        self.assertEqual(event.target_id, target.pk)
        self.assertEqual(
            event.metadata,
            {
                "changed_fields": ["is_active"],
                "is_active": is_active,
                "previous_active_session_id": str(previous.pk) if previous else None,
                "source": "api",
            },
        )

    def test_create_active(self):
        before = self.snapshot()
        response = self.as_(self.admin_a).post(
            reverse("session-list"),
            {"name": "Summer", "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        event = self.only_event(before)
        self.assertChange(event, Session.objects.get(name="Summer"), True, None)
        self.assertEqual(event.school_id, self.school_a.pk)
        self.assertEqual(event.actor_id, self.admin_a.pk)

    def test_a_switch_records_the_previous_session(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        before = self.snapshot()
        self.patch(self.admin_a, self.spring, is_active=True)
        event = self.only_event(before)
        self.assertChange(event, self.spring, True, self.fall)
        self.assertEqual(event.school_id, self.school_a.pk)

    def test_a_create_that_switches_records_the_previous_session(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        before = self.snapshot()
        self.as_(self.admin_a).post(
            reverse("session-list"),
            {"name": "Summer", "is_active": True},
            format="json",
        )
        self.assertChange(
            self.only_event(before), Session.objects.get(name="Summer"), True, self.fall
        )

    def test_a_clear(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        before = self.snapshot()
        self.patch(self.admin_a, self.fall, is_active=False)
        self.assertChange(self.only_event(before), self.fall, False, self.fall)

    def test_an_individual_switch(self):
        self.patch(self.solo, self.mine, is_active=True)
        before = self.snapshot()
        self.patch(self.solo, self.mine2, is_active=True)
        event = self.only_event(before)
        self.assertChange(event, self.mine2, True, self.mine)
        self.assertEqual(event.actor_id, self.solo.pk)

    def test_no_ops_leave_only_the_generic_row(self):
        self.patch(self.admin_a, self.fall, is_active=True)
        cases = [
            lambda: self.patch(self.admin_a, self.fall, is_active=True),
            lambda: self.patch(self.admin_a, self.spring, is_active=False),
            lambda: self.patch(self.admin_a, self.fall, name="Fall term"),
            lambda: self.as_(self.admin_a).post(
                reverse("session-list"),
                {"name": "Draft", "is_active": False},
                format="json",
            ),
        ]
        for index, request in enumerate(cases):
            with self.subTest(case=index):
                before = self.snapshot()
                response = request()
                self.assertIn(response.status_code, (200, 201), response.content)
                self.assertEqual(
                    self.only_event(before).action, AuditAction.STATE_CHANGE
                )
        self.assertEqual(self.active(school=self.school_a), {self.fall.pk})


# ---------------------------------------------------------------------------
# d: concurrent switches (H-274 Gate 3 pattern: 20 threads x 10 rounds)
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class ConcurrentSwitchesEndWithExactlyOneActive(World, TransactionTestCase):
    THREADS = 20
    ROUNDS = 10

    def setUp(self):
        self.build_world()
        self.term = [
            school_session(self.school_a, f"Term {i:02d}") for i in range(self.THREADS)
        ]
        self.term_ids = {session.pk for session in self.term}

    def race(self, user, sessions):
        lock = threading.Lock()
        statuses = []

        def switch(i):
            response = self.patch(user, sessions[i], is_active=True)
            with lock:
                statuses.append(response.status_code)
            return response.status_code

        _, errors = run_concurrently(switch, len(sessions), test=self)
        self.assertEqual(errors, [])
        return statuses

    def test_school_switches(self):
        previous = None
        for round_number in range(self.ROUNDS):
            with self.subTest(round=round_number):
                before = list(AuditEvent.objects.values_list("pk", flat=True))
                statuses = self.race(self.admin_a, self.term)

                self.assertEqual(statuses, [200] * self.THREADS)
                active = self.active(school=self.school_a)
                self.assertEqual(len(active), 1)
                changes = [e for e in _new_events(before) if e.action == CHANGE]
                # Every request changed the flag (each marked a session that
                # was inactive when it ran), so each is one event...
                targets = {e.target_id for e in changes}
                previous_ids = [
                    e.metadata["previous_active_session_id"] for e in changes
                ]
                self.assertEqual(len(changes), self.THREADS)
                self.assertEqual(targets, self.term_ids)
                # ...and they ran one after another: each saw a different
                # previous session, the chain starts at the last round's
                # winner and ends at this round's.
                self.assertEqual(len(set(previous_ids)), self.THREADS)
                self.assertEqual(
                    set(previous_ids) | {str(next(iter(active)))},
                    {str(pk) for pk in targets}
                    | {str(previous.pk) if previous else None},
                )
                previous = Session.objects.get(pk=next(iter(active)))

    def test_individual_switches(self):
        mine = [own_session(self.solo, f"Mine {i:02d}") for i in range(self.THREADS)]
        for round_number in range(self.ROUNDS):
            with self.subTest(round=round_number):
                statuses = self.race(self.solo, mine)
                self.assertEqual(statuses, [200] * self.THREADS)
                self.assertEqual(len(self.active(teacher=self.solo)), 1)
