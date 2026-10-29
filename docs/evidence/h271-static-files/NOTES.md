# H-271 gate expectations (SM, written and committed before any gate run)

## Run 3 on 6dc4232d: STOP (pip-audit exit 1, not caused by H-271)
- red, makemigrations, seam: as written. near: Ran 2064, OK (skipped=6), the 6 declared reasons.
- pip-audit exit 1: 14 advisories beyond the accepted lists, all in production packages already on
  the base (authlib 1.6.12 x7, filelock 3.19.1 x2, oauthlib 3.3.1, social-auth-core 4.7.0,
  tornado 6.5.9 x3); none for whitenoise. The same audit run on 7eec85e0 alone gives the same 14
  and exit 1 (SM check, a temporary worktree). They are advisories published after the accepted
  lists were written; they go to the owner as their own row.
- What was wrong: "pip-audit exits 0" depends on the advisory database on the day, not on this
  change. What this row has to show is that it adds no advisory.
- Run 4 expectation (replaces the pip-audit line): the gate audits BASE (temporary worktree) and the
  tip in the same run. The tip's findings (package, version, id) equal BASE's exactly, the exit
  codes are equal, and no finding names whitenoise. Everything else stands. Run 3 logs: run3/.

## Run 4: tip under test is the commit that adds this record.

## Run 3 expectations (SM, committed before run 3)
Everything below the run records stands, with these changes only:
- Base is now **7eec85e0** (H-262 run 3, which fixes the stale pip-audit test), merged in at
  6dc8f637. Tip under test: the commit that adds this section. Red reverts the Dockerfile and
  settings.py to 7eec85e0; neither file changed between f870fb0b and 7eec85e0, so the red lines
  stay as written. The seam (`git diff --name-only 7eec85e0 HEAD`, production files) is the same
  4 files.
- near: **Ran 2064**, exactly `OK (skipped=6)` (DiscoverRunner at 6dc8f637: 2064, 0 load failures;
  H-262's test fix uses subTest, so the count is unchanged). The 6 skip reasons, exactly:
  2 x `a benchmark, run by label with AUDIT_BENCH=1 only`, `CI_REQUIRE_NETWORK not set`,
  `CI_REQUIRE_REDIS not set`, 2 x `live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1`.
  This replaces the "CI_REQUIRE only, number recorded" rule, which run 2 showed was wrong.

## Run 2 on 8a21315c: STOP (near suite, 1 error not caused by H-271)
- red, makemigrations, seam and own (Ran 7, OK): as written.
- near: Ran 2064, `FAILED (errors=1, skipped=6)`. The error is
  `AutoGrader.tests_pip_audit_gate.CommandTests.test_the_command_audits_the_pinned_requirements_strictly`:
  `TypeError: audit_command() missing 1 required positional argument: 'ids'`.
  It fails the same way on the base f870fb0b alone (Ran 12, FAILED (errors=1)): H-262 changed
  `scripts/pip_audit_gate.audit_command(ids)` to `(requirements, ids)` and did not update this test,
  and H-262's near suite did not include it. An H-262 defect; H-262 is reopened for it.
- Also wrong in these expectations: 2 of the 6 skips read `a benchmark, run by label with
  AUDIT_BENCH=1 only` (opt-in, but not `CI_REQUIRE`), so the skip rule would have stopped too.
  The other 4: `CI_REQUIRE_NETWORK not set`, `CI_REQUIRE_REDIS not set`, and 2 x
  `live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1`.
- Next: H-271 is rebased onto H-262's fixed tip, these expectations are corrected (base, near
  count and exact skips) in a new commit, and run 3 re-runs the whole gate. Run 2 logs: run2/.

## Run 1 on ffaf0b6d: STOP (red run, one fragment)
- Ran 6, FAILED (failures=5, errors=1), the 6 declared blocks, and the guard passed: all as written.
- test_collected_files_go_to_their_own_folder failed for the declared reason (STATIC_ROOT `static`,
  not `staticfiles`), but the declared fragment `static') != PosixPath(` is not in the block:
  unittest shortens long reprs, and the message reads `Posix[45 chars]...-static-files/static') !=
  Posix[45 chars]...`. The fragment was wrong, not the code or the test.
- Corrected below to `/static') != Posix` (red and M4, which used the same fragment). Nothing else
  changed. Run 2 re-runs the whole gate. Run 1 logs: run1/.

Tip under test: 88d08de1 (tests 8297285e + e1f1e254, code 88d08de1), base f870fb0b (H-262 evidence
tip; this row changes the Dockerfile and requirements.txt H-262 changed).
Runner: `python -I docs/evidence/h271-static-files/gate.py 88d08de1`, private venv
`h271_static_files_env` (H-262's requirements-dev.txt + whitenoise 6.12.0 + pip-audit 2.9.0,
Django 5.2.17). The runner checks every line below and prints STOP on any difference.

Development runs before this file: the own tests with the code in place, and one production
`collectstatic` (ENVIRONMENT=prod, fake secrets) into a throwaway `staticfiles/`: 169 files copied,
807 post-processed, `staticfiles.json` written, no `editor.html`. The first own run found that
`cloudinary_storage` (first in INSTALLED_APPS) replaces `collectstatic` with a command that reads
`settings.STATICFILES_STORAGE`, removed in Django 5.1, and crashes. That is not in DESIGN.md: the
fix moves `cloudinary_storage` after `django.contrib.staticfiles` (its docs' order for media-only
use) and e1f1e254 adds a test for it. No red run, near suite or mutant has been run.

## Red (call-site revert: Dockerfile and AutoGrader/settings.py at f870fb0b)
whitenoise stays installed (the venv has it), so the tests import and the guard's storage loads.
- "Ran 6", `FAILED (failures=5, errors=1)`, no `_FailedTest`.
  (7 tests; `AdminIsServedAfterCollectstaticTests` errors in setUpClass, so its one test is not run
  and not counted.)
- ERROR `setUpClass`: `has no attribute 'STATICFILES_STORAGE'` (cloudinary_storage's collectstatic).
- FAIL, with fragments:
  - test_the_container_collects_static_files_before_gunicorn_starts: `-1 == -1`
  - test_collectstatic_is_the_staticfiles_command: `'cloudinary_storage' != 'django.contrib.staticfiles'`
  - test_production_static_storage_is_whitenoise_manifest:
    `'django.contrib.staticfiles.storage.ManifestStaticFilesStorage' != 'whitenoise.storage.CompressedManifestStaticFilesStorage'`
  - test_whitenoise_comes_directly_after_the_security_middleware:
    `'whitenoise.middleware.WhiteNoiseMiddleware' not found in [`
  - test_collected_files_go_to_their_own_folder: `/static') != Posix`
- The guard `test_the_admin_login_fails_on_the_missing_manifest` passes (no block).

## Green
- makemigrations --check: no changes.
- seam (`git diff --name-only f870fb0b HEAD`, production files only):
  `.gitignore`, `AutoGrader/settings.py`, `Dockerfile`, `requirements.txt`.
- own `AutoGrader.tests_h271_static_files`: Ran 7, OK.
- near `AutoGrader audit users ai_processor.tests_h262_prod_image_dev_tools`: **Ran 2064**, OK.
  Count from the DiscoverRunner id list: 2057 at f870fb0b + the 7 new = 2064, 0 load failures.
  Skips allowed only with a `CI_REQUIRE` reason; any other skip is a stop. Their number is recorded,
  not declared (not counted beforehand).
  Why these labels: the middleware and INSTALLED_APPS change touch every request, and AutoGrader
  (settings and request plumbing), audit (AuditMiddleware) and users (UserActivityMiddleware, admin)
  are the suites that drive them; the H-262 tests read the Dockerfile and both requirements files.
  Batch 31's full run covers the rest.
- No existing test is expected to change (grep: no test reads STATIC_ROOT, the staticfiles
  backend, the INSTALLED_APPS order or the Dockerfile CMD; the H-262 tests read the Dockerfile's
  stages and the dev-only package list, which whitenoise is not on).
- pip-audit: `python scripts/pip_audit_gate.py` exits 0 (no advisory on whitenoise 6.12.0 beyond
  the accepted lists).

## Mutants (each runs `AutoGrader.tests_h271_static_files`; KILLED = these tests FAIL/ERROR with these fragments)
| id | change | killed by | fragment |
|---|---|---|---|
| M1 | collectstatic removed from the Dockerfile CMD | test_the_container_collects_static_files_before_gunicorn_starts | `-1 == -1` |
| M2 | WhiteNoiseMiddleware line removed | test_whitenoise_comes_directly_after_the_security_middleware; test_the_admin_login_page_and_its_stylesheet_are_served | `not found in [`; `404 != 200` |
| M3 | prod backend back to ManifestStaticFilesStorage | test_production_static_storage_is_whitenoise_manifest | `!= 'whitenoise.storage.CompressedManifestStaticFilesStorage'` |
| M4 | STATIC_ROOT back to `static` | test_collected_files_go_to_their_own_folder | `/static') != Posix` |
| M5 | `cloudinary_storage` moved back to first in INSTALLED_APPS | test_collectstatic_is_the_staticfiles_command; setUpClass | `'cloudinary_storage' != 'django.contrib.staticfiles'`; `has no attribute 'STATICFILES_STORAGE'` |
| M6 | WhiteNoiseMiddleware moved to the end of MIDDLEWARE | test_whitenoise_comes_directly_after_the_security_middleware | `!=` |

Other tests failing as well are recorded as `also_failed`, not a mismatch.

## After batch 31 (deploy proof, on staging)
`/admin/login/` answers 200 and its hashed `admin/css/base.<hash>.css` answers 200 `text/css`;
`/static/editor.html` answers 404; the deploy log shows the collectstatic line before gunicorn.

## Run 4 on 60c6a945: GREEN, matches every line above
- red: Ran 6, FAILED (failures=5, errors=1), the 6 declared blocks and fragments (red_run.txt).
- makemigrations: no changes. seam: .gitignore, AutoGrader/settings.py, Dockerfile, requirements.txt.
- own: Ran 7, OK. near: Ran 2064, OK (skipped=6), the 6 declared reasons (near.txt.gz).
- pip-audit: BASE exit 1 with 14 findings, tip exit 1 with the same 14; none for whitenoise
  (pip_audit_base.txt, pip_audit.txt). The 14 are owner row H-272, after batch 31.
- mutants M1-M6: all KILLED by their declared tests and fragments, also_failed empty
  (mutation_results.json, mutant_logs/). Runner output: gate_out_run4.txt (exit=0).
