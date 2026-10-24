# H-89: cost of getMessage() per record. Plain Python, no Django settings.
# Best of 7 repeats of 200,000 calls (the minimum is the least disturbed).
import logging
import sys
import timeit

sys.path.insert(0, ".")
from AutoGrader import log_scrubbing as ls  # noqa: E402


def record(msg, args):
    return logging.getLogRecordFactory()(
        "b", logging.INFO, __file__, 1, msg, args, None
    )


def per_call(rec):
    n = 200_000
    return min(timeit.repeat(rec.getMessage, number=n, repeat=7)) / n * 1e9


ls.install()
plain = "License %s renewed for allocation %s"
addr = "Mail to %s bounced for allocation %s"
for label, on, msg, args in (
    ("scrubber off", False, plain, ("3f2b8c1e", 77)),
    ("on, no @", True, plain, ("3f2b8c1e", 77)),
    ("on, an address", True, addr, ("someone@example.test", 77)),
):
    ls.set_enabled(on)
    r = record(msg, args)
    print(f"{label}: {per_call(r):.0f} ns  -> {r.getMessage()!r}")
