# H-262 notes (Release Builder, a0)

- **The row**: dev tools (black, flake8, pytest, flower, virtualenv, etc.) out of the
  production Docker image — multi-stage Dockerfile, requirements split, CI and audit
  pipeline updates.
- **Base:** beta `a3f5a6a1` (batch 30 landed).
- **Branch** `task/h262-prod-image-dev-tools`.

## What changed

### 1. Requirements split

`requirements.txt`: production only — flower, djangorestframework-stubs and 56 other dev
packages removed. `requirements-dev.txt` (NEW): starts with `-r requirements.txt`, adds
back all dev/test packages.

**SM rulings:** flower moves to dev (owner confirmed not on Railway, 2026-10-27).
django-extensions, filelock, setuptools stay in prod. djangorestframework-stubs moves to
dev (SM, 2026-10-29).

### 2. Multi-stage Dockerfile

Build stage (`AS build`): has `build-essential`, `libpq-dev`, `libjpeg62-turbo-dev`,
`zlib1g-dev`, `libwebp-dev`; compiles wheels and installs gunicorn + requirements.
Runtime stage: copies the installed Python environment from the build stage, installs
only runtime system libraries (`libgl1`, `libglib2.0-0`, `poppler-utils`, `libpango*`),
Chromium via `playwright install --with-deps`, and the application code. No compilers, no
dev headers, no `libmariadb-dev` anywhere.

### 3. Audit pipeline

`scripts/pip_audit_gate.py`: audits both `requirements.txt` (with prod-only ignores) and
`requirements-dev.txt` (with prod + dev ignores combined).
`.pip-audit-ignore-dev.toml` (NEW): black and virtualenv advisory entries moved from
`.pip-audit-ignore.toml`.

### 4. CI workflows

- `.github/workflows/tests.yml`: installs `requirements-dev.txt` (was `requirements.txt`)
- `.github/workflows/migration-safety.yml`: installs `requirements-dev.txt` (was `requirements.txt`)
- `.github/workflows/audit.yml`: step name and comment updated; `pip_audit_gate.py`
  handles auditing both files

## Gate pack (written before any gate run)

### Seam check

Production/infrastructure files changed between `a3f5a6a1` and tip:

1. `.github/workflows/audit.yml`
2. `.github/workflows/migration-safety.yml`
3. `.github/workflows/tests.yml`
4. `.pip-audit-ignore-dev.toml` (NEW)
5. `.pip-audit-ignore.toml`
6. `Dockerfile`
7. `requirements-dev.txt` (NEW)
8. `requirements.txt`
9. `scripts/pip_audit_gate.py`

Test file: `ai_processor/tests_h262_prod_image_dev_tools.py`.

No runtime `.py` files (no serializers, views, models, services, or settings changes).

Command: `git diff --name-only a3f5a6a1..HEAD -- ':(exclude)docs/*'` must yield exactly
the 9 production files above plus the 1 test file (10 files total).

### Red run — 12 tests, every production file reverted to base `a3f5a6a1`

**Method:** revert ALL 9 production files to their base state. At base:
- `requirements-dev.txt` does not exist → DELETE it
- `.pip-audit-ignore-dev.toml` does not exist → it is already absent
- `requirements.txt`, `Dockerfile`, `scripts/pip_audit_gate.py`,
  `.pip-audit-ignore.toml`, `.github/workflows/tests.yml`,
  `.github/workflows/migration-safety.yml`, `.github/workflows/audit.yml`
  → `git checkout a3f5a6a1 -- <file>`

Keep only `ai_processor/tests_h262_prod_image_dev_tools.py` from the tip.

**Expected result:** Ran 12, Failures 10, Errors 2.

| # | Test | Expected | Fragment |
|---|---|---|---|
| 1 | `test_requirements_dev_txt_exists` | FAIL | `AssertionError: False is not true : requirements-dev.txt not found` |
| 2 | `test_requirements_dev_includes_production` | ERROR | `FileNotFoundError` — `open(path)` on the deleted `requirements-dev.txt` |
| 3 | `test_dev_packages_not_in_production_requirements` | FAIL | `dev packages found in production requirements: ['bandit', 'black', ...]` (base requirements.txt has all 58 dev packages) |
| 4 | `test_all_dev_packages_in_dev_requirements` | ERROR | `FileNotFoundError` — `_parse_requirements(REPO / "requirements-dev.txt")` on the deleted file |
| 5 | `test_dockerfile_has_build_and_runtime_stages` | FAIL | `1 not greater than or equal to 2 : expected at least 2 FROM stages, found 1` |
| 6 | `test_build_essential_only_in_build_stage` | FAIL | `build-essential must not appear in the runtime stage` (single stage has no "AS build", treated as runtime) |
| 7 | `test_no_libmariadb_dev_anywhere` | FAIL | `libmariadb-dev must be removed entirely` |
| 8 | `test_dev_headers_only_in_build_stage` | FAIL | `libpq-dev must not appear in the runtime stage` (first header checked) |
| 9 | `test_audit_gate_audits_dev_requirements_too` | FAIL | `'requirements-dev.txt' not found in ['requirements.txt'] : no -r requirements-dev.txt in subprocess.run calls` (base main() calls subprocess.run once with `-r requirements.txt` only) |
| 10 | `test_dev_only_ignores_in_separate_file` | FAIL | `False is not true : .pip-audit-ignore-dev.toml not found` |
| 11 | `test_tests_workflow_installs_dev_requirements` | FAIL | `tests.yml must install requirements-dev.txt` (base has `requirements.txt`) |
| 12 | `test_audit_workflow_audits_both_files` | FAIL | `audit.yml must reference requirements-dev.txt` |

**Totals check:** 10 FAIL + 2 ERROR + 0 GREEN = 12 tests. ✓

Tests 2 and 4 ERROR with `FileNotFoundError` because `requirements-dev.txt` does not
exist at base — this is the correct error; the file is NEW in this row. The other 10
each FAIL for a distinct reason matching its specific assertion.

### Pre-code run (not the gate)

Ran before completing code (flower not in requirements-dev.txt, base Dockerfile/workflows
checked out while tip's requirements-dev.txt was kept). Result: Ran 12, Failures 10 — NOT
valid as gate evidence because it mixed base and half-finished tip state. Retained for
the record; the gate red run uses the method above.

### Mutant battery (11 mutants)

| # | Mutant | Killing test | Expected fragment |
|---|---|---|---|
| M1 | Add `flower==2.0.1` back to `requirements.txt` | `test_dev_packages_not_in_production_requirements` | `dev packages found in production requirements: ['flower']` |
| M2 | Remove `-r requirements.txt` line from `requirements-dev.txt` | `test_requirements_dev_includes_production` | `!= '-r requirements.txt'` |
| M3 | Collapse Dockerfile to single FROM (remove build stage) | `test_dockerfile_has_build_and_runtime_stages` | `expected at least 2 FROM stages, found 1` |
| M4 | Add `build-essential` to runtime stage | `test_build_essential_only_in_build_stage` | `build-essential must not appear in the runtime stage` |
| M5 | Add `libmariadb-dev` to Dockerfile | `test_no_libmariadb_dev_anywhere` | `libmariadb-dev must be removed entirely` |
| M6 | Add `libpq-dev` to runtime stage | `test_dev_headers_only_in_build_stage` | `libpq-dev must not appear in the runtime stage` |
| M7 | Change `REQUIREMENTS_DEV` to `"requirements.txt"` in `pip_audit_gate.py` | `test_audit_gate_audits_dev_requirements_too` | `'requirements-dev.txt' not found in ['requirements.txt', 'requirements.txt']` |
| M8 | Delete `.pip-audit-ignore-dev.toml` | `test_dev_only_ignores_in_separate_file` | `.pip-audit-ignore-dev.toml not found` |
| M9 | Revert `tests.yml` to install `requirements.txt` | `test_tests_workflow_installs_dev_requirements` | `tests.yml must install requirements-dev.txt` |
| M10 | Remove `requirements-dev.txt` reference from `audit.yml` | `test_audit_workflow_audits_both_files` | `audit.yml must reference requirements-dev.txt` |
| M11 | Drop the dev `subprocess.run` call from `pip_audit_gate.py` main() | `test_audit_gate_audits_dev_requirements_too` | `'requirements-dev.txt' not found in ['requirements.txt']` |

### Near suites

| App | Expected | Reason |
|---|---|---|
| `ai_processor` | Ran 1467, OK | H-262 tests live here; no runtime code changes |

No other app has runtime code changes.

### Full suite

Expected: **Ran 8484** (8472 base + 12 H-262 tests), **OK**, **skipped=30**.

Discovery diff: 12 new test IDs in `ai_processor.tests_h262_prod_image_dev_tools`
(4 RequirementsSplitTest + 4 DockerfileMultiStageTest + 2 AuditGateTest +
2 CIWorkflowTest). No tests removed.

### Private venv

`~/Documents/Virtualenvs/AutoGrader_env_h262` — built from tip's
`requirements-dev.txt` (carries H-263's upgraded pins).

## Gate run results (2026-10-29, tip 65fb2f8b)

### Red run

Reverted all 9 production files to base `a3f5a6a1`, deleted `requirements-dev.txt`
and `.pip-audit-ignore-dev.toml`.

**Result:** Ran 12, Failures 10, Errors 2 — matches predictions.

Row 9 (`test_audit_gate_audits_dev_requirements_too`, behavioural test): FAIL with
`'requirements-dev.txt' not found in ['requirements.txt'] : no -r requirements-dev.txt
in subprocess.run calls; got ['requirements.txt']` — base main() calls subprocess.run
once with `-r requirements.txt` only.

### Green run

Tip restored. **Result:** Ran 12, OK.

### Near suite

`ai_processor`: Ran 1467, OK (skipped=6) — matches prediction.

### Seam check

`git diff --name-only a3f5a6a1..HEAD -- ':(exclude)docs/*'` → 10 files (9 production +
1 test file) — matches prediction.

### Mutant battery

11/11 killed. All fragments match predictions.

| # | Result |
|---|---|
| M1 | KILLED |
| M2 | KILLED |
| M3 | KILLED |
| M4 | KILLED |
| M5 | KILLED |
| M6 | KILLED |
| M7 | KILLED |
| M8 | KILLED |
| M9 | KILLED |
| M10 | KILLED |
| M11 | KILLED |

### Docker gate proof (deferred until daemon is started by owner)

1. `docker build` both images (prod from `Dockerfile`, dev with `requirements-dev.txt`)
2. Image sizes
3. `pip list` in the runtime image — no black/flake8/pytest/flower/virtualenv etc.
4. No `gcc` in the runtime image
5. Import checks
6. `manage.py check --deploy`
7. Gunicorn boots and `GET /api/v1/health` answers
8. Chromium works
