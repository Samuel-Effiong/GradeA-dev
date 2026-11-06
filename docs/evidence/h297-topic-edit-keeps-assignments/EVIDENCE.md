# H-297 evidence: editing a course's topics no longer deletes its assignments

Base: origin/beta 40f514f68. Branch task/h297-topic-edit-keeps-assignments.

| Step | Result | Where |
|---|---|---|
| Probe on staging 2f00c078 | PATCH /course with the same topic_names: 200, assignment under "Cells" deleted | BOARD H-297 |
| Tests first (35c36e77c) | red 7/9 (the 2 passing pin unchanged behavior: edit without topic_names, course delete) | 01_red.log |
| Fix (d79d15975) | green 9/9 | 02_green.log |
| Plants | P1 SET_NULL->CASCADE killed (4 fail); P2 match-by-name->delete-all killed (4 fail); P3 no-trim survived, explained (DRF CharField trims first) | 03_plants.log |
| Near run: classrooms + assignments | Ran 1526 OK (skipped=13) | ~/Documents/Projects/GAP-a0-runs/h297_near_run.log |

Migration assignments/0041_topic_set_null: AlterField on_delete only; sqlmigrate = no-op (on_delete is enforced by Django). Additive.
No new user-facing wording. Production has no Topic rows (owner, 2026-11-06), so no production data was lost.
Full suite not run (not authorized); CI Tests runs on the beta push.
