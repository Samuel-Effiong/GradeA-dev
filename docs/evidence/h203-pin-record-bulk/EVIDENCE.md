# H-203 follow-up: the roads pin sees the audit history helper (tests only; expectations written BEFORE any run)

Written by ed (Security Engineer), 2026-10-09 20:0x WAT. Branch `task/h203-pin-record-bulk` off the merge-down b16 tip 72bce4fd (Senior Manager's and Release Engineer's orders). Only `users/tests_roads_that_sign_in_or_activate.py` and this folder change; no production file.

## The fault (read; observed by the Release Engineer's run on 72bce4fd)
On the Phase 2 line `users/admin.py` activates users with `history.record_bulk(queryset, is_active=True)`; the pin's ACTIVE_TRUE pattern knew only `.is_active = True`, `update(is_active=True` (first keyword) and `"is_active": True`. Result: `('users/admin.py', 'ACTIVE_TRUE'): found 0, pinned 1`, the pin test red. On beta the site was `queryset.update(is_active=True)`.

## What the helpers are (audit/history.py read, 2026-10-09)
The only function that WRITES through the history layer is `record_bulk(queryset, *, actor=..., **changes)` (`.update(**changes)` under a row lock). `suppressed()` and `acting_as()` are context managers; `record_run` records a grading run; the per-row history is written by post_save/pre_delete signals on an ordinary `.save()`, which the existing patterns see (`.is_active = True`). There is no `record`/`record_update`/`bulk_update` helper. Callers of `record_bulk` in production: users/admin.py (activate, deactivate), students/views.py (publish, resolve), assignments/views.py, billing/services.py (is_active=False on a subscription), classrooms/services/enrollment.py and users/views.py (enrollment_status), each with keyword changes; only users/admin.py writes `is_active=True`.

## The change (users/tests_roads_that_sign_in_or_activate.py)
- `_WRITE_CALL`: `update(` or `record_bulk(` followed by arguments (two levels of nested parentheses, any lines), so a keyword anywhere in the call is seen. ACTIVE_TRUE uses it (`is_active=True` as ANY keyword of an update/record_bulk call) and gains `setattr(obj, "is_active", True)`.
- VERIFIED_WRITE: the bare `email_verified_at=<not None>` already matched inside `update(...)` and `record_bulk(...)` (a keyword anywhere); it gains `setattr(obj, "email_verified_at", <not None>)` (written so a space before `None` cannot slip past the look-ahead).
- Password kinds: `PASSWORD_KEYWORD` (`password=` anywhere) and `SET_PASSWORD` already see a `record_bulk(..., password=...)`; test added. Limits kept: `"password":` as a dict key or a serializer field, a helper with another name, `is_active=<variable>` and a `**changes` dict built elsewhere are not seen.
- New class `PatternReachTests` (8 tests, SimpleTestCase, made-up snippets): ACTIVE_TRUE through the helper, across lines with a nested filter, as a later keyword of `update`, through setattr, and False/filter lookalikes are not sites; email_verified through the helper and setattr (2 hits), cleared to None not a site; a password through the helper seen as a keyword.

## Results by plain Python over the trees (reading; no test run), 20:0x WAT
- **Merged b16 tree (72bce4fd) with the new patterns:** the pin matches the tree exactly: the only new find is `users/admin.py ACTIVE_TRUE` (found 1, pinned 1), no other difference in any of the seven kinds, no new road.
- **Beta checkout (b4fda750) old patterns vs new patterns:** no difference in any kind (beta's form is `update(is_active=True`).
- Predicted by calling the regexes directly before the module is run: the 8 new tests' expected counts hold (a first attempt showed `setattr(user, "email_verified_at", None)` counted 1 because `\s*` backtracked below a look-ahead; fixed by moving the look-ahead before the `\s*`).

## Written expectations (before any run)
- **Module run** `users.tests_roads_that_sign_in_or_activate`: Ran 10 (2 old + 8 new), OK, on this tip. On 72bce4fd's OLD pin file the pin test is red with `('users/admin.py', 'ACTIVE_TRUE'): found 0, pinned 1` (observed by the Release Engineer); the 8 new tests did not exist there.
- **Mutants** (`mutate.py`, 5; run on this tip, own DB):
  - P1 a second `history.record_bulk(queryset, is_active=True)` in users/admin.py: fails `RoadsThatSignInOrActivateTests.test_every_site_is_on_the_named_list_with_its_count` (found 2, pinned 1) only.
  - P2 `history.record_bulk(queryset, email_verified_at=timezone.now())` added in users/admin.py: same test only (VERIFIED_WRITE found 1, pinned 0).
  - P3 `record_bulk` removed from `_WRITE_CALL`: the pin test (admin.py found 0, pinned 1), `test_active_true_is_seen_through_the_history_helper`, `test_active_true_is_seen_across_lines_and_nested_calls`.
  - P4 the setattr form of email_verified_at removed: `test_email_verified_is_seen_through_the_history_helper_and_setattr` only.
  - P5 the call's arguments no longer scanned (`{0}` repetitions): the pin test, the helper test, the across-lines test, `test_active_true_is_seen_when_it_is_not_the_first_keyword`.
  - Not killed by a mutant of their own (named): the setattr/is_active test, the "not a site" test, the None test, the password-helper test; they are assertions on made-up text and are checked by prediction only.
- Gate: this module only; then the Release Engineer's module gate of b16 includes it. No production change, no payload change.
