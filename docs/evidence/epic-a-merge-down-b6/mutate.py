"""Bundle 6 merge-down: H-99's nine mutants against the EPIC's versions of the
files, plus four on the epic-side adaptation (E1-E4), plus six on H-85's
landing in the epic catalogue (H1-H6, TEACHER_CANNOT_JOIN_YET; these run
the four modules in H85_TESTS). Apply each, run its test modules, record
the killers, restore.
Every anchor must occur exactly once and every mutant must parse.

Rule 17: the test subprocess runs with PYTHONDONTWRITEBYTECODE=1, and the
__pycache__ of every mutated module's directory is deleted before each
mutant and after each restore.
"""

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

SERIALIZERS = "classrooms/serializers.py"
ENROLLMENT = "classrooms/services/enrollment.py"
ROSTER = "classrooms/services/roster_import.py"
TESTS = ["classrooms.tests_h99_placeholder_email"]
LICENCE = "billing/license_service.py"
CATALOGUE = "AutoGrader/reason_codes.py"
H85_TESTS = [
    "billing.tests.test_s7d_licence_teacher_codes",
    "billing.tests.test_neutral_subscription_refusal",
    "AutoGrader.tests_reason_codes",
    "AutoGrader.tests_codederror_serialization",
]
SETTINGS = os.environ.get("MUT_SETTINGS", "settings_worktree_mut")
OUT = os.environ.get(
    "MUT_RESULTS", "docs/evidence/epic-a-merge-down-b6/mutation_results.json"
)

FREE_CHECK = (
    "        if find_account_by_email(email) is not None:\n            continue\n"
)
FORM_REFUSAL = (
    "        if is_placeholder_email(value):\n"
    "            raise serializers.ValidationError(NOT_A_STUDENT_MESSAGE)\n"
    "        existing_user = find_account_by_email(value)\n"
    "\n"
)
H85_MAPPING = (
    "    if isinstance(exc, IndividualSubscriptionConflictError):\n"
    "        # H-85: no address and no reason. The raise's own log line gives\n"
    "        # support the reason, by id.\n"
    "        return CodedError(ReasonCode.TEACHER_CANNOT_JOIN_YET)\n"
)
H85_ENTRY = (
    "    ReasonCode.TEACHER_CANNOT_JOIN_YET: _item_spec(\n"
    '        "This teacher can\'t be added to your school yet. "\n'
    '        "Please ask them to contact support.",\n'
    '        "Ask the teacher to contact support.",\n'
    "    ),\n"
)
SINGLE_ADD = FORM_REFUSAL + "        # H-71: one neutral answer"
DIRECT_ADD = FORM_REFUSAL + "        # H-71: every non-student role"

MUTANTS = {
    "P1_a_taken_generated_address_attaches_the_account": (
        SERIALIZERS,
        FREE_CHECK,
        "        if find_account_by_email(email) is not None:\n"
        "            return find_account_by_email(email)\n",
    ),
    "P3_a_refused_insert_is_not_retried": (
        SERIALIZERS,
        "        except IntegrityError:\n            continue\n    # The views'",
        "        except KeyError:\n            continue\n    # The views'",
    ),
    "P4_only_one_address_is_tried": (
        SERIALIZERS,
        "    for _ in range(PLACEHOLDER_EMAIL_ATTEMPTS):\n",
        "    for _ in range(1):\n",
    ),
    "P5_the_suffix_is_four_digits_again": (
        ENROLLMENT,
        "{secrets.token_hex(8)}{PLACEHOLDER_EMAIL_DOMAIN}",
        "{secrets.randbelow(10000)}{PLACEHOLDER_EMAIL_DOMAIN}",
    ),
    "P6_the_domain_match_is_case_sensitive_and_untrimmed": (
        ENROLLMENT,
        "    return normalize_email(email).endswith(PLACEHOLDER_EMAIL_DOMAIN)\n",
        '    return (email or "").endswith(PLACEHOLDER_EMAIL_DOMAIN)\n',
    ),
    "P7_the_shared_service_accepts_a_placeholder_address": (
        ENROLLMENT,
        "        if is_placeholder_email(email):\n            # H-99:",
        "        if False:\n            # H-99:",
    ),
    "P8_the_single_add_form_accepts_a_placeholder_address": (
        SERIALIZERS,
        SINGLE_ADD,
        SINGLE_ADD.replace("if is_placeholder_email(value):", "if False:"),
    ),
    "P9_the_direct_add_form_accepts_a_placeholder_address": (
        SERIALIZERS,
        DIRECT_ADD,
        DIRECT_ADD.replace("if is_placeholder_email(value):", "if False:"),
    ),
    "E1_a_refused_address_row_has_no_staff_code": (
        ROSTER,
        "    NOT_A_STUDENT_MESSAGE: ReasonCode.ROW_STAFF_EMAIL,\n",
        "",
    ),
    "E2_the_service_answers_a_placeholder_differently": (
        ENROLLMENT,
        "            raise EnrollmentError(NOT_A_STUDENT_MESSAGE)\n        student = find",
        "            raise EnrollmentError(CROSS_SCHOOL_REJECTION_MESSAGE)\n"
        "        student = find",
    ),
    "E3_the_forms_answer_a_placeholder_differently": (
        SERIALIZERS,
        DIRECT_ADD,
        DIRECT_ADD.replace(
            "raise serializers.ValidationError(NOT_A_STUDENT_MESSAGE)",
            'raise serializers.ValidationError("Enter a real email address.")',
        ),
    ),
    "E4_the_single_add_form_answers_a_placeholder_differently": (
        SERIALIZERS,
        SINGLE_ADD,
        SINGLE_ADD.replace(
            "raise serializers.ValidationError(NOT_A_STUDENT_MESSAGE)",
            'raise serializers.ValidationError("Enter a real email address.")',
        ),
    ),
    "P10_the_name_parts_are_not_cut": (
        ENROLLMENT,
        "if c.isalnum())[:20]\n    safe_last",
        "if c.isalnum())\n    safe_last",
    ),
    "H1_the_paying_teacher_refusal_is_not_mapped": (
        LICENCE,
        H85_MAPPING,
        "",
        H85_TESTS,
    ),
    "H2_the_refusal_takes_the_other_role_code": (
        LICENCE,
        H85_MAPPING,
        H85_MAPPING.replace("TEACHER_CANNOT_JOIN_YET", "TEACHER_EMAIL_OTHER_ROLE"),
        H85_TESTS,
    ),
    "H3_the_message_says_why": (
        CATALOGUE,
        H85_ENTRY,
        H85_ENTRY.replace(
            '        "Please ask them to contact support.",\n',
            '        "They have a subscription of their own.",\n',
        ),
        H85_TESTS,
    ),
    "H4_the_remediation_says_why": (
        CATALOGUE,
        H85_ENTRY,
        H85_ENTRY.replace(
            '"Ask the teacher to contact support."',
            '"Ask the teacher to cancel their plan."',
        ),
        H85_TESTS,
    ),
    "H5_the_entry_takes_an_email_param": (
        CATALOGUE,
        H85_ENTRY,
        H85_ENTRY.replace(
            '        "Ask the teacher to contact support.",\n',
            '        "Ask the teacher to contact support.",\n'
            '        params={"email"},\n',
        ),
        H85_TESTS,
    ),
    "H6_the_entry_is_retryable": (
        CATALOGUE,
        H85_ENTRY,
        H85_ENTRY.replace(
            '        "Ask the teacher to contact support.",\n',
            '        "Ask the teacher to contact support.",\n        retryable=True,\n',
        ),
        H85_TESTS,
    ),
}


def clear_pycache(path):
    shutil.rmtree(pathlib.Path(path).parent / "__pycache__", ignore_errors=True)


originals: dict = {}
results = {}
env = {**os.environ, "EXEMPT_EMAIL_DOMAINS": "", "PYTHONDONTWRITEBYTECODE": "1"}
try:
    for name, (path, a, b, *own_tests) in MUTANTS.items():
        tests = own_tests[0] if own_tests else TESTS
        src = originals.setdefault(path, open(path).read())
        assert src.count(a) == 1, f"{name}: anchor found {src.count(a)} times"
        mutated = src.replace(a, b, 1)
        ast.parse(mutated)
        clear_pycache(path)
        open(path, "w").write(mutated)
        p = subprocess.run(
            [sys.executable, "manage.py", "test", *tests]
            + [f"--settings={SETTINGS}", "--keepdb", "--noinput"],
            capture_output=True,
            text=True,
            env=env,
        )
        out = p.stdout + p.stderr
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)))
        results[name] = {"killed": p.returncode != 0, "failing_tests": failed}
        print(name, results[name], flush=True)
        open(path, "w").write(src)
        clear_pycache(path)
finally:
    for path, src in originals.items():
        open(path, "w").write(src)
        clear_pycache(path)

with open(OUT, "w") as f:
    json.dump(results, f, indent=2)
    f.write("\n")
print("SURVIVORS:", [k for k, v in results.items() if not v["killed"]])
