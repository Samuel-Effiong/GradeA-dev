# H-276 mutation battery: expectation (committed before the run)

Code under test: a8e39de9. Script: mutants.py beside this file (sha256-verified restore after each).
Tests: users.tests_h276_per_device_logout, users.tests_token_revocation.

| M | Guard removed | Expected killer |
|---|---|---|
| 1 | sid stamped at sign-in | every-sign-in-gets-a-sid; others stay signed in (falls back to global) |
| 2 | auth rejects a revoked sid | logged-out browser signed out instantly |
| 3 | refresh rejects a revoked sid | refreshed-session test / instant sign-out via refresh (refresh is blacklisted anyway, so this may survive; if it does, it's equivalent and argued below) |
| 4 | logout revokes the caller's sid | instant sign-out; T1 |
| 5 | no epoch bump on logout | others stay signed in; epoch not bumped |
| 6 | same-user guard on the posted refresh | someone-else's-refresh test |
| 7 | legacy fallback (no sid → global) | legacy token test |
| 8 | prune old rows | prune test |
| 9 | revocation lookup by sid | instant sign-out |

Expected: all applied, 8 or 9 KILLED, every restore sha256 OK. Only M-3 may survive, as an
equivalent (the posted refresh is also blacklisted, so simplejwt refuses it before our check
matters). Any other survivor = STOP.
