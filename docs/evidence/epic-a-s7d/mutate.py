"""Epic A S7d: apply each mutant, run its batch's tests, record the killers,
restore. Every anchor must occur exactly once in its file, and every mutant
must parse (a syntax error "kills" without a test failing).

Three batches, each well under rule 12's 1800 s cap: MUT_BATCH=1 (the
catalogue layer, B, C, F), 2 (D, roster), 3 (E, licence teachers).
"""

import ast
import json
import os
import re
import subprocess
import sys

RC = "AutoGrader/reason_codes.py"
UV = "users/views.py"
AS = "assignments/services.py"
AI = "ai_processor/services.py"
SV = "students/views.py"
AV = "assignments/views.py"
RI = "classrooms/services/roster_import.py"
CV = "classrooms/views.py"
LS = "billing/license_service.py"
LV = "billing/license_views.py"
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree")

BATCHES = {
    "1": (
        {
            # The catalogue layer.
            "R1_body_ignores_a_chosen_remediation": (
                RC,
                '        "remediation": remediation or spec.remediation or None,\n',
                '        "remediation": spec.remediation or None,\n',
            ),
            "R2_any_remediation_accepted": (
                RC,
                "        if remediation is not None and remediation not in (\n",
                "        if False and remediation not in (\n",
            ),
            "R3_nothing_to_do_is_an_empty_string": (
                RC,
                "        return self._spec.remediation or None\n",
                "        return self._spec.remediation\n",
            ),
            "R4_entry_error_carries_the_detail": (
                RC,
                '        "error": error.message,\n',
                '        "error": error.detail or error.message,\n',
            ),
            "R5_an_approved_text_drifts": (
                RC,
                '        "Row {row} repeats row {first_row}.",\n',
                '        "Row {row} duplicates row {first_row}.",\n',
            ),
            # B.
            "B1_pause_coded_as_another_lock": (
                UV,
                '                reason_code="REGISTRATION_PAUSED",\n'
                '                code_value="REGISTRATION_PAUSED",\n',
                '                reason_code="VERIFY_LOCKED",\n'
                '                code_value="VERIFY_LOCKED",\n',
            ),
            # C.
            "C1_photo_mapped_to_unreadable": (
                AS,
                '                raise FileNotAPdfError(params={"file_name": file_name}) from exc\n',
                '                raise FileUnreadableError(params={"file_name": file_name}) from exc\n',
            ),
            "C2_extract_raises_the_generic_error": (
                AI,
                "            raise PDFNotAPdfError(\n",
                "            raise PDFUnreadableError(\n",
            ),
            "C3_damaged_pdf_called_not_a_pdf": (
                AS,
                "            except PDFNotAPdfError as exc:\n",
                "            except PDFUnreadableError as exc:\n",
            ),
            # F.
            "F1_single_publish_uncoded": (
                SV,
                "            return coded_response(CodedError(ReasonCode.SUBMISSION_NOT_GRADED))\n",
                "            return Response(\n"
                '                {"error": "Cannot publish an ungraded submission."},\n'
                "                status=HTTP_400_BAD_REQUEST,\n"
                "            )\n",
            ),
            "F2_half_graded_not_listed": (
                AV,
                '            assignment.submissions.exclude(pk__in=graded_submissions.values("pk"))\n',
                "            assignment.submissions.filter(graded_at__isnull=True)\n",
            ),
            "F3_nothing_graded_lists_nothing": (
                AV,
                '                    "message": "No graded submissions found to publish.",\n'
                '                    "skipped": skipped,\n',
                '                    "message": "No graded submissions found to publish.",\n',
            ),
        },
        [
            "AutoGrader.tests_reason_codes",
            "AutoGrader.tests_codederror_serialization",
            "users.tests_s7d_registration_paused",
            "ai_processor.tests_pdf_type_validation",
            "students.tests_s7d_publish_codes",
        ],
    ),
    "2": (
        {
            "D1_no_input_not_coded": (
                CV,
                "        if not input_file and not raw_data:\n",
                "        if False:\n",
            ),
            "D2_header_only_is_not_empty": (
                RI,
                "    if not parsed:\n        raise RosterImportError(ReasonCode.ROSTER_EMPTY)\n",
                "    if False:\n        raise RosterImportError(ReasonCode.ROSTER_EMPTY)\n",
            ),
            "D3_row_numbers_off_by_one": (
                RI,
                "    for number, row in enumerate(data_rows, start=1):\n",
                "    for number, row in enumerate(data_rows, start=0):\n",
            ),
            "D4_names_not_checked_before_import": (
                RI,
                "    if not row.names_are_valid:\n",
                "    if False:\n",
            ),
            "D5_invalid_email_imported": (
                RI,
                '            return ReasonCode.ROW_EMAIL_INVALID, {"email": row.email}\n',
                "            pass\n",
            ),
            "D6_duplicate_email_not_normalised": (
                RI,
                '            return ("email", normalize_email(self.email))\n',
                '            return ("email", self.email)\n',
            ),
            "D7_duplicates_not_detected": (
                RI,
                "        elif row.duplicate_key in first_seen:\n",
                "        elif False:\n",
            ),
            "D8_staff_email_not_mapped": (
                RI,
                "    NOT_A_STUDENT_MESSAGE: ReasonCode.ROW_STAFF_EMAIL,\n",
                "",
            ),
            "D9_other_school_not_mapped": (
                RI,
                "    CROSS_SCHOOL_REJECTION_MESSAGE: ReasonCode.ROW_OTHER_SCHOOL,\n",
                "",
            ),
            "D10_no_savepoint_per_row": (
                RI,
                "        with transaction.atomic():\n            if row.email:\n",
                "        with transaction.atomic(savepoint=False):\n            if row.email:\n",
            ),
            "D11_size_not_checked_before_read": (
                RI,
                "        validate_upload_size(input_file, MAX_FILE_BYTES)\n",
                "        pass\n",
            ),
            "D12_a_skip_counted_as_a_failure": (
                RI,
                "        counts[outcome] += 1\n",
                "        counts[FAILED if outcome == SKIPPED else outcome] += 1\n",
            ),
            "D13_new_account_clash_not_prechecked": (
                RI,
                "    if existing is None and _name_clashes(course, row):\n",
                "    if False:\n",
            ),
            "D14_existing_account_clash_not_mapped": (
                RI,
                "        if not _is_name_clash(exc):\n            raise\n",
                "        raise\n",
            ),
        },
        [
            "classrooms.tests_s7d_roster_codes",
            "classrooms.tests_tenancy_and_roster",
        ],
    ),
    "3": (
        {
            "E1_inactive_not_coded": (
                LS,
                "            raise LicenceTeachersRefused(ReasonCode.LICENCE_INACTIVE)\n",
                '            raise LicenseRequestError("This licence isn\'t active.")\n',
            ),
            "E2_no_seats_form_lost": (
                LS,
                '            availability = "no seats left"\n',
                '            availability = "0 seats left"\n',
            ),
            "E3_already_on_licence_reported_as_failed": (
                LS,
                '                results["skipped"].append(\n',
                '                results["errors"].append(\n',
            ),
            "E4_no_savepoint_per_teacher": (
                LS,
                "            with transaction.atomic():\n"
                "                teacher = LicenseSubscriptionService._get_or_invite_teacher(\n",
                "            with transaction.atomic(savepoint=False):\n"
                "                teacher = LicenseSubscriptionService._get_or_invite_teacher(\n",
            ),
            "E5_other_role_not_mapped": (
                LS,
                "        if text == TEACHER_OTHER_ROLE_TEXT:\n",
                "        if False:\n",
            ),
            "E6_other_school_not_mapped": (
                LS,
                "        if text == TEACHER_OTHER_SCHOOL_TEXT:\n",
                "        if False:\n",
            ),
            "E7_not_business_not_mapped": (
                LS,
                "        if text == _not_business_text(email):\n",
                "        if False:\n",
            ),
            "E8_individual_subscription_not_mapped": (
                LS,
                "    if isinstance(exc, IndividualSubscriptionConflictError):\n",
                "    if False:\n",
            ),
            "E9_refusal_log_carries_the_exception": (
                LS,
                "                license_sub.school_id,\n"
                "                failure.reason_code,\n",
                "                license_sub.school_id,\n" "                exc,\n",
            ),
            "E10_malformed_id_escapes": (
                LV,
                "        except (DjangoValidationError, ValueError, TypeError):\n",
                "        except (ValueError, TypeError):\n",
            ),
            "E11_not_on_licence_called_a_failure": (
                LV,
                "        except LicenseRequestError:\n"
                "            return ReasonCode.TEACHER_NOT_ON_LICENCE\n",
                "        except LicenseRequestError:\n"
                "            return ReasonCode.TEACHER_REMOVE_FAILED\n",
            ),
            "E12_a_string_is_read_as_a_list": (
                LV,
                "        if not teacher_emails or not isinstance(teacher_emails, list):\n",
                "        if not teacher_emails:\n",
            ),
            "E13_removal_loses_its_remediation": (
                LV,
                '                    remediation="Choose the teachers to remove.",\n',
                "",
            ),
        },
        [
            "billing.tests.test_s7d_licence_teacher_codes",
            "billing.tests.test_license_teacher_changes_400",
        ],
    ),
    # The two wordings QA approved on 2026-10-01, folded in after run 2.
    "4": (
        {
            "V1_variant_never_picked": (
                "students/task_tracking.py",
                '        variant="none_finished" if completed == 0 else None,\n',
                "        variant=None,\n",
            ),
            "V2_variant_always_picked": (
                "students/task_tracking.py",
                '        variant="none_finished" if completed == 0 else None,\n',
                '        variant="none_finished",\n',
            ),
            "V3_render_ignores_the_variant": (
                RC,
                "        self._message = spec.render(params, display, variant)\n",
                "        self._message = spec.render(params, display)\n",
            ),
            "V4_variant_lost_from_args": (
                RC,
                "            extra = (remediation, variant)\n",
                "            extra = (remediation,)\n",
            ),
            "V5_any_variant_accepted": (
                RC,
                "        if variant is not None and variant not in spec.alternative_messages:\n",
                "        if False:\n",
            ),
            "V6_new_account_clash_default_remediation": (
                RI,
                "            remediation=EMAILED_CLASH_REMEDIATION,\n"
                "            student_display=_full_display(row),\n",
                "            student_display=_full_display(row),\n",
            ),
            "V7_existing_account_clash_default_remediation": (
                RI,
                "                remediation=EMAILED_CLASH_REMEDIATION,\n"
                "                student_display=_full_display(row),\n",
                "                student_display=_full_display(row),\n",
            ),
        },
        [
            "AutoGrader.tests_reason_codes",
            "AutoGrader.tests_codederror_serialization",
            "students.tests_s7d_mid_batch_wording",
            "classrooms.tests_s7d_roster_codes",
        ],
    ),
}
# After step 4 run 1 (SM ruling): C's mutants against the fixed S6b module.
BATCHES["5"] = (
    {k: v for k, v in BATCHES["1"][0].items() if k.startswith("C")},
    ["assignments.tests_file_reason_codes"],
)

batch = os.environ["MUT_BATCH"]
MUTANTS, TESTS = BATCHES[batch]
originals: dict = {}
results = {}
try:
    for name, (path, a, b) in MUTANTS.items():
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        ast.parse(mutated)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *TESTS]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env={**os.environ, "EXEMPT_EMAIL_DOMAINS": ""},
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)

with open(f"docs/evidence/epic-a-s7d/mutation_results_batch{batch}.json", "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
