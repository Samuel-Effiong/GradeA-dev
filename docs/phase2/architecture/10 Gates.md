# Production Implementation and 10-Gate Verification Protocol

## Gate 1 — Baseline / Regression

**Nothing else broke.**

Before a change is called done, the checks of every part of the program that the change touches are run, once, by the release engineer, and they pass. The list of what already failed before the change, and the log of the run, are kept, so a failure that was already there is not blamed on the new work and anyone can read what was run. The person who built the change does not run this gate, and the person who verifies it does not run it again.

### Gate 2 — Mutation

**The checks really catch mistakes.**

For each protection the new work adds, that protection is deliberately broken, one break at a time, and which check must fail is written down BEFORE the run. A break counts as caught only if the run really finished and the check named is among those that failed. A break that is not caught is a hole: it is fixed or explained. Two rules go with this: a check counts as evidence only once it has been seen failing (rule 19), and a new check must fail for the reason written down for it beforehand, not for another reason such as a broken set-up (rule 22).

### Gate 3 — Concurrency

**It holds when many things happen at once.**

Where the work moves money or changes a shared record, it is tested with several real processes acting at the same moment, against a real database, repeated enough to catch a rare bad timing. Each piece of work names how many repeats it used and why; for a race the number is not less than 20 unless the work explains why. Afterwards the arithmetic is checked: no credit lost, none counted twice.

### Gate 4 — Adversarial / Attack

**It holds against someone trying to get round it.**

Checks are written from the side of the person who wants to cheat, or who by mistake does the wrong thing: using another teacher's number, doing something twice, doing something they are not allowed to do, sending a strange input. Each check must show that the program refuses in the right way and writes nothing it should not. This gate also covers the quiet failure: one person's content (a name, an answer, a score) showing up in another person's reply, or in a log.

A piece of work that does not need one of Gates 1 to 4 (for example, a pure wording change has no money in it) may say "does not apply", with the reason.

### Gate 5 — Failure / Recovery

Test what happens when dependencies or operations fail.

Consider:

- PostgreSQL unavailable;
- Redis unavailable;
- Stripe timeout;
- Stripe API error;
- AI provider timeout;
- AI provider rejection;
- network failure;
- worker crash;
- task retry;
- webhook retry;
- duplicate webhook;
- partial database operation;
- transaction rollback;
- process interruption;
- cache failure;
- queue failure;
- malformed external response;
- service unavailable halfway through an operation.

### Gate 6 — Stress / Scale

Test realistic workload, not merely toy data.

Where applicable, test:

- large numbers of users;
- large schools;
- many courses;
- many assignments;
- many submissions;
- large files;
- batch operations;
- simultaneous requests;

### Gate 7 — Real Infrastructure

Do not claim infrastructure correctness using only mocks.

Where relevant, test against real services:

- PostgreSQL;
- Redis;
- Celery/workers;
- Stripe test environment;

### Gate 8 — Live / End-to-End

Test the actual externally reachable workflow.

Where appropriate, use:

**real client/request → authentication → API → serializer → service → database → queue → worker → external provider → database → response**

rather than testing isolated functions only.

Verify:

- deployed URL;
- deployed configuration;
- authentication;
- permissions;
- migrations;
- workers;
- Redis;
- database;
- external integrations;
- webhook routing;
- background jobs;
- actual response behavior.

### Gate 9 — Security / Isolation

Explicitly prove that data and authority remain inside their intended boundaries.

Test boundaries such as:

### User isolation

- User A cannot access User B's private data.

### Teacher isolation

- Teacher A cannot access or modify Teacher B's resources.

### Student isolation

- Student A cannot access Student B's submissions or private information.

### School isolation

- School A cannot access School B's data.

### Role isolation

Verify:

- STUDENT;
- TEACHER;
- SCHOOL_ADMIN;
- SUPER_ADMIN;
- Django/admin-only accounts where applicable.

### Billing isolation

Verify that one user's:

- credits;
- subscription;
- overage;
- refunds;
- disputes;
- transactions

cannot affect another user's account incorrectly.
