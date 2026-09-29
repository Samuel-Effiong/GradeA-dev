"""
Run prod_exposure_query.sql READ-ONLY and keep the emails out of the terminal.

    railway run python docs/evidence/authz-oauth-takeover/run_prod_exposure_query.py

Needs only a Postgres URL in the environment (first found of DATABASE_PUBLIC_URL,
DATABASE_URL, DATABASE_URI) and psycopg2. From your own machine `railway run`
cannot reach Railway's internal hostname, so use the variable that carries the
PUBLIC url (DATABASE_PUBLIC_URL) or run the script inside Railway.

What it does, and nothing else:
  * opens the session with default_transaction_read_only=on, asserts
    transaction_read_only is 'on', runs the single SELECT from the .sql file,
    and ROLLBACKs;
  * writes the full result (it contains emails) to a CSV created with mode 0600,
    by default in your home directory, never in the repository;
  * prints ONLY the row count and, per row, internal id, date_joined and
    activated_at. No emails, no credentials.
Optional argument: output CSV path.
"""

import csv
import os
import sys
from datetime import datetime, timezone

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(HERE, "prod_exposure_query.sql")
# docs/evidence/authz-oauth-takeover -> docs/evidence -> docs -> repo root
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def database_url():
    for name in ("DATABASE_PUBLIC_URL", "DATABASE_URL", "DATABASE_URI"):
        value = os.environ.get(name)
        if value:
            return value
    sys.exit("No DATABASE_PUBLIC_URL / DATABASE_URL / DATABASE_URI in the environment.")


def main():
    sql = open(SQL_PATH).read()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.expanduser(f"~/oauth_exposure_{stamp}.csv")
    )
    if os.path.abspath(out).startswith(REPO_ROOT + os.sep):
        sys.exit("Refusing to write the result inside the repository.")

    conn = psycopg2.connect(
        database_url(),
        connect_timeout=20,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
        cur = conn.cursor()
        cur.execute("SHOW transaction_read_only")
        mode = cur.fetchone()[0]
        if mode != "on":
            sys.exit(f"Refusing to run: transaction_read_only is {mode!r}, not 'on'.")
        cur.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.rollback()
        conn.close()

    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)

    index = {name: i for i, name in enumerate(columns)}
    print(f"transaction_read_only: {mode}")
    print(f"suspect rows: {len(rows)}")
    for row in rows:
        print(
            f"{row[index['id']]}  date_joined={row[index['date_joined']]}"
            f"  activated_at={row[index['activated_at']]}"
        )
    print(f"full result (contains emails), mode 0600: {out}")


if __name__ == "__main__":
    main()
