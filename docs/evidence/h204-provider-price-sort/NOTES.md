# H-204 notes

- **What changed** (approved by the Senior Manager, 25 October 2026): `_provider_block()` in
  `ai_processor/services.py` adds two keys to the `provider` object of every request:
  - `"sort": "price"`: the provider tries the cheapest supplier of the asked model first, and the next cheapest
    on an outage;
  - `"quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"]`: no fp4 endpoint.

  They are module constants (`PROVIDER_SORT`, and `PROVIDER_QUANTIZATIONS` as a tuple), so tests and mutants can
  name them. The block gets a new list each call. It is typed `Dict[str, Any]`, since its values are now a
  string, a list and a boolean. The editor's type checker flagged the old `block["zdr"] = True` into a dict of
  strings; the mypy hook passed both before and after. `data_collection` and the zdr
  switch are unchanged.
- **Production files:** `ai_processor/services.py` only. The code change is the helper and the two constants; the
  comment beside the request in `__ai_model` gains one sentence.
- **Tests:** a new module, `ai_processor/tests_h204_provider_price_sort.py`. In `ai_processor/tests_provider_block.py`
  (H-206), `EXPECTED` and `OFF` gain the two keys (`ROUTING`), because H-206's tests compare whole blocks.
- **Every path carries it:** `__ai_model` is the one place a request leaves (`services.py`, the one
  `chat.completions.create`), and it builds `extra_body.provider` from `_provider_block()`.
  - The whole-tree grep (after batch 24's run ended, as ruled): the only `chat.completions.create` outside the
    tests is in `__ai_model`. The only other client is `benchmark/isolation_run8/isolation_harness.py`, which
    replaces `ai_processor.client` and still sends through `__ai_model`. Every other match is in an old
    `docs/evidence/*/mutate.py`.
  - H-206's static tests (`NoRequestCanBeSentWithoutTheBlock`) keep this true, and run in step 1.
- **The endpoints read** (public `GET /api/v1/models/<id>/endpoints`, no key, 25 October 2026 09:49; saved in the
  Builder's `GAP-a0-runs/h204/endpoints/`):
  - grok-4.3: 4 endpoints, all xAI, all quantization "unknown";
  - claude-haiku-5.5: 10 endpoints, all "unknown";
  - gpt-5.4-nano: 4 endpoints, all "unknown";
  - deepseek-v4-pro: 15 endpoints: 9 fp8, 4 unknown, 2 fp4 (Relace $1.50, AtlasCloud $3.38 output per million).
- **"unknown" stays** (the Senior Manager's ruling 2). Without it grok, haiku and nano would have no endpoint, and
  every call would fail. `test_unknown_stays_because_grok_and_haiku_publish_none` pins it, and mutant P5 shows it.
- **No config-version change** (ruling 3). Neither key is in `GradingConfig` or `ReadingConfig`, the same as zdr in
  H-206, so saved answers are still reused. An answer saved from an fp4 endpoint before the deploy can be reused
  until its 3-day lifetime ends. That is cheaper than paying for a whole round of re-grading.
- **Stated risk, accepted** (ruling 4):
  - `sort: "price"` turns off the provider's load balancing, so the cheapest deepseek suppliers (Baidu $0.416,
    StreamLake $0.418 output) come first even if slower.
  - Grading runs in the background, and the timeouts and fallbacks are unchanged. The docs say so.
  - **If grading time on beta grows noticeably, the sort is dropped and the default order comes back.** That is a
    later decision, not code here.
- **With zdr on**, the sort applies only among the zero-retention endpoints that the zdr filter leaves.
- **Not shown by the tests:** what the provider does with the two keys (no live call; no paid call without the
  Senior Manager's word). The tests show what we send.

## Expected reds against 0e3028a8 (written before any run)

The red run takes `services.py` back to the base and runs both modules as at the tip.

```
ai_processor.tests_h204_provider_price_sort (11 red, 1 green)
  test_the_cheapest_supplier_is_tried_first                    "None != 'price'"
  test_the_allowed_quantizations                               "None != ['fp8', 'fp16', 'bf16', 'fp32', 'unknown']"
  test_unknown_stays_because_grok_and_haiku_publish_none       "'unknown' not found in []"
  test_no_fp4_endpoint                                         "None is not true : no quantization list is sent"
  test_with_zdr_on_the_block_keeps_zdr_and_has_both_keys       "None != 'price'"  (zdr and deny pass first)
  test_each_call_gives_a_new_list                              "None != ['fp8', 'fp16', 'bf16', 'fp32', 'unknown']"
  test_a_text_grading_request_and_its_deepseek_backup          "None != 'price'"
  test_a_text_grading_request_with_zdr_on                      "None != 'price'"
  test_an_image_grading_request_and_its_haiku_backup           "None != 'price'"
  test_the_second_opinion_request                              "None != 'price'"
  test_a_reading_request                                       "None != 'price'"
  GREEN (control): test_with_zdr_off_there_is_no_zdr_key_and_data_collection_is_deny
ai_processor.tests_provider_block (every block holds "'sort': 'price'", from the expected dict in the diff)
  test_the_provider_block_that_reaches_the_sdk_is_exact_on_every_path   8 blocks (one per path)
  test_off_sends_no_zdr_and_on_sends_zdr_true_on_every_path             8 blocks (one per path)
  test_at_the_default_no_request_carries_zdr_anywhere                   1 (fails before its subtests)
  test_with_the_switch_off_the_block_is_exactly_data_collection_deny    1
  test_the_switch_is_read_at_each_call_not_once_at_import               1 (tuple diff)
  test_it_exists_and_returns_the_block                                  1
  test_it_returns_a_new_dict_each_call                                  1
  test_a_change_to_a_sent_block_cannot_reach_the_next_request           1
  GREEN: every other test (zdr booleans, placement, the override, the static guards, the comment text)
Total: 33 FAIL blocks, no ERROR. The script counts them.
```

- The messages were checked by calling unittest's `assertEqual`, `assertIn` and `assertTrue` on the literal base and
  expected values in plain Python. No test method was run. The dict and tuple diffs print the expected dict over
  several lines, and `'sort': 'price'` is one of them.
- **One red traced by hand: `test_the_second_opinion_request` at the base.**
  1. The class settings give second-opinion candidates `[haiku, deepseek]`, minimum confidence 80, sample rate 0
     and cache off.
  2. `create` returns grader A's reply first: model grok, confidence 50, one ESSAY question. That is under 80, so the
     question is re-graded.
  3. `pick_second_model(grok, [haiku, deepseek])` gives haiku. Grader B's request has `model` haiku: request 2 of 2,
     with no third call (the same count as H-254's real-path test).
  4. At the base, `extra_body.provider` is `{"data_collection": "deny"}`. `provider.get("sort")` is None, so the test
     fails with "None != 'price'".
- **The control traced by hand: the zdr-off test.** At the base the block is `{"data_collection": "deny"}`: deny
  passes, and there is no zdr. It stays green after the change, since the change adds no zdr.

## Mutants (expected sets traced against every assertion of both battery modules)

The battery is the two modules. `_REQUESTS` stands for the five request tests: text, text with zdr on, image,
second opinion and reading. "H-206's eight" stands for the eight whole-block tests listed under the reds.

| Id | Break | Expected to fail |
|---|---|---|
| P1 | the sort key dropped | cheapest, zdr-on block, `_REQUESTS`, H-206's eight |
| P2 | the quantizations key dropped | allowed, unknown, no-fp4, zdr-on block, new list, `_REQUESTS`, H-206's eight |
| P3 | sort "throughput" | as P1 ("'throughput' != 'price'") |
| P4 | fp4 added back | allowed, no-fp4, zdr-on block, new list, `_REQUESTS`, H-206's eight. "unknown" is still there |
| P5 | "unknown" dropped | allowed, unknown, zdr-on block, new list, `_REQUESTS`, H-206's eight. No-fp4 passes |
| P6 | the two keys left out when a model is pinned | second opinion. H-206: exact-on-every-path, at-the-default, off/on and switch-off (each on the override path), and override-and-fallback (plain differs from pinned) |
| P7 | the two keys left out when zdr is on | zdr-on block, text with zdr on. H-206: exact-on-every-path, off/on (the "on" half), read-each-call, and the three helper tests (their class has zdr on) |
| P8 | the two keys left out for the reading task types | reading only (H-206's paths send no task type) |

P6 and P8 keep the one `_provider_block()` call inside `__ai_model` (in a dict comprehension), so H-206's static
guard ("exactly one call") stays green, and only the behaviour tests can catch them.

## The gate on 1100954f (granted by the Senior Manager, 25 October 2026, 11:22 WAT)

Run 11:21:49 to 11:27:39 WAT inside one `systemd-inhibit`, gate exit 0. Logs are in `gate_1100954f/` (gzipped).
Load at the end was 5.52 6.10 5.25.

- **Red run** (`services.py` as at 0e3028a8, both modules as at the tip): 33 FAIL blocks, each holding its written
  fragment, no ERROR, no OK line. "Ran 36 tests in 3.664s", "FAILED (failures=33)". Source restored.
- **makemigrations --check:** exit 0, no changes.
- **Seam:** "SEAM OK: only _provider_block and the two constants changed as code".
- **Step 1** (the 2 own modules, 22 near modules, 21 guards), 11:22:19 to 11:25:57: "Ran 772 tests in 197.996s", "OK".
- **Step 2, mutants** (own database, rules 17 and 18), 11:25:57 to 11:27:37: all 8 KILLED, each with "Ran 36 tests".
  Every mutant's failing tests are exactly its expected set, with nothing missing and nothing extra:

| Id | Failing / expected |
|---|---|
| P1 | 15 / 15 |
| P2 | 18 / 18 |
| P3 | 15 / 15 |
| P4 | 17 / 17 |
| P5 | 17 / 17 |
| P6 | 6 / 6 |
| P7 | 8 / 8 |
| P8 | 1 / 1 |

  The source was clean after the mutants, and the mutant database was dropped.
- The Senior Manager read the logs and ruled the gate GREEN.
