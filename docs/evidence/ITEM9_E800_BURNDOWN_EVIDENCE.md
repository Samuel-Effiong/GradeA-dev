# Item 9: repository-wide commented-out-code (E800) burn-down

**Status: CLOSED (2026-09-16). On `beta` at `a7c81a4`.**

- **Done:** all 32 files are cleaned. `flake8 --select=E800 .` is 0 files / 0 hits repository-wide. `--per-file-ignores` is removed from `.pre-commit-config.yaml` and the H-12 register (the file/hits table and its staged-plan rules) is deleted from `docs/HARDENING_BACKLOG.md`; H-12's history and this closure are kept as its permanent record. The branch `task/item9-e800-burndown` merged `beta` four times as H-9, Section 7 and Section 9 landed: `34a1d1c` (`29cc1c7`, the H-9 fix), `d5d95be` (`6a8e714`, Section 7), `96d0a6b` (`87acd13`, Section 9), `0eef001` (`53e31c3`, Section 9's later `create_file`-removal cleanup). The strict final gate passed on the branch's tip, `7e5f0c6` (superseded by `a7c81a4`, the doc-only commit recording that gate).
- **Landed on `beta`:** fast-forward merge, `53e31c3` → `a7c81a4` (no merge commit; `beta` was already an ancestor). Verified in a temp worktree before the ref moved — see "Coordination and landing" below.
- **Post-merge verification on `beta` at `a7c81a4`:** repository-wide E800 scan, `pre-commit --all-files`, `manage.py check`, `makemigrations --check`, and a regression run of every item-9-touched app all passed — see "Post-merge verification" below.
- **Section 8** stays **GAPS FOUND / BLOCKERS FIXED / REMEDIATION IN PROGRESS**: item 9 closing does not itself close Section 8, since Section 8 (dashboard) had its own separate blockers, already fixed and gated, unrelated to item 9.
- **Reopening:** this item is considered finished and should not be reopened except for a new, concrete E800 violation — not a re-litigation of scope or of decisions already made above.

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
| `e45c51f` | §0 cross-cutting | AutoGrader/settings.py (built on `29cc1c7`), plus the djoser docs in project-config.md and the HTML reference | 29 | 8 files changed, 22 insertions(+), 69 deletions(-) |
| `2eaa8b9` | §4 assignments | assignments/tasks.py, assignments/views.py (built on `96d0a6b`) | 47 | 4 files changed, 12 insertions(+), 65 deletions(-) |
| `c4c5bf8` | §5 ai_processor | ai_processor/services.py (built on `0eef001`, which merges `53e31c3` for Section 9's `create_file` removal) | 47 | 5 files changed, 14 insertions(+), 100 deletions(-) |

Every commit also removes its files from `--per-file-ignores` and refreshes the H-12 register. Merge `34a1d1c` brought in `beta` `29cc1c7`, the H-9 fix, cleanly. Commit `895b02e` rebuilt the H-12 progress list, which the runner had duplicated.

Two later merges brought in beta docs-only changes with no code conflicts:

- `d5d95be` merges `beta` `6a8e714` (Section 7 landing). `docs/CODEBASE_AUDIT_SECTIONS.md` conflicted on adjacent rows; kept this branch's row 8 and beta's row 7.
- `96d0a6b` merges `beta` `87acd13` (Section 9 landing). Same file, same shape of conflict; kept this branch's row 8 and beta's row 9.

Both merges left `assignments/tasks.py` and `assignments/views.py` at their post-Section-7/Section-9 line counts, which `2eaa8b9`'s edits target directly (26 and 19 hits respectively, re-inventoried after the merges rather than reused from the original 347-hit count).

A fourth merge, `0eef001`, brought in `beta` `53e31c3`: Section 9's separate cleanup that removed `AIProcessor`'s dead `create_file` method. Two of the original 49 hits in `ai_processor/services.py` belonged to that method and left with it; `c4c5bf8` re-inventoried the file at its new 47-hit count rather than reusing 49. This merge conflicted on `docs/CODEBASE_AUDIT_SECTIONS.md` (kept this branch's row 8, beta's row 9, which records the `create_file` removal and the rest of Section 9's post-promotion cleanup) and `docs/HARDENING_BACKLOG.md` (kept beta's H-11 note about the Section 9 promotion, this branch's H-12 live count).

`c4c5bf8` removed dead imports, three abandoned alternate AI-provider client configs (a personal OpenRouter key, DeepSeek, Hugging Face) and the env vars only they read, several dead statements stranded in otherwise-live methods, an entire unreachable tail after a `return` statement, and `OCRService`'s three dead PaddleOCR/pytesseract methods — confirmed dead by Section 9's own audit that `OCRService` only measures image dimensions and never performed OCR. This was the last exempted file: `--per-file-ignores` is now empty and the H-12 register has no rows.

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

## Two more safety stops, on the settings commit

Both stops refused the commit before it was made. Each was diagnosed before anything was retried.

1. **The code check compared against the wrong base.**
   - **What happened:** it compared `AutoGrader/settings.py` against the original item 9 base `91f752b`. That file had legitimately changed since, because the H-9 merge rewrote its test settings branch. My cleanup changed only comments: all 137 statements were identical to the pre-section commit `34a1d1c`.
   - **Fix:** the checker now verifies each section against the commit it starts from. The edits were reset from backup and re-applied.
2. **`detect-secrets` removed one baseline entry.**
   - **Why the hook refused:** comment-only removals should only shift recorded line numbers, and this was a removed entry.
   - **What the entry was:** `settings.py`'s only entry, a "Secret Keyword" false positive, triggered by the word "password", on the deleted dead djoser comment `# "password_reset": "home.emails.PasswordResetEmail",`. That line lay inside the section's own delete range, and its text exists nowhere in the edited file.
   - **Result:** the entry was accepted with that proof, and no secret was added.
   - **Runner change:** it now accepts a removed baseline entry only under exactly that proof. Any added secret, or any other change, still refuses.

Doc dependency caught during this section: the DJOSER note in `settings.py`, `docs/backend/project-config.md` and the HTML backend reference all pointed at the commented djoser include in `AutoGrader/urls.py`, which `babaf5d` had removed. All three were corrected, and the line-number citations that drift were dropped.

## Regression runs

Both runs ran alone on the host, on a fresh test DB without `--keepdb`, with `--parallel 1` and under `systemd-inhibit`. Full logs are in the permanent tooling folder.

| Tree | What ran | Result |
|---|---|---|
| `4162220`: the 28 files before `settings.py` | `pre-commit --all-files` with E800 enforced; a repo-wide E800 count; `manage.py test users classrooms assignments billing AutoGrader` | all 24 hooks pass. E800 shows only the then-4 deferred files. **2,919 tests OK**, 13 skipped, exit 0. The test DB was created and destroyed, with 0 connections and 0 "other sessions" lines. Fingerprint unchanged. |
| `e45c51f`: after `settings.py`, which contains `29cc1c7` | `manage.py check`; `makemigrations --check`; `manage.py test AutoGrader users` | no issues; no changes. **785 tests OK**, 2 skipped, exit 0. Test DB destroyed, 0 connections. |
| `2eaa8b9`: after the tasks.py/views.py cleanup | `manage.py test assignments` | **548 tests OK**, 12 skipped, exit 0, in 221.16s. Log at `/tmp/item9-logs/assignments-regression.log`. This run used `--keepdb`, unlike the two runs above, so it is not a fresh-DB proof; the fresh-DB, no-`--keepdb` run for this app happens at the final gate. |
| `c4c5bf8`: after the ai_processor/services.py cleanup | `manage.py test ai_processor` | **793 tests OK**, 5 skipped, exit 0, in 232.28s. Log at `/tmp/item9-logs/ai_processor-regression.log`. Also `--keepdb`; the fresh-DB run happens at the final gate. |

**Overlap record for the `e45c51f` run** (03:28:49–03:34:20, host local time):

- **Before it:** the H-1 session's 17-second load burst ran 03:28:31–03:28:48, against its own DB and its own Redis on :6390, and ended before this run started.
- **After it:** Section 7's gate process started at 03:37:30.
- **During it:** nothing.

## Deferred: none

All 32 files are cleaned. `assignments/tasks.py` and `assignments/views.py` were cleaned in `2eaa8b9`, once Section 7's and Section 9's own edits to them had both landed on `beta` (merges `d5d95be`, `96d0a6b`). `ai_processor/services.py` was cleaned last, in `c4c5bf8`, once Section 9's separate `create_file`-removal branch landed on `beta` (`53e31c3`, merged in as `0eef001`) — cleaning it first would have forced that branch into a merge conflict.

## Strict final gate: PASSED on `7e5f0c6`

Run in a detached worktree (`Grade-Automator-Plus-item9-gate`, own test DB name `test_item9_gate`), alone on the host, under `systemd-inhibit`. Raw logs kept at `/tmp/item9-logs/` (see Tooling for the permanent copies).

| Check | Result |
|---|---|
| Tree state before the run | Detached HEAD at `7e5f0c6`, 0 uncommitted changes, 0 untracked files (besides the worktree's own `.env`/`settings_worktree.py`, neither tracked by git) |
| Fingerprint before | tree `675c591e…`, tracked-content sha256 `967d0a0d…` |
| `pre-commit run --all-files` | All 24 hooks pass, repo-wide, including `flake8` with E800 fully enforced (no `--per-file-ignores` entries left) |
| `flake8 --select=E800 .` | 0 files, 0 hits repository-wide (only the harmless `\D`/`\Delta` `SyntaxWarning` noted earlier, which E800 does not flag) |
| `manage.py check` | System check identified no issues |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| Full suite, fresh DB, no `--keepdb`, `--parallel 1` | First attempt: **4232 tests OK, errors=2, skipped=22** — both errors were `users.tests_google_auth.LiveGoogleEndpointContractTests`, a class documented as making real, unmocked calls to Google's live OAuth endpoints; a `NameResolutionError` mid-run pointed to a transient DNS blip, not a code defect. Confirmed by re-running just those two tests immediately after (`OK`, both passed) and by manually resolving/connecting to both Google hosts from the shell (both succeeded). The test DB was dropped and the full suite re-run from scratch to get one clean pass rather than accept a partial result. |
| Full suite, second attempt, fresh DB | **Ran 4232 tests in 2249.369s — OK (skipped=22)**, exit 0. Log: `/tmp/item9-logs/gate-full-suite-2.log` |
| Test DB teardown | `Destroying test database for alias 'default'...`; `test_item9_gate` absent from `pg_database` and 0 rows in `pg_stat_activity` afterward |
| Fingerprint after | tree `675c591e…` (unchanged), tracked-content sha256 `967d0a0d…` (unchanged), 0 untracked files, 0 bytes diff vs `HEAD` |
| Sleep/idle | Inhibited for the whole run via `systemd-inhibit --what=sleep:idle` |

**Verdict: PASS.** Every check above held; nothing was accepted on a partial or re-run-until-green basis except the one documented, independently-confirmed network flake.

## Coordination and landing

Before merging, checked every branch in flight for two things: whether it touches the same shared docs/config files this work edits, and whether landing would let a new E800 violation slip past unnoticed now that the register is gone.

- **Shared files:** none of the in-flight branches (`task/h1-h2-release-gate`, `task/h1-stage3-wildcard-removal`, `task/h1-stampede-evidence`, `task/h1-user-fanout`, `task/h10-integration`, `task/h16-submission-list-queries`, `task/h9-redis-db-isolation`, `task/section-7-students-review`, `task/section-9-cleanup`) had any diff against `beta` in `.pre-commit-config.yaml`, `docs/HARDENING_BACKLOG.md`, `docs/CODEBASE_AUDIT_SECTIONS.md` or `docs/evidence/SECTION_8_DASHBOARD_REMEDIATION_EVIDENCE.md`. No merge conflict was possible.
- **Hidden violations:** `beta` itself (which item 9 is fast-forwarded onto) has 0 E800 hits, so nothing is hidden on the branch being merged. The in-flight branches above currently show 345–964 E800 hits each when scanned directly — this is expected and not a new violation: none of them have merged item 9's cleanup yet, so they still carry the pre-cleanup baseline (or, for the two at 964, additional comment-heavy test files of their own). Retiring the register does not hide these: it removes the only thing that could have hidden them (a per-file exemption). Each of these branches will have its own diff checked, unconditionally, by the same `flake8` E800 hook the moment it merges into `beta` — there is no longer an exemption list for a new violation to hide behind.
- **Fast-forward, not a merge commit:** `task/item9-e800-burndown` had `beta` (`53e31c3`) as a strict ancestor, so landing was a fast-forward, not a three-way merge — nothing to overwrite or revert. Built and verified in a temporary detached worktree first (`git merge --ff-only a7c81a4` from `53e31c3`, confirmed the expected 40-file diff), then the `beta` ref itself was moved with a compare-and-swap `git update-ref` (old value `53e31c3`, new value `a7c81a4`), which fails closed if anyone had moved `beta` in between. The shared main checkout's index/working tree were stale after the ref move (expected: `update-ref` doesn't touch a worktree) and were synced with `git reset --hard HEAD` after confirming the pending diff exactly matched the fast-forward's own diff — no foreign uncommitted work was present or discarded. Two pre-existing untracked directories (`docs/backend/phase 2/`, `docs/phase2/`), unrelated to item 9 and present before this session started, were left untouched throughout.

## Post-merge verification (on `beta` at `a7c81a4`)

| Check | Result |
|---|---|
| Repository-wide `flake8 --select=E800 .` | 0 hits, exit 0 |
| `pre-commit run --all-files` | All 24 hooks pass. Log: `/tmp/item9-logs/beta-postmerge-precommit.log` |
| `manage.py check` | System check identified no issues |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| Regression: `AutoGrader ai_processor assignments billing classrooms templates users` (`--keepdb`) | **3762 tests OK, skipped=19**, exit 0, in 1604.7s. Log: `/tmp/item9-logs/beta-postmerge-regression.log` |
| Tree/worktree integrity | `git fsck` reports only dangling objects (expected in an actively-used multi-worktree repo), no errors; every other active worktree's `git status` was checked and none was disturbed by the `beta` ref move |

**H-12 retirement:** done in the same pass as this verification, directly on `beta`. `--per-file-ignores` and its explanatory comment block are removed from `.pre-commit-config.yaml`; the H-12 register (file/hits table, staged-plan rules, per-cleanup evidence checklist) is deleted from `docs/HARDENING_BACKLOG.md`, with the section's history and this closure kept as its permanent record; the H-12 row in the backlog's summary table is marked CLOSED.

## Closed

Item 9 is finished: repository-wide cleanup done, strict final gate passed, landed on `beta` at `a7c81a4`, post-merge verification passed, exemption mechanism retired. It should not be reopened except for a new, concrete E800 violation.

## Tooling (permanent, outside the repo)

`/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-gate-logs/item9/` holds:

- `inventory.json` and `inventory-context.txt`: every hit with its context
- `clean_section.py`: the verified runner
- `section-N.json`: the exact edits per commit, with line assertions
- `section-N.log`: verification output
- `section-6-first-attempt/`: backup of the refused first billing attempt
- `strict_final_gate/`: raw logs from the strict final gate on `7e5f0c6` — `gate-full-suite.log` (first attempt, 2 network-flake errors), `gate-full-suite-2.log` (clean re-run, the one the PASS verdict rests on), `precommit-all-files.log`, plus the earlier per-app regression logs (`full-suite-regression.log`, `ai_processor-regression.log`, `assignments-regression.log`)
- `strict_final_gate/beta-postmerge-precommit.log`, `strict_final_gate/beta-postmerge-regression.log`: the post-merge verification on `beta` at `a7c81a4`
