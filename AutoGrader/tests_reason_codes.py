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
  - QA-ERR-04: `reference` is the response's `X-Request-ID`.
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
from AutoGrader.error_messages import is_user_facing_error
from AutoGrader.reason_codes import (
    AUDIT_ONLY_CODES,
    ENVELOPE_KEYS,
    LEGACY_CODES,
    REASON_CODES,
    CodedError,
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
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            raise CodedError(ReasonCode.RUBRIC_MISSING, detail=SENTINEL)
        except CodedError as exc:
            return coded_response(exc)


class GatedView(APIView):
    """A view behind billing.access_control.require_ai_access."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @require_ai_access
    def get(self, request):
        return Response({"ok": True})


class PlainSerializerError(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"params": ["This field is required."]}, status=400)


urlpatterns = [
    path("coded/<str:code>", RaiseCoded.as_view()),
    path("refusal/<str:kind>", RaiseRefusal.as_view()),
    path("caught", CaughtInView.as_view()),
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
                self.assertTrue(spec.message.strip() and spec.remediation.strip())
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
                self.assertEqual(envelope["remediation"], spec.remediation)
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

    def test_qa_err_04_an_inbound_request_id_is_the_reference(self):
        inbound = "3f2b9c1e-6d4a-4e8f-9a0b-1c2d3e4f5a6b"
        response = self.client.get("/coded/PROVIDER_FAILURE", HTTP_X_REQUEST_ID=inbound)
        _, envelope = self.envelope(response)
        self.assertEqual(envelope["reference"], inbound)

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
