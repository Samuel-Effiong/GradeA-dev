# H-270 gate expectations (SM, written and committed before any gate run)

## Run 2 expectations (SM, committed before run 2)
Found by batch 31 run 1 (f85790bc, --parallel 4): ERROR
`SavesThatKeepThePictureTests.test_an_old_jpeg_picture_still_loads_unchanged`, `FileNotFoundError`
on `/tmp/h270_media_<x>/profile_pics/old/legacy.jpg`. The module made one temp MEDIA_ROOT at import
and every class's tearDownClass deleted it; the --parallel workers fork after import and share the
path, so one worker deleted another's file. Run 1 ran sequentially and could not see it
(beta-batch-31/NOTES.md, run 1 record, holds the log).

Fix f4da58ff (test file only): each test makes its own temp media folder in setUp, overrides
STORAGES/MEDIA_ROOT for itself, and removes the folder in its cleanup. No production change.

Tip under test: the commit that adds this section. Every line of run 1's expectations below stands
(red 15/13 with the same blocks and fragments, makemigrations, seam, own 15, near 1553 skipped=4
all CI_REQUIRE, M1-M8 with the same tests and fragments), with one added step:
- **parallel**: `users.tests_h270_profile_webp --parallel 3`, five times in a row: each Ran 15, OK.
No deterministic red exists for a race; the break is the committed batch 31 run 1 log.


Tip under test: 958cd554 (tests baa94a5b, code 958cd554), base 94a52815 (H-267 evidence tip).
Runner: `python -I docs/evidence/h270-profile-webp/gate.py 958cd554`, private venv
`h266_list_errors_dict_env` (batch-30 pins, Pillow 13.0.0). The runner checks every line below and
prints STOP on any difference.

Development runs before this file: the 15 new tests, green, with the code in place (to check the
tests run at all). No red run, near suite or mutant has been run.

## Red (call-site revert: users/models.py at 94a52815, i.e. the save() hook and its import removed)
- "Ran 15", `FAILED (failures=13)`, no ERROR, no `_FailedTest`.
- The 13 FAIL blocks are every test except the 2 guards in `SavesThatKeepThePictureTests`.
- 12 of them fail on the stored name: `False is not true : profile_pics/` (assertStoredAsWebP).
- `test_a_webp_upload_loses_its_exif_too` fails on the metadata: `b'EXIF' unexpectedly found in`.
  (The upload is already named .webp and is WebP, so only the EXIF check can catch it.)

## Green
- makemigrations --check: no changes (`upload_to` and the field are unchanged).
- seam (`git diff --name-only 94a52815 HEAD`, production files only):
  `AutoGrader/image_formats.py`, `users/models.py`.
- own `users.tests_h270_profile_webp`: Ran 15, OK.
- near `users classrooms assignments.tests_h267_pillow_formats`: **Ran 1553**, OK (skipped=4).
  Count from the DiscoverRunner id list: 1538 at 94a52815 + the 15 new = 1553, 0 load failures.
  The 4 skips are the opt-in ones H-267's rerun had (users only): 3 `CI_REQUIRE_NETWORK`,
  1 `CI_REQUIRE_REDIS`. Any skip without `CI_REQUIRE` is a stop.
- No existing test stores a profile picture (checked by grep: only H-267's validator tests and a
  read in tests_old_activation_door_is_closed), so no existing test is expected to change.

## Mutants (each runs `users.tests_h270_profile_webp`; KILLED = these tests FAIL with these fragments)
| id | change | killed by | fragment |
|---|---|---|---|
| M1 | hook skipped (`if False:`) | test_the_model_stores_a_new_picture_as_webp | `False is not true` |
| M2 | the upload's EXIF passed to the encoder (`exif=opened.info.get("exif", b"")`) | test_gps_and_orientation_are_gone_and_the_picture_is_upright | `b'EXIF' unexpectedly found in` |
| M3 | no `exif_transpose` | test_gps_and_orientation_are_gone_and_the_picture_is_upright | `(40, 20) != (20, 40)` |
| M4 | no `thumbnail` | test_a_large_picture_is_shrunk_to_1024_on_its_longest_side | `(3000, 1500) != (1024, 512)` |
| M5 | alpha flattened (`mode = "RGB"`) | test_transparency_is_kept | `'RGB' != 'RGBA'` |
| M6 | `_committed` check removed (re-encode on every save) | test_a_save_that_keeps_the_picture_does_not_re_encode_it; test_an_old_jpeg_picture_still_loads_unchanged | `!=` |
| M7 | the original file name kept (`name=os.path.basename(upload.name)`) | test_the_model_stores_a_new_picture_as_webp | `False is not true` |
| M8 | the ICC profile passed to the encoder (`icc_profile=icc or b""`) | test_an_icc_profile_is_not_stored | `b'ICCP' unexpectedly found in` |

Other tests failing as well are recorded as `also_failed`, not a mismatch: the table names the
killing tests, not an exclusive set.

No mutant for "first frame only": the pixels are pasted into a fresh single-frame image, so an
animated output cannot be produced by any one-line change; test_an_animated_gif_is_stored_as_its_first_frame
documents it and fails red.

## Run 1 on 6d800043 (code = 958cd554; the later commit is docs-only): GREEN, matches every line above
- red: Ran 15, FAILED (failures=13), the 13 non-guard blocks with the declared fragments (red_run.txt).
- makemigrations: no changes. seam: AutoGrader/image_formats.py, users/models.py (seam.txt).
- own: Ran 15, OK (own.txt). near: Ran 1553, OK (skipped=4), all 4 CI_REQUIRE opt-in (near.txt.gz).
- mutants M1-M8: all KILLED by their declared tests and fragments (mutation_results.json, mutant_logs/).
  also_failed is as the design implies: M1/M7 fail every WebP test; M2 also fails the WebP-upload test.
- Runner output: gate_out_run1.txt (exit=0).

## Run 2 on 5f5dd848 (tests f4da58ff): GREEN, matches every line above
- red: Ran 15, FAILED (failures=13), the 13 non-guard blocks with the declared fragments.
- makemigrations: no changes. seam: AutoGrader/image_formats.py, users/models.py.
- own: Ran 15, OK. parallel: five `--parallel 3` runs, each Ran 15, OK (parallel/).
- near: Ran 1553, OK (skipped=4), all 4 CI_REQUIRE (near.txt.gz).
- mutants M1-M8: all KILLED by their declared tests and fragments; also_failed as in run 1.
- Runner output: gate_out_run2.txt (exit=0). This file set replaces run 1's (history keeps run 1).
