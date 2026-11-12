"""Usage filters on the user list (beta bug fix, owner-approved 2026-11-12).

GET /api/v1/users/ (super admin only) takes eight range filters:
credit_usage_min/max, credits_left_min/max, papers_graded_min/max and
assignments_created_min/max. Each takes a whole number and both ends are
inclusive. They compare the list's own values (users.user_list), in the
credits users see: credits_used is the unrefunded usage divided by
CONVERSION_FACTOR the way the list shows it, credits_left is the floor
(CreditWallet.display_balance). A user with no credit wallet has no
credits_left, so credits_left filters leave them out; the other three are 0
when empty, so 0 counts.

    python manage.py test users.tests_usage_filters
"""

from django.urls import reverse

from billing.models import CreditWallet
from users.tests_user_list_docs import list_parameters, own_text
from users.tests_user_list_sorting import Base

APPROVED = {
    "credit_usage_min": (
        "Keeps users who have used at least this many credits in total, after "
        "refunds. Users who have used none count as 0."
    ),
    "credit_usage_max": (
        "Keeps users who have used at most this many credits in total, after "
        "refunds. Users who have used none count as 0."
    ),
    "credits_left_min": (
        "Keeps users with at least this many credits left. Users without a "
        "credit wallet are left out."
    ),
    "credits_left_max": (
        "Keeps users with at most this many credits left. Users without a "
        "credit wallet are left out."
    ),
    "papers_graded_min": (
        "Keeps teachers who have had at least this many papers graded by the "
        "AI. Teachers who have graded none count as 0."
    ),
    "papers_graded_max": (
        "Keeps teachers who have had at most this many papers graded by the "
        "AI. Teachers who have graded none count as 0."
    ),
    "assignments_created_min": (
        "Keeps teachers who have created at least this many assignments. "
        "Teachers who have created none count as 0."
    ),
    "assignments_created_max": (
        "Keeps teachers who have created at most this many assignments. "
        "Teachers who have created none count as 0."
    ),
}
SUPER_ADMIN_ONLY = "Works for super admins only; for anyone else it is ignored."


class UsageBase(Base):
    def listed(self, people, **params):
        return set(self.order_of(people, **params))

    def with_used(self, raw, refunded_raw=0):
        user = self.person()
        self.wallet(user)
        if raw:
            self.used(user, raw)
        if refunded_raw:
            self.used(user, refunded_raw, refunded=True)
        return user

    def with_left(self, total, used=0):
        user = self.person()
        self.bucket(self.wallet(user), total, used=used)
        return user

    def teaching(self, assignments=0, graded=0):
        teacher = self.person()
        course = self.course(teacher)
        made = [self.assignment(course) for _ in range(max(assignments, 1))]
        if not assignments:
            made[0].delete()
        for _ in range(graded):
            self.submission(made[0])
        return teacher


class CreditsUsedTests(UsageBase):
    def setUp(self):
        super().setUp()
        # What the list shows: 0, 1, 2, 5 credits.
        self.none = self.with_used(0)
        self.one = self.with_used(1_500)
        self.two = self.with_used(2_000)
        self.five = self.with_used(5_000, refunded_raw=90_000)
        self.people = [self.none, self.one, self.two, self.five]

    def test_min_is_inclusive_and_zero_counts(self):
        self.assertEqual(self.listed(self.people, credit_usage_min=0), set(self.people))
        self.assertEqual(
            self.listed(self.people, credit_usage_min=2), {self.two, self.five}
        )

    def test_max_is_inclusive_and_zero_counts(self):
        self.assertEqual(self.listed(self.people, credit_usage_max=0), {self.none})
        self.assertEqual(
            self.listed(self.people, credit_usage_max=2),
            {self.none, self.one, self.two},
        )

    def test_it_compares_the_value_the_list_shows(self):
        """1500 raw is listed as 1 credit (the list divides in whole
        credits), so 1 keeps it on both ends and 2 does not."""
        self.assertEqual(self.row(self.one)["credit_usage"], 1)
        self.assertIn(self.one, self.listed(self.people, credit_usage_min=1))
        self.assertIn(self.one, self.listed(self.people, credit_usage_max=1))
        self.assertNotIn(self.one, self.listed(self.people, credit_usage_min=2))

    def test_refunded_usage_does_not_count(self):
        self.assertEqual(self.row(self.five)["credit_usage"], 5)
        self.assertEqual(self.listed(self.people, credit_usage_min=6), set())

    def test_a_range(self):
        self.assertEqual(
            self.listed(self.people, credit_usage_min=1, credit_usage_max=2),
            {self.one, self.two},
        )


class CreditsLeftTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.nowallet = self.person()
        CreditWallet.objects.filter(user=self.nowallet).delete()
        self.negative = self.with_left(0, used=500)  # raw -500, listed -1
        self.zero = self.with_left(1_000, used=1_000)
        self.three = self.with_left(3_500)  # listed 3
        self.people = [self.nowallet, self.negative, self.zero, self.three]

    def test_the_list_values_are_what_the_filters_use(self):
        self.assertIsNone(self.row(self.nowallet)["credits_left"])
        self.assertEqual(self.row(self.negative)["credits_left"], -1)
        self.assertEqual(self.row(self.three)["credits_left"], 3)

    def test_min_is_inclusive_and_leaves_out_no_wallet(self):
        self.assertEqual(
            self.listed(self.people, credits_left_min=0), {self.zero, self.three}
        )
        self.assertEqual(self.listed(self.people, credits_left_min=3), {self.three})
        self.assertEqual(
            self.listed(self.people, credits_left_min=-1),
            {self.negative, self.zero, self.three},
        )

    def test_max_is_inclusive_and_leaves_out_no_wallet(self):
        self.assertEqual(
            self.listed(self.people, credits_left_max=0), {self.negative, self.zero}
        )
        self.assertNotIn(self.nowallet, self.listed(self.people, credits_left_max=999))


class CountFiltersTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.empty = self.teaching(0, 0)
        self.one = self.teaching(1, 1)
        self.three = self.teaching(3, 3)
        self.people = [self.empty, self.one, self.three]

    def test_papers_graded(self):
        self.assertEqual(
            self.listed(self.people, papers_graded_min=1), {self.one, self.three}
        )
        self.assertEqual(
            self.listed(self.people, papers_graded_max=1), {self.empty, self.one}
        )
        self.assertEqual(self.listed(self.people, papers_graded_max=0), {self.empty})
        self.assertEqual(self.listed(self.people, papers_graded_min=3), {self.three})

    def test_assignments_created(self):
        self.assertEqual(
            self.listed(self.people, assignments_created_min=1), {self.one, self.three}
        )
        self.assertEqual(
            self.listed(self.people, assignments_created_max=1),
            {self.empty, self.one},
        )
        self.assertEqual(
            self.listed(self.people, assignments_created_max=0), {self.empty}
        )
        self.assertEqual(
            self.listed(self.people, assignments_created_min=3), {self.three}
        )

    def test_ungraded_papers_do_not_count(self):
        teacher = self.person()
        self.submission(self.assignment(self.course(teacher)), ai_graded=False)
        self.assertEqual(self.listed([teacher], papers_graded_max=0), {teacher})


class BadValuesTests(UsageBase):
    def test_a_bad_value_is_a_400_not_a_500(self):
        for name in APPROVED:
            for bad in ("abc", "1.5"):
                with self.subTest(name=name, value=bad):
                    response = self.get_list(**{name: bad})
                    self.assertEqual(response.status_code, 400, response.content[:200])

    def test_an_empty_value_does_nothing(self):
        user = self.person()
        for name in APPROVED:
            with self.subTest(name=name):
                self.assertEqual(self.listed([user], **{name: ""}), {user})


class OnlyForSuperAdminsTests(UsageBase):
    def test_the_filters_do_nothing_for_anyone_else(self):
        teacher = self.person()
        course = self.course(teacher)
        student = self.person(user_type="STUDENT")
        from classrooms.models import EnrollmentStatusType, StudentCourse

        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        client = self.as_user(teacher)
        detail = reverse("user-detail", kwargs={"pk": student.pk})
        for name in APPROVED:
            with self.subTest(name=name):
                from django.core.cache import cache

                cache.clear()
                value = 99 if name.endswith("_min") else -99
                self.assertEqual(client.get(detail, {name: value}).status_code, 200)


class SwaggerTextTests(UsageBase):
    def test_each_filter_has_the_approved_text_and_the_super_admin_sentence(self):
        params = list_parameters(self)
        for name, text in APPROVED.items():
            with self.subTest(name=name):
                self.assertIn(name, params)
                self.assertEqual(
                    own_text(params[name].get("description")),
                    f"{text} {SUPER_ADMIN_ONLY}",
                )
                self.assertEqual(params[name]["schema"]["type"], "integer")


class OldNamesTests(UsageBase):
    def test_the_old_filter_and_sort_names_are_not_accepted(self):
        low, high = self.with_used(1_000), self.with_used(9_000)
        people = [low, high]
        self.assertEqual(self.listed(people, credits_used_min=5), set(people))
        self.assertEqual(self.listed(people, credits_used_max=0), set(people))
        default = self.order_of(people)
        self.assertEqual(self.order_of(people, ordering="credits_used"), default)
        self.assertEqual(self.order_of(people, ordering="-credits_used"), default)
        self.assertEqual(self.order_of(people, ordering="-credit_usage"), [high, low])

    def test_the_row_has_credit_usage_and_no_credits_used(self):
        row = self.row(self.with_used(2_500))
        self.assertEqual(row["credit_usage"], 2)
        self.assertNotIn("credits_used", row)


class ChargesFollowTheBilledUserTests(UsageBase):
    def test_a_log_whose_wallet_is_gone_still_counts_on_the_users_list(self):
        user = self.with_used(4_500)
        CreditWallet.objects.filter(user=user).delete()  # no constraint: log stays
        self.assertEqual(self.row(user)["credit_usage"], 4)
        self.assertEqual(self.listed([user], credit_usage_min=4), {user})
        self.assertEqual(self.listed([user], credit_usage_max=3), set())

    def test_an_old_log_without_a_user_id_counts_through_its_wallet(self):
        from billing.models import CreditUsageLog

        user = self.person()
        wallet = self.wallet(user)
        bucket = self.bucket(wallet, 0, expires_in=-400)
        CreditUsageLog.objects.create(
            user_id=None,  # logs from before the column existed
            user_email=user.email,
            wallet=wallet,
            bucket=bucket,
            amount=2_500,
            feature="Grading Assignment",
        )
        self.assertEqual(self.row(user)["credit_usage"], 2)
