-- AUTHZ-OAUTH: accounts that MAY already have been taken over through the
-- Google "resurrection" branch. READ-ONLY (single SELECT). Do not run against
-- production without the founder's approval.
--
-- Fingerprint of a row activated by google_auth's resurrection branch:
--   * registration_method = 'EMAIL'   (created via /auth/register; Google
--     never rewrites it, whereas Google-created rows are 'GOOGLE')
--   * has a Google credentials row    (created on any Google sign-in)
--   * activation_token IS NOT NULL    (/auth/verify clears the token when it
--     activates a row; google_auth's resurrection branch never touched it, so
--     a Google-activated row still carries the registration-time token)
--   * usable password                 (Django marks an unusable password with
--     a leading '!'; the fix makes every newly activated row match that)
--   * verified + active
--
-- Every hit is a SUSPECT, not a proof: a legitimate person may have registered
-- with their own password, ignored the email and signed in with Google. This
-- database cannot say whether the password was later USED: the JWT login path
-- does not update last_login (verified in the local lab: the attacker's
-- takeover login returned 200 and last_login stayed NULL), and beta has no
-- per-login audit table (users_useractivity is a heartbeat). Triage therefore
-- means: take the list of suspect emails, check web/auth logs for a
-- POST /auth/login 200 for each after activated_at, and contact the owners of
-- any address where that login came from an unexpected client/IP.
SELECT
    u.id,
    u.email,
    u.user_type,
    u.date_joined,
    u.email_verified_at            AS activated_at,
    u.last_login,
    u.activation_expires,
    u.school_id
FROM users_customuser AS u
JOIN users_usergooglecredentials AS g ON g.user_id = u.id
WHERE u.registration_method = 'EMAIL'
  AND u.activation_token IS NOT NULL
  AND u.email_verified_at IS NOT NULL
  AND u.is_active
  AND u.password <> ''
  AND u.password NOT LIKE '!%'
ORDER BY u.email_verified_at DESC;
