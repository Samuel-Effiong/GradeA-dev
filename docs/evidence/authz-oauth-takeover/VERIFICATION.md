# AUTHZ-OAUTH — Verification: prod exposure query wrapper (delta 26715f8..df3bd2c)

Branch `task/authz-oauth-takeover` @ df3bd2c (baseline `26715f8` already VERIFIED-WITH-NOTES: 48 OK/3 skipped, M1-M3 killed, exploit works on beta ea7183b and is refused on the tip, Gate 10 open).

This delta adds one file, `docs/evidence/authz-oauth-takeover/run_prod_exposure_query.py` — a runnable wrapper for `prod_exposure_query.sql`. No fix code changed.

## Reviewed
- `prod_exposure_query.sql`: single SELECT (JOIN users_customuser/users_usergooglecredentials), one trailing `;`. Grepped for INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CALL/GRANT/REVOKE outside comments — none found (the one "update" hit is prose, in a comment about `last_login` not being updated).
- Read-only enforcement is layered: connection opened with `-c default_transaction_read_only=on`, then `conn.set_session(readonly=True, autocommit=False)`, then the script itself runs `SHOW transaction_read_only` and aborts before executing the query if it isn't `'on'`. Always `conn.rollback()` + `close()` in `finally`.
- Console output: only `transaction_read_only`, row count, and per-row `id`, `date_joined`, `activated_at` — checked against the SELECT's full column list (`id, email, user_type, date_joined, activated_at, last_login, activation_expires, school_id`); the print block never indexes `email`, `user_type`, `school_id`, or `last_login`. No credentials anywhere in the script.
- Full result (with email) is written only to a CSV opened with `os.open(..., O_CREAT|O_TRUNC, 0o600)` — created private from the start, no window where it's world-readable — and the script refuses an output path under the repo root.
- `python -m ast` parses the file cleanly. No automated test exercises it (needs a live Postgres); none expected — it's a manual, founder-run operational tool, not app code exercised by the suite.

## Note (non-blocking)
`os.open` with `O_CREAT|O_TRUNC` follows symlinks (no `O_NOFOLLOW`): if the output path already existed as a symlink, the script would overwrite the link's target instead of refusing. Low severity given it's founder-run locally with a fresh home-directory timestamp path by default — worth an `O_NOFOLLOW` if this script is ever reused elsewhere.

## Verdict: VERIFIED
Read-only guarantees hold at three independent layers, unconditional rollback; no email or credential ever reaches stdout; the only PII output is a private file outside the repo. Matches the founder's approval to run this read-only on prod. Baseline verdict for the fix itself (26715f8) is unchanged: VERIFIED-WITH-NOTES, Gate 10 still open.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
