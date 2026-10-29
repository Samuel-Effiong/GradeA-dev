# S3 conflict merge: `task/epic-a-s3` `2011641` into phase2/epic-a `2c950aa`. Resolution advice from ed

0b performs the merge (founder decision); ed supplies the resolution. Nothing here is committed on a branch. Computed read-only with `git merge-tree --write-tree phase2/epic-a task/epic-a-s3` (tree `6274e2aaac184f200b9af37686d69c9e08be3b03`). No test was run by ed.

## Conflicts: 3 files, all unions (v2's N1: keep every side). Take `files/audit/*.py` whole.
| File | Resolution |
|---|---|
| `audit/context.py` | `RequestAuditState.__slots__ = ("stored_event_ids", "suppressed", "request")`; `__init__(request=None)` sets all three, with both comments. Everything else auto-merged: `request_audit_state(request)`, `current_request_actor()`, `record_suppressed_event`, `a_stored_event_survives`, `a_surviving_event_names`. |
| `audit/enums.py` | S2's `ACCOUNT_REGISTER`, then S3's `AUDIT_RETENTION_SWEEP`. `ReasonCode` (epic side) is untouched: `INVALID_REQUEST`, `SERVER_ERROR`, `FAILED_AUTH_CAPPED`, `VERIFY_LOCKED` all present. |
| `audit/metadata.py` | `METADATA_ALLOWLIST`: STATE_CHANGE keeps S1b's summary keys; ACCOUNT_REGISTER (S2 + S1b keys); AUDIT_RETENTION_SWEEP (S3). The auto-merged parts are checked: `ALLOWED_KEYS` has `ledger_id`, the three sweep counts and the four S1b summary keys; CREDIT_TRANSACTION has `ledger_id`; AUTH_LOGIN keeps `lock_triggered` + the summary keys. |

**Auto-merged and checked:**
- `audit/middleware.py`: `with request_audit_state(request)`, S1's generic event, and S2's `emit_anonymous_refusal` behind `not state.suppressed`.
- `billing/models.py` and `billing/license_service.py` merge with disjoint hunks: S3's actor rule, the clawback through `expire_bucket` and the rollover log, beside beta's H-56/H-58/H-59 hunks. Beta's side adds no bare `.update(expires_at=...)` and no new email log line.
- S3 adds no raw cache write, so the guard needs no entry. Since S3's base, the epic side added no test that pins the old credit-event actor.

black 25.1 and flake8 (the hook's arguments) are clean on the three files. `SHA256SUMS` covers them.

## Gates (rule 15)
**Changed modules:**
- `audit.tests_background_attribution`
- `billing.tests.test_credit_transaction_audit`
- `audit.tests_license_admin_attribution`
- `audit.tests_state_change`
- `audit.tests_failed_auth_cap`
- `audit.tests_route_coverage`
- `users.tests_auth_audit_doors`
- `audit.tests_emitter`
- `audit.tests_metadata`
- `AutoGrader.tests_reason_codes`
- `AutoGrader.tests_cache_invalidation_coverage`
- plus v2's probes, which v2 runs: `tests_vf2_s1_attribution`, `tests_vf_s1_probe`, `tests_vf2_s2r1_probe`, `tests_vf2_s1b_probe`, `tests_vf2_s3_probe`.

**ONE regression:** `billing` is recommended. The audit conflicts are covered by the changed modules above. What nothing else exercises is the billing auto-merge (S3's clawback and actor rule in the same files as beta's H-58/59 seat counting).
