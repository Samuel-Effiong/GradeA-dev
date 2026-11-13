"""Epic B1a step 9, Gate 6 investigation (c): where a single call's time goes
(NOTES §21h). Investigation only: it asserts nothing about speed.

For N billed single calls (provider stubbed), flag on and then flag off,
every SQL statement is timed through `connection.execute_wrapper`. Inside
the TestCase every `transaction.atomic()` is a savepoint, so the statements
are grouped into the call's transactions by SAVEPOINT / RELEASE depth.

Printed per mode: per transaction (in call order), the mean query count,
the mean wall ms from its SAVEPOINT to its RELEASE, and the query that
takes the most time; then the call's mean SQL ms, and the time outside SQL.

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import os
import re
import statistics
import time
import unittest
from collections import defaultdict
from typing import Any
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from billing.testing.holds import ai_ready_teacher, ai_response, call_ai, estimate_for

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
PROVIDER = "_AIProcessor__ai_model"
CALLS = 200
WARM_UP = 20
SHOWN = 140


def shape(sql):
    """The statement with its literals and quoted savepoint names folded."""
    sql = re.sub(r'"s\d+_x\d+"', "SP", sql)
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    return " ".join(sql.split())[:SHOWN]


class Recorder:
    """Times every statement and folds them into top-level transactions."""

    def __init__(self):
        self.calls = []
        self.current: Any = None
        self.depth = 0
        self.base = 0

    def start_call(self):
        self.current = {"blocks": [], "loose": [], "started": time.perf_counter()}
        self.base = self.depth

    def end_call(self):
        self.current["wall"] = time.perf_counter() - self.current["started"]
        self.calls.append(self.current)
        self.current = None

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            took = time.perf_counter() - started
            if self.current is not None:
                self.note(sql, took, started)

    def note(self, sql, took, started):
        head = sql.lstrip().upper()
        if head.startswith("SAVEPOINT"):
            self.depth += 1
            if self.depth == self.base + 1:
                self.current["blocks"].append(
                    {"queries": [], "opened": started, "closed": None}
                )
            return
        if head.startswith("RELEASE SAVEPOINT") or head.startswith(
            "ROLLBACK TO SAVEPOINT"
        ):
            if self.depth == self.base + 1 and self.current["blocks"]:
                self.current["blocks"][-1]["closed"] = started + took
            self.depth -= 1
            return
        if self.depth > self.base and self.current["blocks"]:
            self.current["blocks"][-1]["queries"].append((shape(sql), took))
        else:
            self.current["loose"].append((shape(sql), took))


def summarize(label, calls):
    measured = calls[WARM_UP:]
    lines = [f"\nPROFILE {label} calls={len(measured)}"]
    by_position = defaultdict(list)
    for call in measured:
        for index, block in enumerate(call["blocks"]):
            by_position[index].append(block)
    for index in sorted(by_position):
        blocks = by_position[index]
        counts = [len(b["queries"]) for b in blocks]
        walls = [
            (b["closed"] - b["opened"]) * 1000
            for b in blocks
            if b["closed"] is not None
        ]
        per_shape = defaultdict(float)
        for block in blocks:
            for sql, took in block["queries"]:
                per_shape[sql] += took * 1000
        top, top_ms = max(per_shape.items(), key=lambda kv: kv[1], default=("-", 0.0))
        first = blocks[0]["queries"][0][0] if blocks[0]["queries"] else "-"
        lines.append(
            f"  tx{index + 1} n={len(blocks)} queries_mean={statistics.mean(counts):.1f} "
            f"wall_ms_mean={statistics.mean(walls) if walls else 0:.3f} "
            f"top_ms_mean={top_ms / len(blocks):.3f}\n"
            f"      first: {first}\n      top:   {top}"
        )
    loose_counts = [len(c["loose"]) for c in measured]
    loose_ms = [sum(t for _, t in c["loose"]) * 1000 for c in measured]
    sql_ms = [
        (
            sum(t for b in c["blocks"] for _, t in b["queries"])
            + sum(t for _, t in c["loose"])
        )
        * 1000
        for c in measured
    ]
    walls = [c["wall"] * 1000 for c in measured]
    tx_counts = [len(c["blocks"]) for c in measured]
    lines.append(
        f"  transactions_per_call_mean={statistics.mean(tx_counts):.1f} "
        f"queries_outside_tx_mean={statistics.mean(loose_counts):.1f} "
        f"outside_tx_ms_mean={statistics.mean(loose_ms):.3f}"
    )
    lines.append(
        f"  call_wall_ms_mean={statistics.mean(walls):.3f} "
        f"sql_ms_mean={statistics.mean(sql_ms):.3f} "
        f"non_sql_ms_mean={statistics.mean(walls) - statistics.mean(sql_ms):.3f}"
    )
    return "\n".join(lines)


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load tests are opt-in: set RUN_LOAD_TESTS=1")
class Gate6ProfileTests(TestCase):
    def profile(self, teacher):
        recorder = Recorder()
        with connection.execute_wrapper(recorder):
            for _ in range(WARM_UP + CALLS):
                recorder.start_call()
                call_ai(teacher)
                recorder.end_call()
        return recorder.calls

    def test_gate6_profile_a_single_call_flag_on_and_off(self):
        teacher = ai_ready_teacher("g6p@example.com", 600 * estimate_for())
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            with override_settings(BILLING_HOLDS_ENABLED=True):
                on = self.profile(teacher)
            with override_settings(BILLING_HOLDS_ENABLED=False):
                off = self.profile(teacher)
        print(summarize("flag_on", on))
        print(summarize("flag_off", off))
        self.assertEqual(len(on), WARM_UP + CALLS)
        self.assertEqual(len(off), WARM_UP + CALLS)
