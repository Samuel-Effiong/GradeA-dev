# H-291: email addresses are encoded in emailed links

Owner go 2026-11-05 ("apply the fix on the backend, have them encoded").
Reported case: emanprojectz+test11@gmail.com could not activate. The
frontend read the "+" as a space and its form check rejected the address
(raw Zod error on screen). SM does it. Branch `task/h291-email-link-encoding`
from beta a3ca097d8; test DB `test_h291_email_link_encoding`; venv
`~/Documents/Virtualenvs/h284_deps_env`. NO full suite; near runs only.

## Sites (all three that put an address in a link; grep `?email=`/`&email=`)

| Link | Site |
|---|---|
| activation (all non-admin sign-ups) | users/services.py:100 |
| school-admin invite (legacy resend path) | classrooms/serializers.py:934 |
| student invite | classrooms/services/notifications.py:42 |

## Design

`urllib.parse.quote(value, safe="@")` on the email (and on the token where it
is an arbitrary string). "+" -> %2B, "&" -> %26, space etc. encoded; "@" stays
readable so ordinary links look exactly as before (the existing school-admin
test that pins `?email=admin@example.com&token=test-token-123` still holds).
No new wording.

## Expectations (written before the red run)

Test: `users.tests_h291_email_link_encoding` (7 tests) + the existing
`classrooms.test_school_admin_invitation_email`, `users.tests_activation_email_domain`.

E1, red on beta + tests: Ran 7 (the new module), FAILED (failures=5):
activation plus, student activation plus, school-admin plus, student invite
plus, student invite ampersand. PASS (2): token reads back, plain address
unchanged.

E2, after the fix: new module Ran 7 OK; the two existing modules OK.

E3: one mutant per site (revert that site's encoding) -> that site's test fails.

E4, near run: `users classrooms`, OK (count recorded from the run).

## Results

- E1 red (933fd142): Ran 7, FAILED (failures=5), exactly the five named - met.
- E2 green (fix d4d9c570): new module + the two existing modules Ran 12 OK - met.
- E3 mutants, each restored by sha256, all killed: M-h291.1 activation (2 failures),
  M-h291.2 school-admin invite (1), M-h291.3 student invite (2).
- E4 near run `users classrooms` on the fix: Ran 1626, OK (skipped=4) - met.
  Summary `logs/near_summary.txt`; full log GAP-a0-runs/h291_near.log.
