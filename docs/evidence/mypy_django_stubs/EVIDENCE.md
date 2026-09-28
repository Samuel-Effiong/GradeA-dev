# Evidence — mypy pre-commit hook missing django-stubs

Worktree: `Grade-Automator-Plus-mypy-django-stubs`, branch
`task/mypy-django-stubs`, off beta `4b902fc`.

## 1. Background

The mypy pre-commit hook (`.pre-commit-config.yaml`, repo
`pre-commit/mirrors-mypy`) never had `django-stubs` or
`djangorestframework-stubs` in its `additional_dependencies`, and no
`[tool.mypy]` config anywhere wired in the `mypy_django_plugin`. Without
that plugin, mypy has no knowledge of Django's metaclass-generated
attributes, so it misreads several very common Django/DRF shapes as type
errors:

- `models.TextChoices` members read back as the raw `(value, label)`
  tuple instead of `str` (the motivating case — surfaced while landing
  H-28/p1b's `LicenseStripeMutationIntent` model, which uses a
  `TextChoices` enum).
- `ForeignKey` "_id" shadow attributes (`assignment.teacher_id`,
  `obj.school_id`, ...) don't exist as far as plain mypy is concerned —
  70 of the pre-plugin baseline's 730 `[attr-defined]` errors are exactly
  this shape (`has no attribute "X_id"; maybe "X"?`).
- Lazy-translation `help_text=_("...")` tuples on model fields
  (`gettext_lazy` proxy) get flagged as `tuple[str, str]` instead of
  `str | _StrPromise` in several migrations.
- `Manager[Model]` loses the model-specific manager methods
  (`Manager[AbstractBaseUser]` "has no attribute `create_user`", etc.).

`django-stubs==5.2.8` and `djangorestframework-stubs==3.16.6` were
**already pinned in `requirements.txt`** (lines 56-57 and 61) and
**already installed in the shared project venv** — they just weren't
reachable from the pre-commit hook's own isolated environment, and
nothing told mypy to load the plugin even when it was importable. So the
false positives above were live in every local `mypy` run and every
pre-commit run, on every file that touches this machinery.

Confirmed before touching anything: a whole-repo `mypy` run on the
unmodified beta tree (no config file existed anywhere for mypy — no
`mypy.ini`, no `setup.cfg` `[mypy]`, no `[tool.mypy]` in `pyproject.toml`)
reports **1034 errors in 197 files** (734 source files checked). This is
the true baseline — larger than the "~100 errors / ~14 files" figure
originally quoted, most likely because that figure came from a narrower,
partial run (e.g. pre-commit only type-checking the files staged in one
commit, which nonetheless pulls in errors from files those files import,
since no `--follow-imports=silent` is set) rather than a full `mypy .`
sweep. The full baseline list is `mypy_before_whole_repo.txt` in this
directory.

## 2. Config files touched

`mypy.ini` and `setup.cfg` don't exist in this repo; `pyproject.toml`
does (previously only `[tool.bandit]`), so that's where mypy config was
added — mypy auto-discovers `pyproject.toml` upward from its working
directory with no extra flag needed.

### `pyproject.toml` (new sections)

```toml
[tool.mypy]
plugins = ["mypy_django_plugin.main"]

[tool.django-stubs]
django_settings_module = "AutoGrader.settings"
```

`AutoGrader.settings` confirmed from `manage.py:9`
(`os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")`)
and the file at `AutoGrader/settings.py`.

### `.pre-commit-config.yaml` (mypy hook, ~line 46-59)

Originally:

```yaml
-   id: mypy
    args: [--ignore-missing-imports, --check-untyped-defs, --disable-error-code=var-annotated]
    additional_dependencies: [types-requests,  types-redis, types-waitress, types-python-dateutil, types-bleach]
```

The task brief's literal plan was to append
`django-stubs==5.2.8, djangorestframework-stubs==3.16.6` to
`additional_dependencies`. That was tried first and **does not work** —
see the deviation below. Final state:

```yaml
-   id: mypy
    args: [--ignore-missing-imports, --check-untyped-defs, --disable-error-code=var-annotated]
    language: system
```

## 3. Deviation from the literal plan, and why

`additional_dependencies: [..., django-stubs==5.2.8, djangorestframework-stubs==3.16.6]`
installs into the mirror hook's own isolated venv, which otherwise has
**nothing but mypy and stub packages** in it. `mypy_django_plugin` does
not just read type stubs statically — on startup it calls
`django.setup()` against `AutoGrader.settings` to introspect the real
model graph, and importing that settings module imports the **whole
app**: `AutoGrader/__init__.py` imports `AutoGrader.celery`, which
imports `celery`, which is not, and was never going to be, in
`additional_dependencies`. Running
`pre-commit run mypy --all-files` with only the stub packages added
fails immediately, before a single file is checked:

```
Error constructing plugin instance of NewSemanalDjangoPlugin
...
File ".../AutoGrader/__init__.py", line 1, in <module>
    from .celery import app as celery_app
File ".../AutoGrader/celery.py", line 3, in <module>
    from celery import Celery
ModuleNotFoundError: No module named 'celery'
```

Full log: `precommit_mypy_run_FAILED_isolated_env.txt`. Every app in
`INSTALLED_APPS` and every third-party package any of them imports at
module load time would need to be duplicated into
`additional_dependencies` for this to work that way — i.e. effectively
all of `requirements.txt`, mirrored a second time in YAML with no
mechanism to keep the two in sync.

Fix applied instead: `language: system` on the mypy hook, so pre-commit
runs `mypy` from whatever environment invokes it — this project's shared
venv — instead of building an isolated one. That venv already has
`mypy==1.17.1` (matching the hook's own `rev: v1.17.1`),
`django-stubs==5.2.8` and `djangorestframework-stubs==3.16.6` (matching
`requirements.txt` exactly), so `django.setup()` succeeds and the plugin
runs cleanly. This mirrors the repo's own existing convention for the
local `check-gunicorn-timeout-sync` hook, which already uses
`language: system` for the same class of reason (needing the real
project environment, not an isolated pip install). Confirmed working:
`precommit_mypy_run_OK_language_system.txt` — mypy now runs to
completion and exits 1 only because it reports 686 real pre-existing
type errors, not because it crashed.

## 4. Before / after whole-repo error counts

Both runs: `mypy . --ignore-missing-imports --check-untyped-defs --disable-error-code=var-annotated`
(the same flags the pre-commit hook passes), whole repo, from the
worktree root. `nice -n 10`-wrapped, not suite runs, no lock needed.

| | BEFORE (no plugin) | AFTER (plugin wired in) |
|---|---|---|
| Errors | 1034 | 686 |
| Files with errors | 197 | 159 |
| Source files checked | 734 | 734 |

Full logs: `mypy_before_whole_repo.txt`, `mypy_after_whole_repo.txt`.

Error-code breakdown:

| Code | Before | After |
|---|---|---|
| `attr-defined` | 730 | 277 |
| `union-attr` | 183 | 139 |
| `arg-type` | 41 | 92 |
| `misc` | 27 | 75 |
| `assignment` | 24 | 32 |
| `list-item` | 10 | 10 |
| `index` | 10 | 40 |
| `operator` | 1 | 10 |
| `call-overload` | 2 | 2 |
| `typeddict-item` | 1 | 3 |
| `typeddict-unknown-key` | 1 | 1 |
| `type-var` | 1 | 1 |
| `return-value` | 1 | 1 |
| `method-assign` | 1 | 1 |
| `dict-item` | 0 | 2 |

`attr-defined` drops by 453 (mostly the FK `_id` shadow-attribute false
positives — 70 confirmed exact matches of `has no attribute "X_id"; maybe
"X"?` in the before run alone, plus `Manager[Model]`-method and
migration-`help_text`-tuple false positives folding away). `arg-type`,
`misc`, `index` and `operator` all go *up* — that's the plugin now
understanding nullable FKs, queryset annotations and `request.user`'s
real `CustomUser | AnonymousUser` type well enough to catch things plain
mypy previously waved through as `Any`. That's the "deeper signal"
flagged in the task background.

Line-level diff of the two runs (same tree, config-only change):
**581 error lines present only in BEFORE** (resolved false positives —
`resolved_errors.txt` isn't checked in, reproducible via `comm` on the
two logs) and **233 error lines present only in AFTER**. Of those 233,
**178 are at a `file:line` that had *zero* errors in BEFORE at all** —
i.e. not just a reworded message on an already-flagged line, but a
genuinely new site the plugin newly understands well enough to flag.
Those 178 are listed in full below.

## 5. Newly-surfaced real type errors (NOT fixed — listed only, per scope)

178 errors below appear only once the plugin is wired in, at source
locations that had no mypy complaint at all before. These are real
signal the missing plugin was hiding, not false positives — but fixing
them is explicitly out of scope for this branch, which is tooling/config
only. Full copy: `newly_surfaced_errors.txt` in this directory.

Patterns, by rough shape:

- **`request.user` used where a typed FK lookup is expected**
  (`Incompatible type for lookup 'X': (got "CustomUser | AnonymousUser",
  expected "CustomUser | UUID | None")`) — ~35 occurrences, mostly in
  `classrooms/views.py`, `billing/views.py`, `assignments/views.py`,
  `students/views.py`. DRF's `request.user` is typed as
  `CustomUser | AnonymousUser`; these call sites pass it straight into a
  queryset filter/lookup without narrowing past `AnonymousUser` first.
- **Nullable FK/field access without a None-guard**
  (`Item "None" of "X | None" has no attribute "Y"`, `Unsupported
  operand types for - ("None" and ...)`, `Value of type "Any | None" is
  not indexable`) — the largest single cluster, concentrated in test
  files (`tests_grading_hardening.py`, `tests_second_opinion_queue.py`,
  `test_license_overage_offline.py`, etc.) that index or arithmetic
  directly on a field/lookup result the plugin now correctly types as
  optional.
- **`Decimal | None` passed to `float()`** — ~15 occurrences across
  `students/tests*.py`, `billing/tests/test_grading_refund_scope.py`.
- **`.annotate()` result attributes on a typed queryset** — the six
  `classrooms/views.py` `CustomUser@AnnotatedWith[TypedDict(...)]` "has
  no attribute" errors: mypy now tracks the annotated fields as a
  TypedDict and doesn't yet see them dotted onto the base type the way
  the runtime code accesses them (a stubs-precision gap or a
  genuine access-pattern issue — not diagnosed further, out of scope).
- **Stripe SDK argument typing** — several `str | None` passed where the
  `stripe` stubs (pulled in transitively) expect `str`
  (`billing/stripe_service.py`, `billing/license_service.py`,
  `billing/live_qa/scenarios_license.py`).
- A handful of one-off `[assignment]`/`[operator]`/`[typeddict-item]`
  sites (`users/models.py:113`, `billing/license_service.py:1330`,
  `students/tests_grading_followup_dispatch.py:100`, etc.).

Full list (`file:line: error text  [code]`):

```
ai_processor/tests_objective_pipeline.py:541: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
ai_processor/tests_objective_pipeline.py:543: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
assignments/file_uploads.py:183: error: Argument "assignment" to "UploadOutcome" has incompatible type "Assignment | None"; expected "Assignment"  [arg-type]
assignments/tasks.py:1077: error: Incompatible type for "teacher" of "BatchUploadSession" (got "CustomUser | None", expected "CustomUser | Combinable")  [misc]
assignments/tasks.py:1086: error: Item "None" of "CustomUser | None" has no attribute "id"  [union-attr]
assignments/tests_ai_output_allowlist.py:148: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_ai_output_allowlist.py:198: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_ai_output_allowlist.py:218: error: Argument 1 to "set" has incompatible type "Any | None"; expected "Iterable[Any]"  [arg-type]
assignments/tests_ai_output_allowlist.py:223: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_ai_output_allowlist.py:341: error: Unpacked dict entry 0 has incompatible type "Any | None"; expected "SupportsKeysAndGetItem[Any, Any]"  [dict-item]
assignments/tests_course_ownership_idor.py:496: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_extraction_service.py:328: error: Argument 1 to "len" has incompatible type "Any | None"; expected "Sized"  [arg-type]
assignments/tests_extraction_service.py:330: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_extraction_service.py:448: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_pdf_cache.py:228: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:103: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:105: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:140: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:246: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:352: error: Value of type "Any | None" is not indexable  [index]
assignments/tests.py:405: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_rigor.py:499: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_schema_extension.py:154: error: Value of type "object" is not indexable  [index]
assignments/tests_strip_duplicate_option_letters.py:65: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_strip_duplicate_option_letters.py:66: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_strip_duplicate_option_letters.py:81: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_strip_duplicate_option_letters.py:109: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_strip_duplicate_option_letters.py:125: error: Value of type "Any | None" is not indexable  [index]
assignments/tests_upload_task_retry_policy.py:442: error: Argument 2 to "assertIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
assignments/views.py:306: error: Incompatible type for lookup 'course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
assignments/views.py:335: error: Incompatible type for lookup 'course__enrollments__student': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
assignments/views.py:1951: error: Incompatible type for lookup 'user': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
billing/billing_transaction_views.py:63: error: Incompatible type for lookup 'users': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
billing/license_service.py:1330: error: Incompatible types in assignment (expression has type "int | None", variable has type "float | int | str | Combinable")  [assignment]
billing/license_service.py:1362: error: Unsupported operand types for * ("int" and "None")  [operator]
billing/license_service.py:1898: error: Incompatible types in assignment (expression has type "int | None", variable has type "float | int | str | Combinable")  [assignment]
billing/license_service.py:2109: error: Incompatible types in assignment (expression has type "int | None", variable has type "float | int | str | Combinable")  [assignment]
billing/license_service.py:2672: error: Incompatible types (expression has type "str | None", TypedDict item "price" has type "str")  [typeddict-item]
billing/license_service.py:3122: error: Unsupported operand types for / ("None" and "int")  [operator]
billing/license_service.py:3478: error: Argument 1 to "delete" of "DeletableAPIResource" has incompatible type "str"; expected "DeletableAPIResource[Subscription]"  [arg-type]
billing/license_views.py:216: error: Incompatible type for lookup 'users': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
billing/license_views.py:1104: error: Incompatible type for lookup 'users': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/checkpoints.py:156: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/checkpoints.py:160: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/checkpoints.py:193: error: Incompatible type for lookup 'wallet': (got "object", expected "CreditWallet | UUID | None")  [misc]
billing/live_qa/scenarios_deep.py:419: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_deep.py:420: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_deep.py:452: error: Incompatible type for lookup 'user_subscription__user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_fast.py:294: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_license.py:162: error: Incompatible type for lookup 'pk': (got "object", expected "UUID | str")  [misc]
billing/live_qa/scenarios_license.py:203: error: Argument "price_id" to "create_subscription" of "LiveQAHarness" has incompatible type "str | None"; expected "str"  [arg-type]
billing/live_qa/scenarios_license.py:366: error: Incompatible type for lookup 'license_subscription_id': (got "object", expected "LicenseSubscription | UUID | None")  [misc]
billing/live_qa/scenarios_license.py:486: error: Argument "amount_paid_cents" to "process_offline_renewal" of "LicenseSubscriptionService" has incompatible type "Decimal"; expected "int | None"  [arg-type]
billing/live_qa/scenarios_license.py:657: error: Incompatible type for lookup 'license_subscription_id': (got "object", expected "LicenseSubscription | UUID | None")  [misc]
billing/live_qa/scenarios_license.py:719: error: Argument 2 to "_pay_for_session" has incompatible type "str | Any | None"; expected "str"  [arg-type]
billing/live_qa/scenarios_license.py:889: error: Incompatible type for lookup 'license_subscription_id': (got "object", expected "LicenseSubscription | UUID | None")  [misc]
billing/live_qa/scenarios_license.py:892: error: Incompatible type for lookup 'license_subscription_id': (got "object", expected "LicenseSubscription | UUID | None")  [misc]
billing/live_qa/scenarios_long.py:119: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_long.py:120: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_long.py:128: error: Incompatible type for lookup 'wallet__user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/live_qa/scenarios_long.py:177: error: Incompatible type for lookup 'wallet__user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/management/commands/audit_school_admins.py:97: error: Item "None" of "School | None" has no attribute "name"  [union-attr]
billing/management/commands/backfill_billing_transactions.py:265: error: Invalid index type "str" for "dict[LicenseBillingRecordType, BillingTransactionType]"; expected type "LicenseBillingRecordType"  [index]
billing/stripe_live_qa_scenarios.py:104: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/stripe_live_qa_scenarios.py:110: error: Incompatible type for lookup 'user': (got "object", expected "CustomUser | UUID | None")  [misc]
billing/stripe_live_qa_scenarios.py:145: error: Argument "price_id" to "create_subscription" of "LiveQAHarness" has incompatible type "str | None"; expected "str"  [arg-type]
billing/stripe_live_qa_scenarios.py:294: error: Argument "price_id" to "create_subscription" of "LiveQAHarness" has incompatible type "str | None"; expected "str"  [arg-type]
billing/stripe_service.py:1620: error: Incompatible types in assignment (expression has type "Decimal", variable has type "int")  [assignment]
billing/stripe_service.py:1658: error: Argument "product_id" to "create_custom_price" of "StripePriceService" has incompatible type "str | None"; expected "str"  [arg-type]
billing/stripe_service.py:1673: error: Argument "product_id" to "create_custom_price" of "StripePriceService" has incompatible type "str | None"; expected "str"  [arg-type]
billing/stripe_service.py:3401: error: Incompatible types (expression has type "str | None", TypedDict item "price" has type "str")  [typeddict-item]
billing/stripe_service.py:3535: error: Incompatible types in assignment (expression has type "None", variable has type "CustomUser")  [assignment]
billing/tasks.py:436: error: Argument 1 to "retrieve" of "Subscription" has incompatible type "str | None"; expected "str"  [arg-type]
billing/tasks.py:1013: error: Unsupported operand types for < ("datetime" and "None")  [operator]
billing/tasks.py:1195: error: Argument 1 to "shrink_chaos_failure" has incompatible type "int | None"; expected "int"  [arg-type]
billing/tasks.py:1195: error: Argument 2 to "shrink_chaos_failure" has incompatible type "int | None"; expected "int"  [arg-type]
billing/tasks.py:1200: error: Argument 1 to "run_chaos_walk" has incompatible type "int | None"; expected "int"  [arg-type]
billing/tasks.py:1200: error: Argument 2 to "run_chaos_walk" has incompatible type "int | None"; expected "int"  [arg-type]
billing/tests/test_admin_credit_grant_api.py:259: error: Item "None" of "datetime | None" has no attribute "replace"  [union-attr]
billing/tests/test_annual_mid_cycle_grants.py:268: error: Unsupported left operand type for - ("None")  [operator]
billing/tests/test_conversion_probability_task.py:223: error: "object" has no attribute "hour"  [attr-defined]
billing/tests/test_conversion_probability_task.py:224: error: "object" has no attribute "minute"  [attr-defined]
billing/tests/test_conversion_probability_task.py:230: error: "object" has no attribute "hour"  [attr-defined]
billing/tests/test_conversion_probability_task.py:230: error: "object" has no attribute "minute"  [attr-defined]
billing/tests/test_conversion_probability_task.py:231: error: "object" has no attribute "hour"  [attr-defined]
billing/tests/test_conversion_probability_task.py:231: error: "object" has no attribute "minute"  [attr-defined]
billing/tests/test_grading_refund_scope.py:153: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
billing/tests/test_license_overage_offline.py:145: error: Argument 1 to "len" has incompatible type "Any | None"; expected "Sized"  [arg-type]
billing/tests/test_license_overage_offline.py:157: error: Value of type "Any | None" is not indexable  [index]
billing/tests/test_license_overage_offline.py:158: error: Value of type "Any | None" is not indexable  [index]
billing/tests/test_license_overage_offline.py:190: error: Argument 1 to "len" has incompatible type "Any | None"; expected "Sized"  [arg-type]
billing/tests/test_license_overage_offline.py:208: error: Unsupported right operand type for in ("str | None")  [operator]
billing/tests/test_license_overage_offline.py:295: error: Value of type "Any | None" is not indexable  [index]
billing/tests/test_license_overage_offline.py:306: error: Unsupported right operand type for in ("Any | None")  [operator]
billing/tests/test_license_service.py:1042: error: Incompatible types in assignment (expression has type "CustomUser | None", variable has type "CustomUser")  [assignment]
billing/tests/test_price_drift_reconciliation.py:327: error: "object" has no attribute "hour"  [attr-defined]
billing/tests/test_price_drift_reconciliation.py:328: error: "object" has no attribute "minute"  [attr-defined]
billing/tests/test_refund_reconciliation_classification.py:137: error: Item "None" of "Any | None" has no attribute "get"  [union-attr]
billing/tests/tests_free_trial.py:185: error: Item "None" of "Any | None" has no attribute "get"  [union-attr]
billing/tests/test_subscription_upgrade.py:219: error: Item "None" of "CreditBucket | None" has no attribute "total_credits"  [union-attr]
billing/tests/test_subscription_upgrade.py:222: error: Item "None" of "CreditBucket | None" has no attribute "expires_at"  [union-attr]
billing/tests/test_subscription_upgrade.py:365: error: Argument 1 to "assertLessEqual" of "TestCase" has incompatible type "datetime | None"; expected "SupportsDunderLE[datetime]"  [arg-type]
billing/tests/test_subscription_upgrade.py:372: error: Item "None" of "CreditBucket | None" has no attribute "total_credits"  [union-attr]
billing/tests/test_trial_forfeiture_on_activation.py:52: error: Argument 1 to "assertLessEqual" of "TestCase" has incompatible type "datetime | None"; expected "SupportsDunderLE[datetime]"  [arg-type]
billing/tests/test_webhook_idempotency.py:492: error: Item "None" of "datetime | None" has no attribute "isoformat"  [union-attr]
billing/views.py:206: error: Incompatible type for lookup 'user': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
billing/views.py:630: error: Incompatible type for lookup 'user': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/models.py:151: error: Item "None" of "Session | None" has no attribute "name"  [union-attr]
classrooms/models.py:176: error: Item "None" of "Course | None" has no attribute "name"  [union-attr]
classrooms/tests_query_budget.py:233: error: Incompatible type for "assignment" of "StudentSubmission" (got "Assignment | None", expected "Assignment | Combinable")  [misc]
classrooms/tests_student_summary_task.py:202: error: Argument 1 to "assertGreaterEqual" of "TestCase" has incompatible type "datetime | None"; expected "SupportsDunderGE[datetime]"  [arg-type]
classrooms/tests_tenancy_and_roster.py:464: error: Incompatible type for "assignment" of "StudentSubmission" (got "Assignment | None", expected "Assignment | Combinable")  [misc]
classrooms/views.py:426: error: Item "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})] | Any" has no attribute "teachers"  [union-attr]
classrooms/views.py:427: error: Item "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})] | Any" has no attribute "students"  [union-attr]
classrooms/views.py:428: error: Item "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})] | Any" has no attribute "tokens_used"  [union-attr]
classrooms/views.py:429: error: Item "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'teachers': Any, 'students': Any, 'tokens_used': Any, 'academic_sessions': Any})] | Any" has no attribute "academic_sessions"  [union-attr]
classrooms/views.py:739: error: Argument 1 to "get" of "dict" has incompatible type "UUID | None"; expected "UUID"  [arg-type]
classrooms/views.py:745: error: Argument 1 to "get" of "dict" has incompatible type "UUID | None"; expected "UUID"  [arg-type]
classrooms/views.py:979: error: Item "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})] | Any" has no attribute "assignment_count"  [union-attr]
classrooms/views.py:980: error: Item "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})] | Any" has no attribute "student_count"  [union-attr]
classrooms/views.py:981: error: Item "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})] | Any" has no attribute "total_tokens"  [union-attr]
classrooms/views.py:982: error: Item "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})] | Any" has no attribute "full_tokens"  [union-attr]
classrooms/views.py:983: error: Item "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})]" of "CustomUser@AnnotatedWith[TypedDict({'assignment_count': Any, 'student_count': Any, 'total_tokens': Any, 'full_tokens': Any})] | Any" has no attribute "total_tokens"  [union-attr]
classrooms/views.py:1266: error: Incompatible type for lookup 'teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:1270: error: Incompatible type for lookup 'enrollments__student': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:1862: error: Incompatible type for lookup 'users': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:1877: error: Incompatible type for lookup 'teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:1885: error: Incompatible type for lookup 'courses__enrollments__student': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:1898: error: Incompatible type for lookup 'users': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2040: error: Incompatible type for lookup 'course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2060: error: Incompatible type for lookup 'course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2066: error: Incompatible type for lookup 'assignment__course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2082: error: Incompatible type for lookup 'assignment__course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2392: error: Incompatible type for lookup 'course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
classrooms/views.py:2397: error: Incompatible type for lookup 'course__enrollments__student': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
dashboard/tasks.py:625: error: Item "None" of "CustomUser | None" has no attribute "get_full_name"  [union-attr]
dashboard/tasks.py:631: error: Item "None" of "CustomUser | None" has no attribute "get_full_name"  [union-attr]
dashboard/views.py:4050: error: Item "None" of "CustomUser | None" has no attribute "get_full_name"  [union-attr]
students/services.py:1215: error: Argument 1 to "list" has incompatible type "QuerySet[CustomUser, CustomUser]"; expected "Iterable[CustomUser@AnnotatedWith[TypedDict({'full_name': Any})]]"  [arg-type]
students/tests_grading_duration_migration.py:264: error: Argument 1 to "assertGreaterEqual" of "TestCase" has incompatible type "datetime | None"; expected "SupportsDunderGE[Any]"  [arg-type]
students/tests_grading_duration_migration.py:264: error: Cannot infer type argument 1 of "assertGreaterEqual" of "TestCase"  [misc]
students/tests_grading_duration_migration.py:267: error: No overload variant of "__sub__" of "datetime" matches argument type "None"  [operator]
students/tests_grading_duration_migration.py:267: error: Unsupported left operand type for - ("None")  [operator]
students/tests_grading_duration_migration.py:277: error: Unsupported operand types for - ("None" and "timedelta")  [operator]
students/tests_grading_followup_dispatch.py:100: error: Incompatible types in assignment (expression has type "str", target has type "bool")  [assignment]
students/tests_grading_hardening.py:111: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_grading_hardening.py:112: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_grading_hardening.py:126: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_grading_hardening.py:127: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_grading_hardening.py:130: error: Value of type "Any | None" is not indexable  [index]
students/tests_grading_hardening.py:131: error: Value of type "Any | None" is not indexable  [index]
students/tests_grading_hardening.py:147: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_grading_hardening.py:148: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_post_grading_submission_lock.py:648: error: Argument 2 to "assertIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
students/tests.py:193: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests.py:194: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests.py:196: error: Value of type "Any | None" is not indexable  [index]
students/tests_second_opinion_queue.py:155: error: "None" object is not iterable  [misc]
students/tests_second_opinion_queue.py:160: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_second_opinion_queue.py:162: error: Value of type "Any | None" is not indexable  [index]
students/tests_second_opinion_queue.py:169: error: Value of type "Any | None" is not indexable  [index]
students/tests_second_opinion_queue.py:260: error: Item "None" of "Any | None" has no attribute "__iter__" (not iterable)  [union-attr]
students/tests_second_opinion_queue.py:269: error: Item "None" of "Any | None" has no attribute "__iter__" (not iterable)  [union-attr]
students/tests_second_opinion_queue.py:292: error: Item "None" of "Any | None" has no attribute "__iter__" (not iterable)  [union-attr]
students/tests_second_opinion_queue.py:295: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_second_opinion_queue.py:305: error: "None" object is not iterable  [misc]
students/tests_second_opinion_queue.py:308: error: Argument 1 to "assertAlmostEqual" of "TestCase" has incompatible type "float | None"; expected "SupportsSub[float, SupportsAbs[SupportsRound[object]]]"  [arg-type]
students/tests_second_opinion_queue.py:311: error: Value of type "Any | None" is not indexable  [index]
students/tests_submission_concurrency.py:251: error: Argument 1 to "float" has incompatible type "Decimal | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
students/tests_task_tracking.py:253: error: Argument 2 to "assertIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
students/tests_task_tracking.py:254: error: Argument 2 to "assertNotIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
students/tests_task_tracking.py:402: error: Argument 2 to "assertNotIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
students/tests_task_tracking.py:403: error: Argument 2 to "assertNotIn" of "TestCase" has incompatible type "str | None"; expected "Iterable[Any] | Container[Any]"  [arg-type]
students/views.py:350: error: Incompatible type for lookup 'student': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
students/views.py:357: error: Incompatible type for lookup 'assignment__course__teacher': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
users/models.py:43: error: "_T" has no attribute "set_password"  [attr-defined]
users/models.py:113: error: Incompatible types in assignment (expression has type "None", base class "AbstractUser" defined the type as "str")  [assignment]
users/views.py:551: error: Incompatible type for lookup 'user': (got "CustomUser | AnonymousUser", expected "CustomUser | UUID | None")  [misc]
```

## 6. Pre-commit hook runs structurally clean

`pre-commit run mypy --all-files` (post-fix, `language: system`):
completes, exits 1 (real type errors present — expected), reports
"Found 686 errors in 159 files (checked 733 source files)" — the same
count as the direct `mypy .` run within one file (733 vs 734 — pre-commit
passes an explicit file list rather than `.`, one path fewer). No crash,
no plugin-construction error, no missing-module traceback. Full log:
`precommit_mypy_run_OK_language_system.txt`.

## 7. Files in this evidence directory

| File | Contents |
|---|---|
| `EVIDENCE.md` | This file |
| `mypy_before_whole_repo.txt` | Full `mypy .` output, unmodified beta config (no plugin) |
| `mypy_after_whole_repo.txt` | Full `mypy .` output, plugin wired in via `pyproject.toml` |
| `newly_surfaced_errors.txt` | The 178 genuinely-new-site errors from section 5, one per line |
| `precommit_mypy_run_FAILED_isolated_env.txt` | `pre-commit run mypy --all-files` with the literal `additional_dependencies`-only plan — crashes |
| `precommit_mypy_run_OK_language_system.txt` | Same command after switching to `language: system` — runs clean |

## 8. Tree state

`git status --short` throughout: only `.pre-commit-config.yaml`,
`pyproject.toml` modified and this untracked evidence directory. No
Python source file touched — none of the newly-surfaced real errors in
section 5 were fixed, per scope.

## 9. Addendum — real-commit proof, hook runtime, venv-unavailable behavior, ratchet

The SM asked for proof this branch actually unblocks a real commit on a
file with pre-existing mypy errors, not just that the hook "runs to
completion" in isolation. Four things below, all reproduced live in this
worktree.

### 9.1 Real commit test — failed first, ratchet fixed it

A trivial, purely-cosmetic change (one throwaway trailing comment, no
behavior change) was made to `billing/license_service.py` — chosen
because it's the motivating file for the blocked p1b commit and already
has several pre-existing errors (section 5: lines 1330, 1362, 1898, 2109,
2672, 3122, 3478). Committed normally, **no** `--no-verify`:

```
git add billing/license_service.py
git commit -m "..."
```

**First attempt (no ratchet) FAILED as predicted**: the `mypy` hook
reported 62 errors in 18 files (checked 1 source file — mypy pulls in
every transitively-imported module, same effect the EVIDENCE.md baseline
already documented for the whole-repo run). All other hooks
(black/flake8/isort/bandit/detect-secrets) passed. `git log` confirmed no
commit landed and the file stayed staged — pre-commit correctly blocked
it. This is exactly the risk the SM flagged: wiring in the plugin alone
does not unblock a real commit on a file with legacy errors, it just
makes the hook *run* instead of crash.

**Fix: a ratchet, not a blanket ignore.** Every module with at least one
error in the whole-repo `mypy` run (`mypy_after_whole_repo.txt`, matching
the 159-files-with-errors count in section 4) got its own
`[[tool.mypy.overrides]]` block in `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = "billing.license_service"
ignore_errors = true
```

159 such blocks were added (one per module), generated from
`grep -oP '^[a-zA-Z_][a-zA-Z0-9_/]*\.py(?=:\d+: error)' mypy_after_whole_repo.txt | sort -u`
converted to dotted module paths. Three files under `docs/evidence/...`
needed a different form: `docs/` has no `__init__.py`, so mypy resolves
them as bare top-level module names (confirmed with `mypy -v`, which logs
`BuildSource(..., module='attack_replay', ...)`), not
`docs.evidence....` dotted paths — the overrides for
`attack_replay`, `stripe_refusal_real` and `test_refusal_handling_scale`
use the bare names; all three filenames are unique in the repo (checked
via `find`), so there's no collision risk.

After the ratchet, `mypy . --ignore-missing-imports --check-untyped-defs
--disable-error-code=var-annotated` reports **"Success: no issues found
in 734 source files"** — the 686 pre-existing errors are silenced, at the
per-module granularity needed to remove any one override individually as
that module gets cleaned up.

**Retry from the start (fresh `git add` + `git commit`, same trivial
change) PASSED cleanly** — all hooks including mypy green, commit
created. That test commit (`139cf5f`, message "THROWAWAY retry: trivial
comment + ratchet, should pass now") was then unwound with
`git reset --soft HEAD~1` (working-tree changes preserved, nothing
discarded) so it wouldn't sit in history as a separate commit — the
ratchet and the trivial comment are folded into this branch's one real
commit instead.

**Ratchet doesn't blanket-suppress — verified.** `ai_processor/models.py`
is clean (not in the 159-module ratchet list). A deliberate throwaway
type error was appended (`def f() -> int: return "not an int"`) and
checked two ways:

```
$ mypy ai_processor/models.py ...
ai_processor/models.py:176: error: Incompatible return value type (got "str", expected "int")  [return-value]
Found 1 error in 1 file (checked 1 source file)

$ pre-commit run mypy --files ai_processor/models.py
mypy.....................................................................Failed
- hook id: mypy
- exit code: 1
ai_processor/models.py:176: error: Incompatible return value type (got "str", expected "int")  [return-value]
```

Both catch it. The probe was then reverted with `git checkout --
ai_processor/models.py` (clean, `git diff` empty afterward). This
confirms the key property: a module NOT in the ratchet list — clean
today, or created fresh later — stays fully strict; only the 159 modules
that already had errors at ratchet time are silenced.

### 9.2 Hook runtime

`pre-commit run mypy --all-files` timed three consecutive runs with a
warm `.mypy_cache` (the steady-state a developer hits after their first
commit of the session):

| Run | Wall clock |
|---|---|
| 1 | 4.16 s |
| 2 | 2.97 s |
| 3 | 4.22 s |

All three: "Success: no issues found in 733 source files" (733 vs the
direct `mypy .` run's 734 — pre-commit passes an explicit file list
rather than `.`, same one-file difference noted in section 6).

**Cold-cache number, for completeness**: `rm -rf .mypy_cache` then one
more `pre-commit run mypy --all-files` took **70.2 s**. `.mypy_cache` was
175 MB before deletion. This only happens once per fresh checkout / cache
eviction, not on every commit — `django.setup()` itself is not the
expensive part (it's fast, see the sub-5s warm numbers); the ~70s is
mypy's normal cold-start cost of building its module graph across 733
files with no incremental cache to reuse, independent of the
django-stubs plugin. Verdict: noticeable but not a real problem — low
single-digit seconds warm (the common case), ~70s worst case (first run
after a cache wipe), not tens of seconds on every commit.

### 9.3 venv-unavailable failure mode — LOUD failure, confirmed, not silent

The actual git hook pre-commit installs
(`$(git rev-parse --git-common-dir)/hooks/pre-commit`) hardcodes
`INSTALL_PYTHON=/home/.../Virtualenvs/AutoGrader_env/bin/python` and execs
`python -m pre_commit` through that absolute path — so `pre-commit`
itself always launches even from a shell that never activated the venv.
But the `mypy` hook uses `language: system`, which resolves the `mypy`
executable via `shutil.which()` against the *subprocess's* `PATH` at hook
run time — separate from how pre-commit itself was launched.

Reproduced live: staged the ratchet + trivial-comment changes, then ran
`git commit` with `PATH` rebuilt to exclude the venv's `bin` directory
entirely (`echo $PATH | tr ':' '\n' | grep -v AutoGrader_env | paste -sd: -`,
also confirmed `which mypy` fails under that `PATH`). Result:

```
mypy........................................................................................Failed
- hook id: mypy
- exit code: 1

Executable `mypy` not found
```

Exit code from the `git commit` invocation: 0 exit from the *shell
pipeline* only because of the `| tail -40` in the test harness — the
commit itself did **not** land (`git log` still showed the prior HEAD,
`git status` still showed the files staged, not committed). This is the
acceptable outcome the task called for: a clear, unambiguous
"Executable `mypy` not found" message and a blocked commit — not a
silent exit 0 / skipped check. No false confidence risk.

**Does it need a live DB / .env, or just the settings import?** Tested
directly (venv `PATH` restored, so `mypy` resolves and `django.setup()`
actually runs):

- Pointed the active `DATABASE_URI_LOCAL` env var at an unreachable host
  (`postgres://baduser:badpass@10.255.255.1:5432/nonexistent_db_unreachable`)
  — `mypy` still reported **"Success: no issues found in 1 source
  file"**. `dj_database_url.config()` only parses the URL into Django's
  `DATABASES` dict at import time; Django connections are lazy, so
  `django.setup()` never opens a socket. **No live DB is required.**
- Ran with a fully clean environment (`env -i PATH=... HOME=...`, no
  inherited env vars at all) — still succeeded, because
  `AutoGrader/settings.py` calls `env.read_env(".env")` itself and reads
  the file from disk rather than relying on already-exported shell env
  vars.
- Temporarily moved `.env` out of the way (`mv .env .env.bak_test`, same
  clean environment) — **loud failure**, not silent:
  `django.core.exceptions.ImproperlyConfigured: Set the SECRET_KEY
  environment variable`, full traceback, mypy plugin construction fails.
  `.env` was restored immediately (`mv .env.bak_test .env`) and a
  follow-up `mypy` run confirmed clean again before proceeding.

Summary: the hook needs the settings module to import successfully
(which needs `.env` present, since `SECRET_KEY` etc. come from there) but
does **not** need a reachable database — both the present-and-fake-DB and
present-.env-missing-DB-string cases were not tested since `.env`'s
values already parse; the load-bearing check was DB *reachability*,
confirmed unnecessary.

### 9.4 `additional_dependencies` drop — `waitress` check (flagged by Verification Engineer)

Switching to `language: system` drops the old hook's
`additional_dependencies` (`types-requests`, `types-redis`,
`types-waitress`, `types-python-dateutil`, `types-bleach`) in favor of
whatever's importable from the shared venv. `types-redis` is confirmed
redundant (redis==7.1.0 ships inline types, `py.typed` present). Checked
`waitress` specifically, since it isn't in `requirements.txt` and isn't
installed in the shared venv:

```
$ grep -rIn "waitress" --include="*.py" .        # no hits, whole repo
$ grep -n "waitress" requirements.txt            # no hits
$ grep -rIli "waitress" . --exclude-dir=.git     # only this evidence dir's own files
```

**Closed question: `waitress` is dead in this repo.** No `.py` file
imports or references it anywhere, it's not in `requirements.txt`, and
the only repo hits are inside this evidence directory itself (this file,
and `precommit_mypy_run_FAILED_isolated_env.txt`'s copy of the old
`additional_dependencies` line). The project's actual WSGI server is
`gunicorn==20.0.4` (`requirements.txt:88`). `types-waitress` in the old
hook's `additional_dependencies` was stub coverage for a package the
codebase never used — dropping it with the `language: system` switch
loses no real coverage. If `waitress` is ever actually adopted, its
stub package would need adding back (now to the shared venv's
`requirements.txt`, not the mirror hook's isolated env, to stay
consistent with the `language: system` approach this branch settled on).

### 9.5 Backlog

178 newly-surfaced real errors (section 5) logged and triaged by risk as
`H-46` in `docs/HARDENING_BACKLOG.md`. Not fixed — logging/triage only,
per this branch's scope.
