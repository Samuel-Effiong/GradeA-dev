"""The six ANSWER_* settings, pinned for the tests of the reading (AI-call record, slice 2).

AutoGrader/settings.py reads each of the six from the environment and the
project's .env file, so a test that does not pin them passes or fails by the
machine it runs on. The tests of the reading decorate their classes with
`@override_settings(**PINNED)`; a test that wants a different value overrides
it inside. The values are the settings' defaults. No test lives here.
"""

PINNED = {
    "ANSWER_BLANK_VERIFICATION_ENABLED": True,
    "ANSWER_BLANK_VERIFICATION_MAX_PAGES": 10,
    "ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS": 12,
    "ANSWER_BLANK_VERIFICATION_MODEL": "",
    "ANSWER_COMPLETENESS_ENFORCEMENT": "strict",
    "ANSWER_EXTRACTION_SCHEMA_ENABLED": True,
}
