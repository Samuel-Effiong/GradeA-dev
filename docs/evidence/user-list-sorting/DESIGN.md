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
