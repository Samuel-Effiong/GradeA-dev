-- READ-ONLY. Detection for package F6: annual subscriptions that were given
-- the same mid-cycle month of credits more than once, because two
-- process_annual_plan_credit_grants runs overlapped (a redeploy overlap, a
-- second Beat, a manual run) before the re-check under the row lock.
--
-- How a double grant looks in the ledger. Every mid-cycle grant writes one
-- GRANT row with metadata grant_type = 'ANNUAL_MID_CYCLE' and the
-- subscription's id. Genuine grants for one subscription are a month apart,
-- because each moves next_credit_grant_at on by a month. A grant less than
-- 14 days after the previous one for the same subscription is the same
-- month granted again.
--
-- What it cost. The second run retired the month the first run had just
-- granted, rolled part of it over as a CARRY_OVER bucket ("Mid-cycle
-- rollover ..."), then granted the month again. So the customer kept one
-- month's credits plus that unearned carry-over, which is
-- extra_carry_over_credits_raw below (raw units; display = raw / 1000).
-- It is 0 on a plan with no carry-over. The ledger also shows one extra
-- month's GRANT row (extra_monthly_grant_credits_raw).
--
-- Output: ids, counts, credit totals and timestamps only. No emails.
WITH grants AS (
    SELECT
        l.metadata ->> 'subscription_id' AS subscription_id,
        b.wallet_id,
        l.amount,
        l.created_at,
        LAG(l.created_at) OVER (
            PARTITION BY l.metadata ->> 'subscription_id'
            ORDER BY l.created_at, l.id
        ) AS previous_grant_at
    FROM billing_creditledger AS l
    JOIN billing_creditbucket AS b ON b.id = l.bucket_id
    WHERE l.ledger_type = 'GRANT'
      AND l.metadata ->> 'grant_type' = 'ANNUAL_MID_CYCLE'
),
extra_grants AS (
    SELECT *
    FROM grants
    WHERE previous_grant_at IS NOT NULL
      AND created_at - previous_grant_at < INTERVAL '14 days'
)
SELECT
    e.subscription_id,
    e.wallet_id,
    COUNT(*) AS extra_grants,
    SUM(e.amount) AS extra_monthly_grant_credits_raw,
    COALESCE(SUM((
        -- The duplicate run's own rollover: written after the previous
        -- grant and before (in the same transaction as) the extra grant.
        SELECT SUM(c.amount)
        FROM billing_creditledger AS c
        WHERE c.ledger_type = 'GRANT'
          AND c.metadata ->> 'subscription_id' = e.subscription_id
          AND c.reference LIKE 'Mid-cycle rollover within annual plan %'
          AND c.created_at > e.previous_grant_at
          AND c.created_at <= e.created_at
    )), 0) AS extra_carry_over_credits_raw,
    MIN(e.created_at) AS first_extra_grant_at,
    MAX(e.created_at) AS last_extra_grant_at
FROM extra_grants AS e
GROUP BY e.subscription_id, e.wallet_id
ORDER BY first_extra_grant_at;
