# Mutation battery @ f7cd15e4aa1765ed6be9424fe93a4d1f0959f6f7

| Mutant | Guard | Result | Ran | Summary | Restore sha256 ok | Time |
|---|---|---|---|---|---|---|
| M1 | extract_assignment keeps refusal type | KILLED | Ran 2 tests in 4.759s | FAILED (failures=2) | True (4c2226ddac0e) | 38s |
| M2 | extract_assignment_image keeps refusal type | KILLED | Ran 3 tests in 11.974s | FAILED (failures=4) | True (4c2226ddac0e) | 47s |
| M3 | dashboard chat answers refusal 402/403 | KILLED | Ran 25 tests in 25.022s | FAILED (failures=7) | True (13fb928c9100) | 58s |
| M4 | superadmin analytics chat answers refusal 403 | KILLED | Ran 9 tests in 12.423s | FAILED (failures=2) | True (ff4199ef7662) | 44s |
| M5 | DRF handler maps uncaught refusal | KILLED | Ran 6 tests in 21.631s | FAILED (failures=20, errors=1) | True (11e9af940c11) | 26s |
| M6 | student submission views answer refusal 402/403 | KILLED | Ran 18 tests in 69.581s | FAILED (failures=3, skipped=1) | True (6ff27ecd69de) | 73s |
| M7 | UPLOAD_REFUSALS includes permanent AI refusals | KILLED | Ran 2 tests in 16.571s | FAILED (failures=4) | True (226fecf4b458) | 21s |
| M8 | answer-edit task result message is described | KILLED | Ran 2 tests in 14.720s | FAILED (failures=1) | True (226fecf4b458) | 18s |
| M9 | upload task result message is described | KILLED | Ran 2 tests in 14.819s | FAILED (failures=1) | True (226fecf4b458) | 19s |
| M10 | batch grade failure records a message | KILLED | Ran 1 test in 8.872s | FAILED (failures=2) | True (226fecf4b458) | 12s |
| M11 | task failure recorder logs refusal at WARNING | KILLED | Ran 2 tests in 0.032s | FAILED (failures=2) | True (d3e1adc4b59f) | 4s |
| M12 | course summary handles refusal | KILLED | Ran 2 tests in 5.851s | FAILED (failures=1) | True (c3e2069ba223) | 10s |
| M13 | school admin summary handles refusal | KILLED | Ran 2 tests in 5.352s | FAILED (failures=1) | True (c3e2069ba223) | 10s |
| M14 | credit refusal text replaced by generic message | KILLED | Ran 64 tests in 77.769s | FAILED (failures=41) | True (393dc457e7f2) | 82s |
| M15 | 402 for credits, 403 for feature | KILLED | Ran 5 tests in 12.866s | FAILED (failures=19) | True (71e0259bd9fc) | 17s |
| M16 | InsufficientCreditsError is permanent | KILLED | Ran 18 tests in 55.330s | FAILED (failures=23) | True (71e0259bd9fc) | 59s |
| M17 | AIFeatureNotAvailableError is permanent | KILLED | Ran 18 tests in 59.429s | FAILED (failures=10) | True (71e0259bd9fc) | 63s |
| M18 | empty wallet is a credit refusal, not ParseError+HTML | KILLED | Ran 20 tests in 74.348s | FAILED (failures=18, errors=11) | True (39ac759a6212) | 78s |
| M19 | EmptyWalletError is an APIException | SURVIVED | Ran 20 tests in 60.204s | OK | True (59d06190fb76) | 65s |
| M20 | renderer only drops a STRING code | KILLED | Ran 8 tests in 0.002s | FAILED (failures=1) | True (57400248602d) | 3s |
| M21 | renderer drops code beside message | KILLED | Ran 11 tests in 6.597s | FAILED (failures=6) | True (57400248602d) | 10s |
| M22 | course summary counts refused narration | KILLED | Ran 2 tests in 5.505s | FAILED (failures=1) | True (c3e2069ba223) | 9s |
| M23 | refusals log at WARNING | KILLED | Ran 4 tests in 4.882s | FAILED (failures=4) | True (71e0259bd9fc) | 8s |

Worker trees before removal (tracked files vs commit):

- /home/bond-servant-in-training/Documents/Projects/GAP-refusal-mut-w0: clean
- /home/bond-servant-in-training/Documents/Projects/GAP-refusal-mut-w1: clean
- /home/bond-servant-in-training/Documents/Projects/GAP-refusal-mut-w2: clean
- /home/bond-servant-in-training/Documents/Projects/GAP-refusal-mut-w3: clean
