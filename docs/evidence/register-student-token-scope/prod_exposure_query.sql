-- H-47: was POST /auth/register/student used to take over a non-student
-- account? READ-ONLY. The founder runs this; nobody else runs it against
-- production.
--
--   railway run psql "$DATABASE_PUBLIC_URL" -f prod_exposure_query.sql
--
-- Everything runs inside a READ ONLY transaction that is rolled back. No
-- query selects an email, name, password or token, so the output contains
-- internal ids, types and timestamps only.
--
-- WHAT THE DATABASE CAN'T TELL YOU: which endpoint activated a row. Both
-- /auth/verify (the legitimate path) and /auth/register/student clear
-- activation_token and stamp email_verified_at. So query 1 lists every
-- CANDIDATE, and the proof comes from the HTTP logs: register_student stamps
-- email_verified_at = now() in the same request that answers 200. For each
-- candidate, look for a `POST /auth/register/student` 200 within a second or
-- two of its email_verified_at. A match is a takeover; no match means that
-- account went through /auth/verify (or Google) as normal. Guessing runs show
-- up in the same logs as bursts of 400s on that path (beta logged nothing
-- per failure before the H-47 fix).

BEGIN TRANSACTION READ ONLY;

-- 1. Candidates: non-student, self-registered, now active and verified.
SELECT
    u.id,
    u.user_type,
    u.date_joined,
    u.email_verified_at,
    EXTRACT(EPOCH FROM (u.email_verified_at - u.date_joined))::bigint
        AS seconds_from_join_to_verified
FROM users_customuser AS u
WHERE u.user_type <> 'STUDENT'
  AND u.registration_method = 'EMAIL'
  AND u.is_active
  AND u.email_verified_at IS NOT NULL
  AND u.activation_token IS NULL
ORDER BY u.email_verified_at;

-- 2. Exposure right now: how many live 6-digit codes a guesser is guessing
--    against at once (the pool size), by account type.
SELECT
    u.user_type,
    COUNT(*) FILTER (WHERE u.activation_expires > now()) AS live_codes,
    COUNT(*)                                           AS pending_with_6_digit_code
FROM users_customuser AS u
WHERE NOT u.is_active
  AND u.activation_token ~ '^[0-9]{6}$'
GROUP BY u.user_type
ORDER BY u.user_type;

ROLLBACK;
