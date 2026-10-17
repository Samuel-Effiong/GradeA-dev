# H-158: every assertion of the module, read against the real value

d5, 2026-10-07 17:52, before any run. `assignments/tests_question_numbers_in_order.py`,
30 tests, at `7f5d1743` (29 at `af1c5b8c`; E8 added after the runner).

## The forms, and how each was seen
| What a test inspects | Its real form | Seen how |
|---|---|---|
| the numberer's return | (a list of objects, an object of text keys, an integer), compared with `==` | direct call on every one of the function tests' own inputs after the change: each expected value as the test writes it |
| a saved row's `questions` | a list of objects; `question_number` an integer; `question_text` stored as the model wrote it | direct call of `QuestionSerializer` on such a question: the text comes back unchanged; `"3"` becomes 3; `"2a"`, null, true and 2.5 are refused; 0, -1 and 2.0 pass |
| a saved row's `ai_raw_payload` | a JSON object; our key holds text keys ("1") and text or null values | by reading (a JSONField keeps text keys); NOT seen by a call (needs a database) |
| a document (`raw_input`, a snapshot's `raw_input`) | the JSON text the converter makes, a string; a heading reads `Question 1 (10 marks) ...` followed by a dash that the JSON text writes as an escape | direct call of the builder and the converter on the tests' own replies: numbered, each of the three headings once; as the model wrote them (1, 1, 2): "Question 1 (10 marks)" twice, "Question 3 (" never. The tests look only for the ASCII part before the dash |
| captured log lines | plain strings; the line is "Question numbers put in order: user=<id> path=<path> questions=<n> changed=<n>" | direct formatting of the production constant |
| the grading pairing's return | a list of {question, answer} | direct call: with the model's numbers (1, 1, 2) two questions get answer 1; numbered, each its own |
| a serializer's `errors["questions"]` | a list of one error whose text is the sentence | by reading (DRF turns a text under a key into a one-item list); NOT seen by a call (the course field needs a database) |
| a draft's `assignment_snapshot` | a JSON object as stored | by reading and the existing allow-list tests, which read it the same way |

No value looked for in a printed form of a dictionary. The only text
with a character JSON escapes is the heading's dash, and no test looks
for it.

## Per test
"Red under": the mutants of `expected_kills.py` that fail it. "Control":
green with or without the change, NOT evidence of this row.

| Test | Negative checks: what decides them | Read against the real value | Red under |
|---|---|---|---|
| F1 numbers already in order | none | yes (seen) | N6 |
| F2 a repeat | none | yes (seen) | N6 |
| F3 starts at five | none | yes (seen) | (red commit only: no mutant fails it alone) |
| F4 not a positive integer, eight shapes | none | yes (seen, each shape) | N6, N7, N17 |
| F5 a long number kept in part | the label is asserted longer than the bound first | yes (seen) | N8 |
| F6 an entry that is not an object | none | yes (seen) | N10 |
| F7 the input is not changed | "the numbered list differs from the input": decided by the input's own numbers (1, 1, 2 against 1, 2, 3); "not the same object": N9 | yes (seen) | N9 |
| F8 not a list | none | yes (seen) | N11 |
| E1 questions and stored copy in order | none | yes | N1 |
| E2 the model's numbers kept | the key is not on the top level nor on a question: no mutant puts it there; those lines are a control | yes | N2 |
| E3 kept also when nothing changed | none | yes | N2 |
| E4 the document | none (counts) | yes (seen) | N1 |
| E5 one log line | "of three" not in the line: N5 puts the questions there | yes (the line seen) | N3, N5, N6 |
| E6 no such line when nothing changed | preceded by the service's own first line, so there is something to read; decided by N4 and N6 | yes | N4, N6 (green at the red commit) |
| E7 grading pairs each with its own | none; its first half is a control that states the fault | yes (seen, both halves) | N1 |
| E8 the real grading pipeline grades three questions, not two (added at `7f5d1743`) | none; its first half is a control that states the fault: the paper of three graded as two, out of 20 | yes (both halves seen by a direct call of the pipeline; no provider call is made for these objective questions) | N1 |
| R1 create by text | none | yes | N1, N2 |
| R2 edit by text | none | yes | N1, N2 |
| R3 upload | none | yes (headings seen) | N1, N2 |
| R4 a label the serializer would refuse | none | yes ("2a" seen refused by the field) | N1, N2 |
| G1 the draft in order, its document, the log | "of three" not in the line: no mutant of the DRAFT path puts it there (N5 is the extraction's line); that one line is a control | yes | N6, N12, N18 |
| G2 nothing rides in the draft | the snapshot is asserted to hold "questions" first; the key not in it: N15. "ai_raw_payload" not in it and the key not on a question: no mutant; controls | yes | N12, N15 (green at the red commit) |
| G3 the saved assignment in order | `ai_raw_payload` is None: no mutant stores one there; that line is a control | yes | N12 |
| G4 a draft stored before this row | the stored snapshot is asserted to hold the repeat first | yes | N6, N13, N18 |
| S1 a repeat refused with the sentence | none | yes (by reading) | N14 |
| S2 a number and the same as text | none | yes ("1" seen made 1 by the field) | N14 |
| S3 numbers in order pass | | yes | control |
| S4 an update that sends no questions | | yes | control |
| O1 our key is not an override | the row's key is asserted non-empty first; decided by N16 | yes | N16 (green at the red commit) |
| O2 the comparison does run on this row | | yes | control |

## Checks that no mutant decides (not evidence, said plainly)
S3, S4, O2; F3 beyond the red commit; E2's "not on the top level, not
on a question"; G1's "no question text in the draft path's line"; G2's
"no ai_raw_payload in the snapshot, no key on a question"; G3's "no
stored AI copy on a generated assignment".

## Not seen before the run, by reading only
The stored form of our key on a saved row; the serializer's error list;
every route's status code for these replies (the existing allow-list
tests drive the same routes with the same fixtures and expect the same
codes). The eighteen mutants' sets.
