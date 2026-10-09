# Addendum to the verification of BE-I-04 slice A: which statements rested on a probe never seen red

Verifier: the Next-stage Checker (reserve engineer 0c). Written 2026-10-06 19:24 WAT, by the Senior
Manager's order, after a probe of mine for slice B was found unable to fail (rule 19: a
verifier's probe is evidence only if it has been seen red). It adds to
`VERIFICATION_be_i_04_slice_a.md` and its delta record; neither is changed.

In my slice A verification (58326e45) my runner ran only the author's two test modules under my
twelve mutants. My seven probes were run once, passed, and were never run under a mutant. The
record's probe table says "all as expected"; that is true, and none had been shown able to fail.

| Probe | Statement in the record | Seen red? | Did the verdict rest on it alone? |
|---|---|---|---|
| P3 | A row made before migration 0031 reads the placeholder; the migration reverses | Forward half: yes, since, by the author's mutant M4 on the adopted test. Reverse half: no | No. It was a required item, adopted with its red proof |
| P1 | Older code's INSERT without the six columns reads the placeholder | No | No: the author's test reads the columns' database defaults from the schema (killed by L5, M1, M3) and the rollback guard ran (killed by M1) |
| P2 | The admin change form has no input for the six columns | No (and its POST half proved nothing, as the record says) | No: the author's two read-only tests (killed by L4) |
| P4 | The migration's SQL is six plain ADD COLUMN, no UPDATE, no index, no DROP DEFAULT | No | No: the index and the defaults are held by the author's database tests (M1, M2, M3); "no data UPDATE, no RunPython" also by my reading of the migration file |
| P5 | Eight submission serializers in six apps and two real responses name no label column | No | No: it supported a note (R4), not the verdict; the author's serializer tests have red proofs (L7, my V1) |
| P6 | The settings version is the same from two interpreters with different hash seeds | **Yes, now** (below) | No, but it was the one statement resting on my probe and my reading only |
| P7 | No DatabaseDefault object on an instance created and then saved | No | No: the author's unsaved-instance test (killed by L1) |

**P6's red proof.** Run on 2026-10-06 in my slot of 19:16:01 to 19:22:58 WAT, in my own checkout
at 6ec5af66 of the slice B branch (the settings version there also holds the temperature, which
does not change what the probe checks). The P6 class, byte for byte as run at 58326e45, passed
in the baseline. Under my mutant X17 (the version made to depend on the interpreter's hash
seed), expected failing probe written beforehand, it failed: the two interpreters printed two
different versions. Its inner run has its own "Ran 1 test" line. So P6 can fail, and at the
unmutated code it does not.

What carried the slice A verdict: the author's tests with their 28 kills, my 12 kills against
the author's tests, the Release Engineer's full run, and reading. The merge stands (Senior
Manager, 2026-10-06).

Files, under `~/Documents/Projects/GAP-0c-runs/be-i-04-b/`: `tests_0c_probe_p6.py`,
`mutate_0c_b_probes.py`, `logs_probe_proofs/mutants/X17.txt`,
`logs_probe_proofs/2_probes.txt`.
