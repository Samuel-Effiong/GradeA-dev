# QA Server Setup — Billing Nightly Run + Grader Weekly Run

Step-by-step instructions for standing up a **separate testing server** that runs only two
scheduled jobs against isolated infrastructure, never touching production:

- `billing.tasks.nightly_stripe_live_qa` — real-Stripe billing QA suite (test mode)
- `ai_processor.tasks.weekly_grading_benchmark_live` — real-model grading accuracy benchmark

Both tasks are hard no-ops everywhere else in the fleet unless explicitly enabled, which is what
makes it safe to point the *same* codebase and settings module (`AutoGrader.settings`) at this box
instead of maintaining a second one. [`QA.env`](QA.env) in the repo root holds the environment
variables this document walks through filling in and loading.

Read [`QA.env`](QA.env)'s own header comments alongside this document — they cover the same ground
from the "what does each variable do" angle; this document covers "what do I actually run, in what
order."

---

## 0. Before you start: what this server needs, and what it must NOT share with production

- **Its own PostgreSQL database.** Both jobs create real rows — Stripe customers/subscriptions
  mirrored locally, real `CustomUser` accounts, real `AssignmentSubmission` rows for the grading
  benchmark. None of that belongs anywhere near the production database.
- **Its own Redis instance** (or at minimum a dedicated logical DB index on a shared one) — it's
  both the Celery broker/result backend and the Django cache.
- **A Stripe account (or a dedicated test-mode-only setup) with a `sk_test_...` secret key.**
  `ENABLE_STRIPE_LIVE_QA` re-checks this prefix on every single Stripe call the suite makes, not
  just at startup — it refuses to run against anything else. Never put a live (`sk_live_`) key
  anywhere in `QA.env`.
- **An OpenRouter (or whichever provider `ai_processor` is configured against) API key with its
  own spend limit**, separate from production's. The weekly benchmark makes real, billed model
  calls on purpose — that's the entire point of the job — so give it a budget it can't blow past
  production's.
- Everything else in `QA.env` (Cloudinary, Google OAuth, MailerSend/MailerLite, `SECRET_KEY`) is
  required only because `AutoGrader/settings.py` reads them unconditionally at import time — Django
  won't boot without them, even though neither job touches most of them. Dummy-but-valid values are
  fine for these; see [`QA.env`](QA.env)'s comments for which ones.

---

## 1. Get the code onto the box

```bash
git clone <this repo's URL> /opt/gradeaplus-qa
cd /opt/gradeaplus-qa
git checkout beta        # or whichever branch you want this box tracking
```

## 2. Python environment

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins `stripe==14.4.1`; keep it in sync with whatever the branch you checked out
expects — a version drift here is exactly the kind of silent-behavior-change class of bug this QA
suite exists to catch, so don't let the QA box's dependencies quietly diverge from what you deploy.

## 3. Fill in `QA.env` with real values

Open [`QA.env`](QA.env) and replace every `REPLACE_ME` placeholder. In particular:

- `DATABASE_URI_DEV` → the QA Postgres instance from step 0, e.g.
  `postgres://user:pass@host:5432/gradeaplus_qa`  <!-- pragma: allowlist secret -->
  (placeholder format, not a real credential)
- `REDIS_DEV_URL` → the QA Redis instance
- `LOCAL_STRIPE_PUBLIC_KEY` / `LOCAL_STRIPE_SECRET_KEY` / `LOCAL_STRIPE_WEBHOOK_SECRET` → your
  Stripe **test-mode** keys (`ENVIRONMENT=dev` is already set in the file, which is what makes
  `settings.py` read these `LOCAL_*` names instead of the plain `STRIPE_*` ones — don't change
  `ENVIRONMENT` to `prod` on this box)
- `OPENROUTER_API_KEY` → your QA-budget provider key
- `SECRET_KEY` → generate a fresh one, don't reuse production's:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
- `FIELD_ENCRYPTION_KEY` → generate one if any encrypted field is ever written during a QA run:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

**Never commit the filled-in file.** `QA.env` is covered by `.gitignore`'s `*.env` pattern — confirm
that stayed intact (`git check-ignore -v QA.env` should print a match) before you put real secrets
in it.

## 4. Load `QA.env` into the process environment

`AutoGrader/settings.py` calls `env.read_env(".env")` — a **hardcoded filename**, not
`QA.env` — and `django-environ` only fills in values from that file for variables *not already
set* in the real process environment (existing environment variables always take precedence and
are never overwritten by file content). So pick one of these two approaches:

**Option A — export into the real shell environment (works everywhere: bare processes, systemd,
Docker):**

```bash
set -a
source QA.env
set +a
```

Real env vars are now set directly, so `env.read_env(".env")` finding nothing (or finding a
production `.env` you don't want read) doesn't matter — `env.str(...)` reads `os.environ` first.

**Option B — symlink it to the filename settings.py actually looks for** (simplest for a quick
manual test on a box that has no other `.env`):

```bash
ln -s QA.env .env
```

If you're deploying this as a systemd service, use `EnvironmentFile=/opt/gradeaplus-qa/QA.env` in
the unit file instead of either of the above — see the example unit files in step 8.

## 5. Apply migrations

```bash
python manage.py migrate
```

## 6. Seed the plan-feature catalogue

```bash
python manage.py seed_plan_features
```

Idempotent, safe to re-run. Skipping this makes `manage.py check`/`migrate`/`runserver` log a
startup warning naming the missing AI-feature gating keys, and denies those features to every user
on this box — including, potentially, the grading benchmark's throwaway teacher.

## 7. Seed the four `SubscriptionPlan` rows the billing suite needs

There is **no management command or fixture** for this — `require_plan()`
(`billing/stripe_live_qa.py`) and `_require_license_plan()`
(`billing/live_qa/scenarios_license.py`) deliberately never auto-create a plan, because a plan
whose `stripe_price_id` doesn't exist in your Stripe test account would otherwise fail deep inside
a scenario with an opaque Stripe error instead of a clear one up front. Create real Prices in your
Stripe test-mode dashboard first, then seed the local rows to match, via `manage.py shell`:

```bash
python manage.py shell
```

```python
from decimal import Decimal
from billing.models import BillingInterval, PlanCategory, PlanTier, PlanType, SubscriptionPlan

# Individual — STANDARD / MONTHLY (needed by nearly every fast+deep scenario)
SubscriptionPlan.objects.update_or_create(
    name=PlanType.STANDARD,
    defaults=dict(
        display_name="Standard",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.MONTHLY,
        price_cents=Decimal("999"),
        monthly_credits=10_000_000,
        stripe_price_id="price_REPLACE_WITH_REAL_TEST_PRICE_ID",
        carry_over_percent=15,
        carry_over_expiry_months=1,
        is_active=True,
    ),
)

# Individual — STANDARD / ANNUAL (interval-crossing + long-horizon scenarios)
SubscriptionPlan.objects.update_or_create(
    name=PlanType.STANDARD_ANNUAL,
    defaults=dict(
        display_name="Standard Annual",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.ANNUAL,
        price_cents=Decimal("9999"),
        monthly_credits=10_000_000,
        stripe_price_id="price_REPLACE_WITH_REAL_TEST_PRICE_ID",
        carry_over_percent=15,
        carry_over_expiry_months=1,
        is_active=True,
    ),
)

# Individual — PRO / MONTHLY (upgrade/downgrade scenarios)
SubscriptionPlan.objects.update_or_create(
    name=PlanType.PRO,
    defaults=dict(
        display_name="Pro",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        price_cents=Decimal("4999"),
        monthly_credits=50_000_000,
        stripe_price_id="price_REPLACE_WITH_REAL_TEST_PRICE_ID",
        carry_over_percent=15,
        carry_over_expiry_months=1,
        is_active=True,
    ),
)

# License — PRO (interval is irrelevant to _require_license_plan's lookup, but Stripe still
# needs a real recurring Price behind stripe_price_id)
SubscriptionPlan.objects.update_or_create(
    name=PlanType.PRO_LICENSE,
    defaults=dict(
        display_name="Pro License",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        price_cents=Decimal("2999"),
        monthly_credits=20_000_000,
        stripe_price_id="price_REPLACE_WITH_REAL_TEST_PRICE_ID",
        carry_over_percent=25,
        carry_over_expiry_months=1,
        is_active=True,
    ),
)
```

## 8. Verify Django can boot at all

```bash
python manage.py check
```

Should report the plan-feature catalogue check passing (step 6) and no other errors. If this
fails, nothing past this point will work — fix it here before moving on.

## 9. Smoke-test the billing suite manually, before trusting the schedule

```bash
# Confirm every scenario is registered and the four plans from step 7 resolve:
python manage.py run_stripe_live_qa --list

# Run the fast tier once, by hand, and read the output:
python manage.py run_stripe_live_qa --tier fast
```

This talks to real Stripe test mode and takes a few minutes. If it fails here with a
`LiveQAConfigurationError`, the error message names exactly which plan/price is missing — fix step
7, don't guess. A scenario *failing* (as opposed to refusing to run) is a real finding — read
`billing/tests_free_trial.py`/`stripe_live_qa_scenarios.py` for what it's asserting before assuming
it's environmental.

## 10. Smoke-test the grading benchmark manually

```bash
# Free — no model calls, replays recorded responses:
python manage.py grading_benchmark --mode replay --json

# Billed — makes real model calls against OPENROUTER_API_KEY:
python manage.py grading_benchmark --mode live --json
```

`--mode live` bills real credits on the throwaway `grading-benchmark@benchmark.local` teacher it
creates automatically (pass `--teacher-email` to bill an existing one instead). Run it once
deliberately here so you know it works before Celery Beat starts firing it weekly unattended.

## 11. Start the Celery worker and Beat scheduler

```bash
celery -A AutoGrader worker --loglevel=info --logfile=/var/log/gradeaplus-qa/worker.log &
celery -A AutoGrader beat   --loglevel=info --logfile=/var/log/gradeaplus-qa/beat.log &
```

(Use a real process manager — systemd, supervisor — for anything beyond a manual smoke test; see
the example unit files below.)

**Read this before you walk away:** `CELERY_BEAT_SCHEDULE` (`AutoGrader/settings.py`) is **one
shared schedule**, not a per-environment one. Starting Beat here runs *every* periodic task the app
defines — dashboard summaries, at-risk-student alerts, teacher-inactivity emails, credit-bucket
cleanup, MailerLite syncs, `record_concurrent_users`, and so on — not just the two jobs this
document is about. Against an isolated, otherwise-empty QA database those should mostly find
nothing to do and no-op harmlessly, but that's an assumption worth actually confirming (watch the
worker log for the first day) rather than trusting blindly. If you need this box to run *only* the
two named tasks, don't use the stock `beat` scheduler at all — use `celery -A AutoGrader worker` with
`cron`/`systemd timer` entries that call `nightly_stripe_live_qa.delay()` /
`weekly_grading_benchmark_live.delay()` directly instead.

**Confirm results land where you'll see them.** Neither task writes to a dashboard or a DB row —
they log at `INFO` on success and `ERROR` on a real failure (matching the
`sweep_stale_stripe_events` convention elsewhere in this codebase). There is no Sentry/alerting
wired up in this project as of this writing (see the earlier hardening discussion), so an `ERROR`
line with nobody tailing `worker.log` is functionally silent. At minimum, ship these logs
somewhere you actually check — even a daily `grep ERROR worker.log | mail -s "QA server errors" you@...`
cron entry is better than nothing until real alerting exists.

### Example systemd units (adjust paths/user)

```ini
# /etc/systemd/system/gradeaplus-qa-worker.service
[Unit]
Description=GradeA+ QA Celery worker
After=network.target

[Service]
User=gradeaplus
WorkingDirectory=/opt/gradeaplus-qa
EnvironmentFile=/opt/gradeaplus-qa/QA.env
ExecStart=/opt/gradeaplus-qa/venv/bin/celery -A AutoGrader worker --loglevel=info
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/gradeaplus-qa-beat.service
[Unit]
Description=GradeA+ QA Celery beat
After=network.target

[Service]
User=gradeaplus
WorkingDirectory=/opt/gradeaplus-qa
EnvironmentFile=/opt/gradeaplus-qa/QA.env
ExecStart=/opt/gradeaplus-qa/venv/bin/celery -A AutoGrader beat --loglevel=info
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gradeaplus-qa-worker gradeaplus-qa-beat
```

## 12. Confirm the schedule itself

Both jobs are registered in `CELERY_BEAT_SCHEDULE` (`AutoGrader/settings.py`):

- `nightly-stripe-live-qa` — `crontab(minute=0, hour=1)` UTC
- `weekly-grading-benchmark-live` — `crontab(minute=0, hour=3, day_of_week=GRADING_BENCHMARK_DAY_OF_WEEK)`
  UTC, defaulting to Sunday if that variable is unset

`django_celery_beat`'s `DatabaseScheduler` reads the schedule from the database on first boot and
keeps it there — if you ever change `CELERY_BEAT_SCHEDULE` in code afterward, run
`python manage.py migrate` and confirm the change actually reached the `PeriodicTask` table (via
`python manage.py shell` — `django_celery_beat.models.PeriodicTask.objects.all()`), since a stale
DB-scheduled entry silently wins over the code the next time you'd expect it to update.

## 13. Ongoing maintenance

- **Rotate the Stripe test clocks / QA-created objects occasionally.** The suite tears down what it
  creates (`--keep-objects` on the manual command disables that, for debugging only), but a failed
  run mid-scenario can leak Stripe test objects and local rows. They cost nothing in test mode but
  accumulate.
- **Re-record the grading benchmark's baseline deliberately, not accidentally**, whenever the
  dataset or prompts change:
  ```bash
  python manage.py grading_benchmark --mode record --save-baseline path/to/baseline.json
  ```
  This bills real credits — do it on purpose, not as a side effect of debugging something else.
- **Keep this document and `QA.env` in sync with `AutoGrader/settings.py`.** If a new mandatory
  (no-default) `env.str(...)` call is ever added to settings, this box will fail to boot until
  `QA.env` grows the matching entry — that's a deliberate fail-closed design, not a bug to work
  around.
