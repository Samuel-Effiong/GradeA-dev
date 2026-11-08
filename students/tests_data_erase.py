"""NFR-CMP-04 step 3: erasing a student's data (SM GO after gate 21).

`POST students/<id>/erase`. The rulings pinned here are in
docs/evidence/nfr-cmp-04-student-data/NOTES.md, "Step 3":

* A full erasure (a real super admin, or a school admin of the student's
  own school) refuses first: an account that holds anything only staff
  hold (NOT_A_STUDENT), a wallet that is not empty (credits, a deficit or
  any credit history), or billing records (owner R2). Otherwise it deletes
  every DELETE entry of the map for this student only, writes the
  erased-student record and deletes the account. The activity log and
  credit records are kept as they are (D1, D4).
* A course erasure (a teacher, D3) removes the student from the courses the
  teacher owns: enrollments, submissions, chats and processing records
  there (E-Q2). The account and everything else stay (the risk alert too,
  E-Q3).
* Every other caller, target or id, anonymous and a bad token get the
  unknown-URL bytes. Off by default.
"""

import importlib
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.apps import apps
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from ai_processor.models import AICallRecord, ChatMessage, ChatSession
from audit.emitter import emit
from audit.enums import AuditAction
from audit.metadata import ALLOWED_KEYS, METADATA_ALLOWLIST
from audit.models import AuditEvent
from audit.tests_route_coverage import CONCEALED_ROUTES
from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PaymentDispute,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import EnrollmentStatusType, School, StudentCourse
from dashboard.models import StudentRiskAlertState
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    StudentSubmission,
)
from students.student_data_map import STUDENT_DATA_MAP, Match, Role, entries_with_role
from tags._c1_test_fixtures import World, client_for
from tags.models import OwnerType, Tag, TagType
from users.models import (
    BetaWhitelist,
    PasswordChangeOTP,
    PasswordResetOTP,
    RevokedSession,
    UserActivity,
    UserGoogleCredentials,
    UserTypes,
    Waitlist,
)

# Not built yet when the expectations are committed (red first).
data_erase = importlib.import_module("students.data_erase")

API = "/api/v1/"
flag_on = override_settings(STUDENT_DATA_TOOLS_ENABLED=True)
flag_off = override_settings(STUDENT_DATA_TOOLS_ENABLED=False)
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nerase-me"


def url(student_id):
    return f"{API}students/{student_id}/erase"


def unknown_url_bytes():
    return client_for(None).get(f"{API}no-such-route-anywhere").content


def _rows_by_owner(label, field_name, entry, user):
    model = apps.get_model(label)
    if entry.match == Match.EMAIL:
        return set(
            model.objects.filter(**{f"{field_name}__iexact": user.email}).values_list(
                "pk", flat=True
            )
        )
    if entry.match == Match.OWN_ROW:
        return set()
    return set(
        model.objects.filter(**{field_name: user.pk}).values_list("pk", flat=True)
    )


def delete_rows(user):
    """{(label, field): pks} of every DELETE entry pointing at `user`."""
    return {
        key: _rows_by_owner(key[0], key[1], entry, user)
        for key, entry in STUDENT_DATA_MAP.items()
        if entry.role is Role.DELETE
    }


def students_rows(*users):
    """What an erasure may touch, for these students: their rows in every
    DELETE and FINANCIAL_ACCOUNT entry, their accounts, and the
    erased-student records. (Not global table counts: a refused request
    still writes the house's own audit event and the caller's activity.)"""
    rows: dict = {}
    for user in users:
        for key, entry in STUDENT_DATA_MAP.items():
            # The activity middleware adds a row for whoever makes a request,
            # the student included; a refused request can't avoid that.
            # (Its deletion is checked by the full-erasure test.)
            if key == ("users.UserActivity", "user"):
                continue
            if entry.role in (Role.DELETE, Role.FINANCIAL_ACCOUNT):
                rows[(user.pk, key)] = _rows_by_owner(key[0], key[1], entry, user)
        rows[(user.pk, "account")] = (
            get_user_model().objects.filter(pk=user.pk).exists()
        )
    rows["records"] = apps.get_model("students.ErasedStudentRecord").objects.count()
    return rows


def _row(obj):
    """Every column of a row (Django's model_to_dict leaves out the id)."""
    return {f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields}


class _EraseWorld(World):
    def student_with_everything(self, first, school, email=None):
        user = self.user(UserTypes.STUDENT, first, school=school)
        if email:
            get_user_model().objects.filter(pk=user.pk).update(email=email)
            user.refresh_from_db()
        now = timezone.now()
        for course in (self.course_a, self.course_b):
            StudentCourse.objects.create(
                student=user,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
        user.sub_a = StudentSubmission.objects.create(
            assignment=self.a1,
            student=user,
            answers={"1": "A"},
            score=Decimal("8"),
            max_points=10,
            graded_at=now,
            is_published=True,
        )
        user.sub_b = StudentSubmission.objects.create(
            assignment=self.b1,
            student=user,
            answers={"1": "B"},
            score=Decimal("6"),
            max_points=10,
            graded_at=now,
            is_published=True,
        )
        BackgroundProcessingTask.objects.create(
            requested_by=user,
            task_type="UPLOAD",
            assignment=self.a1,
            submission=user.sub_a,
        )
        # E-Q2: a processing record with no submission, in course A.
        user.task_a_alone = BackgroundProcessingTask.objects.create(
            requested_by=user, task_type="UPLOAD", assignment=self.a1
        )
        user.task_b_alone = BackgroundProcessingTask.objects.create(
            requested_by=user, task_type="UPLOAD", assignment=self.b1
        )
        for course in (self.course_a, self.course_b):
            chat = ChatSession.objects.create(user=user, course=course)
            ChatMessage.objects.create(session=chat, role="user", content="hi")
        UserActivity.objects.create(user=user)
        StudentRiskAlertState.objects.create(
            student=user, school=school, is_at_risk=True
        )
        Token.objects.create(user=user)
        PasswordResetOTP.objects.create(user=user, code=f"{self.uid():06d}")
        PasswordChangeOTP.objects.create(user=user, code=f"{self.uid():06d}")
        UserGoogleCredentials.objects.create(
            user=user, access_token="a", token_expiry=now
        )
        OutstandingToken.objects.create(
            user=user, jti=f"jti-{user.pk}", token="t", expires_at=now
        )
        RevokedSession.objects.create(user=user, sid=uuid4())
        BetaWhitelist.objects.create(email=user.email.upper())  # R7: any case
        Waitlist.objects.create(email=user.email)
        return user

    def build(self):
        super().build()
        self.other_school = School.objects.create(name=f"Other {self.uid()}")
        self.other_admin = self.user(
            UserTypes.SCHOOL_ADMIN, "Ola", school=self.other_school
        )
        self.schoolless_admin = self.user(UserTypes.SCHOOL_ADMIN, "Nia")
        self.target = self.student_with_everything("Tara", self.school)
        self.classmate = self.student_with_everything("Kemi", self.school)
        BetaWhitelist.objects.create(email="someone-else@c1.test")
        # Kept records naming the target (D1, D4): never touched.
        emit(
            AuditAction.ROSTER_CHANGE,
            actor=self.target,
            target_type="CustomUser",
            target_id=self.target.pk,
            strict=True,
        )
        teacher_wallet = CreditWallet.objects.get(user=self.teacher)
        bucket = CreditBucket.objects.create(
            wallet=teacher_wallet, bucket_type="MANUAL_GRANT", total_credits=50
        )
        CreditLedger.objects.create(
            user_id=self.target.pk,
            user_email=self.target.email,
            ledger_type="CONSUME",
            amount=-1,
            bucket=bucket,
        )
        CreditUsageLog.objects.create(
            user_id=self.target.pk,
            user_email=self.target.email,
            wallet=teacher_wallet,
            bucket=bucket,
            amount=1,
        )
        AICallRecord.objects.create(
            task_type="grade_assignment",
            feature="Grading Assignment",
            outcome="charged",
            model_asked="m",
            prompt_version="v",
            charged_credits=1,
            billed_user_id=self.target.pk,
        )
        return self


def kept_rows(target):
    """Every KEEP_RECORD row naming `target`, every column."""
    return {
        "audit": sorted(
            (_row(e) for e in AuditEvent.objects.filter(actor_id=target.pk)),
            key=lambda d: str(d["id"]),
        ),
        "ledger": [_row(r) for r in CreditLedger.objects.filter(user_id=target.pk)],
        "usage": [_row(r) for r in CreditUsageLog.objects.filter(user_id=target.pk)],
        "ai": [_row(r) for r in AICallRecord.objects.filter(billed_user_id=target.pk)],
    }


class _EraseCase(TestCase):
    def setUp(self):
        self.storage = InMemoryStorage()
        field = get_user_model()._meta.get_field("profile_image")
        patcher = mock.patch.object(field, "storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.w = _EraseWorld().build()
        self.w.target.profile_image.save("t.png", ContentFile(IMAGE_BYTES), save=True)
        self.image_name = self.w.target.profile_image.name

    def erase(self, caller, student=None, method="post"):
        target = student or self.w.target
        client = client_for(caller)
        with self.captureOnCommitCallbacks(execute=True):
            return getattr(client, method)(url(target.pk))

    def assertNotFound(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, unknown_url_bytes())

    def snapshot(self):
        return students_rows(self.w.target, self.w.classmate)

    def assertNothingChanged(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(get_user_model().objects.filter(pk=self.w.target.pk).exists())
        self.assertFalse(
            AuditEvent.objects.filter(action=data_erase.ERASE_AUDIT_ACTION).exists()
        )

    def assertRefused(self, code):
        before = self.snapshot()
        response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 409, response.content[:400])
        self.assertEqual(response.json()["error"]["field_errors"]["reason_code"], code)
        self.assertNothingChanged(before)


@flag_on
class FullErasureTests(_EraseCase):
    def test_every_delete_table_loses_only_the_targets_rows(self):
        mine = delete_rows(self.w.target)
        theirs = delete_rows(self.w.classmate)
        self.assertTrue(any(mine.values()))
        response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 200, response.content[:400])
        for key, entry in STUDENT_DATA_MAP.items():
            if entry.role is not Role.DELETE or entry.match == Match.OWN_ROW:
                continue
            with self.subTest(key=key):
                model = apps.get_model(key[0])
                self.assertFalse(model.objects.filter(pk__in=mine[key]).exists())
                self.assertEqual(
                    set(
                        model.objects.filter(pk__in=theirs[key]).values_list(
                            "pk", flat=True
                        )
                    ),
                    theirs[key],
                )
        self.assertTrue(
            BetaWhitelist.objects.filter(email="someone-else@c1.test").exists()
        )

    def test_the_account_is_gone_and_the_record_is_written(self):
        target = self.w.target
        response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertFalse(get_user_model().objects.filter(pk=target.pk).exists())
        record = apps.get_model("students.ErasedStudentRecord").objects.get(
            original_user_id=target.pk
        )
        self.assertEqual(
            (record.first_name, record.last_name, record.email),
            (target.first_name, target.last_name, target.email),
        )
        self.assertEqual(record.erased_by_id, self.w.superadmin.pk)
        self.assertLess(abs(record.erased_at - timezone.now()), timedelta(minutes=1))
        self.assertEqual(record.keep_until.year - record.erased_at.year, 7)

    def test_kept_records_are_unchanged(self):  # D1, D4
        before = kept_rows(self.w.target)
        self.assertTrue(all(before.values()))
        self.erase(self.w.superadmin)
        after = kept_rows(self.w.target)
        for kind in before:
            with self.subTest(kind=kind):
                ids = {str(row["id"]) for row in before[kind]}
                self.assertEqual(
                    [r for r in after[kind] if str(r["id"]) in ids], before[kind]
                )

    def test_outstanding_tokens_are_deleted_not_unlinked(self):
        jti = f"jti-{self.w.target.pk}"
        self.erase(self.w.superadmin)
        self.assertFalse(OutstandingToken.objects.filter(jti=jti).exists())

    def test_whitelist_and_waitlist_go_by_email_ignoring_case(self):  # R7
        email = self.w.target.email
        self.erase(self.w.superadmin)
        self.assertFalse(BetaWhitelist.objects.filter(email__iexact=email).exists())
        self.assertFalse(Waitlist.objects.filter(email__iexact=email).exists())
        self.assertTrue(
            BetaWhitelist.objects.filter(email__iexact=self.w.classmate.email).exists()
        )
        self.assertTrue(
            BetaWhitelist.objects.filter(email="someone-else@c1.test").exists()
        )

    def test_the_empty_wallet_is_deleted(self):
        wallet_id = CreditWallet.objects.get(user=self.w.target).pk
        self.erase(self.w.superadmin)
        self.assertFalse(CreditWallet.objects.filter(pk=wallet_id).exists())

    def test_one_event_with_scope_full_naming_no_one(self):
        self.erase(self.w.superadmin)
        event = AuditEvent.objects.get(action=data_erase.ERASE_AUDIT_ACTION)
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, self.w.target.pk)
        self.assertEqual(event.metadata["scope"], "full")
        self.assertGreater(event.metadata["item_count"], 0)

    def test_no_event_written_during_the_erasure_names_the_student(self):
        target = self.w.target
        start = timezone.now()
        self.erase(self.w.superadmin)
        names = (target.email, target.first_name, target.last_name)
        for event in AuditEvent.objects.filter(occurred_at__gte=start):
            text = str(_row(event))
            for value in names:
                with self.subTest(action=event.action, value=value):
                    self.assertNotIn(value, text)

    def test_the_erased_account_cannot_sign_in(self):
        access = str(RefreshToken.for_user(self.w.target).access_token)
        self.erase(self.w.superadmin)
        client = client_for(None)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = client.get(f"{API}students/{self.w.classmate.pk}/data-export")
        self.assertNotFound(response)

    def test_the_wallet_is_locked_for_the_check(self):  # SM W-1
        # Without the lock, a credit grant committing a bucket between the
        # wallet check and the delete would be cascaded away silently.
        with CaptureQueriesContext(connection) as captured:
            response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 200, response.content[:400])
        locked = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and '"billing_creditwallet"' in query["sql"]
            and "FOR UPDATE" in query["sql"]
        ]
        self.assertTrue(locked, "W-1: the wallet is read without FOR UPDATE")

    def test_a_second_erasure_is_not_found(self):
        self.erase(self.w.superadmin)
        self.assertNotFound(self.erase(self.w.superadmin))

    def test_the_image_is_deleted_after_commit(self):  # R8
        self.assertTrue(self.storage.exists(self.image_name))
        self.erase(self.w.superadmin)
        self.assertFalse(self.storage.exists(self.image_name))

    def test_a_storage_failure_keeps_the_erasure_and_logs_error(self):  # R8
        with mock.patch.object(
            self.storage, "delete", side_effect=OSError("storage down")
        ), self.assertLogs("students.data_erase", level="ERROR"):
            response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(pk=self.w.target.pk).exists())

    def test_a_failure_rolls_everything_back_and_deletes_no_image(self):
        before = self.snapshot()
        records = apps.get_model("students.ErasedStudentRecord").objects.count()
        with mock.patch.object(
            data_erase, "_delete_account", side_effect=RuntimeError("injected")
        ):
            response = self.erase(self.w.superadmin)
        self.assertEqual(response.status_code, 500)
        self.assertNothingChanged(before)
        self.assertEqual(
            apps.get_model("students.ErasedStudentRecord").objects.count(), records
        )
        self.assertTrue(self.storage.exists(self.image_name))


@flag_on
class RefusalTests(_EraseCase):
    def test_the_check_covers_every_not_a_student_entry(self):
        self.assertEqual(
            list(data_erase.NOT_A_STUDENT_CHECKS),
            [key for key, _ in entries_with_role(Role.NOT_A_STUDENT)],
        )

    def test_owning_a_course_refuses(self):
        self.w.course(self.w.target, "Own")
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def test_owning_a_tag_refuses(self):
        Tag.objects.create(
            tag_type=TagType.UNIT,
            name="Mine",
            normalized_name="mine",
            owner_type=OwnerType.TEACHER,
            teacher=self.w.target,
        )
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def test_having_made_a_tag_refuses(self):  # the bare created_by_id
        Tag.objects.create(
            tag_type=TagType.UNIT,
            name="Theirs",
            normalized_name="theirs",
            owner_type=OwnerType.TEACHER,
            teacher=self.w.teacher,
            created_by_id=self.w.target.pk,
        )
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def test_a_batch_upload_session_refuses(self):
        BatchUploadSession.objects.create(teacher=self.w.target)
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def test_django_admin_history_refuses(self):
        LogEntry.objects.create(
            user=self.w.target, action_flag=1, object_repr="x", change_message=""
        )
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def test_a_license_row_refuses_before_the_cascade(self):  # SM (B)
        # LicenseSubscription.admin_user is PROTECT: if the refusal ran after
        # the cascade, the caller would get a ProtectedError 500. A
        # characterization test: the check already runs first.
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Erase test license plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=100,
            overage_block_size=10,
            overage_block_price=1,
            stripe_price_id="price_erase_test",
        )
        LicenseSubscription.objects.create(
            school=self.w.school,
            admin_user=self.w.target,
            plan=plan,
            billing_method=LicenseBillingMethod.OFFLINE,
            max_seats=1,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.assertRefused("STUDENT_ERASE_NOT_ONLY_A_STUDENT")

    def _wallet(self):
        return CreditWallet.objects.get(user=self.w.target)

    def test_wallet_credits_refuse(self):
        CreditBucket.objects.create(
            wallet=self._wallet(), bucket_type="MANUAL_GRANT", total_credits=5
        )
        self.assertRefused("STUDENT_ERASE_WALLET_NOT_EMPTY")

    def test_a_dispute_deficit_refuses(self):
        CreditWallet.objects.filter(pk=self._wallet().pk).update(
            dispute_deficit_credits=3
        )
        self.assertRefused("STUDENT_ERASE_WALLET_NOT_EMPTY")

    def test_a_refund_deficit_refuses(self):
        CreditWallet.objects.filter(pk=self._wallet().pk).update(
            refund_deficit_credits=2
        )
        self.assertRefused("STUDENT_ERASE_WALLET_NOT_EMPTY")

    def test_a_wallet_with_credit_history_refuses(self):
        # Spent to 0, but a kept ledger row points at its bucket.
        bucket = CreditBucket.objects.create(
            wallet=self._wallet(),
            bucket_type="MANUAL_GRANT",
            total_credits=5,
            used_credits=5,
        )
        CreditLedger.objects.create(
            user_id=self.w.target.pk, ledger_type="CONSUME", amount=-5, bucket=bucket
        )
        self.assertRefused("STUDENT_ERASE_WALLET_NOT_EMPTY")

    def _transaction(self, **who):
        return BillingTransaction.objects.create(
            source="INDIVIDUAL",
            transaction_type="INDIVIDUAL_OVERAGE_PURCHASE",
            occurred_at=timezone.now(),
            **who,
        )

    def test_a_billing_transaction_refuses(self):  # R2
        self._transaction(user=self.w.target)
        self.assertRefused("STUDENT_ERASE_BILLING_RECORDS")

    def test_performed_by_alone_refuses(self):  # R2
        self._transaction(user=self.w.teacher, performed_by=self.w.target)
        self.assertRefused("STUDENT_ERASE_BILLING_RECORDS")

    def test_a_payment_dispute_refuses(self):  # R2
        PaymentDispute.objects.create(
            user=self.w.target, stripe_dispute_id="dp_1", status="needs_response"
        )
        self.assertRefused("STUDENT_ERASE_BILLING_RECORDS")


@flag_on
class CourseErasureTests(_EraseCase):
    """D3: a teacher removes the student from the courses they own."""

    def test_only_the_teachers_courses_rows_go(self):
        target = self.w.target
        response = self.erase(self.w.teacher)
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(
            set(
                StudentCourse.objects.filter(student=target).values_list(
                    "course", flat=True
                )
            ),
            {self.w.course_b.pk},
        )
        self.assertEqual(
            set(
                StudentSubmission.objects.filter(student=target).values_list(
                    "pk", flat=True
                )
            ),
            {target.sub_b.pk},
        )
        self.assertEqual(
            set(
                ChatSession.objects.filter(user=target).values_list("course", flat=True)
            ),
            {self.w.course_b.pk},
        )
        tasks = set(
            BackgroundProcessingTask.objects.filter(requested_by=target).values_list(
                "pk", flat=True
            )
        )
        self.assertEqual(tasks, {target.task_b_alone.pk})  # E-Q2
        # The classmate in the same course is untouched.
        self.assertEqual(
            StudentCourse.objects.filter(student=self.w.classmate).count(), 2
        )

    def test_the_account_and_everything_else_stays(self):
        target = self.w.target
        self.erase(self.w.teacher)
        self.assertTrue(get_user_model().objects.filter(pk=target.pk).exists())
        self.assertTrue(CreditWallet.objects.filter(user=target).exists())
        self.assertTrue(UserActivity.objects.filter(user=target).exists())
        self.assertTrue(Token.objects.filter(user=target).exists())
        self.assertTrue(
            BetaWhitelist.objects.filter(email__iexact=target.email).exists()
        )
        self.assertTrue(self.storage.exists(self.image_name))
        self.assertFalse(
            apps.get_model("students.ErasedStudentRecord")
            .objects.filter(original_user_id=target.pk)
            .exists()
        )

    def test_the_risk_alert_is_left_alone(self):  # E-Q3
        before = _row(StudentRiskAlertState.objects.get(student=self.w.target))
        self.erase(self.w.teacher)
        self.assertEqual(
            _row(StudentRiskAlertState.objects.get(student=self.w.target)),
            before,
        )

    def test_the_grade_in_the_other_course_is_unchanged(self):
        before = StudentCourse.objects.get(
            student=self.w.target, course=self.w.course_b
        ).final_grade
        self.erase(self.w.teacher)
        self.assertEqual(
            StudentCourse.objects.get(
                student=self.w.target, course=self.w.course_b
            ).final_grade,
            before,
        )

    def test_one_event_with_scope_courses(self):
        self.erase(self.w.teacher)
        event = AuditEvent.objects.get(action=data_erase.ERASE_AUDIT_ACTION)
        self.assertEqual(event.metadata["scope"], "courses")
        self.assertEqual(event.actor_id, self.w.teacher.pk)

    def test_a_student_outside_the_teachers_courses_is_not_found(self):
        StudentCourse.objects.filter(
            student=self.w.target, course=self.w.course_a
        ).delete()
        self.assertNotFound(self.erase(self.w.teacher))


@flag_on
class CallerMatrixTests(_EraseCase):
    def test_a_school_admin_of_the_school_erases_in_full(self):
        response = self.erase(self.w.school_admin)
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertFalse(get_user_model().objects.filter(pk=self.w.target.pk).exists())

    def test_refused_callers_change_nothing(self):
        colleague = self.w.user(UserTypes.TEACHER, "Col", school=self.w.school)
        self.w.course(colleague, "Chemistry")
        half = self.w.user(UserTypes.SUPER_ADMIN, "Hal")  # no is_superuser
        for caller in (
            self.w.other_admin,
            self.w.schoolless_admin,
            self.w.target,
            self.w.classmate,
            colleague,
            self.w.user(UserTypes.TEACHER, "Str"),
            half,
            None,
        ):
            with self.subTest(caller=getattr(caller, "email", "anonymous")):
                before = self.snapshot()
                self.assertNotFound(self.erase(caller))
                self.assertNothingChanged(before)

    def test_a_student_with_no_school_is_for_a_super_admin_only(self):  # R6
        get_user_model().objects.filter(pk=self.w.target.pk).update(school=None)
        self.assertNotFound(self.erase(self.w.school_admin))
        self.assertEqual(self.erase(self.w.superadmin).status_code, 200)

    def test_the_target_must_be_a_student(self):
        self.assertNotFound(self.erase(self.w.superadmin, student=self.w.teacher))
        self.assertTrue(get_user_model().objects.filter(pk=self.w.teacher.pk).exists())

    def test_an_unknown_id_is_not_found(self):
        response = client_for(self.w.superadmin).post(url(self.w.course_a.pk))
        self.assertNotFound(response)

    def test_a_bad_token_is_not_found(self):
        client = client_for(None)
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertNotFound(client.post(url(self.w.target.pk)))

    def test_get_and_head_are_refused_and_erase_nothing(self):
        for method in ("get", "head"):
            with self.subTest(method=method):
                before = self.snapshot()
                response = self.erase(self.w.superadmin, method=method)
                self.assertEqual(response.status_code, 405)
                self.assertNothingChanged(before)


class FlagAndCatalogueTests(_EraseCase):
    @flag_off
    def test_off_by_default_nothing_is_erased(self):
        before = self.snapshot()
        for caller in (None, self.w.superadmin, self.w.school_admin):
            with self.subTest(caller=getattr(caller, "email", "anonymous")):
                self.assertNotFound(self.erase(caller))
        self.assertNothingChanged(before)

    def test_the_route_is_concealed(self):
        self.assertIn("student-data-erase", CONCEALED_ROUTES)

    def test_the_erase_action_and_its_metadata(self):  # E-Q1
        self.assertEqual(data_erase.ERASE_AUDIT_ACTION, "STUDENT_DATA_ERASE")
        allowed = METADATA_ALLOWLIST[AuditAction("STUDENT_DATA_ERASE")]
        self.assertEqual(allowed, {"scope", "item_count"})
        self.assertTrue(allowed <= ALLOWED_KEYS)

    def test_the_new_wording_is_pending(self):
        from audit.enums import ReasonCode
        from AutoGrader.reason_codes import REASON_CODES

        for code in (
            "STUDENT_ERASE_NOT_ONLY_A_STUDENT",
            "STUDENT_ERASE_WALLET_NOT_EMPTY",
            "STUDENT_ERASE_BILLING_RECORDS",
        ):
            with self.subTest(code=code):
                self.assertIn("PENDING", REASON_CODES[ReasonCode(code)].message)
        self.assertIn("PENDING", AuditAction("STUDENT_DATA_ERASE").label)
