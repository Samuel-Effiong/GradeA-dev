"""Answer-extraction benchmark.

Separate from `ai_processor/benchmark/`'s assignment-extraction and grading
benchmarks because it validates a different pipeline: the one that turns a
scanned student script into the answers that get graded. That pipeline had
no benchmark of any kind before this package existed, which is how a
missing structured-output schema on its chunked path survived long enough
to be found by a code review rather than by a test.

Read `README.md` in this directory for the architecture, the scenario
catalogue and how to run it.
"""

from .documents import Ink, build_document
from .provider import FakeProvider, ProviderBehaviour
from .scenarios import SCENARIOS, SCENARIOS_BY_ID, Expectation, Scenario

__all__ = [
    "Ink",
    "build_document",
    "FakeProvider",
    "ProviderBehaviour",
    "SCENARIOS",
    "SCENARIOS_BY_ID",
    "Expectation",
    "Scenario",
]
