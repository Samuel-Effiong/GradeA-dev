# H-267 near suites + mutant at 07f36ccf (SM, 2026-10-29), written before the run

Private venv with batch-30 pins (Pillow 13.0.0, DRF 3.18.2). Counts from a DiscoverRunner id list.

| Suite | Expected |
|---|---|
| assignments.tests_h267_pillow_formats | Ran 21, OK |
| assignments | Ran 797, OK (skipped=13) |
| classrooms | Ran 640, OK |
| users | Ran 877, OK (skipped=4) |

Mutant M10: `_ProfileImageForm(UserChangeForm)` back to `forms.ModelForm` (with its import)
-> KILLED: `test_admin_user_form_shows_the_password_read_only` ("is not an instance of") and
`test_admin_user_form_cannot_change_the_password_hash` ("False is not true"), Ran 21, FAILED (failures=2).

## Result (SM, 2026-10-29): matches every line

- M10: KILLED, Ran 21, FAILED (failures=2), both expected tests with their fragments; source restored.
- assignments.tests_h267_pillow_formats: Ran 21, OK
- assignments: Ran 797, OK (skipped=13)
- classrooms: Ran 640, OK
- users: Ran 877, OK (skipped=4)

H-267 enters batch 31 at the evidence commit that carries this file.
