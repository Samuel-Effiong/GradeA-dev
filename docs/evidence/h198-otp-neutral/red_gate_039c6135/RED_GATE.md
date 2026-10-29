# H-198: the red gate on 039c6135

Granted by the Senior Manager on 25 October 2026. Run 14:39:09 to about 14:42 WAT inside one `systemd-inhibit`, gate
exit 2 ("STOP: not green"). The mutants did not run. Kept as the record, the same as batch 24's red.

- **Red run** (`users/views.py` as at 7286717e): as written. 15 blocks, each holding its written fragment, no ERROR,
  "Ran 94 tests in 5.061s", "FAILED (failures=15)".
- **makemigrations --check and the seam check:** both passed ("SEAM OK: only otp changed, in the two ruled branches (and
  its OpenAPI text)").
- **Step 1** (own, near, the 21 guards): "Ran 733 tests in 125.602s", "FAILED (failures=2, skipped=3)". Both failures
  are in `users.tests_auth_endpoints.OTPEndpointBranchTests`, older tests that pinned the two answers the user ruled
  on:
  - `test_verify_email_on_an_already_verified_account_is_refused` (C), line 354: "AssertionError: 202 != 400";
  - `test_reset_password_on_an_unverified_account_is_refused` (G, switched off, no admin power), line 383:
    "AssertionError: 202 != 400".
- **Why:** the code did what the ruling says; these two tests still expected the old 400. The search for tests that
  pinned C or G looked for the route only inside each test function. This class sets `self.url = reverse("auth-otp")`
  in `setUp`, so the search missed both.
- **The fix** (approved by the Senior Manager):
  - both tests now expect the neutral 202 with the unknown address's bytes, and are renamed `..._gets_the_neutral_answer`;
    - C also checks that no activation token is made;
    - G checks that the activation email is sent and no reset code is made;
  - `users.tests_auth_endpoints` joins the red run (17 blocks over 5 modules) and the mutant battery. M1, M2, M3, M4, M5
    and M8 name the two tests where they apply;
  - a wider search, by syntax tree, of every test file that names the route anywhere (`setUp`, class attributes,
    helpers, module constants). Its table is in `NOTES.md`.
- **Files:** `gate_console.txt` (the gate's own output), `red_run_code_as_at_base.txt.gz`, `makemigrations_check.txt`,
  `seam_changed_files.txt`, `seam_code.txt`, `modules_and_guards.txt.gz` (step 1, the two failure blocks near its end).
