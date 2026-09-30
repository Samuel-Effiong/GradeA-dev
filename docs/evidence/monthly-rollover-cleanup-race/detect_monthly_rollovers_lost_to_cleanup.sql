-- READ-ONLY. Detection for package F6: monthly buckets the 05:00 cleanup
-- wrote off although their owner's refresh or renewal was still owed, so
-- the carry-over that refresh would have made was lost.
--
-- How it looks in the ledger. The cleanup (expire_bucket) writes an EXPIRE
-- row "Automatic expiration of MONTHLY bucket." for the bucket's unused
-- credits. When the owner then receives a new MONTHLY grant (the refresh
-- or renewal that should have rolled the bucket over) within 3 days, the
-- write-off happened while that refresh was owed. On the pre-fix code this
-- happened most months for annual individual subscribers and licence
-- teachers, and whenever a monthly plan's renewal arrived after 05:00.
--
-- What it cost. The owner's plan is their active licence allocation's
-- plan, else their most recent subscription's plan, else their most recent
-- allocation's plan. estimated_carry_over_lost_raw applies that plan's
-- carry_over_percent to each written-off amount, the way the rollover
-- does (int(unused * percent / 100); raw units, display = raw / 1000). The
-- rollover caps it only by the plan's max_bank (the wallet's total live
-- carry-over, which depends on the wallet at the time), shown beside it,
-- so the estimate is an UPPER BOUND. carry_over_max is not applied: the
-- rollover doesn't read it. Rows where nothing was lost (a plan with no
-- carry-over) are left out; rows whose plan can't be found are kept, with
-- carry_over_percent NULL, for review. Review before compensating: a
-- customer who lapsed and re-subscribed within 3 days is reported too.
--
-- Output: ids, counts, credit amounts and timestamps only. No emails.
WITH cleanup_write_offs AS (
    SELECT l.user_id, b.wallet_id, l.amount, l.created_at
    FROM billing_creditledger AS l
    JOIN billing_creditbucket AS b ON b.id = l.bucket_id
    WHERE l.ledger_type = 'EXPIRE'
      AND b.bucket_type = 'MONTHLY'
      AND l.reference = 'Automatic expiration of MONTHLY bucket.'
),
lost AS (
    SELECT w.*
    FROM cleanup_write_offs AS w
    WHERE EXISTS (
        SELECT 1
        FROM billing_creditledger AS g
        JOIN billing_creditbucket AS gb ON gb.id = g.bucket_id
        WHERE g.user_id = w.user_id
          AND g.ledger_type = 'GRANT'
          AND gb.bucket_type = 'MONTHLY'
          AND g.created_at > w.created_at
          AND g.created_at <= w.created_at + INTERVAL '3 days'
    )
),
owners AS (
    SELECT
        o.user_id,
        COALESCE(
            (
                SELECT ls.plan_id
                FROM billing_schoolcreditallocation AS a
                JOIN billing_licensesubscription AS ls
                    ON ls.id = a.license_subscription_id
                WHERE a.user_id = o.user_id AND a.is_active AND ls.is_active
                ORDER BY a.created_at DESC
                LIMIT 1
            ),
            (
                SELECT s.plan_id
                FROM billing_usersubscription AS s
                WHERE s.user_id = o.user_id
                ORDER BY s.is_active DESC, s.created_at DESC
                LIMIT 1
            ),
            (
                SELECT ls.plan_id
                FROM billing_schoolcreditallocation AS a
                JOIN billing_licensesubscription AS ls
                    ON ls.id = a.license_subscription_id
                WHERE a.user_id = o.user_id
                ORDER BY a.created_at DESC
                LIMIT 1
            )
        ) AS plan_id
    FROM (SELECT DISTINCT user_id FROM lost) AS o
)
SELECT
    l.wallet_id,
    l.user_id,
    p.id AS plan_id,
    p.carry_over_percent,
    p.max_bank,
    COUNT(*) AS months_lost,
    SUM(l.amount) AS unused_credits_written_off_raw,
    SUM(FLOOR(l.amount * p.carry_over_percent / 100)) AS estimated_carry_over_lost_raw,
    MIN(l.created_at) AS first_write_off_at,
    MAX(l.created_at) AS last_write_off_at
FROM lost AS l
JOIN owners AS o ON o.user_id = l.user_id
LEFT JOIN billing_subscriptionplan AS p ON p.id = o.plan_id
GROUP BY l.wallet_id, l.user_id, p.id, p.carry_over_percent, p.max_bank
HAVING p.id IS NULL OR SUM(FLOOR(l.amount * p.carry_over_percent / 100)) > 0
ORDER BY first_write_off_at;
