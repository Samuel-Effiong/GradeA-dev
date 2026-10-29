MADE AFTER THE RUN (2026-10-08, 07:19 WAT; the chain ended 07:18:39). The written expected sets in second_form_38b4c6b6/expected_kills.py.txt and the run's results beside it are untouched; this is a correction, not a prediction.

# H-158 chain on 38b4c6b6: the five differing mutants, read from the code (no re-run)
Written AFTER the run (chain end 07:18:39 on 2026-10-08), as a correction of my
written sets, not as a prediction. Each extra test fails for the reason below.

- N6 (`if True:` counts every entry as changed): the very-many-digits test
  asserts changed == 1 for [1, huge, 3]; with the mutant changed is 3. Extra.
- N8 (kept text no longer bounded): the same test asserts the kept text of
  the huge value is huge[:32]; the mutant keeps all 5000 characters. Extra.
- N20 (`None not in kept_numbers` removed): the huge value and the
  99-nines-plus-"a" value are no numbers (None), but the paper is now kept
  as it is: the huge test finds no 1,2,3 numbering; the long-number test
  finds no kept["2"] (the mutant keys it "None"). Both extra.
- N21 (the keep-as-it-is branch switched off): every paper is numbered 1..N,
  so the nine-digit test finds 2 where it expects "999999999". Extra.
- N23 (the string-of-digits branch switched off): "999999999" reads as no
  number, so the paper is renumbered: the same nine-digit test fails. Extra.
Why I missed them: the sets were written before the digit-length tests (T14,
T15) existed, and I did not re-read every test against every mutant after
adding them (rule 19, "count control assertions per mutant").
