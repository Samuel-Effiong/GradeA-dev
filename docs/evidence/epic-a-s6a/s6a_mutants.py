"""S6a mutants: (name, file, old, new). Each old must match exactly once."""

MUTANTS = [
    (
        "M1_renderer_shows_envelope_keys",
        "users/renderers.py",
        "                and not (coded and key in ENVELOPE_KEYS)\n",
        "",
    ),
    (
        "M2_reference_dropped",
        "AutoGrader/reason_codes.py",
        '"reference": get_request_id(),',
        '"reference": None,',
    ),
    (
        "M3_emitter_accepts_any_upper_code",
        "audit/emitter.py",
        "if not isinstance(reason_code, str) or reason_code not in ReasonCode.values:",
        "if not isinstance(reason_code, str) or not reason_code.isupper():",
    ),
    (
        "M4_params_not_whitelisted",
        "AutoGrader/reason_codes.py",
        "        unknown = set(params) - spec.params\n",
        "        unknown = set()\n",
    ),
    (
        "M5_legacy_code_dropped",
        "AutoGrader/reason_codes.py",
        '        body["code"] = LEGACY_CODES[code]\n',
        "        pass\n",
    ),
    (
        "M6_refusal_response_answers_everything",
        "billing/refusals.py",
        "    if not is_permanent_refusal(error):\n        return None\n",
        "",
    ),
    (
        "M7_decorator_no_credits_mapping",
        "billing/access_control.py",
        "                if reason in (\n                    NO_CREDITS_REMAINING_REASON,\n",
        "                if False and reason in (\n                    NO_CREDITS_REMAINING_REASON,\n",
    ),
    (
        "M8_detail_leaks_into_message",
        "AutoGrader/error_messages.py",
        "        return error.message\n",
        '        return f"{error.message} {error.detail}"\n',
    ),
    (
        "M9_handler_ignores_coded_errors",
        "users/exceptions.py",
        "if is_permanent_refusal(exc) or isinstance(exc, CodedError):",
        "if is_permanent_refusal(exc):",
    ),
    (
        "M10_provider_counts_as_user_facing",
        "AutoGrader/error_messages.py",
        "return error.spec.error_class in (ErrorClass.USER, ErrorClass.VALIDATION)",
        "return True",
    ),
]
