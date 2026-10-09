# Verification by Verifier 2 (v2): merge-down b14 (beta batches 12s, 13, 13h, 14 into phase2/epic-a)

Tip verified: `8a83d13b` (merge `a5c4ad11` of origin/beta `8567a7a0` into phase2/epic-a `554fcf11`, merge-base `035e0a07`, 148 non-merge commits
of beta; five commits of 0b after the merge). Author: 0b. Run: Release Engineer's slot 2026-10-09 11:37:25 to 11:39:52 (load 3.17 at start).
Runner `78c5d57357b44396`, script `bac56bb48e6eeb92`, written before the run, from reading the merge blobs. 0b's 17-module run (Ran 414 OK) and his
signature hunt were NOT repeated (rule 15). Nothing here replaces the bundle's ONE full run, which is the judge of the merged tree.

## Word: VERIFIED-WITH-NOTES

## 1. The merge, by reading git objects (nothing run)
- **Files.** Since the merge-base the Phase 2 side changed 1130 files, beta 748, both sides 19. All 1111 files changed only on the Phase 2 side and all 729 changed only on
  beta's side equal their side's blob in the merge: 0 exceptions. Of the 19 both-side files, 15 equal what git's own three-way merge gives, byte for byte
  (assignments/serializers, services, views; billing/license_service, models, services; classrooms/serializers, services/enrollment, services/roster_import, views and three
  test modules; docs/HARDENING_BACKLOG.md; users/serializers.py). Four files, five conflict regions, resolved by hand; each read against both sides:
  a) `classrooms/tests_h99_placeholder_email.py` imports: both kept. Right.
  b) `classrooms/tests_roster_ready_to_use.py` imports: beta's `CROSS_SCHOOL_REJECTION_MESSAGE` dropped. Right: base had the import and one use, the Phase 2 side removed the
     use, beta kept both; the merged file has no use left (0 hits at the tip).
  c) `users/admin.py` `deactivate_users`: `history.record_bulk(queryset, is_active=False, token_epoch=Case(...))`. `audit/history.py` `record_bulk` passes `**changes` to ONE
     `.update()` on the locked rows, and builds the event from `_diff` of the stored before/after rows, so the Case expression is never written into an event.
  d) `users/views.py` imports: the union; `ReasonCode`, `REASON_CODES`, `StudentRegistrationCompletionSerializer` dropped as unused.
  e) `users/views.py` `register_student`: beta's 410 body. The Phase 2 side's budget check, REGISTRATION_PAUSED envelope and sign-in audit events on that door go with it. The renew
     door is closed on beta too (`classrooms/views.py` 1590-1598), so the two removed door tests are door-only.
- **The silent break (clean for git, wrong in meaning).** My own search found what 0b found: at the merge commit the two beta arms of `/auth/verify` (users/views.py 1076 and 1081)
  called the nested `refuse()` with one argument where the Phase 2 function takes three (they would answer 500). Zero at the tip (`d4ff5be7`). A second search: the arity of every
  call to a module-level function with a unique name (1542 of them) and of `self.` methods: no new mismatch at the merge or the tip beyond the parents; a looser method-name pass
  gave one false positive (a logger call). The two scripts are in `tools/`.
- **Migrations.** Beta added none since the base; the Phase 2 side's chain is the only one. No second leaf is possible.
- **Post-merge commits** (eight files): the refuse() fix reads right (same wrong-code answer, the account named, the lock set by the spending guess); the s7d module removed (4 door-only
  tests); the student-invitation audit test now activates through `/auth/verify` (202) and keeps its acting_as check; one audit test removed; a new rate-limit test on the school-admin
  door; the dead pause code marked as H-207's.
- **What 0b's targeted run did not include:** `audit.tests_route_coverage` and `audit.tests_history_guard` (the Phase 2 line's guards on beta's new code). Beta's only new write route
  is PATCH `users/<pk>/student-name`; its only new production `.update()` is the licence roll-up on untracked fields.

## 2. The run
- **Baseline**, nine judged modules (users.tests_switched_off_means_out, tests_auth_audit_doors, tests_reset_for_an_invited_student, tests_models_and_admin, audit.tests_history,
  tests_history_guard, tests_admin_action, users.tests_old_activation_door_is_closed, tests_register_throttle_school_admin): **Ran 203 tests, OK**.
- **Guard step (report-only)**: `audit.tests_route_coverage`: **Ran 22 tests, OK.** Beta's new route passes the Phase 2 line's S2 sweep.
- **14 deliberate faults, one resolved place each, judged by the existing modules, named must-tests written beforehand: 13 KILLED (every must-test failed), restores 14 of 14:**
  A1 no token-epoch bump (3 failing: the 2 named + the already-inactive-row test, which asserts the bump too); A2 bump even an inactive row (1); A3 `deactivate_users` without history
  (2: the named admin audit test and the S4 guard's `test_no_unlisted_bulk_write_of_a_tracked_field`); V1 admin-power verify arm back to one argument (9: the 2 named + 7 tests of the
  same arm); V2 switched-off arm back to one argument (4); V3/V4 the arm names no account (1 each: the new audit test); V5/V6 wrong reason code (1 each); V7 the spending guess sets no
  lock (2: the two lock tests); D1 the closed door answers 400 (6: the 2 named + 4 door tests); D2 the activation names no actor (2); D3 the school-admin door without its rate limit (1: the new test).
- **D4 SURVIVED, as predicted:** the closed student door without its per-address rate limit (`throttle_classes=[RegisterThrottle]` on `register_student`), Ran 12 OK. No test on either
  line covers that limit. Row **H-212** (LOW, tests only, on beta first; numbered by the Senior Manager); an expected survivor, not counted against the merge.

## Notes
- N1: by the Senior Manager's ruling the guard step is report-only and the bundle's one full run judges the merged tree; my baseline and guards cover the nine modules above, not the whole suite.
- N2: the Phase 2 side's S4 guard allows only record_bulk for tracked fields; A3 shows it is live on the merged `deactivate_users`.
- N3: the dead code left by the closed door (the REGISTRATION_PAUSED code and four helpers in `users/throttling.py`) is marked H-207. A search of the tip's non-test, non-docs Python found no caller of the four helpers outside `users/throttling.py` itself (one internal use at line 133, `register_student_budget_retry_after`) and a settings mention; the code is referenced only by its own definitions, by tests and by evidence scripts.

## Files
`runner`, `script`, `tools/` (the arity searches and the lines-per-side script), driver and mutant logs (gzipped) in `logs/`.
