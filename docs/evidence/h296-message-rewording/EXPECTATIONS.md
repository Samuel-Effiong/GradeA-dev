# H-296 expectations (written before the code)

Owner approval 2026-11-05: "I accept your wordings, apply them and for the
publish all, use your reworded text". Source: Claude Doc "Backend Message
Rewording for Approval" (rev 10, unedited by the owner). Publish All keeps
200 (recommendation accepted) with the reworded text and zero counts (8.1).
Row 1.7 (session delete stays 204) and 6.6 (H-292) need no change here.
Base: task/h295 a59443663 (shares the status-refusal texts).

Test file users/tests_h296_message_rewording.py (38 tests - recount: 38, not 46):
- Red: expected to fail = every test that pins a new text or behavior;
  expected to pass before the fix = the 2 "keeps the old form" controls.
  So 36 fail / 2 pass. (Some API fixtures may need adjusting - reported.)
- Green: 38/38.
- Existing tests pinning old texts (users/tests_renderers.py,
  tests_reset_otp_budget, tests_admin_power_principle, tests_h277,
  tests_rate_limited_envelope, tests_h256, tests_h274_sign_in_message,
  billing/tests/test_overage_cap.py, AutoGrader/tests_reason_codes.py
  APPROVED pins, H-295 tests, numbered-list renderer tests) are updated to
  the approved text, each listed in NOTES.
- Plants: one per mechanism (success message, not-found map, form reword,
  name-clash code, publish-all counts) -> each killed.
- Near run: users classrooms assignments students billing AutoGrader audit
  dashboard, OK.
