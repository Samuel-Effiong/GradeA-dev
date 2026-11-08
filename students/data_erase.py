"""NFR-CMP-04 step 3: erasing a student's data.

Two scopes, decided by who asks (one place, `ERASE_CALLERS`):

* **full**: a real super admin, or a school admin of the student's own
  school (never when either school is empty, R6). Refuses first if the
  account holds anything only staff hold, if the wallet is not empty
  (credits, a deficit or any credit history), or if billing records point at
  it (owner R2). Otherwise deletes every DELETE entry of
  `students.student_data_map` for this student only, writes the
  erased-student record, deletes the account, and removes the profile image
  from storage once the transaction has committed (R8).
* **courses**: a teacher (D3). Removes the student from the courses the
  teacher owns: the course-scoped entries in those courses only. The account
  and everything else stay; the risk alert is left as it is (E-Q3).

The activity log and credit records are never touched (KEEP_RECORD; D1,
D4). Everything runs in one transaction with the student's row locked, so
a failure anywhere leaves nothing changed. The rulings are in
docs/evidence/nfr-cmp-04-student-data/NOTES.md, "Step 3".
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.emitter import emit
from audit.enums import AuditAction
from AutoGrader.reason_codes import CodedError
from classrooms.models import Course, StudentCourse
from students.data_export import COURSE_PATH
from students.student_data_map import STUDENT_DATA_MAP, Match, Role, entries_with_role
from users.models import CustomUser, UserTypes

logger = logging.getLogger(__name__)

ERASE_AUDIT_ACTION = AuditAction.STUDENT_DATA_ERASE

#: The erased-student record is kept this long (owner, 2026-11-06).
KEEP_FOR = relativedelta(years=7)

#: Every column only a teacher or an admin can hold. Any row pointing at
#: the student refuses a full erasure. Read from the map, so a new entry
#: is enforced without a change here.
NOT_A_STUDENT_CHECKS = tuple(key for key, _ in entries_with_role(Role.NOT_A_STUDENT))

#: Deleted first, in this order: with the enrollments gone, the final-grade
#: recalculation a submission delete fires finds nothing to rewrite.
FIRST = ("classrooms.StudentCourse", "students.StudentSubmission")


@dataclass(frozen=True)
class EraseScope:
    kind: str  # "full" or "courses"
    course_ids: Optional[frozenset] = None


def _super_admin(caller, student):
    return EraseScope("full") if caller.is_real_super_admin() else None


def _school_admin(caller, student):
    if caller.school_id is None or caller.school_id != student.school_id:
        return None
    return EraseScope("full")


def _teacher(caller, student):
    owned = frozenset(
        Course.objects.filter(teacher=caller).values_list("id", flat=True)
    )
    if not StudentCourse.objects.filter(student=student, course_id__in=owned).exists():
        return None
    return EraseScope("courses", owned)


#: Who may erase, and how much (D3, R6). One place.
ERASE_CALLERS: dict[str, Callable] = {
    UserTypes.SUPER_ADMIN: _super_admin,
    UserTypes.SCHOOL_ADMIN: _school_admin,
    UserTypes.TEACHER: _teacher,
}


def erase_scope(caller, student):
    """The caller's erasure scope over `student`, or None ("not found")."""
    if student.user_type != UserTypes.STUDENT or caller.pk == student.pk:
        return None
    decide = ERASE_CALLERS.get(caller.user_type)
    return decide(caller, student) if decide else None


def _refusal(student):
    """The reason a full erasure must not run, or None."""
    for label, field_name in NOT_A_STUDENT_CHECKS:
        model = apps.get_model(label)
        if model.objects.filter(**{field_name: student.pk}).exists():
            return "STUDENT_ERASE_NOT_ONLY_A_STUDENT"
    # SM W-1: locked, so a credit grant can't add a bucket between this check
    # and the delete (the bucket would be cascaded away). Lock order: the
    # student row (erase_student), then the wallet; nothing locks a user row
    # after a wallet (NOTES, W-1).
    wallet = (
        apps.get_model("billing.CreditWallet")
        .objects.select_for_update()
        .filter(user=student)
        .first()
    )
    if wallet is not None and (
        wallet.buckets.exists()  # credits, or credit history kept records point at
        or wallet.dispute_deficit_credits
        or wallet.refund_deficit_credits
    ):
        return "STUDENT_ERASE_WALLET_NOT_EMPTY"
    billing = apps.get_model("billing.BillingTransaction").objects.filter(
        Q(user=student) | Q(performed_by=student)
    )
    disputes = apps.get_model("billing.PaymentDispute").objects.filter(user=student)
    if billing.exists() or disputes.exists():
        return "STUDENT_ERASE_BILLING_RECORDS"
    return None


def _delete_entries(student, scope):
    """Delete the student's rows of every DELETE entry in scope; return
    how many rows were deleted (cascades not counted)."""
    keys = [
        key
        for key, entry in STUDENT_DATA_MAP.items()
        if entry.role is Role.DELETE
        and entry.match != Match.OWN_ROW
        and (scope.course_ids is None or entry.course_scoped)
    ]
    keys.sort(
        key=lambda key: (
            key[0] not in FIRST,
            FIRST.index(key[0]) if key[0] in FIRST else 0,
        )
    )
    deleted = 0
    for label, field_name in keys:
        entry = STUDENT_DATA_MAP[(label, field_name)]
        model = apps.get_model(label)
        if entry.match == Match.EMAIL:
            rows = model.objects.filter(**{f"{field_name}__iexact": student.email})
        else:
            rows = model.objects.filter(**{field_name: student.pk})
        if scope.course_ids is not None:
            rows = rows.filter(**{f"{COURSE_PATH[label]}__in": scope.course_ids})
        deleted += rows.count()
        rows.delete()
    return deleted


def _delete_account(student):
    student.delete()


def _delete_image(storage, name):
    """R8: after commit. A storage failure leaves the erasure done."""
    try:
        storage.delete(name)
    except Exception:
        logger.error(
            "student data erase: profile image not deleted from storage (%s)",
            name,
            exc_info=True,
        )


def erase_student(student_id, scope, *, by, request=None):
    """Erase within `scope`. Returns `{scope, item_count}`, or raises
    CodedError for a refused full erasure (nothing changed)."""
    with transaction.atomic():
        student = CustomUser.objects.select_for_update().get(pk=student_id)
        if scope.kind == "full":
            code = _refusal(student)
            if code is not None:
                raise CodedError(code)
        item_count = _delete_entries(student, scope)
        if scope.kind == "full":
            wallets = apps.get_model("billing.CreditWallet").objects.filter(
                user=student
            )
            item_count += wallets.count()
            wallets.delete()
            erased_at = timezone.now()
            apps.get_model("students.ErasedStudentRecord").objects.create(
                original_user_id=student.pk,
                first_name=student.first_name,
                middle_name=student.middle_name or "",
                last_name=student.last_name,
                email=student.email,
                erased_at=erased_at,
                erased_by_id=by.pk,
                keep_until=erased_at + KEEP_FOR,
            )
            image = student.profile_image
            if image:
                storage, name = image.storage, image.name
                transaction.on_commit(lambda: _delete_image(storage, name))
            _delete_account(student)
            item_count += 1
        result = {"scope": scope.kind, "item_count": item_count}
        emit(
            ERASE_AUDIT_ACTION,
            actor=by,
            target_type="CustomUser",
            target_id=student_id,
            request=request,
            school_id=student.school_id,
            metadata=result,
        )
    return result
