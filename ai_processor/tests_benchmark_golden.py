"""
Golden-master regression tripwire for the grading benchmark.

WHY THIS EXISTS, AND WHY IT IS STRICTER THAN IT LOOKS

`tests_grading_benchmark.ReplayRunTest.test_replay_is_deterministic` already
proves that two replays *inside one process* agree. That catches
non-determinism, but it cannot catch a change in behaviour: if a refactor
shifts every score by one level, two replays still agree with each other
perfectly and that test stays green.

This test compares against a **committed snapshot** instead, so it catches
exactly what the other test cannot — our own code changing the numbers. Replay
mode reads fixed, recorded model responses, so the entire pipeline downstream of
the model is deterministic: any difference at all is a change WE made, never the
model drifting. That makes an exact-equality assertion both safe and extremely
sensitive; a single rounding step moving is enough to fail it.

It exists to guard the benchmark itself while the run-history feature is built
on top of it (see .claude plan "Benchmark History"), because the benchmark is
the instrument every accuracy decision now depends on. Three files that feature
touches — runner.py, scoring.py and the grading_benchmark command — ARE that
instrument.

The one field excluded is `cost.total_seconds`, which is wall-clock time.

REGENERATING THE SNAPSHOT

Only when the numbers are *meant* to move — new recordings, a corrected ground
truth, an intentional scoring change:

    UPDATE_BENCHMARK_GOLDEN=1 python manage.py test \\
        ai_processor.tests_benchmark_golden

Then read `git diff` on the fixture and satisfy yourself that every change is
one you intended. Never regenerate to make a red test go green.

Run with:
    python manage.py test ai_processor.tests_benchmark_golden
"""

import json
import os
from pathlib import Path
from unittest import skipUnless

from django.test import TestCase

from ai_processor.benchmark import runner, scoring
from ai_processor.benchmark.dataset import IDENTICAL_ANSWER_PROBES
from ai_processor.tests_grading_benchmark import HAS_RECORDINGS, BenchmarkFixtureMixin

GOLDEN_PATH = (
    Path(__file__).resolve().parent / "benchmark" / "golden" / ("replay_report.json")
)

# Wall-clock only. Everything else in the report is a pure function of the
# recorded responses and our own code.
VOLATILE_FIELDS = ("total_seconds",)


def _normalize(report):
    """
    Round-trip through JSON so the freshly computed report and the file on
    disk are compared as the same types (the fixture was written with
    `default=str`, which would otherwise make e.g. a tuple compare unequal
    to the list it was saved as).
    """
    report = json.loads(json.dumps(report, sort_keys=True, default=str))
    for field in VOLATILE_FIELDS:
        report.get("cost", {}).pop(field, None)
    return report


def _flatten(obj, prefix=""):
    """Nested report -> {"overall.exact_rate": 0.8421, ...} so a failure can
    name the handful of fields that moved instead of dumping the whole
    document and leaving you to spot the difference."""
    flat = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            flat.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            flat.update(_flatten(value, f"{prefix}[{index}]"))
    else:
        flat[prefix] = obj
    return flat


@skipUnless(
    HAS_RECORDINGS,
    "No recorded model responses. Create them with "
    "`manage.py grading_benchmark --mode record` (makes real, billed calls).",
)
class BenchmarkGoldenMasterTest(BenchmarkFixtureMixin, TestCase):
    def _current_report(self):
        teacher = self._make_teacher()
        run = runner.execute_benchmark(teacher, mode=runner.MODE_REPLAY)
        report = scoring.score_run(run)
        report["consistency"] = scoring.check_consistency(run, IDENTICAL_ANSWER_PROBES)
        return _normalize(report)

    def test_replay_report_matches_committed_snapshot(self):
        current = self._current_report()

        if os.environ.get("UPDATE_BENCHMARK_GOLDEN"):
            GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN_PATH.write_text(
                json.dumps(current, indent=2, sort_keys=True, default=str)
            )
            self.skipTest(f"Snapshot regenerated at {GOLDEN_PATH} — review the diff.")

        self.assertTrue(
            GOLDEN_PATH.exists(),
            f"Missing golden snapshot at {GOLDEN_PATH}. Regenerate with "
            "UPDATE_BENCHMARK_GOLDEN=1 (see this module's docstring).",
        )
        expected = json.loads(GOLDEN_PATH.read_text())

        current_flat, expected_flat = _flatten(current), _flatten(expected)
        differences = [
            f"  {key}: snapshot={expected_flat.get(key)!r} now={current_flat.get(key)!r}"
            for key in sorted(set(current_flat) | set(expected_flat))
            if current_flat.get(key) != expected_flat.get(key)
        ]
        self.assertEqual(
            differences,
            [],
            "The benchmark's replay results changed.\n\n"
            "Replay reads FIXED recorded responses, so this is a change in our "
            "code, not the model drifting. Either it was unintended (a "
            "regression — fix it), or it was intended (regenerate the snapshot "
            "with UPDATE_BENCHMARK_GOLDEN=1 and review the diff).\n\n"
            + "\n".join(differences[:40])
            + (
                f"\n  ... and {len(differences) - 40} more"
                if len(differences) > 40
                else ""
            ),
        )

    def test_snapshot_pins_the_headline_numbers(self):
        # A second, blunter guard. If someone regenerates the snapshot
        # carelessly to silence the test above, these hand-written values
        # (transcribed from the Run 5 write-up in benchmark/FINDINGS.md)
        # still have to be edited deliberately, which is a much harder thing
        # to do by accident.
        expected = json.loads(GOLDEN_PATH.read_text())
        self.assertEqual(expected["overall"]["questions"], 133)
        self.assertEqual(expected["overall"]["exact_rate"], 0.8421)
        self.assertEqual(expected["overall"]["within_one_level_rate"], 1.0)
        self.assertEqual(expected["deterministic"]["claimed"], 34)
        self.assertEqual(expected["deterministic"]["correct"], 34)
        self.assertEqual(expected["evidence"]["verified"], 97)
        self.assertEqual(expected["evidence"]["checked"], 98)
