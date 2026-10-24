# H-206 notes

- **The switch is OFF by default** (the owner, 24 October 2026: privacy first and the extra
  DeepSeek cost is accepted, but `AI_PROVIDER_ZDR_ENABLED` defaults to false). Switching it on
  is a host setting plus a restart of the web, worker and Beat processes.
- **google/gemini-2.5-flash** (our third second-opinion candidate) has NO zero-retention
  endpoint today: with the switch on, calls to it would fail. H-205 replaces it; H-206 makes no
  change for it.
- Mutants Z1-Z15 (no Z2): expected sets rewritten for the default-off tests (mutate.py, written
  before any run). Z12 now sets the default back to on.

## Expected result of the default-off tests against the code before the fix

```
H-206 default-OFF tests: expected result against the code BEFORE the fix (settings default=True,
helper fallback True). Written 2026-10-24 before any run of these tests. Fragment = text that
must appear inside that test's own FAIL block.

RED (4):
  TheSwitch.test_the_default_is_off
      fragment: "True is not False"
  TheSwitch.test_the_settings_module_reads_the_variable_with_default_false
      fragment: 'env.bool("AI_PROVIDER_ZDR_ENABLED", default=True)'
  TheSwitch.test_at_the_default_no_request_carries_zdr_anywhere
      fragment: "'zdr': True"   (the first assertion: _provider_block() == OFF)
  TheSwitch.test_the_switch_is_read_at_each_call_not_once_at_import
      fragment: "Tuples differ"
GREEN both before and after the fix (set the switch by override_settings, so the default does not matter):
  TheSwitch.test_off_sends_no_zdr_and_on_sends_zdr_true_on_every_path
  TheSwitch.test_with_the_switch_off_the_block_is_exactly_data_collection_deny
  every test in TheRequestAsksForZeroRetentionOnEveryPath and TheHelper (class-level override on)
  all static tests (NAMED reason text reworded only)
```
