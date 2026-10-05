# H-112: the H-1 stampede harness reads its database server URL from a variable

Author: Integration & Release (0b), 2026-10-05. Verifier: v2. Base: task/beta-batch-7 `085adecd`.

## What was wrong
`docs/evidence/h1_stampede/harness.tar.gz` (committed at `a9baf3e5`, 2026-09-15) held, in two of its shell scripts, a database URL with a literal password: `postgresql://postgres:<password>@127.0.0.1:5432/…`. Three lines: `run_all.sh` line 28, `run_followup.sh` lines 32 and 48. One distinct string of 12 characters. It was found on 2026-10-05, when 1a's pattern check matched inside the archive; a plain grep does not open it.

**The string is not printed anywhere in this change:** not in this record, not in the diff below, not in a log, not in a commit message. Whether it is a real password is with the founder.

## What this change does
The archive is rebuilt. In the three lines, `postgresql://postgres:<password>@127.0.0.1:5432` is replaced by `${STAMPEDE_PG_SERVER:?…}`, so each script takes the server URL from a required variable and stops with a message if it is unset. `SHA256SUMS.txt` in the same directory gets the new archive's hash on its first line. Nothing imports or runs the harness; no test is involved.

The three lines as they are now:

```
run_all.sh:28        export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_$NAME"
run_followup.sh:32   export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_s3"
run_followup.sh:48   export STAMPEDE_DB="${STAMPEDE_PG_SERVER:?set STAMPEDE_PG_SERVER to the Postgres server URL without a database name}/ag_h1_stampede_$NAME"
```

Before, each read `export STAMPEDE_DB="postgresql://postgres:<MASKED>@127.0.0.1:5432/ag_h1_stampede_…"` with the same ending.

## Checks (0b; v2 repeats the comparison and the scan)
| Check | Result |
|---|---|
| Occurrences of the string in the old archive, any member | 3, all in the three lines above |
| Members: names, order, modes, owners, times, types, old against new | identical, 8 members, same tar format (PAX), gzip header time 0 as before |
| The six other members (`stampede_settings.py`, `seed.py`, `targets.py`, `measure_cost.py`, `measure_stampede.py`, `measure_warm_vs_cold.py`) | byte-identical |
| `run_all.sh` | 1 line differs (28); 2873 → 2920 bytes |
| `run_followup.sh` | 2 lines differ (32, 48); 3498 → 3592 bytes |
| The string in the new archive (searched in the gunzipped tar) | absent |
| `bash -n` on both changed scripts | exit 0 |
| `sha256sum -c SHA256SUMS.txt` | all 9 lines OK |
| Old archive sha256 | `78cb3cb46f909536…` |
| New archive sha256 | `d07af47015753f30…` |
| Whole-tree scan of this commit (URLs with a password part; archives opened) | no line in `harness.tar.gz` |

The scripts were not run: running them needs a Postgres server and the seeded databases of the 2026-09 measurement.

## What this change does NOT do
- **It does not remove the string from the repository's history.** It stays reachable at `a9baf3e5` and every commit after it up to this one, on origin/beta. Taking it out of the tree does not take it off the remote. No pushed branch is rewritten without the founder.
- If the string is a real password, the remedy is to change that password. That is the founder's action.
- origin/main never had this file.

## The scan and its limits
Tool: `credscan.py` (0b; kept outside the repo in `GAP-0b-runs/credscan/`). It reads every file of a commit, opens `.gz`, `.tar.gz`/`.tgz`, `.tar` and `.zip` (nested to three levels), and reports URLs of the form `scheme://user:password@host` with the password masked: line count, length, shape class, file.
- On beta `141c8031` and phase2/epic-a `db6f5155` this archive was the only place with this string. The other hits are five placeholder lines the SM has classified (`.example.env`, two CI workflows, `QA_SERVER_SETUP.md`, `docs/evidence/mypy_django_stubs/EVIDENCE.md`).
- It finds that URL form only. A password in another form (`KEY=value`, a header, a JSON field) is not found by it. The repo's detect-secrets hook covers some of those, but does not open archives. Binary formats other than the four above are not opened.
