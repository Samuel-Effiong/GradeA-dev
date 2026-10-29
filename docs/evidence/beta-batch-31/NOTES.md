# Beta batch 31

## Bundle

Base: beta `a3f5a6a1` (batch 30 evidence: Ran 8472, OK, skipped=30).

Six --no-ff merges, in order (each row passed Gate 1 at the sha shown):

1. H-269 @ `51423d8e`, carrying H-268 (`77e8865b`): MID_BATCH variant chosen by the spec; overage cap wording
2. H-266 @ `39e5aea4`: list validation errors as a dict (DRF 3.18)
3. H-265 @ `e4059125`: user-facing error text instead of `str(exc)`
4. H-270 @ `8ae54dbf`, carrying H-267 (`94a52815`): upload formats JPEG/PNG/GIF/WEBP; profile pictures stored as metadata-free WebP
5. H-261 @ `cf860f73`: `failure_reason` stores Stripe's error type, not its text
6. H-271 @ `6783941f`, carrying H-262 (`7eec85e0`): dev tools out of the prod image; collectstatic + WhiteNoise

Bundle tip: the merge of H-271 (`279aa182`); this NOTES commit sits on it.

One conflict, docs/HARDENING_BACKLOG.md (H-266 merge): kept the H-265 row, took H-266's Built row over
its Open row. H-265 and H-261 both change the overage checkout's `except stripe.error.StripeError`
block in billing/license_service.py; git merged it without a conflict, and the result keeps both
(H-261's `stripe_error_label(exc)` in `failure_reason`, H-265's `describe_stripe_error` in the
raised message). All 14 `stripe_error_label` lines H-261 adds are present in the bundle.

## Expected results

Method: Django `DiscoverRunner` ids on beta `a3f5a6a1` (8472) and on the bundle (8587); sorted id
lists compared with `comm` (ids_removed.txt, ids_added.txt). 0 load failures.

| | Count | |
|---|---|---|
| Base (batch 30) | 8472 | |
| Removed | 2 | `assignments.tests_h263_list_error_shape.RubricErrorShapeTests` x2, replaced by H-266 |
| Added | 117 | H-262 12, H-266 6, H-267 21, H-271 7, H-268/H-269 14 (tests_reason_codes) + 3 (test_overage_cap), H-265 28, H-261 13, H-270 15 |
| **Expected Ran** | **8587** | 8472 - 2 + 117 |

Expected: **Ran 8587, OK, skipped=30**. None of the added tests has a skip; the 30 are batch 30's.
Chromium PDF tests must run: Chromium 145 launches in the venv (checked before the run).

Known and not a test failure: `scripts/pip_audit_gate.py` exits 1 on 14 advisories published after
the accepted lists (authlib, filelock, oauthlib, social-auth-core, tornado), on beta already; owner
row H-272 after this batch. The CI audit job stays red until then.

## Strict run

Private venv `h271_static_files_env` (the bundle's requirements.txt and requirements-dev.txt are
byte-identical to H-271's, from which it was built; Django 5.2.17, whitenoise 6.12.0).
Runner: GAP-a0-runs/b29/strict_bundle.sh, copied to GAP-a0-runs/b31/ with the venv switched to the
private one (the original activates the shared AutoGrader_env) and the log folder to b31/.
`--settings=settings_worktree` (DB `test_beta_batch_31`), `--parallel 4`, mypy and makemigrations
pre-checks, load/memory guards and the 300 s stall watchdog as in the script.

## Run 1 on f85790bc: STOP (1 error, H-270's test isolation)
- Ran 8587 (as expected), `FAILED (errors=1, skipped=30)`; mypy and makemigrations passed, no stall,
  no memory stop, 0 blocked outbound calls, wall 527 s.
- ERROR `users.tests_h270_profile_webp.SavesThatKeepThePictureTests.test_an_old_jpeg_picture_still_loads_unchanged`:
  `FileNotFoundError: ... /tmp/h270_media_<x>/profile_pics/old/legacy.jpg`.
- Cause (SM; H-270 is the SM's row): the test module makes ONE temp `MEDIA_ROOT` at import, and every
  test class's `tearDownClass` deletes it. `--parallel 4` forks the workers after import, so all
  workers share that path; a class finishing on one worker deleted the file another worker had
  just saved. H-270's gate ran its tests sequentially and could not see it. Test code only; the
  production change is not involved.
- Next: H-270 run 2 on its own branch (a temp media folder per test, removed by that test), gated
  with a parallel run of its module; the fixed tip is merged into this bundle and the full run is
  repeated as run 2. Logs: run1_strict_summary.txt, run1_error_block.txt; the full log (683 KB gzipped, over the hook limit) is archived at GAP-a0-runs/b31/run1_full_run.txt.gz, sha256 a49eabac5448ada6….

## Run 2 expectations (committed before run 2)
H-270 run 2 (Gate 1 green at a49b4a0d: per-test media folder, five --parallel 3 runs OK) is merged at
4b91c643; outside docs it changes only users/tests_h270_profile_webp.py, with the same test names.
Expected, unchanged: **Ran 8587, OK, skipped=30**. Same runner, venv and settings as run 1.

## Run 2 on 126b5303: GREEN, matches
- **Ran 8587, OK (skipped=30)**: mypy passed, makemigrations clean, watchdog never fired, no memory
  stop (min available 8630 MB), 0 blocked outbound calls, wall 532 s (run2_strict_summary.txt).
- The 30 skip reasons are identical to batch 30's, all opt-in; no Chromium/Playwright skip, so the
  PDF tests ran (run2_skips.txt).
- Full log archived at GAP-a0-runs/b31/run2_full_run.txt.gz (668 KB, over the hook limit),
  sha256 443d4ffdd833392e….
