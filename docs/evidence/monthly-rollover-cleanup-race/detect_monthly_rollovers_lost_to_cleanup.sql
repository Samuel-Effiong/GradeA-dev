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
-- What it cost. unused_credits_written_off_raw is the unused balance
-- written off (raw units; display = raw / 1000). The carry-over the
-- customer actually lost is the plan's carry_over_percent of it, capped by
-- carry_over_max and max_bank, so this is an UPPER BOUND; it is 0 lost on a
-- plan with no carry-over (but those customers still had the day without
-- monthly credits). Review before compensating: a customer who lapsed and
-- re-subscribed within 3 days is reported too.
--
-- Output: ids, counts, credit totals and timestamps only. No emails.
WITH cleanup_write_offs AS (
    SELECT l.user_id, b.wallet_id, l.amount, l.created_at
    FROM billing_creditledger AS l
    JOIN billing_creditbucket AS b ON b.id = l.bucket_id
    WHERE l.ledger_type = 'EXPIRE'
      AND b.bucket_type = 'MONTHLY'
      AND l.reference = 'Automatic expiration of MONTHLY bucket.'
)
SELECT
    w.wallet_id,
    w.user_id,
    COUNT(*) AS months_lost,
    SUM(w.amount) AS unused_credits_written_off_raw,
    MIN(w.created_at) AS first_write_off_at,
    MAX(w.created_at) AS last_write_off_at
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
GROUP BY w.wallet_id, w.user_id
ORDER BY first_write_off_at;
