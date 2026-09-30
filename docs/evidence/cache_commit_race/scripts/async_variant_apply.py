"""Rewrite the post-commit bump to run on a background thread.

Used only by async_variant_probe.sh, inside a disposable worktree.
Usage: python async_variant_apply.py <path to cache_generation.py> <delay s>
"""

import sys

path, delay = sys.argv[1], float(sys.argv[2])
source = open(path).read()
old = "        transaction.on_commit(lambda: _bump_now(unique))"
new = (
    "        transaction.on_commit(\n"
    f"            lambda: threading.Timer({delay}, _bump_now, [unique]).start()\n"
    "        )  # PROBE: non-blocking post-commit bump"
)
assert source.count(old) == 1, source.count(old)
source = "import threading\n" + source.replace(old, new)
open(path, "w").write(source)
print("applied")
