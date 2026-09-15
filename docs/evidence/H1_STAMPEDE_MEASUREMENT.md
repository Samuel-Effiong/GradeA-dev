# H-1 Stage 3 item 2 — stampede measurement after H-10

This evidence lives in the repo rather than a scratchpad, which is wiped between sessions. The raw result rows, run
outputs and the exact harness and seed scripts are in `docs/evidence/h1_stampede/`,
listed in `SHA256SUMS.txt`.

**Purpose (owner's order):** H-10 has merged. Re-measure cache
rebuild and stampede behaviour on current beta. Then decide which cache
families actually need stampede protection. Wildcard removal (Stage 3) comes
after that decision.

**Status: measured. Recommendation below (§6) awaits the owner's decision.**

---

## 1. Setup

| | |
|---|---|
| Code | beta `29cc1c7` (after H-10 and H-9); production code paths identical to `91f752b`, no new migrations (verified by diff) |
| Server | local gunicorn 20.0.4, **9 workers × 4 gthreads**, the Dockerfile's production shape |
| Settings | `DEBUG=False`; DRF throttling disabled so bursts measure the cache, not the throttle |
| Redis | a **dedicated** `redis-server` on `127.0.0.1:6390`, used by nothing else. Rebuilds are counted exactly as the `SET` delta over a burst, minus a same-size warm-burst baseline |
| PostgreSQL | 18.6, dedicated DBs `ag_h1_stampede_s1/s2/s3` |
| Host | 8 cores. Every run started only when no other session's tests were running: §2–§4 ran 03:07–03:22, and the s3 herd in §5 ran 03:28:31–03:28:48 (see note ²) |
| Cache keys | real versioned keys. "Cold" means the family's real generation scope was bumped, so the read is a genuine miss through production code |

### Datasets

| Label | Schools | Courses / school | Students | Enrolments / student | Enrolments | Assignments | Submissions |
|---|---|---|---|---|---|---|---|
| s1 | 10 | 24 | 600 | 12 | 7,200 | 1,920 | 17,311 |
| s2 | 10 | 240 | 600 | **120 (unrealistic)** | 72,000 | 19,200 | 172,958 |
| s3 | 10 | 240 | 6,000 | 12 | 72,000 | 19,200 | 172,882 |

s2 scaled courses tenfold but not the student pool. The per-student views it
reports are inflated: `my_courses` rebuilt in 638 ms on s2 but 80 ms on s3.
s3 is the realistic large-school dataset, and s2 is kept only as a stress
point.

## 2. Rebuild cost per cached family (in-process, 5 cold builds each)

All 24 families returned HTTP 200 at every scale. **Query counts are
identical at s1, s2 and s3 for every family**, so H-10's flatness holds
everywhere. Rebuild time instead grows with rows processed.

| Family | Cold p50 s1 / s2 / s3 (ms) | Queries |
|---|---|---|
| 23 school-admin summary | 211 / 2,329 / **1,429** | 17 |
| 16 superadmin usage | 54 / 576 / **652** | 7 |
| 21 superadmin students | 33 / 302 / **285** | 5 |
| 18 superadmin scaling signals | 19 / 85 / 106 | 8 |
| 17 superadmin AI performance | 15 / 81 / 90 | 9 |
| 11 student my_courses | 52 / 638 / 80 | 6 |
| 26 teacher overview | 65 / 140 / 62 | 18 |
| teacher submission list (mixin) | 48 / 45 / 47 | **63** |
| all other 16 families | ≤ 51 at every scale | ≤ 17 |

Warm reads are 2–5 ms everywhere. At realistic scale (s3), only **23, 16 and
21** cross the ~200 ms threshold approved for stampede protection.

The superadmin students family (21) was ~1,938 ms before H-10, on an earlier
seed, so the two numbers compare only roughly.

## 3. Same-key stampede (live gunicorn)

Each burst is N simultaneous requests at ONE cache key, fired immediately
after its scope is bumped.

At N=50, the expensive families rebuild **~25–35 times out of 50**. Almost
every request rebuilds, because none waits for another's result. Cheap
families under ~20 ms largely limit themselves at s1: 1–5 rebuilds at N=50.

## 4. Stampede cost versus plain queueing (warm vs cold, N=50, 5 rounds)

A warm burst (all hits) at the same concurrency isolates queueing. The extra
latency is the stampede itself.

| Family | s1 extra p50 | s2 extra p50 | **s3 extra p50** | s3 warm p50 | s3 cold p50 | s3 rebuilds / 50 |
|---|---|---|---|---|---|---|
| 23 school-admin summary | +2,107 ms | +16,301 ms | **+18,187 ms** | 57 ms | 18,244 ms | 27–34 |
| 16 superadmin usage | +607 ms | +5,817 ms | **+5,407 ms** | 40 ms | 5,446 ms | 22–33 |
| 21 superadmin students | +137 ms | +2,756 ms | **+2,746 ms** | 55 ms | 2,801 ms | 27–32 |
| 26 teacher overview | +688 ms | +1,189 ms | **+1,382 ms** | 93 ms | 1,475 ms | 28–35 |
| 11 student my_courses | +679 ms | +7,845 ms¹ | **+1,280 ms** | 95 ms | 1,375 ms | 32–33 |

¹ The s2 figure for `my_courses` is inflated by that seed (§1).

- **Queueing is negligible.** Warm bursts stay at 40–95 ms at N=50, so 36
  worker threads are not the bottleneck.
- **The whole cold-burst cost is duplicate rebuilds.** Zero non-200
  responses occurred in any round.

## 5. Who actually shares these cache keys

A same-key stampede happens only when many requests hit the SAME key at
once. The key construction for the expensive families:

| Family | Key | Scopes that invalidate it | Realistic concurrent readers of one key |
|---|---|---|---|
| 16, 21 superadmin | `superadmins:user_id__<id>:…` (**per user**) | `global` | 1 (production has one superadmin) |
| 23 school-admin summary | `schooladmins:user_id__<id>:view__summary` (**per user**) | `usr` + `sch` | ~1 (one person; production has at most 1 active admin per school) |
| 26 teacher overview | `teacheradmins:user_id__<id>:instance__id__<session>:…` (**per user**) | `usr` | ~1 |
| 11 my_courses | per student | `usr` + `global` | ~1 per key, but **many keys invalidated together** |

**Every expensive family is keyed per user.** A 50-way same-key stampede
needs one user sending 50 simultaneous requests. Real exposure is 1–2
concurrent requests per key: a page load, a retry, or a second tab. So §3–§4
measure what a stampede *would* cost, not a load production generates.

The realistic multi-request pattern is a **herd**: one bump invalidates
MANY users' distinct keys, and they rebuild independently. Single-flight
cannot help a herd, because each key is different.

| Herd | s1 | s2 | s3 |
|---|---|---|---|
| 10 students' my_courses after a `global` bump | p50 274 / p95 372 ms | p50 2,031 / p95 3,157 ms | p50 276 / p95 313 ms² |
| 50 students' my_courses after a `global` bump | p50 1,360 / p95 2,346 ms | p50 9,328 / p95 14,448 ms¹ | p50 792 / p95 1,262 ms² |
| 7 school-admin panels after a school bump | p50 41 ms | p50 33 ms | p50 34 ms² |

² The s3 herd ran 03:28:31–03:28:48. Another session's test run began at 03:28:49, according to that run's own log (its suites began at 03:28:56). The two windows are adjacent, not overlapping. That session had first been scheduled for ~03:25, so the timing was cross-checked against its log rather than assumed.

## 6. Recommendation (for the owner's decision)

**1. Single-flight: no cache family needs it today.** At realistic scale
three families cross the ~200 ms threshold (23, 16, 21), but all three are
keyed per user and read by about one person at a time. The measured
amplification needs dozens of simultaneous requests at one user's key, which
production does not generate. Adding locks would cost a Redis round trip on
every miss, plus lock-handling code, for a scenario not observed.

**2. TTL jitter: not needed either.** Freshness is generation-driven. Per-user
keys are written when each user first views them, so their TTLs are already
staggered. A synchronised mass expiry only follows a cold start or deploy,
and then at the same per-user concurrency.

**3. Re-evaluate single-flight if any of these become true** (record as the
trigger):
- a cache key **shared by many users** (not per user) has a cold rebuild
  ≥ 200 ms;
- a per-user expensive family gains real concurrent readers, for example
  multiple superadmins or a shared school dashboard key;
- the school-admin summary's production cold rebuild reaches ≥ 1 s for a
  real school.

**4. What the measurements DO show is worth tracking, as separate items
rather than stampede protection:**
- **Family 23 rebuild cost.** 1.4 s at 240 courses/school, growing with rows
  processed. It is invalidated by any activity in the school (`sch`), so
  admins of busy schools will often load it cold. It needs query or
  aggregation work.
- **The herd behind `global`-scoped per-user families** (11 my_courses,
  16/21 superadmin): one change anywhere invalidates everyone's copy. That
  cannot be fixed with locks, only by narrowing the dependency, e.g. family
  11's `global` scope. Size it with the s3 herd figures in §5.
- **Teacher submission list: 63 queries** at every scale, a per-row query
  pattern. Not slow at these sizes, but it grows with page size.

**5. H-1 order:** if the owner accepts (1)–(3), Stage 3 item 2 is DECIDED:
no stampede protection, justified by measurement, with re-evaluation
triggers. H-1 proceeds to Stage 3 item 4, legacy wildcard removal, under its
own verification.
