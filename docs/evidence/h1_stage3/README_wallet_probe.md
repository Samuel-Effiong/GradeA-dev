# Wallet invalidation probe (H-37)

`probe_wallet_invalidation.py` is a probe, not a regression test. It prints
verdicts and asserts nothing, so it lives here rather than in the test suite.
To rerun it, copy it to `AutoGrader/tests_probe_wallet_invalidation.py` and run
that module with the worktree's test settings.

`wallet_invalidation_probe.log` is its output. The `probe_sha256` line in the
log is the sha256 of this file, byte for byte.
