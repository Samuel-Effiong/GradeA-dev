# Item 9: repository-wide commented-out-code (E800) burn-down

**Status: IN PROGRESS.**

- **Done:** 28 of 32 files are cleaned (222 of the 347 hits counted on `beta` `91f752b`), on branch `task/item9-e800-burndown`. None of it is on `beta` yet.
- **Remaining:** 4 files and 125 hits, deferred until the branches that edit them land.
- **Section 8** stays **GAPS FOUND / BLOCKERS FIXED / REMEDIATION IN PROGRESS** until item 9 is finished and passes its final gate.

## Scope (owner decision, 2026-09-14)

- **Coverage:** flake8-eradicate E800 applies to the whole repository. The per-file exemption list is a temporary work queue, not a policy.
- **Per file:** remove genuinely dead commented-out code, and keep comments that document behaviour, rationale or configuration.
- **Proof:** prove nothing functional changed, run the relevant tests, and use mutation testing where behaviour could be affected.
- **Register:** keep it matched to the real repository, with no deleted-file entries and no broad exemptions.
- **Finish:** a strict final gate on the exact committed tree, then retire the exemption mechanism.

## Method

1. **Inventory.** Run `flake8 --select=E800` on `beta` `91f752b`: 32 files, 347 hits. The per-line context is kept in the permanent tooling folder below.
2. **Classify every hit by reading its full block.** Contiguous commented blocks were read to their true extent, because continuation lines often are not flagged.
3. **Edit exactly.** Every deletion or rewrite names its line range and asserts each line's current text before anything changes, so the edits cannot drift. isort and black then settle formatting.
4. **Verify each changed Python file** before its commit may be created:
   - The module's statement bodies are identical to `91f752b`: `ast.dump(..., include_attributes=False)` with `type_comments=True`. No executable code, docstring or type comment changed.
   - `# type: ignore` markers are compared by count and tag. Their recorded line numbers legitimately move when comment lines above them are deleted.
   - Every `noqa`, `nosec`, `type:`, `pragma`, `fmt:`, `isort:`, `pylint:` or `mypy:` comment line is preserved character for character.
   - `flake8 --select=E800` is 0 for the file.
5. **Reconcile the register live, in the same commit.**
   - The cleaned files leave `--per-file-ignores`.
   - The H-12 register is regenerated from a fresh repository-wide count.
   - The register must equal exactly the set of files with hits, or the commit is refused.
6. **Pass `pre-commit` on every changed file,** with no hook modifications allowed.
7. **Commit once per owning section.** Billing is split in two for reviewability.

**Mutation testing.** It does not apply to these commits. Each changed file's statement bodies are proven identical to the base, so there is no behavioural difference for a mutant to expose. Removing a comment cannot change behaviour, and the proof rules out any other edit. Mutation testing will apply if a deferred file's cleanup needs anything beyond comment changes, and none is planned.

## Commits

| Commit | Owner | Files | Hits removed | Diff |
|---|---|---|---|---|
| `a9c2e73` | §10 templates | templates/assignment_to_prosemirror.py | 2 | 5 files changed, 10 insertions(+), 13 deletions(-) |
| `babaf5d` | §0 cross-cutting | AutoGrader/urls.py | 6 | 5 files changed, 8 insertions(+), 13 deletions(-) |
| `350fdd6` | §1 users | users/ models, serializers, services, views, tests_throttle_client_identity | 27 | 9 files changed, 13 insertions(+), 66 deletions(-) |
| `a73471a` | §3 classrooms | classrooms/ models, serializers, views, test_bulk_enrollment, test_views | 26 | 9 files changed, 10 insertions(+), 65 deletions(-) |
| `3268459` | §4 assignments | assignments/ admin, serializers, tests_rigor | 17 | 7 files changed, 12 insertions(+), 40 deletions(-) |
| `7734659` | §2 billing | access_control, license_service, license_views, models, services, stripe_service, stripe_view_schemas, tasks | 77 | 12 files changed, 28 insertions(+), 214 deletions(-) |
| `9b10778` | §2 billing | serializers, views, tests/tests.py, live_qa/invariants_individual, management/commands/backfill | 67 | 9 files changed, 28 insertions(+), 136 deletions(-) |

Every commit also removes its files from `--per-file-ignores` and refreshes the H-12 register. Commit `895b02e` rebuilt the H-12 progress list, which the runner had duplicated.

## Classification decisions worth reviewing

**Reworded and kept.** These are real documentation that E800 misread as code:

- `users/tests_throttle_client_identity.py`: Railway proxy header values from a real production reading, now written as `header -> value` with the carets still aligned.
- `assignments/tests_rigor.py`, `dashboard/tests_rigor.py`: weighted-average explanations.
- `classrooms/serializers.py`: `# Logic: status and score`.
- `billing/stripe_view_schemas.py`: all 10 endpoint labels, rewritten as `HTTP POST to /api/...` for consistency (4 were flagged).
- `billing/models.py`: `kind=SCENARIO` and `kind=CHAOS` field-group labels.
- `billing/serializers.py`: `action == "..."` response-field labels.
- `billing/services.py`: a function-name list inside the rollover rationale.
- `billing/live_qa/invariants_individual.py`: a section heading.

**Replaced with accurate prose, not deleted.**

- `billing/access_control.py` "INTEGRATION EXAMPLES": the examples were stale. They showed a middleware returning a DRF `Response`, a non-existent `ai_access_enabled` field and an invented Celery task. They are replaced by a pointer to `require_ai_access`'s own docstring and to `can_user_access_ai(user, feature=...)`.
- `billing/views.py` sales leads: the commented power-user filter was the only written record of the four lead rules. The endpoint no longer applies them. They are now described in prose and mapped to the per-lead `flags`, and the step comments are renumbered.

**Kept as-is:** explanatory comments next to removed code, such as the lead-in above `super().create`, and live TODOs.

## Observations, not changed by item 9

Item 9 changes comments only, so behaviour and docstrings were left alone.

1. **`contract_months` docstrings.** `LicenseSubscriptionService.create`'s docstring still says the service validates `contract_months` (9, 10 or 12). The check inside the service had been commented out, and the create serializer's `validate_contract_months` now does it. The docstrings are stale, and a direct service caller is not validated.
2. **Redundant license-category check.** The commented check on plan change is covered by `ChangeLicensePlanSerializer`, whose queryset is already limited to `PlanCategory.LICENSE`.
3. **Harmless `SyntaxWarning`.** `SyntaxWarning: invalid escape sequence '\D'` during a full E800 scan comes from LaTeX prose (`\Delta H`) in comments in `ai_processor/evidence.py` and `ai_processor/tests_evidence_latex.py`. E800 does not flag them.

## Checker false positive, caught and fixed

The first billing attempt was refused: whole-module AST dumps differed for `billing/stripe_service.py`.

- **Diagnosis:** the statement bodies were identical. Only `Module.type_ignores` differed, because one `# type: ignore[attr-defined]` marker's recorded line number moved from 981 to 979 when comment lines above it were deleted.
- **Fix:** the checker now compares statement bodies exactly, and type-ignore markers by count and tag. Commits `a9c2e73`–`3268459` had already passed the stricter whole-module comparison.
- **Re-apply:** the uncommitted billing edits were backed up, reset and re-applied. The result was byte-identical to the first attempt.

## Deferred: 4 files, 125 hits

| File | Hits | Waiting for | Why |
|---|---|---|---|
| `ai_processor/services.py` | 49 | Section 9 branch | the branch edits this file |
| `AutoGrader/settings.py` | 29 | H-1 Redis/Celery isolation fix (H-9) | the fix edits the test settings branch |
| `assignments/tasks.py` | 27 | Section 7 and Section 9 branches | both edit this file |
| `assignments/views.py` | 20 | Section 9 branch | the branch edits this file |

Cleaning these first would force the owning sessions into merge conflicts. Each will be cleaned, with the same verification, once its branch is on `beta`.

## Still to do before item 9 can close

1. **Per-app regression runs** for users, classrooms, assignments, billing and AutoGrader. They are strictly serial under the shared-Redis rule and queued after the H-1 session's overlap proof.
2. **Clean the 4 deferred files** once their branches land.
3. **Retire the exemption mechanism.** Once `flake8 --select=E800 .` is clean, remove `--per-file-ignores` and the H-12 register.
4. **Repository-wide checks:** pre-commit with E800 enforced, static and security checks, and the full suite.
5. **Strict final gate on the exact committed tree:** fresh DB with no `--keepdb`, fingerprint before and after, logs stored permanently.

## Tooling (permanent, outside the repo)

`/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-gate-logs/item9/` holds:

- `inventory.json` and `inventory-context.txt`: every hit with its context
- `clean_section.py`: the verified runner
- `section-N.json`: the exact edits per commit, with line assertions
- `section-N.log`: verification output
- `section-6-first-attempt/`: backup of the refused first billing attempt
