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
- D4a delete the `throttle_classes=[RegisterThrottle]` line: test 1 fails `410 != 429` (the fourth answer: list differs at the last item), test 3 fails `410 != 429`; test 2 stays green (killed by 1 and 3).
- D4b replace it with `throttle_classes=[LoginThrottle]` (scope "login", patched to 1000/min): same set as D4a.
- D4c replace it with `throttle_classes=[]`: same set as D4a.
- D4d leave the class but patch `RegisterThrottle.scope` to "anon" via the file (`scope = "anon"`, rate 1000/min): same set as D4a.
Not guarded (named): a throttle whose key is NOT the address (one global bucket) would turn test 2 red only if the class were replaced by one with a constant key; no such class exists, so test 2 is a control and is not claimed to kill any mutant.
Gate list for the grant: users.tests_register_student_rate_limit, users.tests_throttle_client_identity, users.tests_old_activation_door_is_closed. No payload change (rule 20 not touched).
