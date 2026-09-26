# Evidence — /subscription/me always returns 200 with a status field

Worktree: `GAP-subscription-me-status`, branch `task/subscription-me-status`,
off beta `969dc89`. This evidence at commit `66b8b4c`.

## 1. Background

User-approved rework of `GET /subscription/me`
(`SubscriptionManagementViewSet.get_my_subscription`, `billing/views.py`):
today it 404s whenever `resolve_user_billing_context` finds no active
subscription on either track. The frontend must instead always get a
200 and read a new `status` field: `ACTIVE`, `EXPIRED`, or `NONE`.

## 2. The three states

1. **ACTIVE** — unchanged existing behavior for all three
   `subscription_source` tracks (INDIVIDUAL / LICENSE_TEACHER /
   LICENSE_ADMIN). Each of the three response serializers now also
   carries `"status": "ACTIVE"`.
2. **EXPIRED** — INDIVIDUAL track only, per the assigned scope.
   Reached only when `resolve_user_billing_context` returns
   `source=None` (i.e. no active UserSubscription AND no active
   license context — an active license context always wins over a
   stale inactive individual row, by the resolver's documented
   first-match-wins order). If the caller has a most-recent inactive
   `UserSubscription` (`is_active=False`, ordered by
   `-billing_cycle_end`), it's serialized with the same
   `MySubscriptionSerializer`, `status="EXPIRED"`, with
   `next_renewal_date`/`days_until_renewal` forced to `null` — the old
   `get_days_until_renewal` did `obj.billing_cycle_end - now` with no
   floor, which for a lapsed cycle is negative and would otherwise clamp
   to a misleading `0` ("renews today"). No new "reason" field was
   added; `stripe_status` and the existing `cancellation` dict
   (`cancelled_at`/`has_pending_cancellation`/
   `cancellation_effective_date`/`cancellation_message`) already carry
   why it lapsed.
3. **NONE** — no `UserSubscription` has ever existed for this user and
   no license context either. Can't serialize a nonexistent model
   instance, so `_none_subscription_response()` (`billing/views.py`)
   returns a flat dict matching `MySubscriptionSerializer`'s field
   names, with everything `null`/`false`/`0` except `"status": "NONE"`.

Scope note honored: LICENSE_TEACHER/LICENSE_ADMIN EXPIRED handling was
explicitly NOT designed in this pass (SM's instruction was to stop and
ask if that case came up) — it didn't: the license-track precedence
test below confirms an active license context is resolved before the
individual-track EXPIRED lookup is ever reached, so no license-side
"inactive but existed before" case needed a design decision here.

## 3. Implementation (billing/serializers.py, billing/views.py)

- `MySubscriptionSerializer` — added `status`
  (`SerializerMethodField`, reads `self.context.get("status", "ACTIVE")`).
  Converted `next_renewal_date` from a plain
  `DateTimeField(source="billing_cycle_end")` to a
  `SerializerMethodField` so it can also null out for EXPIRED; added the
  same guard to `get_days_until_renewal`.
- `MyLicenseTeacherSubscriptionSerializer` /
  `MyLicenseAdminSubscriptionSerializer` — added `status =
  serializers.CharField(default="ACTIVE")`, same pattern already used
  for their existing `subscription_source` constant field. Always
  ACTIVE: both are only ever reached via an active
  allocation/LicenseSubscription (see scope note above).
- `get_my_subscription` — INDIVIDUAL path now passes
  `context={"request": request, "status": "ACTIVE"}`. When
  `context.source` is `None`, looks up the most-recent inactive
  `UserSubscription` for EXPIRED before falling back to
  `_none_subscription_response()` for NONE.
- `@extend_schema`'s 404 doc example (which was already wrong even for
  the pre-existing code — it showed `{"status": "inactive", "message":
  ...}` while the real 404 body was `{"detail": "..."}`) is replaced
  entirely with a 200 response description + three examples (ACTIVE /
  EXPIRED / NONE), since there is no 404 anymore.

## 4. Existing-contract sweep

Grepped the whole repo for anything asserting the old 404 behavior on
this specific endpoint (as opposed to the *different* actions `resume`/
`cancel`, which independently 404 via `get_object()` on their own
`is_active=True` queryset and are untouched by this change — see
`test_no_active_subscription`/`test_no_active_subscription_returns_404`
in `test_subscription_reactivation.py`/`test_subscription_cancel.py`,
both target `reverse("subscription-resume")`/`reverse("subscription-
cancel")`, not `subscription-get-my-subscription`).

Only one existing test hits `/subscription/me` itself:
`test_free_plan_activation_security.py::
test_a_licensed_teacher_cannot_activate_the_license_plan_from_me`. It
asserts `200` + `subscription_source == "LICENSE_TEACHER"` — both still
true; it doesn't check for the absence of `status`, so it needed no
change. Ran it explicitly below along with the other subscription
suites this endpoint's serializers/models touch — no update needed,
no regression.

No frontend-facing contract doc in this repo asserts the 404 (checked
`billing/stripe_view_schemas.py`'s prose reference to the endpoint —
descriptive only, no schema assertion).

## 5. Test suite (billing/tests/test_subscription_me_status.py)

10 new tests:

- `ActiveStatusTests` (3): individual, license-teacher, license-admin —
  each asserts `status == "ACTIVE"` alongside the pre-existing
  `subscription_type`/`subscription_source` checks.
- `ExpiredStatusTests` (5): lapsed individual sub reports `EXPIRED`;
  `next_renewal_date`/`days_until_renewal` are `null` instead of a
  clamped `0`; `stripe_status`/`cancellation.cancelled_at` still carry
  the lapse reason; the most-recent (by `billing_cycle_end`) of two
  inactive subs is the one returned, not the oldest; an ACTIVE license
  allocation on the same user takes precedence over an inactive
  individual row (EXPIRED never wrongly surfaces for an actual live
  license teacher).
- `NoneStatusTests` (2): a user who never had any subscription gets
  `NONE`; the NONE payload's key set matches the ACTIVE payload's key
  set exactly (same shape, safe defaults) via the same user before/
  after deleting their subscription.

```
python manage.py test billing.tests.test_subscription_me_status --settings=settings_worktree -v 2
```

**Ran 10 tests. OK.**

Plus the existing suites this endpoint/its models touch, to confirm no
regression (`test_free_plan_activation_security`,
`test_subscription_reactivation`, `test_subscription_cancel`,
`test_subscription_upgrade`, `test_cancellation_visibility`,
`test_license_cancellation`):

```
python manage.py test billing.tests.test_free_plan_activation_security \
  billing.tests.test_subscription_reactivation billing.tests.test_subscription_cancel \
  billing.tests.test_subscription_upgrade billing.tests.test_cancellation_visibility \
  billing.tests.test_license_cancellation --settings=settings_worktree -v 1
```

**Ran 224 tests. OK.**

## 6. Mutation testing

7 mutants (`mutate.py.txt`) against the 7 sites this change added or
touched, across `billing/views.py` and `billing/serializers.py`.
Applied one at a time from a collision-safe backup copy, restore
verified by md5 against the pre-mutation original after every mutant
(never `git checkout`), confirmed both programmatically
(`restored_md5_ok`) and by `git status --short` showing only the
untracked evidence directory throughout.

**Result: 7 / 7 KILLED**, first pass, no fixes needed. Re-ran the full
battery a second time against the committed tree (`66b8b4c`, after
black's pre-commit reformat) to confirm the mutant patterns still match
post-format — also 7/7.

| id | protection weakened | expected test |
|----|---|---|
| STATUS | `get_status` ignores context, always reports ACTIVE | `test_lapsed_individual_subscription_reports_status_expired` |
| NEXTREN | `next_renewal_date` EXPIRED guard removed | `test_expired_nulls_out_renewal_fields_instead_of_negative_days` |
| DAYSREN | `days_until_renewal` EXPIRED guard removed | `test_expired_nulls_out_renewal_fields_instead_of_negative_days` |
| FILTER | EXPIRED lookup filters `is_active=True` instead of `False` | `test_lapsed_individual_subscription_reports_status_expired` |
| ORDER | EXPIRED lookup ordering reversed (oldest lapsed sub wins instead of newest) | `test_most_recent_inactive_subscription_is_the_one_returned` |
| NONEVAL | `_none_subscription_response` reports ACTIVE instead of NONE | `test_user_who_never_had_a_subscription_reports_status_none` |
| BRANCH | EXPIRED branch disabled, every no-active-context caller falls straight to NONE | `test_lapsed_individual_subscription_reports_status_expired` |

Raw log: `mutation_log.jsonl`. Full results incl. md5s:
`mutation_results.json`.

## 7. Full regression

Isolated env `audit-lead-subme` (private Postgres 16 + Redis),
`settings_worktree.py` -> `test_subscription_me_status`. Log redirected
to a file, never piped through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --noinput
```

**Ran 4631 tests. OK (skipped=26).**

Full tail: `full_regression_summary.txt`.

## 8. Tree state

`git status --short` throughout steps 5-7: clean except this untracked
evidence directory. No mutation, test run, or regression left any
tracked file modified.
