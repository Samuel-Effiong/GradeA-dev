# B1b-10 battery: the match-count table (at f886de96b, product = 67851f24e)

Each anchor's count is from `run_battery.py check` (`battery_check_f886de96b.txt`). Full edits, labels and notes: `battery.json`.

| # | Plant | Group | Files | Anchor matches | Status | Killer label (tests) | Declared | Fragment(s) |
|---|---|---|---|---|---|---|---|---|
| 1 | M-B1 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `32 != 7` |
| 2 | M-B2 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `True is not false` |
| 3 | M-B3 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `(200, 0) != (100, 100)` |
| 4 | M-B6 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `True is not false` |
| 5 | M-B7 | B1a | billing/holds.py | 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `0 is not None` |
| 6 | M-B11 | B1a | billing/holds.py | 1, 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `1 != 0` |
| 7 | M-B13 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `ZeroCreditViolation not raised` |
| 8 | M-B14 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (errors=1) | `InsufficientCreditsError` |
| 9 | M-B15 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (errors=1) | `creditwallet_reserved_credits_nonneg` |
| 10 | M-B16 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `InsufficientCreditsError not raised` |
| 11 | M-B18 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `InsufficientCreditsError not raised` |
| 12 | M-B21 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 1` |
| 13 | M-B22 | B1a | billing/holds.py | 1, 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'PENDING' != 'FAILURE'` |
| 14 | M-B23 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Tuples differ` |
| 15 | M-B25 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 80` |
| 16 | M-B26 | B1a | billing/holds.py | 1, 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `1 != 0` |
| 17 | M-B27 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Expected '_AIProcessor__ai_model' to not have been called` |
| 18 | M-B28 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `(60, 60, 0) != (60, 60, 40)` |
| 19 | M-B30 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `InsufficientCreditsError not raised` |
| 20 | M-B31s | B1a | billing/holds.py | 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `True == True` |
| 21 | TP-1 | B1a | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 20003` |
| 22 | PFB-1 | B1b-1..3 | billing/apps.py | 1 | same | 14 test(s) | Ran 14, FAILED (failures=14) | `600 != 300 : reserved`; `item_sum job=` |
| 23 | PFB-2 | B1b-1..3 | billing/holds.py | 1 | same | 7 test(s) | Ran 7, FAILED (failures=7) | `'RESERVED' != AIJobStatus.SETTLED` |
| 24 | PFB-3 | B1b-1..3 | billing/holds.py | 1 | same | 11 test(s) | Ran 11, FAILED (failures=11) | `item_sum job=` |
| 25 | PFB-4 | B1b-1..3 | billing/holds.py | 1 | same | 6 test(s) | Ran 6, FAILED (failures=2, errors=4) | `KeyError: 'item_id'` |
| 26 | PFB-6 | B1b-1..3 | billing/holds.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=1, errors=1) | `120 != 300`; `creditwallet_reserved_credits_nonneg` |
| 27 | PFB-7 | B1b-1..3 | billing/holds.py | 1 | same | 4 test(s) | Ran 4, FAILED (failures=4) | `item_sum job=` |
| 28 | PFB-8 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | (none recorded; the declared tests must fail) |
| 29 | PFB-9 | B1b-1..3 | billing/holds.py | 1 | same | 7 test(s) | Ran 7, FAILED (failures=7) | (none recorded; the declared tests must fail) |
| 30 | PFB-9b | B1b-1..3 | billing/apps.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | (none recorded; the declared tests must fail) |
| 31 | PB1-1 | B1b-1..3 | AutoGrader/settings.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'True' != 'False'` |
| 32 | PB2-1 | B1b-1..3 | assignments/upload_door.py | 1 | same | 25 test(s) (env {'BILLING_HOLDS_ENABLED': 'True'}) | Ran 25, FAILED (failures=22, errors=3) | (none recorded; the declared tests must fail) |
| 33 | PB3-1 | B1b-1..3 | billing/holds.py | 1 | reanchored | 4 test(s) | Ran 4, FAILED (failures=4) | `2 != 1 : the number of settle charges` |
| 34 | PB3-2 | B1b-1..3 | billing/holds.py | 1 | reanchored | 4 test(s) | Ran 4, FAILED (failures=4) | `[300] != []` |
| 35 | PB3-3 | B1b-1..3 | billing/holds.py | 1 | same | 11 test(s) | Ran 11, FAILED (failures=11) | `!= 0 : a hold was left reserved` |
| 36 | PB4-1 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `reached the provider` |
| 37 | PB4-2 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `False is not True` |
| 38 | PP1 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `39 != 37` |
| 39 | PP1b | B1b-1..3 | billing/holds.py | 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `'RUNNING' != 'SETTLED'` |
| 40 | PP2 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `39 != 37` |
| 41 | PP3 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `38 != 37` |
| 42 | PP3b | B1b-1..3 | billing/models.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `InsufficientCreditsError not raised` |
| 43 | PP4 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `38 != 37` |
| 44 | PP5 | B1b-1..3 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `38 != 37` |
| 45 | PPL | B1b-1..3 | billing/holds.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `a billing write came before the wallet lock`; `38 != 37` |
| 46 | PE-1 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'SUFFICIENT' != 'MARGINAL'` |
| 47 | PE-2 | B1b-4 | billing/estimates.py | 1, 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'teacher_shape' != 'assignment'` |
| 48 | PE-3 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'teacher_shape' != 'assignment'` |
| 49 | PE-3b | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'assignment' != 'teacher_shape'` |
| 50 | PE-4 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'teacher_shape' != 'shape'` |
| 51 | PE-5 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `32800 != 21280` |
| 52 | PE-6 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `!= 810` |
| 53 | PE-7 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `3000 != 750` |
| 54 | PE-8 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `8000 != 12000` |
| 55 | PE-9 | B1b-4 | billing/estimates.py | 1, 1 | reanchored | 1 test(s) | Ran 1, FAILED (failures=1) | `12000 != 8000` |
| 56 | PE-10 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `is not an instance of <class 'list'>` |
| 57 | PE-11 | B1b-4 | billing/estimate_views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `1 != 0` |
| 58 | PE-12 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `[1199] != [200]` |
| 59 | PE-13 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `[700] != [200]` |
| 60 | PE-14 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `!= [100]` |
| 61 | PE-15 | B1b-4 | billing/estimate_views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `200 != 404` |
| 62 | PE-16 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `!= []` |
| 63 | PE-17 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `!= []` |
| 64 | PE-18 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `!= []` |
| 65 | PE-19 | B1b-4 | billing/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `unexpectedly None` |
| 66 | PE-20 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Expected 'delay' to not have been called` |
| 67 | PE-21 | B1b-4 | billing/management/commands/billing_settings.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 1` |
| 68 | PE-22 | B1b-4 | billing/management/commands/billing_settings.py | 1, 1 | same | 1 test(s) | Ran 1, FAILED (failures=5, errors=3) | `CommandError not raised` |
| 69 | PE-23 | B1b-4 | billing/estimates.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `2.0 != 3.0` |
| 70 | PE-24 | B1b-4 | billing/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `not less than or equal to` |
| 71 | PE-24b | B1b-4 | billing/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `no logs of level WARNING or higher triggered on AutoGrader.beat_locks` |
| 72 | PK-1 | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `unexpectedly None` |
| 73 | PK-2 | B1b-5 | students/item_retry.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `202 != 409` |
| 74 | PK-3 | B1b-5 | students/signals.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `True is not false` |
| 75 | PK-4 | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `500 != 200` |
| 76 | PK-5 | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'FILE_UNREADABLE' != ''` |
| 77 | PK-5b | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'SUCCESS' != ` |
| 78 | PK-6 | B1b-5 | students/task_tracking.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `unexpectedly None` |
| 79 | PK-7 | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `datetime.timedelta(days=30) != datetime.timedelta(days=3)` |
| 80 | PK-8 | B1b-5 | students/kept_uploads.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `is not None` |
| 81 | PK-9 | B1b-5 | students/item_retry.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `First differing element 0:` |
| 82 | PK-10 | B1b-5 | students/data_export.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `First extra element 4:` |
| 83 | PK-11 | B1b-5 | AutoGrader/settings.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `ImproperlyConfigured not raised` |
| 84 | PK-12 | B1b-5 | students/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `unexpectedly None` |
| 85 | PK-13 | B1b-5 | students/student_data_map.py | 1 | same | 1 test(s) | Ran 1, FAILED (errors=1) | `KeyError: ('students.KeptUpload', 'processing_task')` |
| 86 | PK-14 | B1b-5 | assignments/upload_prompts.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `return a JSON.` |
| 87 | PK-15 | B1b-5 | students/item_retry.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `202 != 409` |
| 88 | PK-16 | B1b-5 | students/tests_data_erase.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `set() is not true` |
| 89 | PK-6a-1 | B1b-5 | students/task_tracking.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `a refused item's file must go` |
| 90 | PK-6a-2 | B1b-5 | students/task_tracking.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `an unclassified failure keeps its file` |
| 91 | PK-6a-3 | B1b-5 | students/task_tracking.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `an upload credit refusal is retryable` |
| 92 | PB6-1 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `4 != 2 : the promoter launched beyond k` |
| 93 | PB6-2 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 3 : expiry deleted papers` |
| 94 | PB6-3 | B1b-6 | billing/batch_queue.py | 1 | same | 2 test(s) | Ran 2, OK (declared survivor, SM ruling §6.6.10) | (none recorded; the declared tests must fail) |
| 95 | PB6-4 | B1b-6 | assignments/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Lists differ: ['` |
| 96 | PB6-5 | B1b-6 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 1` |
| 97 | PB6-6 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'FAILURE' != BackgroundTaskStatus.SUCCESS : the sweep overwrote a skipped paper` |
| 98 | PB6-7 | B1b-6 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `2 != 1 : counted once, not twice` |
| 99 | PB6-8 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 100 : a hold returned twice` |
| 100 | PB6-9 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | ` is not None : promotion did not clear the credit stop` |
| 101 | PB6-10 | B1b-6 | billing/batch_queue.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'RESERVED' != AIJobStatus.FAILED : D6-a: failures count toward the last item` |
| 102 | PB6-11 | B1b-6 | assignments/views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'celery-a144b1e3' is not None : D6-d: a waiting paper has no task` |
| 103 | PB6-12 | B1b-6 | assignments/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (errors=1) | `AIJob matching query does not exist.` |
| 104 | PB6-13 | B1b-6 | assignments/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `0 != 1 : the scheduled batch did not go through the helper` |
| 105 | PB6-14 | B1b-6 | assignments/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'1 / 3' != '3 / 3'` |
| 106 | PB6b-1 | B1b-6b | billing/estimate_views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `200 != 400 : 501 ids were accepted` |
| 107 | PB6b-2 | B1b-6b | billing/estimate_views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'Submission ids: Ensure this field has no [19 chars]nts.' != 'You can get a cost estimate for up to 500[64 chars]ers.'` |
| 108 | PB7-1 | B1b-7 | billing/low_balance.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `Lists differ: ['You have 25% of your credits left', 'You have 25% of your credits left'] != ['You have 25% of your credits left']`; `2 != 1 : two racing checks sent twice` |
| 109 | PB7-2 | B1b-7 | billing/low_balance.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `Lists differ: ['You have 25% of your credits left', 'You have 25% of your credits left'] != ['You have 25% of your credits left']`; `2 != 1 : two racing checks sent twice` |
| 110 | PB7-3 | B1b-7 | billing/low_balance.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Lists differ: ['You have 25% of your credits left'] != ['You have 25% of your credits left', 'You have 25% of your credits left']` |
| 111 | PB7-5 | B1b-7 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Lists differ: [('credit_estimate_gap', {'bucket': '1.2-2[47 chars]e'})] != []` |
| 112 | PB7-6 | B1b-7 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `('credit_estimate_band_hit', {'hit': 'true'}) not found in [('credit_estimate_gap', {'bucket': '1.2-2'}), ('credit_estimate_band_hit', {'hit': 'false'})]` |
| 113 | PB7-e1 | B1b-7 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `None != '` |
| 114 | PB7-e2 | B1b-7 | students/views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'absent' != '` |
| 115 | PB7-e3 | B1b-7 | students/views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'ai_job_id' unexpectedly found in {'id': '` |
| 116 | PB7-other | B1b-7 | billing/job_views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `200 != 404` |
| 117 | P8-1 | B1b-8 | billing/low_balance.py | 1 | same | 1 test(s) | Ran 1, FAILED (errors=1) | `'NoneType' object has no attribute 'date'` |
| 118 | P8-2 | B1b-8 | users/views.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'kept_until' unexpectedly found in {'task_id': '` |
| 119 | P8-3 | B1b-8 | students/task_tracking.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `'QUEUED_PENDING_CREDITS' != AIJobStatus.CANCELLED` |
| 120 | P8-4 | B1b-8 | billing/batch_queue.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `Expected 'delay' to not have been called. Called 1 times.`; `Expected 'delay' to not have been called. Called 1 times.` |
| 121 | P8-5 | B1b-8 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `no logs of level WARNING or higher triggered on billing.holds` |
| 122 | P9-1 | B1b-9 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `1 != 0` |
| 123 | P9-2 | B1b-9 | billing/management/commands/billing_holds_drain.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `not found in 'DRAINED\n'` |
| 124 | P9-3 | B1b-9 | billing/holds.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `Tuples differ: (0, 1) != (1, 1)`; `Tuples differ: ('PENDING', '') != ('FAILURE', 'PROVIDER_FAILURE')` |
| 125 | P9-4 | B1b-9 | billing/models.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Expected 'delay' to not have been called. Called 1 times.` |
| 126 | P9-4b | B1b-9 | billing/tasks.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `Expected 'promote_all' to not have been called. Called 1 times.` |
| 127 | P9-5 | B1b-9 | billing/batch_queue.py | 1 | same | 5 test(s) | Ran 5, FAILED (failures=5) | `Expected 'delay' to not have been called. Called 1 times.`; `Expected 'delay' to not have been called. Called 1 times.`; `Expected 'delay' to not have been called. Called 1 times.`; `Expected 'delay' to not have been called. Called 1 times.`; `Expected 'delay' to not have been called. Called 1 times.` |
| 128 | P9-6 | B1b-9 | billing/holds.py | 1 | same | 1 test(s) | Ran 1, FAILED (failures=1) | `InsufficientCreditsError not raised` |
| 129 | P9-7 | B1b-9 | billing/management/commands/billing_holds_drain.py | 1 | same | 2 test(s) | Ran 2, FAILED (failures=2) | `Items in the second set but not the first:`; `Tuples differ: ('PENDING', '') != ('FAILURE', 'PROVIDER_FAILURE')` |
