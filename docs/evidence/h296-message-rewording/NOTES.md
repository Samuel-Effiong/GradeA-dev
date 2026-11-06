# H-296 notes

Owner approval 2026-11-05 of the doc "Backend Message Rewording for
Approval" (rev 10, unedited), Publish All: keep 200 + reworded text + zero
counts.

## Results
- Red: 36/38 failing, 2 controls passing (red.txt). The expectation first
  said 46 tests; the file has 38 (count slip, corrected in EXPECTATIONS
  after the red run; the predicted split "all but the 2 controls" held).
- Green: 38/38.
- Plants P1-P5 (success message, not-found map, form reword, name-clash
  code in roster import, publish-all zero counts) all killed, restored by
  sha256 (plant.txt).
- Near: users classrooms assignments students billing AutoGrader audit
  dashboard Ran 7493 OK (skipped=24) (near.txt). No migration.

## Found while applying
- users/views.py already had a GOOGLE_SIGN_IN_FAILED constant further down,
  which silently overrode the new one: the two Google refusals showed the
  older "Google sign-in failed. Please try again." The approved text now
  lives in GOOGLE_NO_SIGN_IN. The sweep only checked the text exists in the
  file; tests_rate_limited_envelope now pins the API answer.
- The name-clash refusal is now recognized by roster import by its code
  ("name_clash"), not by matching its words.

## Applied beyond the doc's rows (same approved fix, flagged to the owner)
- "Please subscribe again via select-plan." in 4 sibling messages -> the
  row 5.8 ending "Choose a plan to subscribe again."
- American spelling (owner rule) in other user-visible texts: reason codes
  TEACHER_ALREADY_ON_LICENCE / TEACHER_NOT_ON_LICENCE, the four activity-log
  reason labels in audit/enums.py, three billing serializer refusals, the
  "another billing change" refusal, "carried over from the current license".
  Internal log/command texts keep "licence" (not user-facing).
- Redirect text 5.4 applied to all three overage checkout paths (teacher,
  school admin, serializer default).

## Existing tests updated to the approved text (pins only)
users: tests_admin_power_principle, tests_auth_endpoints,
tests_auth_input_validation, tests_final_branches, tests_h274_sign_in_message,
tests_h277_logout_owner, tests_rate_limited_envelope, tests_reset_otp_budget,
tests_renderers, tests_h295_status_refusal_message; classrooms:
tests_h295_course_without_session, tests_teacher_names_student_on_add;
assignments: tests_h263_list_error_shape; students: tests_s7d_publish_codes;
billing: test_h28_cancel_phases, test_license_service, test_license_seat_400,
test_license_seat_counting, test_license_teacher_changes_400,
test_overage_cap (dash assertion dropped: the new text has none),
test_s7d_licence_teacher_codes, test_subscription_reactivation;
AutoGrader: tests_reason_codes (approved pins + overage sentences).
