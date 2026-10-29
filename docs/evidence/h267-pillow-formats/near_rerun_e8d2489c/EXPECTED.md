# H-267 near-suite rerun (SM, 2026-10-29), written before the run

Why: the gate on 6a3fe3db ran the near suites in the shared `AutoGrader_env`, which still
has the pre-H-263 pins (Pillow 12, DRF 3.16), and the assignments "Ran N" line was missing.
H-267's base (aede70b4) and beta carry Pillow 13.0.0 / DRF 3.18.2, so all three suites are
rerun in a private venv with those pins (`h266_list_errors_dict_env`, built from
aede70b4's requirements.txt; H-267 does not change requirements.txt).

Tip: e8d2489c (code identical to the gated 6a3fe3db; e8d2489c is evidence only).

Expected (counts from a DiscoverRunner id list at the tip, 0 load failures, not grep):

| Suite | Expected |
|---|---|
| assignments | Ran 795, OK |
| classrooms | Ran 640, OK |
| users | Ran 877, OK (skipped=4) |

Assignments' Chromium PDF tests must run (Playwright Chromium is installed). Any skip there
for a missing browser is a stop.

## Result at e8d2489c (SM, 2026-10-29): STOP

- classrooms: Ran 640, OK (as written)
- users: Ran 877, OK (skipped=4) (as written)
- assignments: Ran 795, FAILED (failures=1, skipped=13). The 13 skips are opt-in tests
  (8 RUN_LOAD_TESTS, 4 RUN_REAL_AI, 1 6,000-student school), none a missing-browser skip;
  the skip count was not written down, which was the SM's omission. The failure is real:
  `ConcurrentTypeOnlySuperadminAITest` -> admin save refused, "password: This field is
  required." Cause: H-267's `_ProfileImageForm(forms.ModelForm, fields="__all__")`
  replaced UserAdmin's UserChangeForm, so the password hash became a required editable
  field (every admin user save failed; an admin could overwrite the hash).
- SM ruling: fix in H-267. Tests b56028d9 (2, red at e8d2489c: FAIL "is not an instance
  of", FAIL "False is not true"); fix 07f36ccf (extend UserChangeForm).
