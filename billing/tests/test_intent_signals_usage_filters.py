"""The sales-leads list takes usage filters and usage sorts (beta bug fix,
owner-approved 2026-11-12).

GET analytics beta/intent-signals (super admin only) gains
credits_used_min/max, credits_left_min/max, papers_graded_min/max,
assignments_created_min/max and activated_after/before, and `ordering` also
takes credits_used, credits_left, papers_graded, assignments_created and
activated_at, each with or without "-". The values are the user list's own
(users.user_list), so a lead's credits_used is every credit the teacher ever
used (after refunds), not BetaProfile.total_credits_used, and the row shows
what it showed. Empty values sort last both ways; ties go by user id. The
`signals` order and the default order are as they were.

    python manage.py test billing.tests.test_intent_signals_usage_filters
"""

from datetime import timedelta

from django.urls import reverse

from assignments.models import Assignment
from billing.models import CreditBucket, CreditBucketType, CreditUsageLog, CreditWallet
from classrooms.models import Course, Session
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes
from users.tests_usage_filters import APPROVED

from . import test_intent_signals as base
from .test_intent_signals_lead_filters import URL, LeadFilterBase
from .test_intent_signals_pins import NOW

SORTS = ("credits_used", "credits_left", "papers_graded", "assignments_created",
         "activated_at")  # fmt: skip
ORDERING_ADDITION = (
    "You can also sort by `credits_used`, `credits_left`, `papers_graded`, "
    "`assignments_created` or `activated_at`. Add `-` in front for the "
    "largest or newest first. Empty values come last either way, and ties are "
    "broken by ID."
)


class UsageBase(LeadFilterBase):
    def wallet(self, user, total=0, used=0):
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.filter(wallet=wallet).delete()
        return wallet, CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=total,
            used_credits=used,
            expires_at=NOW + timedelta(days=30),
        )

    def lead(self, score=0.0, used=0, refunded=0, left=None, **fields):
        """A teacher whose list values are: credits_used from `used` (raw,
        unrefunded usage), credits_left from `left` (raw; None = no wallet)."""
        user = self.teacher(score, **fields)
        if left is None:
            CreditWallet.objects.filter(user=user).delete()
            return user
        wallet, bucket = self.wallet(user, total=left)
        for amount, is_refunded in ((used, False), (refunded, True)):
            if amount:
                CreditUsageLog.objects.create(
                    user_id=user.id,
                    user_email=user.email,
                    wallet=wallet,
                    bucket=bucket,
                    amount=amount,
                    feature="Grading Assignment",
                    is_refunded=is_refunded,
                )
        return user

    def teaching(self, user, assignments=0, graded=0):
        course = Course.objects.create(
            name="Bio",
            teacher=user,
            session=Session.objects.create(name="Term", teacher=user),
        )
        made = [
            Assignment.objects.create(title="Q", course=course, total_points=10)
            for _ in range(max(assignments, 1))
        ]
        if not assignments:
            made[0].delete()
            return user
        for i in range(graded):
            student = CustomUser.objects.create_user(
                email=f"s{user.pk}-{i}@x.example",
                password="pw-not-a-secret-1A!",  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                is_active=True,
            )
            StudentSubmission.objects.create(
                assignment=made[0],
                student=student,
                answers={"q1": "a"},
                ai_graded_at=NOW,
            )
        return user


class CreditsUsedFilterTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.none = self.lead(left=0)
        self.one = self.lead(used=1_500, left=0)  # listed 1
        self.two = self.lead(used=2_000, left=0)
        self.five = self.lead(used=5_000, refunded=90_000, left=0)
        self.nowallet = self.lead(left=None)  # credits_used counts as 0

    def test_min_and_max_are_inclusive_and_zero_counts(self):
        self.assertEqual(
            self.kept(credits_used_min=2), {self.two.email, self.five.email}
        )
        self.assertEqual(
            self.kept(credits_used_max=0), {self.none.email, self.nowallet.email}
        )
        self.assertEqual(
            self.kept(credits_used_max=2),
            {self.none.email, self.nowallet.email, self.one.email, self.two.email},
        )

    def test_it_compares_the_value_the_user_list_shows(self):
        self.assertIn(self.one.email, self.kept(credits_used_min=1))
        self.assertIn(self.one.email, self.kept(credits_used_max=1))
        self.assertNotIn(self.one.email, self.kept(credits_used_min=2))

    def test_refunds_do_not_count(self):
        self.assertEqual(self.kept(credits_used_min=6), set())

    def test_it_uses_every_credit_ever_used_not_the_beta_counter(self):
        BetaProfile = type(self.two.beta_profile)
        BetaProfile.objects.filter(user=self.two).update(total_credits_used=50_000)
        BetaProfile.objects.filter(user=self.five).update(total_credits_used=0)
        self.assertEqual(self.kept(credits_used_min=5), {self.five.email})
        self.assertNotIn(self.two.email, self.kept(credits_used_min=3))
        self.assertIn(self.two.email, self.kept(credits_used_max=2))

    def test_the_row_still_shows_the_beta_counter(self):
        BetaProfile = type(self.two.beta_profile)
        BetaProfile.objects.filter(user=self.two).update(total_credits_used=777)
        rows = {r["email"]: r for r in self.leads()}
        self.assertEqual(rows[self.two.email]["metrics"]["credit_usage"], 777)


class CreditsLeftFilterTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.nowallet = self.lead(left=None)
        self.negative = self.lead(left=-500)  # floor -1
        self.zero = self.lead(left=0)
        self.three = self.lead(left=3_500)  # 3

    def test_min_is_inclusive_and_leaves_out_no_wallet(self):
        self.assertEqual(
            self.kept(credits_left_min=0), {self.zero.email, self.three.email}
        )
        self.assertEqual(self.kept(credits_left_min=3), {self.three.email})
        self.assertEqual(
            self.kept(credits_left_min=-1),
            {self.negative.email, self.zero.email, self.three.email},
        )

    def test_max_is_inclusive_and_leaves_out_no_wallet(self):
        self.assertEqual(
            self.kept(credits_left_max=0), {self.negative.email, self.zero.email}
        )
        self.assertNotIn(self.nowallet.email, self.kept(credits_left_max=999))


class CountFiltersTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.empty = self.teaching(self.teacher(1.0), 0, 0)
        self.one = self.teaching(self.teacher(2.0), 1, 1)
        self.three = self.teaching(self.teacher(3.0), 3, 3)

    def test_papers_graded(self):
        e, o, t = self.empty.email, self.one.email, self.three.email
        self.assertEqual(self.kept(papers_graded_min=1), {o, t})
        self.assertEqual(self.kept(papers_graded_max=1), {e, o})
        self.assertEqual(self.kept(papers_graded_max=0), {e})
        self.assertEqual(self.kept(papers_graded_min=3), {t})

    def test_assignments_created(self):
        e, o, t = self.empty.email, self.one.email, self.three.email
        self.assertEqual(self.kept(assignments_created_min=1), {o, t})
        self.assertEqual(self.kept(assignments_created_max=1), {e, o})
        self.assertEqual(self.kept(assignments_created_max=0), {e})
        self.assertEqual(self.kept(assignments_created_min=3), {t})


class ActivatedFilterTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.old = self.teacher(1.0)
        self.mid = self.teacher(2.0)
        self.new = self.teacher(3.0)
        self.never = self.teacher(4.0)
        for user, when in (
            (self.old, NOW - timedelta(days=30)),
            (self.mid, NOW - timedelta(days=10)),
            (self.new, NOW - timedelta(days=1)),
            (self.never, None),
        ):
            CustomUser.objects.filter(pk=user.pk).update(email_verified_at=when)

    def test_after_and_before_are_inclusive_and_drop_never(self):
        bound = (NOW - timedelta(days=10)).isoformat()
        self.assertEqual(
            self.kept(activated_after=bound), {self.mid.email, self.new.email}
        )
        self.assertEqual(
            self.kept(activated_before=bound), {self.old.email, self.mid.email}
        )

    def test_a_range_and_a_bare_date(self):
        self.assertEqual(
            self.kept(
                activated_after=(NOW - timedelta(days=20)).isoformat(),
                activated_before=(NOW - timedelta(days=5)).isoformat(),
            ),
            {self.mid.email},
        )
        self.assertNotIn(self.never.email, self.kept(activated_before="2100-01-01"))


class SortTests(UsageBase):
    def build(self):
        self.a = self.lead(score=10.0, used=3_000, left=1_000)
        self.b = self.lead(score=20.0, used=1_000, left=5_000)
        self.c = self.lead(score=30.0, used=2_000, left=3_000)
        self.nowallet = self.lead(score=40.0, left=None)
        self.teaching(self.a, 1, 1)
        self.teaching(self.b, 3, 3)
        self.teaching(self.c, 2, 2)
        for user, days in ((self.a, 10), (self.b, 30), (self.c, 20)):
            CustomUser.objects.filter(pk=user.pk).update(
                email_verified_at=NOW - timedelta(days=days)
            )
        CustomUser.objects.filter(pk=self.nowallet.pk).update(email_verified_at=None)

    def order(self, ordering):
        return self.emails(ordering=ordering)

    def test_credits_used_both_ways_empty_values_are_not_last_it_is_zero(self):
        self.build()
        # No wallet counts as 0 used, so it comes first ascending, last
        # descending: it is a value, not an empty one.
        n = self.nowallet.email
        self.assertEqual(
            self.order("credits_used"),
            [n, self.b.email, self.c.email, self.a.email],
        )
        self.assertEqual(
            self.order("-credits_used"),
            [self.a.email, self.c.email, self.b.email, n],
        )

    def test_credits_left_both_ways_no_wallet_last(self):
        self.build()
        n = self.nowallet.email
        self.assertEqual(
            self.order("credits_left"),
            [self.a.email, self.c.email, self.b.email, n],
        )
        self.assertEqual(
            self.order("-credits_left"),
            [self.b.email, self.c.email, self.a.email, n],
        )

    def test_papers_graded_and_assignments_created_both_ways(self):
        self.build()
        n = self.nowallet.email
        for key in ("papers_graded", "assignments_created"):
            with self.subTest(key):
                self.assertEqual(
                    self.order(key), [n, self.a.email, self.c.email, self.b.email]
                )
                self.assertEqual(
                    self.order(f"-{key}"),
                    [self.b.email, self.c.email, self.a.email, n],
                )

    def test_activated_at_both_ways_never_last(self):
        self.build()
        n = self.nowallet.email
        self.assertEqual(
            self.order("activated_at"),
            [self.b.email, self.c.email, self.a.email, n],
        )
        self.assertEqual(
            self.order("-activated_at"),
            [self.a.email, self.c.email, self.b.email, n],
        )

    def test_ties_go_by_user_id_both_ways(self):
        twins = [self.lead(score=float(i), used=2_000, left=0) for i in range(4)]
        by_id = [u.email for u in sorted(twins, key=lambda u: u.pk)]
        for ordering in ("credits_used", "-credits_used"):
            with self.subTest(ordering):
                self.assertEqual(self.order(ordering), by_id)

    def test_empty_ties_go_by_user_id_too(self):
        twins = [self.lead(score=float(i), left=None) for i in range(3)]
        by_id = [u.email for u in sorted(twins, key=lambda u: u.pk)]
        for ordering in ("credits_left", "-credits_left"):
            with self.subTest(ordering):
                self.assertEqual(self.order(ordering), by_id)

    def test_a_sort_and_a_filter_combine(self):
        self.build()
        self.assertEqual(
            self.emails(ordering="-credits_used", credits_left_min=0),
            [self.a.email, self.c.email, self.b.email],
        )

    def test_the_signals_order_and_the_default_order_are_as_they_were(self):
        four = self.teacher(10.0, **base.ALL4)
        one = self.teacher(90.0, has_hit_80_percent=True)
        zero = self.teacher(50.0)
        self.assertEqual(self.emails(), [one.email, zero.email, four.email])
        self.assertEqual(
            self.emails(ordering="signals"), [zero.email, one.email, four.email]
        )
        self.assertEqual(
            self.emails(ordering="-signals"), [four.email, one.email, zero.email]
        )
        for ignored in ("", "score", "signal_count", "credits", "-nonsense"):
            with self.subTest(ignored):
                self.assertEqual(
                    self.emails(ordering=ignored), [one.email, zero.email, four.email]
                )


class BadValuesTests(UsageBase):
    def test_a_bad_value_is_a_400_not_a_500(self):
        for name in (*APPROVED,):
            for bad in ("abc", "1.5"):
                with self.subTest(name=name, value=bad):
                    response = self.client.get(reverse(URL), {name: bad})
                    self.assertEqual(response.status_code, 400, response.content[:200])
        for name in ("activated_after", "activated_before"):
            with self.subTest(name=name):
                response = self.client.get(reverse(URL), {name: "yesterday"})
                self.assertEqual(response.status_code, 400)

    def test_empty_values_do_nothing(self):
        self.teacher(1.0)
        plain = self.client.get(reverse(URL))
        empty = self.client.get(
            reverse(URL), {**{n: "" for n in APPROVED}, "activated_after": ""}
        )
        self.assertEqual(empty.content, plain.content)


class SwaggerTests(UsageBase):
    def params(self):
        return {
            p["name"]: p
            for p in base.LeadOpenApiTests.operation(self).get("parameters", [])  # type: ignore[arg-type]
        }

    def test_each_filter_has_the_approved_text_without_the_super_admin_sentence(self):
        from users.filters import _SUPER_ADMIN_ONLY, FILTER_HELP

        params = self.params()
        for name, text in APPROVED.items():
            with self.subTest(name=name):
                self.assertEqual(params[name]["description"], text)
                self.assertEqual(params[name]["schema"]["type"], "integer")
        for name in ("activated_after", "activated_before"):
            with self.subTest(name=name):
                self.assertEqual(
                    params[name]["description"],
                    FILTER_HELP[name].replace(f" {_SUPER_ADMIN_ONLY}", ""),
                )

    def test_ordering_lists_every_value_and_ends_with_the_approved_text(self):
        ordering = self.params()["ordering"]
        allowed = set(
            ordering["schema"].get("items", ordering["schema"]).get("enum", [])
        )
        self.assertEqual(
            allowed,
            {"signals", "-signals", *SORTS, *(f"-{k}" for k in SORTS)},
        )
        self.assertTrue(
            ordering["description"].endswith(f" {ORDERING_ADDITION}"),
            ordering["description"],
        )
