"""B1b-10 D2 (NOTES §10.2): read-only diagnosis of the E-off user-list failures.

Run from the worktree root under flock, the five unset:
    python docs/evidence/epic-b1b/step10/d2_diag.py

It changes no product file. It sets up the test databases, reuses the
Fixer's own fixtures (QueryCountTests.make_teachers, MixedFixture), and
prints:
  1. the list's queries for 5- and 25-row pages, grouped by normalized SQL;
  2. the payload against the pin, summarized by key per row.
"""

import os
import re
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")

import django  # noqa: E402

django.setup()

import json  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.runner import DiscoverRunner  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from users import tests_user_list_payload_pin as pin_mod  # noqa: E402
from users import tests_user_list_sorting as sort_mod  # noqa: E402


def norm(sql):
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    sql = re.sub(r"IN \([?, ]+\)", "IN (...)", sql)
    return sql[:220]


class D2Queries(sort_mod.QueryCountTests):
    def capture(self, page_size, **params):
        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            response = self.get_list(page_size=page_size, **params)
        self.assertEqual(response.status_code, 200)
        return [q["sql"] for q in ctx.captured_queries]

    def test_d2_queries(self):
        self.make_teachers(30)
        runs = {
            "5 rows, default": self.capture(5),
            "25 rows, default": self.capture(25),
            "5 rows, -credits_left": self.capture(5, ordering="-credits_left"),
        }
        for name, sqls in runs.items():
            print(f"\n=== {name}: {len(sqls)} queries")
            for sql, n in Counter(norm(s) for s in sqls).most_common():
                print(f"{n:4d}  {sql}")


class D2Pin(pin_mod.MixedFixture):
    def test_d2_pin(self):
        payload = self.listed_payload()
        pinned = json.loads(pin_mod.PIN.read_text())
        print(f"\n=== pin: {len(pinned)} rows pinned, {len(payload)} listed")
        print("rows only in payload:", sorted(set(payload) - set(pinned)))
        print("rows only in pin:", sorted(set(pinned) - set(payload)))
        for email in sorted(set(payload) & set(pinned)):
            got, want = payload[email], pinned[email]
            lines = []
            for scope, g, w in [("row", got, want)] + (
                [
                    (
                        "credit_wallet",
                        got.get("credit_wallet") or {},
                        want.get("credit_wallet") or {},
                    )
                ]
                if isinstance(got.get("credit_wallet"), dict)
                or isinstance(want.get("credit_wallet"), dict)
                else []
            ):
                added = sorted(set(g) - set(w))
                removed = sorted(set(w) - set(g))
                changed = sorted(
                    k
                    for k in set(g) & set(w)
                    if g[k] != w[k] and not (scope == "row" and k == "credit_wallet")
                )
                if added or removed or changed:
                    lines.append(
                        f"  {scope}: added={added} removed={removed} changed={changed}"
                    )
                    for k in added:
                        lines.append(f"    + {k} = {g[k]!r}")
                    for k in changed:
                        lines.append(f"    ~ {k}: pin {w[k]!r} -> now {g[k]!r}")
            print(f"{email}: {'identical' if not lines else ''}")
            for line in lines:
                print(line)


def main():
    runner = DiscoverRunner(verbosity=1, interactive=False)
    runner.setup_test_environment()
    old = runner.setup_databases()
    try:
        suite = unittest.TestSuite([D2Queries("test_d2_queries"), D2Pin("test_d2_pin")])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        runner.teardown_databases(old)
        runner.teardown_test_environment()
    sys.exit(0 if result.wasSuccessful() else 1)


main()
