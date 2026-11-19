"""Epic F1 adversarial matrix fixture (plan §7.1, NFR-TST-08; the Fixer's).

Written against docs/evidence/epic-f1/INTERFACE.md only: department rows come
from `departments.tests_factories` (`make_department`, `add_member`) or the
routes; everything else (schools, licenses, teachers on a license, sessions,
courses, students, submissions, wallets) is built with existing project code,
mostly through real routes with real JWTs, as the H-38 tests do.

The world (one per test class, `build_world()` from setUpTestData):

School S1 (license L1), admins A1 and A1b:
  T1  D1 member, can edit
  T2  D1 member, restricted (can_edit False)
  T3  D2 member only
  T4  on the license, in no department
  T5  D1 and D2 member (shares D1 with T1 and T2: "A" for the M-rows)
  T6  was a D1 member, then removed from the license (ex-member)
School S2 (license L2), admin A2:
  T8  D3 member
I1 individual teacher (no school), A0 school admin with no school, SU super
admin, ST a student enrolled in T5's course.

Every teacher on a license has a course in their school's session, an
enrolled student, a topic, a published assignment, a graded submission and a
credit bucket. Each carries a CANARY: a sentinel in the course name, the
student's name and the assignment title, and an implausible bucket total
checked raw and with thousands separators (plan §5.3: "73190" alone can
collide with an id or a page size, so the numbers are long and odd).
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import Course, School, StudentCourse, Topic
from classrooms.tests_support_add_by_email import add_by_email
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

API = "/api/v1"
PASSWORD = "Str0ng-f1-matrix-password!"  # pragma: allowlist secret

#: Letters only (names refuse digits), unique enough never to occur by chance.
SENTINEL = "Qxvorpalzest"

#: Teachers on a license, with the departments they join.
S1_TEACHERS = ("t1", "t2", "t3", "t4", "t5", "t6")
S2_TEACHERS = ("t8",)
LETTERS = {"t1": "a", "t2": "b", "t3": "c", "t4": "d", "t5": "e", "t6": "f", "t8": "h"}


def canary_balance(index):
    """Long, odd, distinct per teacher: 7,319,447 + 104,729 * index."""
    return 7_319_447 + 104_729 * index


class _EnvelopeClient(APIClient):
    """`response.json()` gives the house envelope's `data` on a success
    (the renderer sends an empty list as `{}`, users/renderers.py), and the
    raw body on an error. `response.content` stays the raw bytes."""

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        raw = response.json

        def unwrapped():
            payload = raw()
            if isinstance(payload, dict) and payload.get("success") is True:
                data = payload.get("data")
                return [] if data == {} else data
            return payload

        response.json = unwrapped  # type: ignore[method-assign]
        return response


def jwt_client(user):
    """A real signed access token (the app's own token class, with the
    sign-in epoch) checked by the real JWT authenticator."""
    from users.tokens import EpochRefreshToken

    client = _EnvelopeClient()
    if user is not None:
        token = EpochRefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def make_user(email, user_type, school=None, first_name=None, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=first_name or email.split("@")[0].title(),
        last_name="Matrix",
        user_type=user_type,
        school=school,
        is_active=True,
        **extra,
    )


def _ok(response, status=(200, 201)):
    assert response.status_code in status, (response.status_code, response.content)
    return response


def make_license(school, admin, plan):
    return LicenseSubscription.objects.create(
        school=school,
        admin_user=admin,
        plan=plan,
        billing_cycle_start=timezone.now(),
        billing_cycle_end=timezone.now() + timedelta(days=30),
        max_seats=20,
        is_active=True,
    )


def add_to_license(admin, license_sub, teachers):
    response = _ok(
        jwt_client(admin).post(
            f"{API}/license-subscriptions/{license_sub.id}/add_teachers",
            {"teacher_emails": [t.email for t in teachers]},
            format="json",
        )
    )
    assert response.data["successful"] == len(teachers), response.content
    for teacher in teachers:
        teacher.refresh_from_db()
        assert teacher.school_id == license_sub.school_id, teacher.email


def remove_from_license(admin, license_sub, teacher):
    response = _ok(
        jwt_client(admin).post(
            f"{API}/license-subscriptions/{license_sub.id}/remove_teachers",
            {"teacher_ids": [str(teacher.id)]},
            format="json",
        )
    )
    assert response.data["successful"] == 1, response.content
    teacher.refresh_from_db()


def make_session(admin, name):
    return _ok(
        jwt_client(admin).post(f"{API}/sessions", {"name": name}, format="json")
    ).data["id"]


def give_teacher_data(teacher, session_id, letter, index):
    """The teacher's own course, student, topic, assignment, graded submission
    and credit bucket, each carrying the teacher's canary."""
    client = jwt_client(teacher)
    tag = f"{SENTINEL}{letter}"
    course_id = _ok(
        client.post(
            f"{API}/course",
            {"name": f"Course {tag} 4471", "session": session_id},
            format="json",
        )
    ).data["id"]
    student_email = f"pupil-{letter}@f1matrix.test"
    _ok(
        client.post(
            f"{API}/course/{course_id}/students",
            add_by_email(student_email, first_name=f"Pupil{tag}", last_name="Canary"),
            format="json",
        )
    )
    student = CustomUser.objects.get(email=student_email)
    topic = Topic.objects.create(name=f"Topic {tag}", course_id=course_id)
    assignment = Assignment.objects.create(
        title=f"Quiz {tag}",
        course_id=course_id,
        total_points=10,
        due_date=timezone.now() + timedelta(days=7),
        status=AssignmentStatus.PUBLISHED,
    )
    submission = StudentSubmission.objects.create(
        student=student,
        assignment=assignment,
        answers={},
        score=Decimal("7.00"),
        graded_at=timezone.now(),
        feedback={"grading_summary": {"total_score": 7, "max_total_points": 10}},
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    balance = canary_balance(index)
    bucket = CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=balance,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=25),
    )
    enrollment = StudentCourse.objects.get(course_id=course_id, student=student)
    return SimpleNamespace(
        teacher=teacher,
        tag=tag,
        balance=balance,
        course=Course.objects.get(pk=course_id),
        student=student,
        enrollment=enrollment,
        topic=topic,
        assignment=assignment,
        submission=submission,
        wallet=wallet,
        bucket=bucket,
    )


def canary_strings(data):
    """Every form a teacher's private data could show up in a response."""
    return [
        data.tag,
        str(data.balance),
        f"{data.balance:,}",
        str(data.course.id),
        str(data.student.id),
        str(data.submission.id),
        str(data.wallet.id),
        str(data.bucket.id),
        data.student.email,
    ]


def build_world():
    """Build the whole matrix world. Call from setUpTestData."""
    w = build_base_world()
    place_in_departments(w)
    return w


def build_base_world():
    """Everything except department rows (existing project code only)."""
    w = SimpleNamespace()
    w.plan = SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="F1 Matrix License Plan",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20000,
    )
    w.s1 = School.objects.create(name="Matrix School One")
    w.s2 = School.objects.create(name="Matrix School Two")
    w.a1 = make_user("admin-a1@f1matrix.test", UserTypes.SCHOOL_ADMIN, w.s1)
    w.a1b = make_user("admin-a1b@f1matrix.test", UserTypes.SCHOOL_ADMIN, w.s1)
    w.a2 = make_user("admin-a2@f1matrix.test", UserTypes.SCHOOL_ADMIN, w.s2)
    w.a0 = make_user("admin-a0@f1matrix.test", UserTypes.SCHOOL_ADMIN, None)
    w.su = make_user(
        "super@f1matrix.test",
        UserTypes.SUPER_ADMIN,
        None,
        is_superuser=True,
        is_staff=True,
    )
    w.l1 = make_license(w.s1, w.a1, w.plan)
    w.l2 = make_license(w.s2, w.a2, w.plan)

    w.teachers = {}
    for key in S1_TEACHERS + S2_TEACHERS:
        w.teachers[key] = make_user(f"{key}@f1matrix.test", UserTypes.TEACHER)
        setattr(w, key, w.teachers[key])
    w.i1 = make_user("individual@f1matrix.test", UserTypes.TEACHER)

    add_to_license(w.a1, w.l1, [w.teachers[k] for k in S1_TEACHERS])
    add_to_license(w.a2, w.l2, [w.teachers[k] for k in S2_TEACHERS])

    w.session1 = make_session(w.a1, "Matrix 2026 Term 1")
    w.session2 = make_session(w.a2, "Matrix 2026 Term 1 S2")
    w.data = {}
    for index, key in enumerate(S1_TEACHERS + S2_TEACHERS):
        session = w.session1 if key in S1_TEACHERS else w.session2
        w.data[key] = give_teacher_data(w.teachers[key], session, LETTERS[key], index)
    # The student actor: T5's enrolled student.
    w.st = w.data["t5"].student
    return w


def place_in_departments(w):
    """D1, D2 (S1) and D3 (S2) and their members, through the lead's
    factories only; then T6 leaves the license (the ex-member)."""
    from departments.tests_factories import add_member, make_department

    w.d1 = make_department(w.s1, name="Matrix Science")
    w.d2 = make_department(w.s1, name="Matrix Humanities")
    w.d3 = make_department(w.s2, name="Matrix Science")
    add_member(w.d1, w.t1, can_edit=True)
    add_member(w.d1, w.t2, can_edit=False)
    add_member(w.d2, w.t3)
    add_member(w.d1, w.t5)
    add_member(w.d2, w.t5)
    add_member(w.d3, w.t8)
    add_member(w.d1, w.t6)
    remove_from_license(w.a1, w.l1, w.t6)
    for user in (w.t1, w.t2, w.t3, w.t4, w.t5, w.t6, w.t8):
        user.refresh_from_db()


def all_users(w):
    """Every actor in the world, by label (anonymous is None)."""
    return {
        "A1": w.a1,
        "A1b": w.a1b,
        "A2": w.a2,
        "A0": w.a0,
        "SU": w.su,
        "T1": w.t1,
        "T2": w.t2,
        "T3": w.t3,
        "T4": w.t4,
        "T5": w.t5,
        "T6": w.t6,
        "T8": w.t8,
        "I1": w.i1,
        "ST": w.st,
        "anon": None,
    }


# --- route enumeration (shared by the M-rows sweep and D0) -------------------

#: Readable routes left out of the sweeps, each with its reason.
SWEEP_EXCLUDED = {
    "schema": "the OpenAPI document: code, not data; seconds per call",
    "swagger-ui": "HTML shell for the schema",
}

#: The four F1 readable routes (INTERFACE.md, ad1fb1d09).
DEPARTMENT_READ_ROUTES = frozenset(
    {
        "departments-admin-list",
        "departments-admin-detail",
        "departments-mine",
        "departments-roster",
    }
)


def _walk(patterns, prefix="", namespace=None):
    from django.urls import URLPattern, URLResolver

    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(
                pattern.url_patterns,
                prefix + str(pattern.pattern),
                pattern.namespace or namespace,
            )
        elif isinstance(pattern, URLPattern):
            name = pattern.name
            if namespace and name:
                name = f"{namespace}:{name}"
            yield prefix + str(pattern.pattern), name, pattern.callback


def _reads(callback):
    actions = getattr(callback, "actions", None)
    if actions:
        return "get" in actions
    view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view_cls is not None:
        return hasattr(view_cls, "get")
    return True  # a function view: probe it with GET


def read_routes():
    """{url_name: [path kwarg names]} for every named readable route outside
    the Django admin (format-suffix twins dropped)."""
    import re

    from django.urls import get_resolver

    routes = {}
    for route_path, name, callback in _walk(get_resolver().url_patterns):
        if not name or route_path.startswith("admin/") or name.startswith("admin:"):
            continue
        kwargs = sorted(
            set(re.findall(r"\(\?P<(\w+)>", route_path))
            | {seg.split(":")[-1] for seg in re.findall(r"<([^>]+)>", route_path)}
        )
        if "format" in kwargs or not _reads(callback):
            continue
        routes[name] = kwargs
    return routes


def object_ids(data, allocation_id=None):
    """Every id of a teacher's private objects, by kind."""
    ids = {
        "course": data.course.id,
        "student": data.student.id,
        "teacher": data.teacher.id,
        "enrollment": data.enrollment.id,
        "topic": data.topic.id,
        "assignment": data.assignment.id,
        "submission": data.submission.id,
        "wallet": data.wallet.id,
        "bucket": data.bucket.id,
    }
    if allocation_id is not None:
        ids["allocation"] = allocation_id
    return ids


#: Which kinds of id a path kwarg can carry; any other kwarg gets every kind.
KWARG_KINDS = {
    "course_id": ("course",),
    "student_id": ("student",),
    "assignment_id": ("assignment",),
    "user_id": ("teacher", "student"),
    "department_id": (),
    "membership_id": (),
}


def urls_with_ids(name, kwarg_names, ids):
    """Every URL for the route with the given ids substituted: one URL per
    kind for a generic kwarg (pk, id, ...), the matching kind for a typed
    one, a made-up UUID where no kind fits. A route without kwargs gives
    one URL."""
    import itertools
    import uuid

    from django.urls import NoReverseMatch, reverse

    if not kwarg_names:
        return [reverse(name)]
    choices = []
    for kwarg in kwarg_names:
        kinds = KWARG_KINDS.get(kwarg, tuple(ids))
        values = [ids[k] for k in kinds if k in ids] or [uuid.uuid4()]
        choices.append(values)
    urls = []
    for combo in itertools.product(*choices):
        try:
            urls.append(
                reverse(name, kwargs=dict(zip(kwarg_names, combo, strict=True)))
            )
        except NoReverseMatch:
            continue
    return urls


def private_canaries(data):
    """A's private strings that must never reach B, whatever the route:
    no ids (an error message may echo the id B typed)."""
    return [
        data.tag,
        str(data.balance),
        f"{data.balance:,}",
        data.student.email,
    ]


def allocation_id(teacher):
    from billing.models import SchoolCreditAllocation

    return (
        SchoolCreditAllocation.objects.filter(user=teacher, is_active=True)
        .values_list("id", flat=True)
        .first()
    )
