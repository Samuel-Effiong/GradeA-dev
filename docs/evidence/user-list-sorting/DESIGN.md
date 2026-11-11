# User list sorting and filters: survey and design (checkpoint 1)

Brief: SM -16, 2026-11-11 (owner-approved). GET /api/v1/users/ (CustomUserViewSet,
super admin only). Base: origin/staging aeb8b157e. No code yet.

## 1. What the survey found

S1. **The list today costs 309 queries for a 25-row page** (cold cache, 30
teachers with wallets; measured with a scratch test that was not kept). Per
row: 6 creditbucket, 2 usersubscription, 1 creditwallet, 1 settings, 1
licensesubscription, 1 schoolcreditallocation. All of it comes from the
existing nested `credit_wallet` (CreditWalletSerializer method fields) and
`settings`. **"One SQL query per page" can't hold unless that existing
payload is reworked.** See decision D1.

S2. **The list is cached** (UserCacheMixin, CACHE_TTL 300 s, keyed by the
requesting super admin's generation plus an md5 of the query params). Sign-ins
stamp last_login with a queryset update on purpose, so they bump no
generation ("last_login is in no cached payload", users/services.py:43).
Credits, usage and grading also bump nothing on the super admin's generation.
New values can therefore be up to 5 minutes stale. Every distinct
ordering/filter string gets its own key. See D2.

S3. **filter_queryset also runs in get_object()** (views.py comment at
filterset_class). An ordering key that needs an annotation would 500 on
GET /users/<id>/?ordering=credits_left if the annotation were list-only. So
every new filter and ordering adds its own `alias()`, and works on any action.
Only the serializer fields are list-only.

S4. **Current subscription** follows billing's own rule
(billing/access_control.py `_resolve_access_context`), in this order:
1. a license teacher allocation (SchoolCreditAllocation is_active,
   is_admin_allocation False, license_subscription__is_active);
2. a license admin allocation (the same with is_admin_allocation True);
3. an individual UserSubscription with is_active. The DB constraint
   one_active_subscription_per_user allows at most one.

The plan is `license_subscription.plan` or `subscription.plan`, a
SubscriptionPlan with tier, category and interval. I use this rule rather
than `CustomUser.get_active_subscription`, which misses school admins (the
resolver's own docstring says so).

S5. **Activation**: email_verified_at. Every activation door stamps it:
- verify (views.py:1166);
- reset_password (1628);
- the school admin invite (2193);
- classrooms/serializers.py:1110.

**Google sign-ups set it at creation** (views.py:2427), and an existing
unverified account gets it at its first Google sign-in (2506).

S6. **last_login**: stamped on every sign-in road (stamp_last_login;
UPDATE_LAST_LOGIN stays off). Today it is not in the list payload.

S7. **Assignments**: `Assignment.teacher` is marked "IN REVIEW FOR REMOVAL"
(SET_NULL). The owner is `course.teacher`, which is how the dashboard counts
them. "Papers graded" is StudentSubmission with `ai_graded_at` not null; that
is the dashboard's "total AI graded" (dashboard/views.py:516), written only by
the grading service (students/services.py:1113).

S8. **Credits**:
- billing/holds.py `spendable_credits(wallet)` = max(0,
  total_remaining_credits() - reserved_credits).
- total_remaining_credits = Sum(total_credits - used_credits) over buckets
  with expires_at null or > now.
- Usage, the dashboard's definition (dashboard/views.py:2041): Sum(amount) of
  CreditUsageLog with wallet__user=user and is_refunded False, all time.
- Display = value // CONVERSION_FACTOR (1000).

S9. No migration is needed. The indexes the subqueries need already exist:
- creditbucket.wallet_id (FK);
- usagelog (wallet, -created_at);
- usersubscription's partial unique index on (user) where active;
- allocation user_id (FK);
- assignment course_id (FK);
- course teacher_id (FK);
- submission assignment_id (FK).

## 2. Fields (read-only, LIST response only, super admin only)

The list uses a new serializer, `UserListSerializer(CustomUserSerializer)`,
via get_serializer_class when action == "list". Retrieve, me, patch, create,
lock and unlock keep CustomUserSerializer, unchanged.

| field | value | null when |
|---|---|---|
| last_login | users.last_login | never signed in |
| activated_at | users.email_verified_at | never activated |
| plan_tier | resolved plan's tier (S4) | no current plan |
| plan_category | resolved plan's category | no current plan |
| plan_interval | resolved plan's interval | no current plan |
| assignments_created | count of Assignment with course.teacher = user | never (0) |
| papers_graded | count of StudentSubmission with assignment.course.teacher = user and ai_graded_at not null | never (0) |
| credits_left | spendable_credits(wallet) // 1000 | no wallet (D4) |
| credits_used | Sum(amount, is_refunded False) // 1000 | never (0) |

Students and admins get 0 or null as the table says. All values come from
annotations on the page query (Subquery, Coalesce, Greatest); there is no
per-row Python.

## 3. Ordering keys (?ordering=, comma list, "-" for descending)

Existing: first_name, last_name, email, username (unchanged).

New: last_login, activated_at, plan_tier, plan_category, plan_interval,
assignments_created, papers_graded, credits_left, credits_used.

- Nulls go last in both directions (F(..).asc/desc(nulls_last=True)) for:
  last_login, activated_at, plan_*, and credits_left.
- The sort uses the raw value: credits are sorted on raw credits, not the
  display units.
- `id` is always appended last as a tiebreaker. Without an ordering the
  model's default (first_name, last_name, email) applies, plus id.
- Unknown values are ignored, as today.
- Implementation: a subclass of OrderingFilter with a key -> expression map.
  It adds the alias it needs, so the key works under get_object too (S3).

**plan_tier order** (D3): STANDARD < PRO < POWER < CUSTOM, with TRIAL and
BETA first (TRIAL < BETA < STANDARD ...). It is a Case/When rank, not
alphabetical. plan_category and plan_interval sort by their codes.

## 4. Filters (query params)

- last_login_after, last_login_before: ISO datetime; inclusive >= and <=.
  Never-signed-in users are excluded by either filter.
- activated_after, activated_before: the same, on email_verified_at.
- plan_tier: one PlanTier code. plan_category: INDIVIDUAL or LICENSE.
  plan_interval: MONTHLY, ANNUAL or NONE. Each is a ChoiceFilter on the
  resolved plan (S4).
- has_plan: true or false (optional; finds users with no current plan).
- A bad value gets DRF's existing 400 field error, with no new wording.

## 5. Queries and plans

- Page query: one SELECT on users_customuser with correlated subqueries:
  - 3 plan subqueries, each resolved by Coalesce over the 3 tracks;
  - 2 count subqueries;
  - 2 credit subqueries.
- Under ORDER BY of an annotation, Postgres evaluates that subquery for every
  user that passes the filters, then sorts, then applies LIMIT 25. Expected
  plan: SubPlan index scans on the FK indexes in S9 and a top-N heapsort. I'll
  record EXPLAIN ANALYZE on a seeded test DB (around 2000 users) in the
  evidence. No index is expected; I'll report it if the plan shows a seq scan.
- The COUNT query for pagination gains subqueries only when a plan or date
  filter is used.
- With D1 (A): the new fields and sorts add **0 queries**. A test pins this
  with assertNumQueries, comparing a 25-row page with and without
  ?ordering=credits_left against the base count.

## 6. Tests and plants (after the checkpoint)

Tests come first, with the red run declared. They are:
- each ordering key in both directions, including nulls last and the id
  tiebreak;
- each filter;
- the field values;
- SQL credits_left equals Python `spendable_credits() // 1000` over these
  fixtures: no buckets, a live bucket, an expired bucket, an
  expires_at-null bucket, an OVERAGE bucket, holds (reserved) above the
  balance (floor at 0), and several buckets;
- credits_used excludes refunded rows;
- the plan precedence: license teacher over license admin over individual;
- the query-count pin;
- the key-pin on retrieve, me, patch, lock and unlock: the exact key set,
  unchanged, for a super admin and for a teacher;
- non-super admins still get 403 on list;
- ordering keys on a retrieve URL don't 500;
- OpenAPI lists the ordering values and the new params.

Plants: one per key family (dates, plan, counts, credits_left, credits_used,
tiebreak) plus the retrieve/student guard.

Near run: users, billing and guards67.

## 7. Decisions for the SM

- **D1, the query rule** (S1).
  - A (recommended): the new values add 0 queries, and the existing 309 stays
    as it is. The N+1 in credit_wallet/settings goes into the hardening
    backlog.
  - B: also fix the existing N+1 now. That means select_related or prefetch
    for wallet, settings and buckets, and reworking CreditWalletSerializer's
    method fields for list. It touches an existing payload and billing code
    outside the six items.
- **D2, staleness** (S2).
  - A (recommended): accept up to 5 minutes (CACHE_TTL), as other list
    values already are.
  - B: don't cache the list. Every list call then pays the full cost.
- **D3, the plan_tier rank**: TRIAL < BETA < STANDARD < PRO < POWER < CUSTOM.
  Confirm, or give the owner's order.
- **D4, credits_left with no wallet**: null, sorted last (recommended,
  because "no wallet" is not "0 left"), or 0.
- **D5, ownership**: assignments and papers by course.teacher (recommended,
  S7). Co-teachers aren't counted.
- **D6, field names**: last_login, activated_at, plan_tier, plan_category,
  plan_interval, assignments_created, papers_graded, credits_left and
  credits_used are API keys, not user-facing text. Confirm.
- **D7, "key-pin sweep"**: I read it as a test pinning the exact key set of
  retrieve, me, patch, lock and unlock. If you mean an existing harness,
  name it.

## 8. Rulings (SM -16, 2026-11-11) and how they landed

- **D1: B (the owner changed it from A).** The existing per-row queries are
  fixed in this job.
  - Before: **309 queries** for a 25-row page (M0).
  - After: **13 queries, fixed whatever the page size** (M1). Pinned by
    QueryCountTests: 5 rows == 25 rows, cold cache, under every sort key and
    under filters.
  - The existing payload is pinned to the base commit's by
    users/tests_user_list_payload_pin.py, a snapshot captured before any
    change, and it passes unchanged.
  - How it's done:
    - list_queryset() adds select_related("settings", "credit_wallet") and
      prefetches the wallet's buckets, the active subscriptions (with
      plan), the active managed licenses and the active allocations (with
      license).
    - The 4 cycle-usage sums are SQL annotations.
    - ListCreditWalletSerializer (users/list_serializers.py) reads these.
      Each method falls back to CreditWalletSerializer's own when the data
      is absent.
  - No hardening backlog item is needed now.
- **D2: A.** The list may be up to CACHE_TTL (5 min) stale. The list cache
  key already hashes every query param; CacheKeyTests pin it, and plant
  Q-cache kills 14 tests.
- **D3:** TRIAL < BETA < STANDARD < PRO < POWER < CUSTOM, as a rank inside
  the plan lookup.
- **D4:** every null sorts last in both directions. credits_left is null
  with no wallet.
- **D5:** course.teacher. **Co-teachers are not counted**: an assignment
  counts only for its course's teacher, and Assignment.teacher (in review
  for removal) is ignored.
- **D6:** the nine keys and eight params are API keys only. They are
  documented in OpenAPI: the ordering enum and a description, and the
  filters come from django-filter.
- **D7:** no existing test pins a whole key set on these responses (NOTES).
  ListOnlyTests pins the nine keys' absence on retrieve, me, patch, lock,
  unlock and the sign-in payload, for a super admin and a teacher.

Found in build, not in the plan: the new filters are **no-ops for anyone
but a super admin**. filter_queryset runs under get_object(), so for a
teacher or school admin a plan or date filter on a retrieve would be a
200-vs-404 oracle on another account. Test ListOnlyTests
.test_the_list_filters_do_nothing_off_the_list_for_others; plant Q-gate.

Side finding, out of scope and unchanged: UserCacheMixin keys a retrieve by
instance id alone, so a filtered retrieve inside the TTL returns the cached,
unfiltered answer.

## 9. billing/serializers.py and the B1b merge

**CreditWalletSerializer is not changed.** The per-row work is overridden
in a subclass, users.list_serializers.ListCreditWalletSerializer, that only
the list uses. Every other caller behaves as before.

B1b adds balance_status, reserved_credits and spendable to
CreditWalletSerializer. After the merge, ListCreditWalletSerializer
inherits them, and any of them that queries per row will turn
QueryCountTests red (5 rows != 25 rows). That's the intended alarm. To keep
them at 0 extra queries:

- reserved_credits: a wallet column. It is already loaded by
  select_related("credit_wallet"), so it costs nothing if B1b declares it as
  a plain model field.
- spendable: override in ListCreditWalletSerializer as
  `max(0, self._live_remaining(obj, buckets) - obj.reserved_credits)` over
  the prefetched buckets, the same as billing.holds._free. Don't call
  billing.holds.spendable_credits(wallet): it re-reads the wallet with
  CreditWallet.objects.get(), one query per row.
- balance_status: override it to read the same in-memory spendable and
  totals. If it needs another relation (a plan or a threshold), add a
  Prefetch to users.user_list.list_queryset.
- The prefetched buckets are the full set for the wallet (no filter), so any
  live/expired split can be made in Python with the row's clock
  (`self._now`).

## 10. Performance (EXPLAIN ANALYZE, 2000 users, 25-row page)

| sort | time |
|---|---|
| default | 6.3 ms |
| -credits_left | 20.2 ms |
| -papers_graded | 10.4 ms |
| plan_tier | 14.2 ms (it was 235.8 before the rank moved into the plan lookup) |

No index or migration was needed. The plans are in explain_*.txt.

## 11. Backlog: the user retrieve cache ignores query params (SM -16: write up, do not fix here)

**What.** UserCacheMixin.get_cache_key("retrieve") builds
`<model>s:user_id__<requester>:instance_id__<pk>` plus the requester's cache
generation. The list key hashes the query params; the retrieve key does not.
filter_queryset still runs in get_object(), so a retrieve's params can change
the answer (a 404 when the filter excludes the row). Once a 200 for that pk is
cached, the same request with any params gets that 200 for up to CACHE_TTL
(300 s) or until the requester's generation is bumped. Only successful data
is cached, so a cached 404 never hides a row.

**Who can reach it.** Every authenticated user on GET /api/v1/users/<pk>/, for
the rows their own get_queryset allows: themselves; a teacher, their reachable
students (H-38); a school admin, their school; a super admin, everyone. The
filters involved are UserEnrollmentFilter (every role) and the new list
filters, which are no-ops unless the requester is a super admin.

**Can it return something a filter would hide?** Yes, but only to the same
caller, and only a row that caller can already fetch without the filter. The
requester's id is in the key and the requester's generation is in the
version, so no entry is shared across callers or across roles. A cached hit
therefore never shows a caller a row or field that their own unfiltered
retrieve would not show them. The effect is a wrong 200 where a 404 was
expected. That makes the filter-as-oracle weaker, not stronger. **No
cross-caller or cross-role leak, so no STOP.**

**Backlog fix (separate job).** Either hash the query params into the
retrieve key as the list key does, or have retrieve ignore filter params
entirely (filter_queryset is meant for lists). Add a test that runs the same
pk with and without a filter that excludes it, for each role.
