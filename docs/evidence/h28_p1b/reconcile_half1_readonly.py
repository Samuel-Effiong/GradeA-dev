#!/usr/bin/env python3
"""H-28 (P1b) reconciliation, HALF 1 — PURE DB, STRICTLY READ-ONLY.

Question: has convert_license_to_offline() already died mid-flight on a real
school, leaving the partially-written shape behind?

The shape this CAN see: billing_method still says STRIPE while
stripe_subscription_id is NULL/blank.

The shape this CANNOT see, and it matters: a school whose
stripe_subscription_id is STILL POPULATED but whose subscription was deleted
at Stripe. That is precisely what convert_license_to_offline() leaves when it
dies AFTER the Stripe delete but BEFORE the local writes — the writes that
would have NULLed the id never ran. So an EMPTY RESULT HERE IS NOT AN
ALL-CLEAR. Only half 2 (live Subscription.retrieve per school) can answer that.

SAFETY: the connection is opened read-only (`SET TRANSACTION READ ONLY` via
psycopg2's readonly flag). Only SELECTs are issued. No writes, no DDL, no
Stripe calls of any kind.
"""

import os
import re
import sys

import psycopg2
import psycopg2.extras

ENV_KEY = os.environ.get("P1B_DB_KEY", "DATABASE_URI")


def dsn_from_env_file(path, key):
    text = open(path).read()
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    if not m:
        sys.exit(f"{key} not found in {path}")
    return m.group(1).strip().strip('"').strip("'")


def redact(dsn):
    return re.sub(r"://[^@]*@", "://<redacted>@", dsn)


def main():
    dsn = dsn_from_env_file(".env", ENV_KEY)
    print(f"TARGET ({ENV_KEY}): {redact(dsn)}")

    conn = psycopg2.connect(dsn, connect_timeout=15)
    # Belt and braces: read-only session, and never commit anything.
    conn.set_session(readonly=True, autocommit=False)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    print("\n=== 0. sanity: is this the database I think it is? ===")
    cur.execute("SELECT current_database(), inet_server_addr()::text, version()")
    db, host, ver = cur.fetchone()
    print(f"database={db} server={host} {ver.split(',')[0]}")

    print("\n=== 1. licence population ===")
    cur.execute(
        """
        SELECT billing_method,
               is_active,
               COUNT(*) AS n,
               COUNT(*) FILTER (
                   WHERE stripe_subscription_id IS NULL
                      OR btrim(stripe_subscription_id) = ''
               ) AS missing_sub_id
        FROM billing_licensesubscription
        GROUP BY billing_method, is_active
        ORDER BY billing_method, is_active
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no licence subscriptions at all in this database)")
    for r in rows:
        print(
            f"  billing_method={r['billing_method']:<8} "
            f"is_active={r['is_active']!s:<5} "
            f"count={r['n']:<5} missing_stripe_subscription_id={r['missing_sub_id']}"
        )

    print("\n=== 2. THE SHAPE: billing_method=STRIPE but no stripe_subscription_id ===")
    cur.execute(
        """
        SELECT ls.id,
               s.name  AS school,
               ls.is_active,
               ls.auto_renew,
               ls.stripe_status,
               ls.stripe_customer_id IS NOT NULL AS has_customer_id,
               ls.created_at,
               ls.updated_at
        FROM billing_licensesubscription ls
        LEFT JOIN classrooms_school s ON s.id = ls.school_id
        WHERE ls.billing_method = 'STRIPE'
          AND (ls.stripe_subscription_id IS NULL
               OR btrim(ls.stripe_subscription_id) = '')
        ORDER BY ls.updated_at DESC
        """
    )
    hits = cur.fetchall()
    print(f"  ROWS: {len(hits)}")
    for r in hits:
        print(
            f"    licence={r['id']} school={r['school']!r} active={r['is_active']} "
            f"auto_renew={r['auto_renew']} stripe_status={r['stripe_status']!r} "
            f"has_customer_id={r['has_customer_id']} updated={r['updated_at']}"
        )

    print("\n=== 3. HALF-2 SIZING: STRIPE licences that DO hold an id ===")
    print("    (these are the ones needing a live Subscription.retrieve;")
    print("     d4 wants this number BEFORE any Stripe call is made)")
    cur.execute(
        """
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE is_active) AS n_active
        FROM billing_licensesubscription
        WHERE billing_method = 'STRIPE'
          AND stripe_subscription_id IS NOT NULL
          AND btrim(stripe_subscription_id) <> ''
        """
    )
    r = cur.fetchone()
    print(f"  half-2 candidate rows: {r['n']} (of which active: {r['n_active']})")

    print("\n=== 4. CONVERTED_TO_OFFLINE audit trail (did a conversion ever run?) ===")
    try:
        cur.execute(
            """
            SELECT COUNT(*) AS n, MAX(created_at) AS latest
            FROM billing_licensebillingrecord
            WHERE record_type = 'CONVERTED_TO_OFFLINE'
            """
        )
        r = cur.fetchone()
        print(f"  CONVERTED_TO_OFFLINE records: {r['n']} latest={r['latest']}")
        print("  NOTE: this row is written INSIDE the same transaction as the")
        print("  Stripe delete, so a death mid-flight leaves NO record here.")
        print("  A zero here is consistent with both 'never ran' and 'ran and died'.")
    except psycopg2.Error as exc:
        conn.rollback()
        print(f"  (could not read billing record table: {exc})")

    conn.rollback()  # never commit; nothing to commit, but be explicit
    cur.close()
    conn.close()
    print("\nread-only session closed; no writes issued, no Stripe calls made.")


if __name__ == "__main__":
    main()
