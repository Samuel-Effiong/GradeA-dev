# Signals sort and filter, beta: survey (checkpoint 1, no code)

Brief 4 (SM -16, queued 2026-11-11; confirmed unchanged by SM -31). The owner
approved signals sort and filter for BETA (2026-11-11), the same bug-fix
path as brief 3. Base origin/beta 44904774a (includes the user-list port
and the SM's staging merge 1724b835f).

## 1. What exists on beta today

All four rules read plain **columns** of `billing.BetaProfile` (OneToOne
`user.beta_profile`):

| flag | rule today | column(s) |
|---|---|---|
| at_80_percent | `has_hit_80_percent` | BooleanField |
| frequent_user | `distinct_login_days >= 8` | PositiveSmallIntegerField |
| active_last_week | `last_active_at >= now - 7 days`, null → False | DateTimeField null |
| is_power_grader | `credits_used_grading > credits_used_creation` | 2 × PositiveIntegerField |

`conversion_probability` is a non-null FloatField (default 0.0, indexed;
Meta.ordering = ["-conversion_probability"]). It can be null only through
the LEFT JOIN, for a user with no profile (on the user list).

The same four rules are written out by hand in **five** places in
billing/views.py, each calling `timezone.now()` itself:

1. `BetaAnalyticViewSet.intent_signals` (beta/intent-signals): the per-row
   "flags" dict. Queryset: `BetaProfile.objects.all()` filtered to
   user_type TEACHER. Order `-conversion_probability, -total_credits_used`.
   Paginated (StandardPageNumberPagination). Manual `?search=`. No filter
   backends, no cache.
2. `BetaAnaylicChartViewSet.intent_signal_distribution`: a Python loop that
   counts 1-4 (0 is not reported), over `get_teacher_beta_profiles()` (the
   same teacher cohort), in Meta order; user_ids are listed in that order.
   No cache.
3. `BetaAnalyticViewSet.intent_signal_detail`: the same "flags" dict for one
   user (no teacher filter).
4. `BetaAnalyticViewSet.summary`: the four rules as aggregate `Count(filter=Q)`
   values (active_recent, high_consumption, habit_formation,
   primary_graders), over **all** profiles, not only teachers.
5. `custom_ai_prompt` reuses `intent_signals(...).data[:10]` (see section 7).

Profiles are created only for teachers: the signup signal
(users/signals.py) and the two get_or_create sites in billing/services.py
are all gated on `CustomUser.is_beta_eligible()` (user_type == TEACHER).
Nothing removes a profile if the account's type changes later.

## 2. Query counts (M0, NOTES)

Beta base, cold cache, super admin client:

- intent-signals: page_size 5 = **9**, 25 = **9** (already fixed);
- intent-signal-distribution: 5 profiles = **8**, 30 = **8** (already
  fixed);
- user list: 5 = **13**, 25 = **13** (brief 3).

Target: unchanged on all three. Every new value is a SQL expression on the
query that already runs (the user list adds a LEFT JOIN to beta_profile, no
extra query), so the counts must stay 9 / 8 / 13 and 5 = 25.

## 3. Design

**C. One shared module, `billing/intent_signals.py`:**

- `flag_expressions(now, prefix="")` returns name → boolean SQL expression
  for the four flags, built from Q objects:
  - `has_hit_80_percent=True`;
  - `distinct_login_days__gte=8`;
  - `last_active_at__gte=now - 7d` (null → False, as in the Case);
  - `credits_used_grading__gt=F(credits_used_creation)`.
  `prefix="beta_profile__"` serves the user list.
- `signal_count(now, prefix="")` returns the sum of the four (0-4),
  Coalesced to 0, so no profile gives 0 with every flag False.
- `annotate_signals(queryset, now, prefix="")` adds the four flags and
  signal_count.
- `order_by_signals(queryset, descending, prefix="")` orders by the count
  (in the requested direction), then conversion_probability DESC NULLS LAST,
  then id (see Q-S2).
- `SignalFilterSet` fields, shared by both endpoints:
  - `signals_min` (ChoiceFilter 0-4);
  - `at_80_percent`, `frequent_user`, `active_last_week`, `is_power_grader`
    (BooleanFilter).
  Each filters on the annotations.
- `request_now(request)`: `now` taken once per request and kept on the
  request object, read through a module-level `clock()` that tests patch.
  The 7-day boundary test uses freezegun (1.5.5 is in requirements-dev and
  the venv).

**The existing payloads, kept byte-identical by reading the annotations
instead of recomputing:**

- intent_signals: same queryset, plus `annotate_signals`. The "flags" dict
  reads `p.at_80_percent` and the other annotations. With no params: same
  order, same pagination, same keys.
- intent_signal_distribution: the loop stays (keeping the user_ids order)
  but reads `profile.signal_count`. Moving the counting into SQL would risk
  the user_ids order, so I'm not doing that.
- intent_signal_detail and summary: see Q-S1.

**A. User list:**

- `list_queryset` adds `annotate_signals(prefix="beta_profile__")`.
- UserListSerializer gains 5 read-only keys: `signal_count`,
  `at_80_percent`, `frequent_user`, `active_last_week`, `is_power_grader`.
  Retrieve and the other non-list responses still have none of them (the
  existing D7 test, extended).
- Ordering key `signals` in SORT_KEYS: a composite handled in
  apply_ordering.
- The 5 filters join LIST_FILTERS through `filter_list_value`, so they do
  nothing unless the requester is a super admin (the get_object() oracle
  gate from brief 2).

**B. intent-signals:**

- Optional `?ordering=signals|-signals`. Anything else is ignored, as
  unknown params are today. No param leaves the existing default order.
- The 5 filters through SignalFilterSet applied in the action. A bad value
  is a 400 with django-filter's built-in message (no new wording), the same
  as the user list.
- OpenAPI: the new params are documented on both endpoints (API names only).

**Cache.**
- User list: UserCacheMixin hashes every query param into the list key, so
  the new params vary it with no code change. Tests per new param, plus the
  plant "cache key ignores the params".
- intent-signals and the distribution have **no cache on beta**, so there
  is no key to vary or to plant there (see Q-S5).

## 4. Questions for the SM

- **Q-S1, scope of "one shared function".** The brief names A, B, the flags
  dict and the distribution. The same rules also live in
  `intent_signal_detail` (the identical flags dict) and `summary` (the four
  aggregate counts). Proposal: route both through the shared module too,
  pinned byte-identical, so no surface can disagree. summary keeps its
  all-profiles cohort; changing that would change a number.
- **Q-S2, directions.** I read "`signals`: count, then conversion_probability
  (nulls last), then id; `-signals` reverses the count" as:
  - `signals` = count ascending (DRF convention), `-signals` = count
    descending;
  - the tie-break is ALWAYS conversion_probability DESC NULLS LAST (best
    lead first), then id ascending, in both directions.
  Please confirm, or say whether `signals` should mean most signals first.
- **Q-S3, non-teachers on the user list.** intent-signals and the chart
  count teachers only. Proposal: on the user list, signals come from the
  profile only when user_type is TEACHER; anyone else gets 0, every flag
  False and a null score, so the list agrees with the Credit Usage page.
  The alternative is to read any existing profile.
- **Q-S4, the 7-day boundary.** Keep today's inclusive rule (`>= now - 7
  days`). Tests: exactly now - 7 days → True, 1 µs earlier → False, null →
  False.
- **Q-S5, cache plant.** Since only the user list caches, the "ignore the
  param in the cache key" plant and its tests are on the user list. OK?

## 5. Plants (proposed; one per run, kill declared first)

1. Drop one flag (is_power_grader) from the shared signal_count.
2. Break the tie-break (conversion_probability ascending).
3. The user-list cache key ignores the params (UserCacheMixin).
4. The intent-signals default order changes when no param is given (the
   view always applies order_by_signals). The pin must catch it.
5. The user-list filter gate removed (the signals filters apply for a
   non-super admin too).
6. The 7-day boundary made exclusive (`gt`).

## 6. Staging-side differences (for the beta-into-staging merge)

- billing/views.py: **identical** on beta and staging.
- billing/models.py: staging adds only B1a/B1b models and fields. BetaProfile
  is the same.
- users/user_list.py: differs only in the credits hunk (see
  docs/evidence/user-list-beta/DESIGN.md section 8). The signals code will
  sit away from credits_left_raw/credits_left, so the merge should not
  conflict there.
- billing/intent_signals.py: new on both sides after the merge.

## 7. Side findings (not in scope, not fixed)

- `custom_ai_prompt`: `self.intent_signals(...).data[:10]` takes a slice of
  a paginated response's dict, which always raises. The bare `except` turns
  that into `intent_leads = []`, so the AI prompt never sees leads.
  Unchanged by this work.
- summary counts the four rules over all profiles, while intent-signals and
  the chart count teachers only (Q-S1 keeps that as is).
- intent-signals' OpenAPI example "flags" shows 3 keys (no frequent_user).
  The real payload has 4.

## 8. Plan and time

Tests first (pins for intent-signals default, distribution, detail and
summary taken on the base before any change; then values, ordering,
filters, the boundary, query counts, cache), red, product, green, the 6
plants, near (users + billing + guards60), end checkpoint.
Re-estimate: **5-6 h** after the rulings, against the brief's half day.
That breaks down as tests and pins 2 h, product 1.5 h, plants 45 min, near
15 min, evidence and checkpoint 45 min. The extra comes from Q-S1's two
surfaces and the 4 pins.
