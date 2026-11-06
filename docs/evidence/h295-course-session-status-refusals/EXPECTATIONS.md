# H-295 expectations (written before the code)

Owner go 2026-11-05 ("1. Yes") for the H-294 bugs B1 and B2. B3 (publish-all
200) waits for the owner's answer on the recommendation (doc section 8).

- B1 red: classrooms/tests_h295_course_without_session.py, 6 tests; before
  the fix the 3 no-session tests fail (500), the 3 controls pass.
- B2 red: users/tests_h295_status_refusal_message.py, 5 tests; before the
  fix 3 fail (unit pair + 2 API), 2 controls pass.
  (Risk: the API tests need a teacher with no subscription; signup grants a
  trial in signals - if `subscription-status` finds one, the test setup is
  wrong, not the fix; will report.)
- Green: 11/11.
- Plants: B1 drop the None guard -> 3 fail; B2 drop the pair rule -> 3 fail.
- Near run: classrooms + users + billing, OK.

## Results
- Red 6/11 failing, exactly as expected (red.txt). Green 11/11 (green.txt).
- Plants: B1 guard off -> 3 fail; B2 pair rule off -> 3 fail; both restored, sha256 match (plant.txt).
- Near: classrooms + users + billing Ran 4004 OK (skipped=4) (near.txt; full log GAP-a0-runs/h295-near.log).
