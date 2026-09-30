"""Epic A S6a: the reason-code catalogue and its error envelope (FR-A-06).

* Completeness: the enum, the user-facing specs and the audit-only list agree;
  the ten FR-A-06 ids are present; every reason code the code emits is in the
  catalogue.
* `CodedError`: params are whitelisted scalars; the message comes from the
  spec; `detail` never reaches it.
* Through the real API (a test URLconf whose view raises each coded failure,
  with the project's middleware, exception handler and renderer):
  - the body carries the envelope in `error.field_errors` (F7), with the
    legacy lowercase `code` for the two old refusals only (F8);
  - QA-ERR-03: no exception text, traceback or class name reaches the body;
  - QA-ERR-04: `reference` is the response's `X-Request-ID`, which is always
    the server's id (X-5, SM ruling). A client's inbound id is never echoed
    as the reference; a UUID one is kept only as `client_request_id`.
* The renderer shows the display sentence alone.

No mocks (rule 14): every failure is a real exception raised by a real view.
"""

import ast
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.enums import FR_A_06_CODES, ErrorClass, ReasonCode
from AutoGrader.error_messages import (
    describe_background_task_error,
    describe_user_error,
    is_user_facing_error,
)
from AutoGrader.reason_codes import (
    AUDIT_ONLY_CODES,
    ENVELOPE_KEYS,
    LEGACY_CODES,
    REASON_CODES,
    CodedError,
    coded_entry,
    coded_response,
)
from billing import access_control
from billing.access_control import (
    NO_CREDITS_REMAINING_REASON,
    TRIAL_CREDITS_EXHAUSTED_REASON,
    AIFeatureNotAvailableError,
    require_ai_access,
)
from billing.errors import (
    INSUFFICIENT_CREDITS_MESSAGE,
    EmptyWalletError,
    InsufficientCreditsError,
)
from billing.refusals import refusal_response
from users.renderers import flatten_errors

SENTINEL = "SENTINEL-4f1c balance=12.5 at /srv/app/secret.py"

REPO = Path(settings.BASE_DIR)


def sample_params(code):
    spec = REASON_CODES[code]
    return {name: f"p_{name}" for name in sorted(spec.params)}


# ---------------------------------------------------------------- test URLconf


class RaiseCoded(APIView):
    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, code):
        try:
            raise RuntimeError(SENTINEL)
        except RuntimeError as cause:
            raise CodedError(code, params=sample_params(code), detail=SENTINEL) from (
                cause
            )


class RaiseRefusal(APIView):
    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, kind):
        if kind == "credits":
            raise InsufficientCreditsError(SENTINEL)
        if kind == "empty_wallet":
            raise EmptyWalletError(SENTINEL)
        raise AIFeatureNotAvailableError("AI grading isn't included in your plan.")


class CaughtInView(APIView):
    """A view that catches and answers with coded_response itself, as
    students.views._failure_response does."""

    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            raise CodedError(ReasonCode.RUBRIC_MISSING, detail=SENTINEL)
        except CodedError as exc:
            return coded_response(exc)


class GatedView(APIView):
    """A view behind billing.access_control.require_ai_access."""

    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]

    @require_ai_access
    def get(self, request):
        return Response({"ok": True})


class CodedSeeingTheClientId(APIView):
    """Records the client id the middleware kept, then fails coded."""

    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]
    seen: dict = {}

    def get(self, request):
        self.seen["client_request_id"] = getattr(request, "client_request_id", None)
        raise CodedError(ReasonCode.PROVIDER_FAILURE, detail=SENTINEL)


class PlainSerializerError(APIView):
    authentication_classes: list = []
    throttle_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"params": ["This field is required."]}, status=400)


urlpatterns = [
    path("coded/<str:code>", RaiseCoded.as_view()),
    path("refusal/<str:kind>", RaiseRefusal.as_view()),
    path("caught", CaughtInView.as_view()),
    path("coded-client-id", CodedSeeingTheClientId.as_view()),
    path("plain", PlainSerializerError.as_view()),
    path("gated", GatedView.as_view()),
]


# ---------------------------------------------------------------- completeness


def emitted_reason_code_literals():
    """{code: [path:line]} for every UPPER string production code passes as
    an audit reason code: a `reason_code=` keyword, `sign_in_failed`'s code
    argument, an assignment to a name or attribute ending in `_reason`, and
    the code slot of an `audit_failure = (account, code)` tuple."""
    found = {}

    def note(value, where):
        for node in ast.walk(value):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]+", node.value)
            ):
                found.setdefault(node.value, []).append(where)

    for file in sorted(REPO.rglob("*.py")):
        rel = file.relative_to(REPO).as_posix()
        name = file.name
        if (
            rel.startswith(("docs/", "static/", "media/", "node_modules/", "."))
            or "/migrations/" in rel
            or "site-packages" in rel
            or "/tests/" in rel
            or name == "tests.py"
            or name.startswith(("test_", "tests_"))
        ):
            continue
        tree = ast.parse(file.read_text(), filename=rel)
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "reason_code":
                        note(keyword.value, where)
                callee = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if callee == "sign_in_failed" and len(node.args) >= 4:
                    note(node.args[3], where)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    target_name = getattr(target, "id", None) or getattr(
                        target, "attr", None
                    )
                    if not target_name:
                        continue
                    if target_name.endswith("_reason"):
                        note(node.value, where)
                    elif target_name == "audit_failure" and isinstance(
                        node.value, ast.Tuple
                    ):
                        note(node.value.elts[-1], where)
    return found


class CatalogueCompletenessTests(SimpleTestCase):
    def test_every_code_is_user_facing_or_audit_only_and_not_both(self):
        user_facing = set(REASON_CODES)
        self.assertEqual(user_facing & AUDIT_ONLY_CODES, set())
        self.assertEqual(user_facing | AUDIT_ONLY_CODES, set(ReasonCode))

    def test_the_ten_fr_a_06_codes_are_present_and_user_facing(self):
        self.assertEqual(
            {code.value for code in FR_A_06_CODES},
            {
                "MISSING_STUDENT_NAME",
                "STUDENT_NOT_ON_ROSTER",
                "FILE_UNREADABLE",
                "FILE_TYPE_UNSUPPORTED",
                "FILE_TOO_LARGE",
                "SUBMISSION_EMPTY",
                "RUBRIC_MISSING",
                "DUPLICATE_SUBMISSION",
                "PROVIDER_FAILURE",
                "INSUFFICIENT_CREDITS_MID_BATCH",
            },
        )
        self.assertLessEqual(FR_A_06_CODES, set(REASON_CODES))

    def test_every_value_is_upper_snake_and_stable(self):
        for code in ReasonCode:
            with self.subTest(code=code):
                self.assertRegex(code.value, r"^[A-Z][A-Z0-9_]{0,63}$")
                self.assertEqual(code.name, code.value)

    def test_every_spec_is_consistent(self):
        for code, spec in REASON_CODES.items():
            with self.subTest(code=code):
                self.assertIsInstance(spec.error_class, ErrorClass)
                self.assertTrue(400 <= spec.http_status < 600)
                self.assertLessEqual(spec.placeholders(), spec.params)
                self.assertLessEqual(set(spec.defaults), spec.params)
                self.assertNotIn("{", spec.remediation)
                self.assertTrue(spec.message.strip())
                # S7d: "" is "nothing to do" (the body says null), never
                # whitespace.
                self.assertEqual(spec.remediation, spec.remediation.strip())
                for alternative in spec.alternative_remediations:
                    self.assertNotIn("{", alternative)
                    self.assertTrue(alternative.strip())
                if spec.http_status == 503:
                    self.assertIsNotNone(spec.retry_after)

    def test_only_the_two_old_refusals_keep_a_lowercase_code(self):
        """F8: kept for one release beside reason_code."""
        self.assertEqual(
            LEGACY_CODES,
            {
                ReasonCode.INSUFFICIENT_CREDITS: "insufficient_credits",
                ReasonCode.AI_FEATURE_NOT_AVAILABLE: "ai_feature_not_available",
            },
        )

    def test_every_reason_code_the_code_emits_is_in_the_catalogue(self):
        emitted = emitted_reason_code_literals()
        unknown = {
            code: where
            for code, where in emitted.items()
            if code not in ReasonCode.values
        }
        self.assertEqual(
            unknown,
            {},
            "reason codes emitted but not in audit.enums.ReasonCode; the "
            "emitter rejects them, so those audit events are lost. Add each "
            "to the enum, with a spec or as audit-only.",
        )

    def test_the_scan_finds_the_codes_it_is_meant_to(self):
        """Guard on the guard: one example per pattern the scan covers."""
        emitted = emitted_reason_code_literals()
        for code in (
            "SESSION_REVOKE_FAILED",  # reason_code= keyword
            "RESET_LOCKED",  # sign_in_failed's code argument
            "GOOGLE_EXCHANGE_FAILED",  # self._audit_reason = ...
            "INVALID_CODE",  # audit_failure = (account, code)
        ):
            with self.subTest(code=code):
                self.assertIn(code, emitted)


#: The QA catalogue additions (sections B-F), exactly as approved by the
#: founder acting as QA on 2026-09-30
#: (docs/phase2/qa/catalogue_additions_proposal.md): (status, message
#: template, remediation). An empty remediation is the proposal's "none".
APPROVED_ADDITIONS = {
    "REGISTRATION_PAUSED": (
        429,
        "Student registration is paused for a short while because of too many "
        "invalid activation codes. Please try again later; if your code has "
        "expired by then, ask for a new one.",
        "Try again in a few minutes. If your code has expired, ask your teacher "
        "for a new one.",
    ),
    "FILE_NOT_A_PDF": (
        422,
        "{file_name} is not a PDF. If it is a photo or scan, upload it as an "
        "image instead.",
        "Upload the photo or scan as an image (JPEG, PNG, GIF or WebP).",
    ),
    "ROSTER_NO_INPUT": (
        400,
        "Upload a roster file or paste your student list.",
        "Choose a CSV file, or paste rows copied from your spreadsheet.",
    ),
    "ROSTER_EMPTY": (
        400,
        "This roster has no student rows.",
        "Check that the file has one student per row, then try again.",
    ),
    "ROSTER_FILE_UNREADABLE": (
        400,
        "{file_name} isn't readable as text.",
        "Export your roster as a CSV file (UTF-8) and try again.",
    ),
    "ROSTER_TOO_MANY_ROWS": (
        400,
        "This roster has {row_count} rows. Upload at most {max_rows} rows at a "
        "time.",
        "Split the roster into smaller files.",
    ),
    "ROW_NAME_MISSING": (
        422,
        "Row {row}: a first and a last name are required.",
        "Add the missing name and import the row again.",
    ),
    "ROW_NAME_INVALID": (
        422,
        "Row {row}: each name needs between 2 and 150 characters.",
        "Correct the name and import the row again.",
    ),
    "ROW_ALREADY_ENROLLED": (
        422,
        "Row {row}: {student_display} is already in this course.",
        "",
    ),
    "ROW_NAME_CLASH": (
        422,
        "Row {row}: a student named {student_display} is already in this course.",
        "Add an email address to tell the two students apart.",
    ),
    "ROW_STAFF_EMAIL": (
        422,
        "Row {row}: this email can't be added as a student.",
        "Use the student's own email address.",
    ),
    "ROW_OTHER_SCHOOL": (
        422,
        "Row {row}: this account can't be added to this school. If you believe "
        "this is a mistake, contact your school administrator.",
        "",
    ),
    "ROW_ACCOUNT_DISABLED": (
        422,
        "Row {row}: this student's account is disabled.",
        "Contact support if they should have access.",
    ),
    "ROW_EMAIL_INVALID": (
        422,
        'Row {row}: "{email}" isn\'t a valid email address.',
        "Correct the email and import the row again.",
    ),
    "ROW_DUPLICATE": (422, "Row {row} repeats row {first_row}.", ""),
    "ROW_FAILED": (
        422,
        "Row {row}: this student couldn't be added.",
        "Check the row and try again. If it keeps failing, contact support and "
        "quote the reference.",
    ),
    "TEACHER_LIST_EMPTY": (
        400,
        "Add at least one teacher.",
        "Enter the teachers' email addresses.",
    ),
    "LICENCE_INACTIVE": (
        400,
        "This licence isn't active, so teachers can't be added to it.",
        "Renew the licence, or contact us.",
    ),
    "LICENCE_SEATS_EXCEEDED": (
        400,
        "Your licence has {availability} ({in_use} of {max_seats} in use).",
        "Add fewer teachers, remove a teacher, or ask us to add seats.",
    ),
    "TEACHER_EMAIL_NOT_BUSINESS": (
        422,
        "{email} isn't a school or work email address.",
        "Use the teacher's school or work email.",
    ),
    "TEACHER_EMAIL_OTHER_ROLE": (
        422,
        "This email can't be added as a teacher.",
        "Use the teacher's own account email.",
    ),
    "TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION": (
        422,
        "{email} has their own subscription, which must be cancelled before they "
        "can join the licence.",
        "Ask the teacher to cancel their individual subscription, then add them "
        "again.",
    ),
    "TEACHER_IN_OTHER_SCHOOL": (
        422,
        "This teacher already belongs to another school.",
        "Contact support if the teacher has moved schools.",
    ),
    "TEACHER_ALREADY_ON_LICENCE": (422, "{email} is already on this licence.", ""),
    "TEACHER_NOT_ON_LICENCE": (
        422,
        "This teacher isn't an active teacher on this licence.",
        "",
    ),
    "TEACHER_ADD_FAILED": (
        422,
        "We couldn't add this teacher.",
        "Try again. If it keeps failing, contact support and quote the reference.",
    ),
    "TEACHER_REMOVE_FAILED": (
        422,
        "We couldn't remove this teacher.",
        "Try again. If it keeps failing, contact support and quote the reference.",
    ),
    "SUBMISSION_NOT_GRADED": (
        400,
        "This submission hasn't been graded yet, so it can't be published.",
        "Grade it first, then publish.",
    ),
}


class QaCatalogueAdditionsTests(SimpleTestCase):
    """S7d: the approved codes, their texts and statuses, exactly."""

    def test_every_approved_code_is_user_facing_with_its_approved_text(self):
        for value, (http_status, message, remediation) in APPROVED_ADDITIONS.items():
            with self.subTest(code=value):
                spec = REASON_CODES[ReasonCode(value)]
                self.assertEqual(spec.error_class, ErrorClass.USER)
                self.assertEqual(spec.http_status, http_status)
                self.assertEqual(spec.message, message)
                self.assertEqual(spec.remediation, remediation)

    def test_the_seat_message_reads_as_both_approved_forms(self):
        def seats(**params):
            availability = (
                f"{params['remaining']} seats left, but you're adding "
                f"{params['adding']} teachers"
                if params["remaining"]
                else "no seats left"
            )
            return str(
                CodedError(
                    ReasonCode.LICENCE_SEATS_EXCEEDED,
                    params=params,
                    display={"availability": availability},
                )
            )

        self.assertEqual(
            seats(remaining=2, adding=5, in_use=8, max_seats=10),
            "Your licence has 2 seats left, but you're adding 5 teachers "
            "(8 of 10 in use).",
        )
        self.assertEqual(
            seats(remaining=0, adding=1, in_use=10, max_seats=10),
            "Your licence has no seats left (10 of 10 in use).",
        )

    def test_the_individual_subscription_text_lives_in_one_constant(self):
        from AutoGrader import reason_codes

        self.assertEqual(
            REASON_CODES[ReasonCode.TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION].message,
            reason_codes.TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION_MESSAGE,
        )

    def test_a_row_code_always_carries_its_row(self):
        for code, spec in REASON_CODES.items():
            if code.value.startswith("ROW_"):
                with self.subTest(code=code):
                    self.assertIn("row", spec.params)
                    self.assertIn("{row}", spec.message)

    def test_no_neutral_code_names_a_role_or_a_school(self):
        for value in ("ROW_STAFF_EMAIL", "TEACHER_EMAIL_OTHER_ROLE"):
            with self.subTest(code=value):
                spec = REASON_CODES[ReasonCode(value)]
                self.assertLessEqual(spec.params, {"row"})
                for word in ("admin", "staff", "super", "{"):
                    self.assertNotIn(word, spec.message.lower().replace("{row}", ""))
        # The school ones may say "your school administrator"; they name no
        # school and take no param but the row.
        for value in ("ROW_OTHER_SCHOOL", "TEACHER_IN_OTHER_SCHOOL"):
            with self.subTest(code=value):
                spec = REASON_CODES[ReasonCode(value)]
                self.assertLessEqual(spec.params, {"row"})
                self.assertNotIn("{", spec.message.replace("{row}", ""))


class RemediationChoiceTests(SimpleTestCase):
    """S7d: one code, a remediation per route (TEACHER_LIST_EMPTY), and "none"
    as null."""

    def test_an_approved_alternative_is_carried_to_the_body(self):
        error = CodedError(
            ReasonCode.TEACHER_LIST_EMPTY, remediation="Choose the teachers to remove."
        )
        self.assertEqual(error.remediation, "Choose the teachers to remove.")
        body = coded_response(error).data
        self.assertEqual(body["remediation"], "Choose the teachers to remove.")

    def test_the_default_is_the_specs(self):
        error = CodedError(ReasonCode.TEACHER_LIST_EMPTY)
        self.assertEqual(error.remediation, "Enter the teachers' email addresses.")
        self.assertEqual(
            coded_response(error).data["remediation"],
            "Enter the teachers' email addresses.",
        )

    def test_an_unapproved_remediation_is_refused(self):
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.TEACHER_LIST_EMPTY, remediation=SENTINEL)
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.RUBRIC_MISSING, remediation="Do something.")

    def test_nothing_to_do_is_null(self):
        error = CodedError(ReasonCode.TEACHER_NOT_ON_LICENCE)
        self.assertIsNone(error.remediation)
        self.assertIsNone(coded_response(error).data["remediation"])

    def test_a_chosen_remediation_survives_being_rebuilt_from_its_args(self):
        """Celery's json backend rebuilds an error as cls(*args)."""
        error = CodedError(
            ReasonCode.TEACHER_LIST_EMPTY, remediation="Choose the teachers to remove."
        )
        rebuilt = CodedError(*error.args)
        self.assertEqual(rebuilt.remediation, error.remediation)
        plain = CodedError(ReasonCode.RUBRIC_MISSING)
        self.assertEqual(len(plain.args), 4)


class CodedEntryTests(SimpleTestCase):
    def test_an_entry_keeps_the_routes_keys_and_adds_the_coded_ones(self):
        error = CodedError(
            ReasonCode.ROW_DUPLICATE, params={"row": 4, "first_row": 2}, detail=SENTINEL
        )
        entry = coded_entry(error, row=4, name="Ann One", status="skipped")
        self.assertEqual(
            entry,
            {
                "row": 4,
                "name": "Ann One",
                "status": "skipped",
                "error": "Row 4 repeats row 2.",
                "reason_code": "ROW_DUPLICATE",
                "error_class": "USER",
                "message": "Row 4 repeats row 2.",
                "remediation": None,
                "retryable": False,
                "params": {"row": 4, "first_row": 2},
                "reference": None,
            },
        )
        self.assertNotIn(SENTINEL, str(entry))


# ---------------------------------------------------------------- CodedError


class CodedErrorTests(SimpleTestCase):
    def test_the_message_is_rendered_from_the_spec(self):
        error = CodedError(
            ReasonCode.FILE_TOO_LARGE,
            params={
                "file_name": "p07.pdf",
                "actual": "312 pages",
                "limit": "300 pages",
                "dimension": "pages",
            },
        )
        self.assertEqual(str(error), "p07.pdf is 312 pages and the limit is 300 pages.")

    def test_defaults_fill_a_param_the_raiser_left_out(self):
        error = CodedError(
            ReasonCode.FILE_TYPE_UNSUPPORTED,
            params={"file_name": "notes.txt", "detected_type": "text"},
        )
        self.assertIn("PDF, JPEG, PNG, GIF, WebP", str(error))

    def test_display_shapes_the_message_but_never_the_params(self):
        # S6b N1: params are numbers; the message shows them formatted.
        error = CodedError(
            ReasonCode.FILE_TOO_LARGE,
            params={
                "file_name": "big.pdf",
                "actual": 66_270_003,
                "limit": 52_428_800,
                "dimension": "bytes",
            },
            display={"actual": "63.2 MB", "limit": "50 MB"},
        )
        self.assertEqual(str(error), "big.pdf is 63.2 MB and the limit is 50 MB.")
        self.assertEqual(error.params["actual"], 66_270_003)
        self.assertEqual(error.params["limit"], 52_428_800)

    def test_display_may_stand_in_for_an_unknown_param(self):
        error = CodedError(
            ReasonCode.FILE_TOO_LARGE,
            params={"file_name": "x.png", "limit": 50, "dimension": "pixels"},
            display={"actual": "over 179 MP", "limit": "50 MP"},
        )
        self.assertNotIn("actual", error.params)
        self.assertIn("over 179 MP", str(error))

    def test_display_for_no_placeholder_is_refused(self):
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.RUBRIC_MISSING, display={"raw": SENTINEL})

    def test_display_values_must_be_text(self):
        with self.assertRaises(TypeError):
            CodedError(
                ReasonCode.FILE_UNREADABLE,
                params={"file_name": "a.pdf"},
                display={"file_name": 7},
            )

    def test_detail_stays_out_of_the_message(self):
        error = CodedError(ReasonCode.RUBRIC_MISSING, detail=SENTINEL)
        self.assertNotIn(SENTINEL, str(error))
        self.assertEqual(error.detail, SENTINEL)

    def test_a_param_outside_the_spec_is_refused(self):
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.RUBRIC_MISSING, params={"raw": SENTINEL})

    def test_a_non_scalar_param_is_refused(self):
        with self.assertRaises(TypeError):
            CodedError(
                ReasonCode.FILE_UNREADABLE, params={"file_name": RuntimeError("x")}
            )

    def test_a_missing_param_is_refused(self):
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.FILE_UNREADABLE)

    def test_an_audit_only_code_has_no_error_body(self):
        with self.assertRaises(ValueError):
            CodedError(ReasonCode.WRONG_PASSWORD)

    def test_a_subclass_fixes_its_code(self):
        class RubricMissingError(CodedError):
            reason_code = ReasonCode.RUBRIC_MISSING

        self.assertEqual(RubricMissingError().reason_code, ReasonCode.RUBRIC_MISSING)

    def test_the_message_layer_shows_the_message_and_never_the_detail(self):
        """The path background-task item text and _failure_response's
        fallback take (describe_user_error / describe_background_task_error),
        which coded_response itself does not go through. QA-ERR-03 for
        per-item errors (the S6a mutation battery's M8)."""
        for code in REASON_CODES:
            with self.subTest(code=code):
                error = CodedError(code, params=sample_params(code), detail=SENTINEL)
                for describe in (describe_user_error, describe_background_task_error):
                    shown = describe(error, fallback_message="fallback")
                    self.assertEqual(shown, error.message)
                    self.assertNotIn(SENTINEL, shown)

    def test_only_user_and_validation_classes_are_user_facing(self):
        self.assertTrue(is_user_facing_error(CodedError(ReasonCode.RUBRIC_MISSING)))
        self.assertTrue(is_user_facing_error(CodedError(ReasonCode.NOT_RETRYABLE)))
        self.assertFalse(is_user_facing_error(CodedError(ReasonCode.PROVIDER_FAILURE)))

    def test_refusal_response_still_answers_refusals_only(self):
        self.assertIsNone(refusal_response(CodedError(ReasonCode.RUBRIC_MISSING)))
        self.assertIsNone(refusal_response(ValueError("x")))
        self.assertEqual(
            refusal_response(InsufficientCreditsError("x")).status_code, 402
        )


# ---------------------------------------------------------------- through the API


@override_settings(ROOT_URLCONF=__name__)
class CodedEnvelopeThroughTheAPITests(SimpleTestCase):
    def envelope(self, response):
        body = response.json()
        self.assertIs(body["success"], False)
        return body, body["error"]["field_errors"]

    def assert_nothing_leaks(self, response):
        raw = response.content.decode()
        for leak in (SENTINEL, "Traceback", "RuntimeError", "CodedError"):
            self.assertNotIn(leak, raw)

    def test_every_user_facing_code_answers_its_own_envelope(self):
        for code, spec in REASON_CODES.items():
            with self.subTest(code=code):
                response = self.client.get(f"/coded/{code.value}")
                self.assertEqual(response.status_code, spec.http_status)
                body, envelope = self.envelope(response)
                message = spec.render(sample_params(code))
                self.assertEqual(body["message"], message)
                self.assertEqual(envelope["error"], message)
                self.assertEqual(envelope["reason_code"], code.value)
                self.assertEqual(envelope["error_class"], spec.error_class.value)
                self.assertEqual(envelope["remediation"], spec.remediation or None)
                self.assertIs(envelope["retryable"], spec.retryable)
                self.assertEqual(envelope["params"], sample_params(code))
                self.assertEqual(envelope.get("code"), LEGACY_CODES.get(code))
                if spec.retry_after is not None:
                    self.assertEqual(response["Retry-After"], str(spec.retry_after))

    def test_qa_err_03_no_exception_text_reaches_any_body(self):
        for code in REASON_CODES:
            with self.subTest(code=code):
                self.assert_nothing_leaks(self.client.get(f"/coded/{code.value}"))
        for kind in ("credits", "empty_wallet", "plan"):
            with self.subTest(kind=kind):
                self.assert_nothing_leaks(self.client.get(f"/refusal/{kind}"))

    def test_qa_err_04_reference_is_the_request_id(self):
        for url in ("/coded/RUBRIC_MISSING", "/refusal/credits", "/caught"):
            with self.subTest(url=url):
                response = self.client.get(url)
                _, envelope = self.envelope(response)
                self.assertTrue(envelope["reference"])
                self.assertEqual(envelope["reference"], response["X-Request-ID"])

    def test_qa_err_04_an_inbound_request_id_is_never_the_reference(self):
        """X-5 wins over the first QA-ERR-04 reading (SM ruling): echoing a
        client-controlled value as our reference would let a caller forge
        correlation ids. The reference is the server's id; the client's UUID
        is kept only as `client_request_id`."""
        inbound = "3f2b9c1e-6d4a-4e8f-9a0b-1c2d3e4f5a6b"
        CodedSeeingTheClientId.seen.clear()
        response = self.client.get("/coded-client-id", HTTP_X_REQUEST_ID=inbound)
        _, envelope = self.envelope(response)
        self.assertEqual(envelope["reason_code"], "PROVIDER_FAILURE")
        self.assertEqual(envelope["reference"], response["X-Request-ID"])
        self.assertNotEqual(envelope["reference"], inbound)
        self.assertNotEqual(envelope["reference"], inbound.replace("-", ""))
        self.assertEqual(CodedSeeingTheClientId.seen["client_request_id"], inbound)

    def test_the_old_refusals_keep_their_message_and_legacy_code(self):
        cases = (
            ("credits", 402, "insufficient_credits", "INSUFFICIENT_CREDITS"),
            ("empty_wallet", 402, "insufficient_credits", "INSUFFICIENT_CREDITS"),
            ("plan", 403, "ai_feature_not_available", "AI_FEATURE_NOT_AVAILABLE"),
        )
        for kind, status_code, legacy, code in cases:
            with self.subTest(kind=kind):
                response = self.client.get(f"/refusal/{kind}")
                self.assertEqual(response.status_code, status_code)
                body, envelope = self.envelope(response)
                self.assertEqual(envelope["code"], legacy)
                self.assertEqual(envelope["reason_code"], code)
                if kind != "plan":
                    self.assertEqual(body["message"], INSUFFICIENT_CREDITS_MESSAGE)
                else:
                    self.assertEqual(
                        body["message"], "AI grading isn't included in your plan."
                    )

    def test_a_view_that_catches_and_answers_itself_gets_the_same_envelope(self):
        response = self.client.get("/caught")
        self.assertEqual(response.status_code, 409)
        _, envelope = self.envelope(response)
        self.assertEqual(envelope["reason_code"], "RUBRIC_MISSING")
        self.assert_nothing_leaks(response)

    def test_a_plain_serializer_error_is_not_mistaken_for_an_envelope(self):
        """A field named like an envelope key (`params`) still shows its
        error when the dict carries no reason_code."""
        body = self.client.get("/plain").json()
        self.assertEqual(body["message"], "Params: This field is required.")


@override_settings(ROOT_URLCONF=__name__)
class RequireAiAccessTests(SimpleTestCase):
    """@require_ai_access answers with the coded body (S6a, SM option A),
    never the internal reason it logs."""

    def test_a_refused_request_gets_the_plan_refusal_without_the_reason(self):
        response = self.client.get("/gated")
        self.assertEqual(response.status_code, 403)
        body = response.json()
        envelope = body["error"]["field_errors"]
        spec = REASON_CODES[ReasonCode.AI_FEATURE_NOT_AVAILABLE]
        self.assertEqual(envelope["reason_code"], "AI_FEATURE_NOT_AVAILABLE")
        self.assertEqual(envelope["code"], "ai_feature_not_available")
        self.assertEqual(body["message"], spec.message)
        self.assertEqual(envelope["reference"], response["X-Request-ID"])
        raw = response.content.decode()
        self.assertNotIn("not authenticated", raw)
        self.assertNotIn("AI access denied", raw)

    def test_a_balance_reason_gets_the_credits_refusal(self):
        for reason in (NO_CREDITS_REMAINING_REASON, TRIAL_CREDITS_EXHAUSTED_REASON):
            with self.subTest(reason=reason):

                def refuse(user, feature=None, reason=reason):
                    return False, reason

                with patch.object(access_control, "can_user_access_ai", refuse):
                    response = self.client.get("/gated")
                self.assertEqual(response.status_code, 402)
                envelope = response.json()["error"]["field_errors"]
                self.assertEqual(envelope["reason_code"], "INSUFFICIENT_CREDITS")
                self.assertEqual(envelope["error"], INSUFFICIENT_CREDITS_MESSAGE)
                self.assertNotIn(reason, response.content.decode())


class RendererMessageTests(SimpleTestCase):
    def test_a_coded_body_flattens_to_its_display_sentence_alone(self):
        body = {"error": "The one sentence.", "reason_code": "RUBRIC_MISSING"}
        body.update({key: "x" for key in ENVELOPE_KEYS if key not in body})
        self.assertEqual(flatten_errors(body), "The one sentence.")
