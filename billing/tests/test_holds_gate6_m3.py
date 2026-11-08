"""Epic B1a step 9, Gate 6 measurement 3 (NOTES §21j). Recorded beside
measurements 1 and 2 (§21g, §21i), never replacing either.

Declared method change from measurement 2: a TransactionTestCase, so each
call runs in its own committed transactions (no single test transaction
holding dead row versions). Otherwise as measurement 2: M = 2000 calls per
mode; off and on interleaved in blocks of 100 (off first), after a 20-call
warm-up in each mode; the load average logged at start and end. Pass
threshold unchanged: added p95 <= 5 ms.

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import os
import statistics
import time
import unittest
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings

from ai_processor.services import AIProcessor
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    holds,
)

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
PROVIDER = "_AIProcessor__ai_model"
CALLS = 2000
BLOCK = 100
WARM_UP = 20
PASS_P95_ADDED_MS = 5.0


def p50(values):
    return statistics.median(values)


def p95(values):
    return statistics.quantiles(values, n=100)[94]


def ms(seconds):
    return seconds * 1000.0


def timed(real, into):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return real(*args, **kwargs)
        finally:
            into.append(time.perf_counter() - started)

    return wrapper


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load tests are opt-in: set RUN_LOAD_TESTS=1")
class Gate6Measurement3Tests(TransactionTestCase):
    def run_calls(self, teacher, count, into):
        for _ in range(count):
            started = time.perf_counter()
            call_ai(teacher)
            if into is not None:
                into.append(time.perf_counter() - started)

    def test_gate6_measurement_3_interleaved_committed(self):
        load_start = os.getloadavg()
        teacher = ai_ready_teacher("g6m3@example.com", 2400 * estimate_for())
        module = holds()
        off: list = []
        on: list = []
        reserve_times: list = []
        settle_times: list = []
        flag_on = override_settings(BILLING_HOLDS_ENABLED=True)
        flag_off = override_settings(BILLING_HOLDS_ENABLED=False)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            with flag_off:
                self.run_calls(teacher, WARM_UP, None)
            with flag_on:
                self.run_calls(teacher, WARM_UP, None)
            with patch.object(
                module,
                "begin_single_call",
                timed(module.begin_single_call, reserve_times),
            ), patch.object(
                module,
                "finish_single_call",
                timed(module.finish_single_call, settle_times),
            ):
                for _ in range(CALLS // BLOCK):
                    with flag_off:
                        self.run_calls(teacher, BLOCK, off)
                    with flag_on:
                        self.run_calls(teacher, BLOCK, on)
        load_end = os.getloadavg()

        added_p50 = ms(p50(on) - p50(off))
        added_p95 = ms(p95(on) - p95(off))
        print(
            "\nGATE6_M3 calls_per_mode={c} block={b} load_start={ls} load_end={le} "
            "off_p50_ms={a:.3f} off_p95_ms={o:.3f} on_p50_ms={n:.3f} on_p95_ms={q:.3f} "
            "added_p50_ms={e:.3f} added_p95_ms={f:.3f} reserve_p50_ms={g:.3f} "
            "reserve_p95_ms={h:.3f} settle_p50_ms={i:.3f} settle_p95_ms={j:.3f}".format(
                c=CALLS,
                b=BLOCK,
                ls="/".join(f"{x:.2f}" for x in load_start),
                le="/".join(f"{x:.2f}" for x in load_end),
                a=ms(p50(off)),
                o=ms(p95(off)),
                n=ms(p50(on)),
                q=ms(p95(on)),
                e=added_p50,
                f=added_p95,
                g=ms(p50(reserve_times)),
                h=ms(p95(reserve_times)),
                i=ms(p50(settle_times)),
                j=ms(p95(settle_times)),
            )
        )
        self.assertEqual((len(off), len(on)), (CALLS, CALLS))
        self.assertEqual((len(reserve_times), len(settle_times)), (CALLS, CALLS))
        self.assertLessEqual(added_p95, PASS_P95_ADDED_MS)
