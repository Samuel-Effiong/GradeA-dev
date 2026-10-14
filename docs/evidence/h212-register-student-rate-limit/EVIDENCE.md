# H-212: the closed student door's rate limit has tests (tests only; expectations written BEFORE any run)

Written by ed (Security Engineer), 2026-10-09 18:5x WAT. Nothing has been run. Base: beta b4fda750. No production file changes.

## What is pinned
`users/views.py register_student`: `throttle_classes=[RegisterThrottle]` (scope "register", `AnonRateThrottle`, key = the caller's address; `users/throttling.py` overrides no key). The scope is shared with register/school-admin and renew-student-token (users/tests_throttle_client_identity.py documents the pooling); the new tests use only register/student, so the pooling cannot disturb them.
New module `users/tests_register_student_rate_limit` (3 tests). Setup: LocMem cache, `SimpleRateThrottle.THROTTLE_RATES` patched to register 3/hour, anon and login 1000/min; the address is set with REMOTE_ADDR (DRF's `get_ident` returns it when no X-Forwarded-For is sent and NUM_PROXIES is set).

## Hand traces (real code)
- Test 1: requests 1-3 pass `allow_request` (history 0,1,2 < 3) and get the H-152 410; request 4 sees 3 stamps in the hour, `allow_request` False -> DRF `throttled` -> 429.
- Test 2: the bucket key contains the address, so a request from OTHER_ADDRESS has empty history -> 410.
- Test 3: a fifth request is still refused (history stays 3, no new stamp is written on a refusal) -> 429.

## Expected results (no production change, so everything is green on base)
These are tests of existing behaviour: all three PASS on beta b4fda750. They are proven able to fail only by the mutants below (rule 19).
Mutants of users/views.py register_student:
- D4a delete the `throttle_classes=[RegisterThrottle]` line: test 1 fails with the list form `[410, 410, 410, 410] != [410, 410, 410, 429]` and `First differing element 3: 410 429` (its written fragment is `First differing element 3: 410 429`), test 3 fails with the bare `410 != 429`; test 2 stays green (killed by 1 and 3).
- D4b replace it with `throttle_classes=[LoginThrottle]` (scope "login", patched to 1000/min): same set as D4a.
- D4c replace it with `throttle_classes=[]`: same set as D4a.
- D4d leave the class but patch `RegisterThrottle.scope` to "anon" via the file (`scope = "anon"`, rate 1000/min): same set as D4a.
Not guarded (named): a throttle whose key is NOT the address (one global bucket) would turn test 2 red only if the class were replaced by one with a constant key; no such class exists, so test 2 is a control and is not claimed to kill any mutant.
Gate list for the grant: users.tests_register_student_rate_limit, users.tests_throttle_client_identity, users.tests_old_activation_door_is_closed. No payload change (rule 20 not touched).

## Limit (Verifier 1's note 2)
The tests patch the rate to 3/hour, so they prove the throttle is attached to the door, not the production budget: changing the production "register" rate (10/hour in AutoGrader/settings.py) or dropping its key keeps all three green. Not claimed.

## The 429 carries RATE_LIMITED (Builder 58, 2026-10-14, SM's order; base beta 913f248f via a6's update da4a1665)

The user's commit 156495e5 gave every plain DRF `Throttled` the generic coded envelope (`custom_exception_handler` in users/exceptions.py, the `elif isinstance(exc, Throttled)` branch). `RegisterThrottle` is a plain `AnonRateThrottle` with no `.envelope`, so the closed door's 429 takes that branch. Test 1 (on the 4th response) and test 3 now also assert `response.data.get("reason_code") == "RATE_LIMITED"`, read from the key with `.get()` so that a missing key is a clean FAIL, not a KeyError ERROR. Test 2 (410 for another address) is unchanged.

Written reasons (before any run):
- Base 913f248f: all three tests GREEN (no production change in this row).
- New mutant D5 (users/exceptions.py): the generic `elif isinstance(exc, Throttled) ...` branch removed (its `add_coded_envelope(...)` call is never made). Killed by test 1 and test 3, each with fragment `None != 'RATE_LIMITED'` inside its own FAIL block. Test 2 stays green.
- D4a-D4d: unchanged. Test 1 still fails first at its status-list assertion (`First differing element 3: 410 429`), before the new line is reached. Test 3 fails at `410 != 429`.
Gate list addition: users.tests_rate_limited_envelope (the envelope's own module), to show the branch the new assertions lean on.
