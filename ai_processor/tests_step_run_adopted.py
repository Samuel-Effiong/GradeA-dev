"""A test adopted from the Next-stage Checker's probe for slice 0.

`test_a_refused_mark_changes_nothing` is the Checker's, as written in its
probe module tests_0c_probe_ai_call_record_0.py (sha256 4edbdc6eda7a4276...),
handed over in its verification record of slice 0 (VERIFIED-WITH-NOTES at
2b54b7e1) and adopted by the Senior Manager's ruling of 8 October 2026. It
was seen red by the Checker's own run of 8 October 2026, 18:01 to 18:05 WAT,
against its mutant XS14 (a refused mark that still leaves "received" set),
the one failing test of that mutant. The Builder's own 33 slice-0 mutants did
not catch XS14. Changes from the Checker's text: the module and class names,
this docstring, and the provider name (a local constant instead of the
probe's shared one).
"""

from django.test import SimpleTestCase

from ai_processor.step_run import StepRun

ALPHA = "alpha/model"


class ARefusedMarkTest(SimpleTestCase):
    def test_a_refused_mark_changes_nothing(self):
        run = StepRun("recheck")
        with self.assertRaises(RuntimeError):
            run.mark_reply_received(ALPHA)
        self.assertFalse(run.reply_received)
        self.assertIsNone(run.reply_model)
        self.assertFalse(run.call_left)
