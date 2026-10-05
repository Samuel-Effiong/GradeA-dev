# H-112: the H-1 stampede harness takes its database server and password from variables

Author: Integration & Release (0b), 2026-10-05. Verifier: v2. Base: task/beta-batch-7 `085adecd`.

**This record replaces the one committed at `7a32e6fe`, which v2 REJECTED. See "The first attempt" below.**

## What was wrong
`docs/evidence/h1_stampede/harness.tar.gz` (committed at `a9baf3e5`, 2026-09-15) held ONE password, written in TWO forms, on FOUR lines of two shell scripts:

| Script | Line | Form |
|---|---|---|
| `run_all.sh` | 28 | inside a database URL, percent-encoded (12 characters) |
| `run_followup.sh` | 32 | inside a database URL, percent-encoded (12 characters) |
| `run_followup.sh` | 48 | inside a database URL, percent-encoded (12 characters) |
| `run_followup.sh` | 14 | plain (10 characters), the value of an exported `PGPASSWORD` |

The URLs name the `postgres` user on host 127.0.0.1, port 5432. Line 15 of `run_followup.sh` uses the exported value for `psql` as that user. By the scripts' own use, the string is the Postgres superuser's password on the machine where the September 2026 measurement ran. Whether it is still in use anywhere is with the founder.

**The string is not printed anywhere in this change**, in either form: not in this record, not in a log, not in a commit message. This record also writes no URL with anything in the password position.

## What this change does
The archive is rebuilt from the original (the one at `085adecd`):
- On the three URL lines, everything from the scheme to the port is replaced by a required variable, `STAMPEDE_PG_SERVER`.
- Line 14 exports `PGPASSWORD` from the environment and requires it to be set.
- Each script stops with a message naming the variable if it is unset.
- `SHA256SUMS.txt` in the same directory gets the new archive's hash on its first line.

Nothing imports or runs the harness; no test is involved.

The four lines as they are now:

```
run_all.sh:28        export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_$NAME"
run_followup.sh:14   export PGPASSWORD="${PGPASSWORD:?set PGPASSWORD for the postgres user before running this script}"
run_followup.sh:32   export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_s3"
run_followup.sh:48     export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_$NAME"
```

## Checks (0b; v2 repeats them by its own program)
The search for the old string covers three forms: as found in the URLs, its percent-decoded form, and the decoded form percent-encoded again (two distinct strings in all, 12 and 10 characters).

| Check | Result |
|---|---|
| Lines holding any form in the ORIGINAL archive | `run_all.sh` 28; `run_followup.sh` 14, 32, 48 |
| Lines holding any form in the archive of the first attempt (`7a32e6fe`) | `run_followup.sh` 14 |
| Lines holding any form in the NEW archive | none |
| Members: names, order, modes, owners, times, types, original against new | identical, 8 members, same tar format (PAX), gzip header time 0 as before |
| The six other members (`stampede_settings.py`, `seed.py`, `targets.py`, `measure_cost.py`, `measure_stampede.py`, `measure_warm_vs_cold.py`) | byte-identical |
| `run_all.sh` | 1 line differs (28); 2873 → 2920 bytes |
| `run_followup.sh` | 3 lines differ (14, 32, 48); 3498 → 3660 bytes |
| `bash -n` on both changed scripts | exit 0 |
| `sha256sum -c SHA256SUMS.txt` | all 9 lines OK |
| Any form in any tracked file of this tree, archives opened (2,773 files; `.gz`, `.tar.gz`, `.tar`, `.zip`, nested) | none. This clears the two sibling archives `raw/harness-logs.tar.gz` and `raw/run-outputs.tar.gz`, so they are left as they are |
| Original archive sha256 | `78cb3cb46f909536…` |
| New archive sha256 | `24916dc18066aec5…` |

The scripts were not run: running them needs a Postgres server and the seeded databases of the September measurement.

## The first attempt, and why it was rejected
The commit `7a32e6fe` replaced the three URL lines only. Its checks passed because they looked for the URL form: the scan reported URLs with a password part, and the "old string" search used the string as it stood in the URLs, which is percent-encoded. Line 14 holds the same password decoded, so neither check saw it. v2 found it by comparing decoded against encoded forms. The lesson is in the scan now (below).

The archive of `7a32e6fe` stays in this branch's history. It adds nothing that the original at `a9baf3e5` does not already hold.

## What this change does NOT do
- **It does not remove the password from the repository's history.** Both forms stay reachable at `a9baf3e5` and at every commit after it, on origin/beta. Taking it out of the tree does not take it off the remote. No pushed branch is rewritten without the founder.
- If the password is still in use, the remedy is to change it. That is the founder's action.
- origin/main never had this file.

## The scan and its limits
Tool: `credscan.py` (0b; kept outside the repo in `GAP-0b-runs/credscan/`). It reads every file of a commit and opens `.gz`, `.tar.gz`/`.tgz`, `.tar` and `.zip`, nested to three levels. Every value is masked in its output (kind, line count, length, shape class, file). It finds:
1. URLs with anything in the password position.
2. Assignments `NAME=value` and `NAME: value` where NAME contains PASS, PWD, SECRET or TOKEN, or is a KEY of a secret kind (API, ACCESS, PRIVATE, SIGNING, ENCRYPTION, AUTH, SECRET); `export` and quotes optional.
3. One value written two ways: every value found is compared with the percent-decoded and percent-encoded forms of every other.

Limits:
- A password in a form outside 1 and 2 (a header, a JSON field under another name, a command-line flag) is not found.
- Assignments under a name with KEY alone (cache keys and the like) are counted, not listed.
- In test files, literal assignments are counted, not listed: test passwords are expected there.
- Binary formats other than the four above are not opened.
- The shape classes are a guide. "LITERAL" means "not obviously a variable or a placeholder word", not "a real secret".
