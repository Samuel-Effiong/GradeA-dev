# Subscription Flows — Full Detail, Plain Language

The same level of detail as the technical flowcharts (`SUBSCRIPTION_FLOW_DIAGRAMS.md`), just
described the way you'd explain it out loud instead of in code terms — for presentations and
anyone who isn't reading the codebase day to day.

---

## 1. Individual (Teacher) Subscription

Every step, choice, and edge case in how one teacher's subscription works, from signing up
to renewing to cancelling.

```mermaid
flowchart TD
    classDef entry fill:#2b6cb0,color:#fff,stroke:#1a4971,stroke-width:1px
    classDef decision fill:#d69e2e,color:#1a1a1a,stroke:#975a16,stroke-width:1px
    classDef error fill:#c53030,color:#fff,stroke:#742a2a,stroke-width:1px
    classDef webhook fill:#6b46c1,color:#fff,stroke:#44337a,stroke-width:1px
    classDef state fill:#2d3748,color:#fff,stroke:#1a202c,stroke-width:1px
    classDef task fill:#4a5568,color:#fff,stroke:#2d3748,stroke-width:1px
    classDef credit fill:#0987a0,color:#fff,stroke:#065666,stroke-width:1px

    subgraph Legend["Legend"]
        direction LR
        L1["Something the\nteacher does"]:::entry
        L2["A decision\npoint"]:::decision
        L3["Credits\nchange"]:::credit
        L4["Stripe confirms\nsomething happened"]:::webhook
        L5["Blocked or\nended"]:::error
        L6["Current state of\nthe account"]:::state
        L7["Automatic background\ncheck"]:::task
    end

    %% ===================== ENTRY POINTS =====================
    Signup(["New teacher\ncreates an account"]):::entry
    TrialCheckoutEP(["Starts a trial and adds\na card up front"]):::entry
    DirectCheckoutEP(["Goes straight to checkout\n(no trial)"]):::entry
    LegacySubscribeEP(["Subscribes directly\n(an older checkout path)"]):::entry
    SelectPlanEP(["Requests a plan change\n(upgrade / downgrade / resubscribe)"]):::entry
    CancelEP(["Clicks Cancel"]):::entry
    ResumeEP(["Clicks Resume\n(undo a cancellation)"]):::entry
    OverageEP(["Buys extra credits"]):::entry
    PaymentMethodEP(["Manages their\npayment method"]):::entry

    %% ===================== A. SIGNUP / AUTOMATIC TRIAL =====================
    Signup --> AutoTrialGuard{"Has this person\never had a trial before?"}
    AutoTrialGuard -- "yes" --> AutoTrialReject["No trial given"]:::error
    AutoTrialGuard -- "no" --> AutoTrialGrant["14-day free trial starts\n5,000 credits included\nno card required"]:::credit
    AutoTrialGrant --> AutoTrialState

    %% ===================== B. EXPLICIT TRIAL CHECKOUT (rare — card upfront) =====================
    TrialCheckoutEP --> TrialCkGuard{"Already on a trial, already\nsubscribed, or is this a\nschool/license plan?"}
    TrialCkGuard -- "yes, blocked" --> TrialCkReject["Request blocked"]:::error
    TrialCkGuard -- "no, ok to proceed" --> TrialCkSession["Stripe's checkout page opens\n(trial starts once the card is added)"]
    TrialCkSession --> CheckoutCompleted

    %% ===================== C. DIRECT CHECKOUT (unified builder) =====================
    DirectCheckoutEP --> DirectCkGuard{"Does the teacher already\nhave a paid, active\nsubscription?"}
    DirectCkGuard -- "yes" --> DirectCkReject["Blocked — use Upgrade\nor Downgrade instead"]:::error
    DirectCkGuard -- "no, or only a trial" --> DirectCkSession["Stripe's checkout page opens"]
    DirectCkSession --> CheckoutCompleted

    LegacySubscribeEP --> LegacyCkSession["Stripe's checkout page opens\n(older subscribe flow)"]
    LegacyCkSession --> CheckoutCompleted

    %% ===================== CHECKOUT CONFIRMATION =====================
    CheckoutCompleted(["Stripe confirms:\ncheckout is complete"]):::webhook
    CheckoutCompleted --> FlowSwitch{"What kind of\ncheckout was this?"}
    FlowSwitch -- "a normal signup checkout" --> HandleIndivCheckout["Process the\ncompleted checkout"]
    FlowSwitch -- "an older-style checkout" --> HandleIndivSubscribe["Process the\ncompleted checkout"]
    FlowSwitch -- "an older-style trial checkout" --> HandleIndivTrial["Process the\ncompleted trial checkout"]
    HandleIndivTrial --> CardTrialState
    FlowSwitch -- "a mid-trial upgrade" --> HandleTrialToPaid["Process the\nmid-trial upgrade"]
    FlowSwitch -- "an upgrade payment" --> HandleUpgradeCkCompleted
    FlowSwitch -- "an extra-credits purchase" --> HandleOverageCk

    HandleIndivCheckout --> TrialMetaGuard{"Was this checkout\nconverting an existing trial?"}
    TrialMetaGuard -- "yes" --> FinalizeTrialToPaid["Convert the trial\nto a paid plan"]
    TrialMetaGuard -- "no, brand new" --> ActivateSub["Activate the new\npaid subscription"]
    HandleIndivSubscribe --> ActivateSub
    HandleTrialToPaid --> FinalizeTrialToPaid

    ActivateSub --> BetaGate{"Is this the special Beta\nplan, and the teacher isn't\neligible for it?"}
    BetaGate -- "yes, blocked" --> BetaReject["Blocked — the Beta plan\nis teachers-only"]:::error
    BetaGate -- "no, proceed" --> ForfeitLingerTrial["Any leftover trial\ncredits are forfeited"]:::credit
    ForfeitLingerTrial --> GrantMonthly1["The new plan's credits\nare added to the account"]:::credit
    GrantMonthly1 --> ActiveState

    FinalizeTrialToPaid --> FTPGuard{"Is there actually an\nactive trial to convert?"}
    FTPGuard -- "no" --> FTPReject["Blocked — nothing\nto convert"]:::error
    FTPGuard -- "yes" --> FTPForfeit["Leftover trial\ncredits are forfeited"]:::credit
    FTPForfeit --> GrantMonthly2["The new plan's credits\nare added to the account"]:::credit
    GrantMonthly2 --> ActiveState

    %% ===================== TRIAL STATE & ITS OUTCOMES =====================
    %% There are two DIFFERENT trials, and only one of them ever involves
    %% Stripe. Keeping them as separate boxes (rather than one shared
    %% "trial" state) is deliberate -- merging them would make it look
    %% like a card could be declined on a trial that never had a card.
    AutoTrialState(["Automatic trial\n(no card on file — nothing has\nbeen set up with Stripe yet)"]):::state
    AutoTrialState --> AutoTrialOutcome{"How does\nthis trial end?"}
    AutoTrialOutcome -- "teacher chooses to subscribe\n(their first real checkout)" --> DirectCheckoutEP
    AutoTrialOutcome -- "credits run out\nbefore 14 days are up" --> ExpireTrialTask
    AutoTrialOutcome -- "14 days pass\nwithout subscribing" --> ExpireTrialTask

    CardTrialState(["Trial with a card on file\n(started through the optional,\nrarely-used upfront-card checkout)"]):::state
    CardTrialState --> CardTrialOutcome{"How does\nthis trial end?"}
    CardTrialOutcome -- "teacher upgrades early" --> TrialToPaidSession["Stripe's checkout page opens\nto convert the trial to paid"]
    TrialToPaidSession --> CheckoutCompleted
    CardTrialOutcome -- "trial ends, card is\ncharged automatically" --> InvoiceSucceededTrial
    CardTrialOutcome -- "trial ends, card\nis declined" --> InvoiceFailedTrial
    CardTrialOutcome -- "trial is cancelled from\nStripe's side early" --> SubDeletedTrial
    CardTrialOutcome -- "credits run out, or 14 days\npass, before Stripe's own\ncharge attempt" --> ExpireTrialTask

    InvoiceSucceededTrial(["Stripe confirms:\nthe trial's first\npayment succeeded"]):::webhook
    InvoiceSucceededTrial --> FinalizeTrialViaStripe["Convert the trial\nto a paid subscription"]
    FinalizeTrialViaStripe --> FTVSGuard{"Is this account still\nactually on a trial?"}
    FTVSGuard -- "no — already handled" --> FTVSNoop["Nothing more to do"]:::error
    FTVSGuard -- "yes" --> FTVSForfeit["Leftover trial credits forfeited,\nnew plan's credits added"]:::credit
    FTVSForfeit --> ActiveState

    InvoiceFailedTrial(["Stripe confirms:\nthe card was declined\nat the trial's end"]):::webhook
    InvoiceFailedTrial --> ExpireTrialForce["The trial is closed out"]
    ExpireTrialForce --> TrialLapsed

    SubDeletedTrial(["Stripe confirms:\nthe trial was\ncancelled early"]):::webhook
    SubDeletedTrial --> TrialLapsed

    ExpireTrialTask(["Nightly check for trials that\nshould have ended — by time OR\nby running out of credits\n(applies to both trial types)"]):::task
    ExpireTrialTask --> ExpireTrialNatural["The trial is closed out"]
    ExpireTrialNatural --> TrialLapsed

    TrialLapsed["Any remaining\ntrial credits expire"]:::credit
    TrialLapsed --> LapsedState(["No active subscription\n(trial ended without converting)"]):::state
    LapsedState -.->|"can start a paid plan\nany time"| DirectCheckoutEP

    %% ===================== ACTIVE STATE =====================
    ActiveState(["Subscription is active\n— paid, and renewing\nautomatically"]):::state

    %% ===================== SELECT-PLAN DECISION ENGINE =====================
    SelectPlanEP --> LockGuard{"Is another change already\nbeing processed for\nthis account?"}
    LockGuard -- "yes" --> LockReject["Blocked — please wait\na moment and try again"]:::error
    LockGuard -- "no" --> AutoResume{"Is the account currently\nset to cancel?"}
    AutoResume -- "yes" --> ReactivateFirst["Undo the scheduled\ncancellation first"]
    AutoResume -- "no" --> DetermineBranch
    ReactivateFirst --> DetermineBranch

    DetermineBranch{{"What kind of\nchange is this?"}}:::decision
    DetermineBranch -- "no subscription yet,\nor still on a trial" --> BranchCheckout["Send to checkout"]
    DetermineBranch -- "a payment is\ncurrently failing" --> PastDueReject["Blocked — fix the\npayment method first"]:::error
    DetermineBranch -- "same plan already active,\nnothing else scheduled" --> AlreadySubReject["Blocked — already\non this plan"]:::error
    DetermineBranch -- "same plan requested, but a\nchange is already scheduled" --> BranchCancelPending["Cancel the\nscheduled change"]
    DetermineBranch -- "a custom / contact-sales\nplan on either side" --> UnrankedReject["Blocked — please\ncontact support"]:::error
    DetermineBranch -- "upgrading, and switching\nfrom yearly to monthly billing" --> BranchUpgradeScheduled["Schedule the\nupgrade for later"]
    DetermineBranch -- "upgrading (same billing\ncycle, or monthly to yearly)" --> BranchUpgrade["Apply the\nupgrade now"]
    DetermineBranch -- "downgrading to a\nsmaller plan" --> BranchDowngrade["Schedule the\ndowngrade for later"]
    DetermineBranch -- "same tier, switching\nyearly to monthly billing" --> BranchLateralScheduled["Schedule the\nswitch for later"]

    BranchCheckout --> DirectCheckoutEP

    %% ----- upgrade (immediate) -----
    BranchUpgrade --> UpgradePreview["Calculate the\nprice difference"]
    UpgradePreview --> AmountDueCheck{"Is anything\nowed right now?"}
    AmountDueCheck -- "no, it's free\nor a credit" --> ApplyDirect["Apply the upgrade\nimmediately"]
    AmountDueCheck -- "yes, something\nis owed" --> UpgradeCkSession["Stripe's checkout page opens\nto collect the difference"]
    UpgradeCkSession --> HandleUpgradeCkCompleted["Process the\ncompleted payment"]
    HandleUpgradeCkCompleted --> StaleGuard{"Is this still the same\nsubscription as when\nthe checkout started?"}
    StaleGuard -- "no, it changed\nin the meantime" --> StaleReject["Skipped — flagged\nfor the team to review"]:::error
    StaleGuard -- "yes" --> ApplySwap["Apply the upgrade"]
    ApplyDirect --> IntervalCheck1{"Does this cross between\nmonthly and yearly billing?"}
    ApplySwap --> IntervalCheck1
    IntervalCheck1 -- "yes" --> ActivateSubUpgrade["Start a fresh billing\ncycle on the new plan"]
    IntervalCheck1 -- "no, same\nbilling cycle" --> ImmediateSwap["Swap plans in place\n(billing date unchanged)"]
    ActivateSubUpgrade --> DoubleChargeGuard{"Did Stripe accidentally\ncreate a second charge?"}
    DoubleChargeGuard -- "yes" --> VoidOrRefund["Cancel or refund\nthe accidental charge"]:::error
    DoubleChargeGuard -- "no" --> RolloverUpgrade
    VoidOrRefund --> RolloverUpgrade
    ImmediateSwap --> RolloverUpgrade
    RolloverUpgrade["Unused credits roll over\n(up to a cap), and the new\nplan's credits are added"]:::credit
    RolloverUpgrade --> ActiveState

    UpgradePreview -.->|"card declined, or extra\nverification is needed"| RevertPrice["The attempted\nupgrade is undone"]:::error
    RevertPrice --> ActiveState

    %% ----- downgrade / deferred changes -----
    BranchDowngrade --> ScheduleChange
    BranchUpgradeScheduled --> ScheduleChange
    BranchLateralScheduled --> ScheduleChange
    ScheduleChange["Schedule the change\nwith Stripe for later"]
    ScheduleChange --> ScheduleExistsCheck{"Is a change already\nscheduled?"}
    ScheduleExistsCheck -- "yes" --> ReuseSchedule["Update the\nexisting schedule"]
    ScheduleExistsCheck -- "no" --> CreateSchedule["Create a new schedule:\nkeep the current plan until\nrenewal, then switch"]
    ScheduleExistsCheck -.->|"a conflicting schedule\nis found instead"| AutoRelease["Clear it out\nand try again"]
    AutoRelease --> CreateSchedule
    ReuseSchedule --> PendingChangeState
    CreateSchedule --> PendingChangeState
    PendingChangeState(["A plan change is scheduled\nfor the next billing date"]):::state
    PendingChangeState -.->|"teacher can cancel\nthe scheduled change"| BranchCancelPending

    BranchCancelPending --> ReleaseSchedule{"Can the scheduled change\nbe cancelled with Stripe?"}
    ReleaseSchedule -- "no" --> ReleaseFail["Blocked — nothing changes\nlocally until this succeeds"]:::error
    ReleaseSchedule -- "yes" --> CancelScheduled["The scheduled\nchange is cancelled"]
    CancelScheduled --> ActiveState

    PendingChangeState -->|"when the billing\ndate arrives"| ScheduledChangeApplies["The new price\ntakes effect"]
    ScheduledChangeApplies --> RenewalCore

    %% ===================== CANCEL / RESUME =====================
    CancelEP --> CancelLockGuard{"Is another change already\nbeing processed?"}
    CancelLockGuard -- "yes" --> LockReject
    CancelLockGuard -- "no" --> CancelSchedRelease["Cancel any pending\nscheduled change first"]
    CancelSchedRelease --> StripeCancelAtPeriodEnd["Tell Stripe\nnot to renew"]
    StripeCancelAtPeriodEnd --> CancellingState(["Subscription won't renew,\nbut stays active until the\npaid period ends"]):::state

    ResumeEP --> ResumeLockGuard{"Is another change in\nprogress, or has the\nperiod already ended?"}
    ResumeLockGuard -- "period already ended" --> ResumeExpiredReject["Blocked — it already\nrenewed on its own"]:::error
    ResumeLockGuard -- "ok, still time" --> ResumeStatusGuard{"Has the subscription\nfully ended already?"}
    ResumeStatusGuard -- "yes" --> ResumeMustResubReject["Blocked — needs a\nfresh subscription instead"]:::error
    ResumeStatusGuard -- "no, still recoverable" --> UndoCancelAtPeriodEnd["Tell Stripe\nto keep renewing"]
    UndoCancelAtPeriodEnd --> LocalSaveCheck{"Did saving this\nchange succeed?"}
    LocalSaveCheck -- "no" --> RollbackResume["Undo the change\nwith Stripe too"]:::error
    LocalSaveCheck -- "yes" --> ActiveState

    CancellingState -->|"when the paid\nperiod ends"| SubDeletedCancel(["Stripe confirms:\nsubscription has ended"]):::webhook
    SubDeletedCancel --> CanceledState(["Subscription\nhas ended"]):::state

    %% ===================== RENEWAL CYCLE =====================
    ActiveState -->|"when it's time\nto renew"| InvoiceSucceededRenewal(["Stripe confirms:\nrenewal payment succeeded"]):::webhook
    InvoiceSucceededRenewal --> RenewalCore{{"Process\nthe renewal"}}:::decision
    RenewalCore --> IsTrialCheck{"Is this account\nactually still a trial?"}
    IsTrialCheck -- "yes" --> FinalizeTrialViaStripe
    IsTrialCheck -- "no" --> RenewalGuards{"Is this a genuine new\nbilling period, and is\nthe account still active?"}
    RenewalGuards -- "no" --> RecordOnly["Payment is recorded,\nbut no new credits given\n(already up to date)"]
    RenewalGuards -- "yes" --> ProcessRollover["Process\nthe renewal"]
    ProcessRollover --> RolloverRenewal["Unused credits roll over\n(up to a cap), new credits are\nadded, and any scheduled plan\nchange takes effect"]:::credit
    RolloverRenewal --> ActiveState

    %% ===================== PAYMENT FAILURE / DUNNING =====================
    ActiveState -->|"card charge fails"| InvoiceFailedRenewal(["Stripe confirms:\npayment failed"]):::webhook
    InvoiceFailedRenewal --> RecordFailedTxn["The failed payment\nis recorded"]
    RecordFailedTxn --> SetPastDue["Account is marked\npayment overdue\n(access continues, for now)"]
    SetPastDue --> PastDueState(["Payment is\noverdue"]):::state
    PastDueState -->|"Stripe retries the card\nautomatically over\nthe next several days"| RetryOutcome{"Does a retry succeed\nbefore time runs out?"}
    RetryOutcome -- "yes" --> InvoiceSucceededRenewal
    RetryOutcome -- "no, never recovered" --> SubDeletedDunning(["Stripe confirms:\nsubscription cancelled"]):::webhook
    SubDeletedDunning --> CanceledState
    PastDueState -.->|"blocks any plan change\nuntil this is fixed"| PastDueReject

    %% ===================== DASHBOARD-SIDE EDITS =====================
    ActiveState -.->|"our support team makes a\nchange directly in Stripe"| SubUpdated(["Stripe confirms:\nsomething changed"]):::webhook
    SubUpdated --> StatusMap["Our records are\nupdated to match"]
    StatusMap --> DeactivatingCheck{"Did the subscription\nget cancelled or\nfail entirely?"}
    DeactivatingCheck -- "yes" --> DeactivateLocal["Subscription marked\nas inactive"]:::state
    DeactivatingCheck -- "no" --> SyncStatusOnly["Just the status updates\n(plan and price are\nhandled elsewhere)"]
    SubUpdated --> SyncCancelIntent["Also checked: was renewal\nturned on or off from\nStripe's side directly?"]

    %% ===================== REFUNDS =====================
    ActiveState -.->|"support issues a\nrefund in Stripe"| ChargeRefunded(["Stripe confirms:\na refund happened"]):::webhook
    ChargeRefunded --> MatchInvoice{"Find the matching\npayment in our records"}
    MatchInvoice -- "found it" --> UpdateTxnStatus["That payment is marked\nas refunded"]
    MatchInvoice -- "no match found" --> FlagManualReview["Flagged for the team\nto review by hand"]:::error
    UpdateTxnStatus -.->|"note: credits are NOT\nautomatically taken back"| NoClawback["Someone must manually\nadjust the credits, if needed"]:::error

    %% ===================== OVERAGE PURCHASE =====================
    OverageEP --> OverageCkSession["Stripe's checkout page opens\nto buy extra credits"]
    OverageCkSession --> CheckoutCompleted
    HandleOverageCk["Process the\ncompleted purchase"]
    HandleOverageCk --> OverageCapRecheck{"Is the teacher still\nunder the purchase limit?"}
    OverageCapRecheck -- "limit was exceeded\n(payment already went through)" --> OverageManualFlag["Recorded as paid,\nflagged for manual review"]:::error
    OverageCapRecheck -- "still within the limit" --> GrantOverage["Extra credits are added\n(these never expire)"]:::credit
    GrantOverage --> ActiveState

    %% ===================== PAYMENT METHODS =====================
    PaymentMethodEP --> PMActions{"What is the\nteacher doing?"}
    PMActions -- "adding a card" --> SetupIntent["Stripe collects\nthe card details"]
    SetupIntent --> SetupIntentSucceeded(["Stripe confirms:\ncard saved"]):::webhook
    SetupIntentSucceeded --> PMAttached["Card is now\non file"]
    PMActions -- "opening the billing portal" --> BillingPortal["Stripe's own\npayment page opens"]
    PMActions -- "deleting a card" --> DeleteGuard{"Would this remove the only\ncard on an active,\npaid subscription?"}
    DeleteGuard -- "yes" --> DeleteReject["Blocked — add another\ncard first"]:::error
    DeleteGuard -- "no" --> CardDeleted["Card removed"]
    PMActions -- "setting a default card" --> DefaultSet["Default card\nis updated"]

    %% ===================== SCHEDULED SYSTEM TASKS =====================
    ReconcileTask(["Daily safety check:\ncatches any renewal\nthat was somehow missed"]):::task
    ReconcileTask -.-> RenewalGuards
    CleanupTask(["Daily cleanup:\nexpires any credits\npast their date"]):::task
    CleanupTask --> ExpireBucketSweep["Expired credits\nare written off"]:::credit
    AnnualGrantTask(["Monthly top-up check\nfor yearly plans"]):::task
    AnnualGrantTask --> GrantMonthlyMidCycle["New month's credits are added\n(billing date doesn't change —\nyearly plans still refresh\ncredits every month)"]:::credit
```

**How credits get used up, in order:** rollover credits and trial credits are spent first
(since they expire and can't be topped up again), then the plan's regular monthly credits,
then any special bonus credits, and finally any extra credits bought separately — those never
expire, so they're saved for last.

---

## 2. License (School) Subscription

Every step, choice, and edge case in how a school's multi-teacher license works — same level
of detail as the technical version, described the way you'd explain it out loud.

A license is billed one of two ways: **Stripe** (a real recurring subscription, cards charged
automatically) or **Offline** (the school pays outside the app — invoice, PO, wire transfer —
and our team records it by hand). Most of the branching below exists because those two paths
behave differently.

```mermaid
flowchart TD
    classDef entry fill:#2b6cb0,color:#fff,stroke:#1a4971,stroke-width:1px
    classDef decision fill:#d69e2e,color:#1a1a1a,stroke:#975a16,stroke-width:1px
    classDef success fill:#2f855a,color:#fff,stroke:#1c4532,stroke-width:1px
    classDef error fill:#c53030,color:#fff,stroke:#742a2a,stroke-width:1px
    classDef webhook fill:#6b46c1,color:#fff,stroke:#44337a,stroke-width:1px
    classDef state fill:#2d3748,color:#fff,stroke:#1a202c,stroke-width:1px
    classDef task fill:#4a5568,color:#fff,stroke:#2d3748,stroke-width:1px
    classDef credit fill:#0987a0,color:#fff,stroke:#065666,stroke-width:1px
    classDef offline fill:#805ad5,color:#fff,stroke:#553c9a,stroke-width:1px

    subgraph Legend["Legend"]
        direction LR
        L1["Something someone\ndoes"]:::entry
        L2["A decision\npoint"]:::decision
        L3["Credits\nchange"]:::credit
        L4["Stripe confirms\nsomething happened"]:::webhook
        L5["Blocked or\nrejected"]:::error
        L6["Worked, no\nissue found"]:::success
        L7["Current state\nof the license"]:::state
        L8["Automatic background\ncheck"]:::task
        L9["Handled outside\nStripe / by hand"]:::offline
    end

    %% ===================== ENTRY: CONTRACT CREATION =====================
    CreateEP(["Our team sets up a\nnew school license\n(school, plan, contract length,\nseat count, teacher emails)"]):::entry
    CreateEP --> BillingMethodChoice{"Billed through Stripe,\nor offline?"}
    BillingMethodChoice -- "Stripe" --> LicenseCkSession["Stripe's checkout page opens\n(one charge covering\nall the seats)"]
    BillingMethodChoice -- "Offline" --> CreateLicenseSync["Set up the license\nright away"]

    LicenseCkSession --> LicenseCheckoutCompleted(["Stripe confirms:\ncheckout complete"]):::webhook
    LicenseCheckoutCompleted --> HandleLicenseCreate["Process the\ncompleted checkout"]
    HandleLicenseCreate --> CreateLicenseSync

    CreateLicenseSync --> ValidatePlan{"Is this a real,\nproperly-configured\nschool plan?"}
    ValidatePlan -- "no" --> PlanValidationReject["Blocked"]:::error
    ValidatePlan -- "yes" --> ValidateAdmin{"Is the person setting\nthis up allowed to,\nfor this school?"}
    ValidateAdmin -- "no" --> AdminValidationReject["Blocked"]:::error
    ValidateAdmin -- "yes" --> SeatsPositive{"Is at least\none seat requested?"}
    SeatsPositive -- "no" --> SeatsZeroReject["Blocked"]:::error
    SeatsPositive -- "yes" --> ExistingLicenseCheck{"Does this school already\nhave an active license?"}

    ExistingLicenseCheck -- "yes" --> CarryForwardCheck{"Should this school's current\nteachers carry over\nto the new license?"}
    CarryForwardCheck -- "yes" --> SnapshotCarryForward["Note down who's\ncurrently enrolled"]
    CarryForwardCheck -- "no" --> SeatCombinedCheck
    SnapshotCarryForward --> SeatCombinedCheck{"Do the carried-over teachers\nplus the new ones add up\nto more seats than purchased?"}
    SeatCombinedCheck -- "yes, too many" --> SeatCombinedReject["Blocked — the whole setup\nis cancelled, old license\nstays untouched"]:::error
    SeatCombinedCheck -- "no, fits" --> RejectOldOverageReqs["Any pending extra-credit\nrequests on the old license\nare automatically closed out"]
    RejectOldOverageReqs --> DeactivateOldLicense["The old license\nis retired"]
    DeactivateOldLicense --> CreateRow
    ExistingLicenseCheck -- "no" --> CreateRow

    CreateRow["The new license is created\n(contract runs for the\nagreed length of time)"]
    CreateRow --> GrantAdminAlloc["The school admin gets a small\ncredit allowance for their\nown analytics/reporting use"]:::credit
    GrantAdminAlloc --> CarryForwardEnroll["Carried-over teachers\nare re-enrolled automatically\n(no new invite needed)"]
    CarryForwardEnroll --> InviteNewTeachers["New teachers are invited\nand enrolled one by one\n(one failure doesn't stop the rest)"]
    InviteNewTeachers --> LicenseActiveState

    %% ===================== LICENSE ACTIVE STATE =====================
    LicenseActiveState(["License is active\n(billed through Stripe,\nor tracked offline)"]):::state

    %% ===================== TEACHER ENROLLMENT =====================
    EnrollEP(["School admin adds\none or more teachers"]):::entry
    EnrollEP --> SchoolMatchCheck{"Does this teacher\nbelong to this school?"}
    SchoolMatchCheck -- "no" --> SchoolMismatchReject["Blocked"]:::error
    SchoolMatchCheck -- "yes" --> IndivConflictCheck{"Does this teacher already\nhave their own personal\nsubscription?"}
    IndivConflictCheck -- "yes" --> IndivConflictReject["Blocked — they need to cancel\ntheir personal subscription\nfirst"]:::error
    IndivConflictCheck -- "no" --> AlreadyEnrolledCheck{"Already enrolled\nin this license?"}
    AlreadyEnrolledCheck -- "yes" --> EnrollNoop["Nothing to do —\nalready enrolled"]:::success
    AlreadyEnrolledCheck -- "no" --> ReactivationCheck{"Is this someone who was\nremoved before, coming back?"}
    ReactivationCheck -- "yes\n(seat check skipped)" --> CreateAllocation
    ReactivationCheck -- "no, brand new" --> SeatsRemainingCheck{"Are there any\nseats left?"}
    SeatsRemainingCheck -- "no" --> SeatCapReject["Blocked — no\nseats available"]:::error
    SeatsRemainingCheck -- "yes" --> CreateAllocation["A seat is\nset up for them"]
    CreateAllocation --> BudgetCheck{"Does this license have\nan unlimited seat count?"}
    BudgetCheck -- "yes" --> GrantFullNoCap["Full monthly credits\nare given, no limit"]:::credit
    BudgetCheck -- "no" --> RemainingBudget{"Has the school already used up\nits shared monthly credit pool?"}
    RemainingBudget -- "yes, nothing left" --> GrantZero["Teacher is enrolled, but starts\nwith 0 credits this month\n(flagged internally)"]:::credit
    RemainingBudget -- "no, some left" --> GrantCapped["Given whatever's left in the\nshared pool, up to their\nnormal monthly amount"]:::credit
    GrantFullNoCap --> RolloverEnroll
    GrantZero --> RolloverEnroll
    GrantCapped --> RolloverEnroll
    RolloverEnroll["Any credits they already had\n(e.g. from a personal plan)\nare carried over"]:::credit
    RolloverEnroll --> ResetOverageEnroll["Their extra-credit\npurchase count resets"]
    ResetOverageEnroll --> LicenseActiveState

    %% ===================== TEACHER REMOVAL =====================
    RemoveEP(["School admin\nremoves a teacher"]):::entry
    RemoveEP --> RemoveTeacher["The teacher's\nseat is freed up"]
    RemoveTeacher --> ExpireAllBuckets["All of their remaining\ncredits expire immediately\n(their history is kept for records)"]:::credit
    ExpireAllBuckets --> LicenseActiveState

    %% ===================== SEAT COUNT CHANGE =====================
    SeatsEP(["School admin changes\nthe number of seats"]):::entry
    SeatsEP --> SeatsValidGuard{"Is the new number invalid —\nzero or less, fewer than\nteachers currently enrolled,\nor the same as now?"}
    SeatsValidGuard -- "yes, invalid" --> SeatsChangeReject["Blocked"]:::error
    SeatsValidGuard -- "no, valid" --> SeatsIncreaseCheck{"Is this adding\nseats, or removing?"}
    SeatsIncreaseCheck -- "adding seats" --> SeatsProrationInvoice["Charged right away for\nthe added seats"]
    SeatsIncreaseCheck -- "removing seats" --> SeatsProrationNone["No refund — the smaller\ncount takes effect at\nthe next renewal"]
    SeatsProrationInvoice --> SeatsBillingCheck{"Billed through\nStripe, or offline?"}
    SeatsProrationNone --> SeatsBillingCheck
    SeatsBillingCheck -- "Stripe" --> StripeModifyQty["Stripe's seat count\nis updated"]
    SeatsBillingCheck -- "Offline" --> OfflineSeatsRecord["Recorded by hand\nfor the school's records"]:::offline
    StripeModifyQty --> SeatsInvoicePaidCheck{"(when adding seats)\nDid the charge\nactually succeed?"}
    SeatsInvoicePaidCheck -- "no" --> RevertSeatsQty["Seat count is reverted —\nnothing changes\nuntil payment works"]:::error
    SeatsInvoicePaidCheck -- "yes, or removing seats" --> RecordSeatsTxn["The charge (if any)\nis recorded"]
    RecordSeatsTxn --> UpdateLocalSeats["Seat count\nis updated"]
    OfflineSeatsRecord --> UpdateLocalSeats
    UpdateLocalSeats --> LicenseActiveState

    %% ===================== PLAN CHANGE =====================
    PlanChangeEP(["School admin\nchanges the plan"]):::entry
    PlanChangeEP --> PlanSamePriceCheck{"Is this actually\na different plan\nor price?"}
    PlanSamePriceCheck -- "no, nothing changed" --> PlanChangeNoopReject["Blocked — nothing\nto change"]:::error
    PlanSamePriceCheck -- "yes" --> UpdatePlanFields["The license's\nplan is updated"]
    UpdatePlanFields --> UpdateAllocFuture["Every teacher's monthly amount\nupdates for FUTURE grants\n(credits they already have\nthis cycle are untouched)"]:::credit
    UpdateAllocFuture --> PlanBillingCheck{"Billed through\nStripe, or offline?"}
    PlanBillingCheck -- "Stripe" --> PlanProrationCheck{"Is the new plan\nmore expensive?"}
    PlanProrationCheck -- "yes, upgrade" --> PlanProrationInvoice["Charged the difference\nright away"]
    PlanProrationCheck -- "no, downgrade\nor sideways" --> PlanProrationNone["No charge — takes\neffect at next renewal"]
    PlanProrationInvoice --> ChangeLicensePrice["Stripe's price\nis updated"]
    PlanProrationNone --> ChangeLicensePrice
    PlanBillingCheck -- "Offline" --> OfflinePlanRecord["Recorded by hand\nfor the school's records"]:::offline
    ChangeLicensePrice --> MailerliteResync["Every teacher's mailing-list\ninfo is refreshed to match"]
    OfflinePlanRecord --> MailerliteResync
    MailerliteResync --> LicenseActiveState

    %% ===================== RENEWAL =====================
    LicenseActiveState -->|"as the contract's\nrenewal date approaches"| RenewalDriverCheck{"Billed through\nStripe, or offline?"}
    RenewalDriverCheck -- "Stripe" --> RenewalWebhookOrTask{"Which notices it first —\nStripe's own confirmation,\nor our nightly check?"}
    RenewalWebhookOrTask -- "Stripe confirms\npayment first" --> LicenseInvoiceSucceeded(["Stripe confirms:\nrenewal payment succeeded"]):::webhook
    RenewalWebhookOrTask -- "nightly check catches it\n(backup, in case Stripe's\nconfirmation was missed)" --> RenewalTask(["Nightly check for\nlicenses due to renew"]):::task

    LicenseInvoiceSucceeded --> BillingReasonCheck{"Is this genuinely\na new billing period?"}
    BillingReasonCheck -- "no" --> RecordTxnOnlyLicense["Payment is recorded,\nnothing else changes"]
    BillingReasonCheck -- "yes" --> IsActiveCheckWebhook{"Is the license\nstill active?"}
    IsActiveCheckWebhook -- "no — it was\nalready cancelled" --> IgnoreRenewalWebhook["Ignored — a cancelled\nlicense must not come\nback to life on its own"]:::error
    IsActiveCheckWebhook -- "yes" --> ProcessLicenseRenewalCore

    RenewalTask --> TaskAutoRenewCheck{"Was this license\nset to NOT renew?"}
    TaskAutoRenewCheck -- "yes" --> TaskCancelStripe["Stripe is told to stop billing,\nand the license is deactivated"]:::state
    TaskAutoRenewCheck -- "no, should renew" --> TaskSubIdCheck{"Is there a real Stripe\nsubscription behind this?"}
    TaskSubIdCheck -- "no" --> TaskSkipWarn["Skipped, flagged\nfor review"]:::error
    TaskSubIdCheck -- "yes" --> TaskInvoicePaidCheck{"Did the\nrenewal charge succeed?"}
    TaskInvoicePaidCheck -- "no" --> TaskSetPastDue["Marked payment overdue,\nskipped for now"]:::state
    TaskInvoicePaidCheck -- "yes" --> NewPeriodInvoiceCheck{"Is this actually a NEW\nbilling period, not\nan old one already handled?"}
    NewPeriodInvoiceCheck -- "no, nothing new" --> TaskSkipStale["Skipped — nothing\nto renew yet"]:::error
    NewPeriodInvoiceCheck -- "yes" --> TaskLockRecheck["Double-check it\nreally hasn't already\nbeen renewed"]
    TaskLockRecheck -- "already renewed\n(Stripe's own confirmation\ngot there first)" --> TaskNoopRenewed["Nothing to do —\nalready handled"]:::success
    TaskLockRecheck -- "still due" --> ProcessLicenseRenewalCore

    RenewalDriverCheck -- "Offline" --> OfflineRenewEP(["Our team manually\nrenews the contract"]):::entry
    OfflineRenewEP --> ProcessOfflineRenewal["Process the\nmanual renewal"]:::offline
    ProcessOfflineRenewal --> OfflineGuards{"Is this really an offline\nlicense, still active, with a\nvalid new end date?"}
    OfflineGuards -- "no" --> OfflineRenewReject["Blocked"]:::error
    OfflineGuards -- "yes" --> PerTeacherRolloverOffline["Each teacher's unused\ncredits roll over, and a\nfresh month's credits\nare added"]:::credit
    PerTeacherRolloverOffline --> ResetConsumedOffline["The school's shared\nmonthly usage counter\nresets to zero"]
    ResetConsumedOffline --> OfflineRenewRecord["Recorded by hand\nfor the school's records"]:::offline
    OfflineRenewRecord --> LicenseActiveState

    ProcessLicenseRenewalCore{{"Process\nthe renewal\n(shared by both paths above)"}}:::decision
    ProcessLicenseRenewalCore --> RenewalIdempotencyGuard{"Has this contract period\nalready been renewed?"}
    RenewalIdempotencyGuard -- "yes, already done" --> RenewalNoop["Nothing to do"]:::success
    RenewalIdempotencyGuard -- "no, genuinely due" --> RenewalActiveGuard{"Is the license\nstill active?"}
    RenewalActiveGuard -- "no" --> RenewalInactiveWarn["Skipped, flagged\nfor review"]:::error
    RenewalActiveGuard -- "yes" --> RenewalAutoRenewGuard{"Was this license\nset to renew?"}
    RenewalAutoRenewGuard -- "no" --> RenewalDeactivate["The license\nis deactivated"]:::state
    RenewalAutoRenewGuard -- "yes" --> PerTeacherRollover["Every enrolled teacher's unused\ncredits roll over, and a fresh\nmonth's credits are added\n(one teacher's issue doesn't\nstop the others)"]:::credit
    PerTeacherRollover --> AnyTeacherSucceeded{"Did renewal succeed for\nat least one teacher\n(or are there none enrolled)?"}
    AnyTeacherSucceeded -- "yes" --> AdvanceCycle["The contract's billing dates\nmove forward, and the shared\nmonthly usage counter resets"]
    AnyTeacherSucceeded -- "no — it failed for\nEVERY teacher" --> TotalFailure["The whole license is\ndeactivated rather than left\nhalf-renewed, and flagged\nfor review"]:::error
    AdvanceCycle --> LicenseActiveState

    %% ===================== MONTHLY CREDIT REFRESH (in-contract) =====================
    MonthlyRefreshTask(["Monthly check: top up credits\nfor licenses mid-contract\n(applies the same way whether\nbilled through Stripe or offline)"]):::task
    MonthlyRefreshTask --> RefreshFilter["Only looks at licenses\nthat are active and\nstill within their contract"]
    RefreshFilter --> RefreshWindowCheck{"Has the shared monthly\nusage counter already been\nreset this month?"}
    RefreshWindowCheck -- "yes, already reset\n(by an earlier teacher's check)" --> RefreshGrantOnly["Just gives this teacher\ntheir fresh month's credits —\ndoesn't reset the counter again"]:::credit
    RefreshWindowCheck -- "no, first check\nthis month" --> RefreshResetAndGrant["Resets the shared usage counter\nAND gives this teacher their\nfresh month's credits"]:::credit
    RefreshGrantOnly --> LicenseActiveState
    RefreshResetAndGrant --> LicenseActiveState

    %% ===================== PAYMENT FAILURE =====================
    LicenseActiveState -.->|"a Stripe charge fails"| LicenseInvoiceFailed(["Stripe confirms:\npayment failed"]):::webhook
    LicenseInvoiceFailed --> LicenseRecordFailed["The failed payment\nis recorded"]
    LicenseRecordFailed --> LicenseSetPastDue["Marked payment overdue\n(access continues, for now)"]:::state
    LicenseSetPastDue -->|"Stripe's automatic retries\nrun out, or someone cancels\nit directly in Stripe"| LicenseSubDeleted

    %% ===================== CANCELLATION =====================
    CancelLicenseEP(["Our team cancels\nthe license"]):::entry
    CancelLicenseEP --> LicenseCancelGuard{"Is it already inactive,\nor already set to\nnot renew?"}
    LicenseCancelGuard -- "yes, already handled" --> LicenseCancelReject["Blocked — nothing\nnew to do"]:::error
    LicenseCancelGuard -- "no" --> LicenseCancelBillingCheck{"Billed through\nStripe, or offline?"}
    LicenseCancelBillingCheck -- "Stripe" --> StripeToldToStop["Stripe is told to\nstop renewing"]
    StripeToldToStop --> StaysActiveUntilEnd["The license stays active —\nteachers keep access —\nuntil the period already\npaid for ends"]:::state
    LicenseCancelBillingCheck -- "Offline" --> LicenseDeactivateNow["The license is\ndeactivated right away\n(nothing left to wait out)"]:::offline
    StaysActiveUntilEnd -->|"when the paid\nperiod ends"| LicenseSubDeleted
    LicenseDeactivateNow --> LicenseCanceledState

    LicenseSubDeleted(["Stripe confirms:\nsubscription has ended"]):::webhook
    LicenseSubDeleted --> LicenseCanceledState["The license\nhas ended"]:::state
    LicenseActiveState -.->|"our support team makes a\nchange directly in Stripe"| LicenseSubUpdated(["Stripe confirms:\nsomething changed"]):::webhook
    LicenseSubUpdated --> LicenseStatusSync["Our records are\nupdated to match"]
    LicenseStatusSync --> LicenseDeactivatingCheck{"Did it get cancelled\nor fail entirely?"}
    LicenseDeactivatingCheck -- "yes" --> LicenseCanceledState
    LicenseDeactivatingCheck -- "no" --> LicenseActiveState

    %% ===================== OFFLINE <-> STRIPE CONVERSION =====================
    ConvertToStripeEP(["Our team switches an\noffline license\nover to Stripe billing"]):::entry
    ConvertToStripeEP --> ConvertCkSession["Stripe's checkout\npage opens"]
    ConvertCkSession --> ConvertCkCompleted(["Stripe confirms:\ncheckout complete"]):::webhook
    ConvertCkCompleted --> HandleConvertToStripe["Process the\ncompleted checkout"]
    HandleConvertToStripe --> ConvertGuard{"Is this license\nstill actually offline?"}
    ConvertGuard -- "no — already\nconverted" --> ConvertNoop["Nothing to do"]:::error
    ConvertGuard -- "yes" --> FlipToStripe["Switched over to\nStripe billing"]:::success
    FlipToStripe --> LicenseActiveState

    ConvertToOfflineEP(["Our team switches a\nStripe-billed license\nover to offline"]):::entry
    ConvertToOfflineEP --> ConvertOfflineGuard{"Is this license\nalready offline?"}
    ConvertOfflineGuard -- "yes" --> ConvertOfflineReject["Blocked"]:::error
    ConvertOfflineGuard -- "no" --> DeleteStripeSubImmediate["The Stripe subscription is\nended immediately\n(no refund for unused time)"]
    DeleteStripeSubImmediate --> FlipToOffline["Switched over to\noffline billing"]:::offline
    FlipToOffline --> LicenseActiveState

    %% ===================== OVERAGE (per teacher, within license) =====================
    LicOverageEP(["A teacher or admin wants\nmore credits than the\nplan includes"]):::entry
    LicOverageEP --> OverageEligibility{"Is this person actually\nenrolled in the license?"}
    OverageEligibility -- "no" --> OverageEligibilityReject["Blocked"]:::error
    OverageEligibility -- "yes" --> OverageMethodChoice{"How are they\ngetting the extra credits?"}
    OverageMethodChoice -- "buying it themselves\nthrough Stripe" --> LicOverageIntent["Purchase is\nstarted"]
    LicOverageIntent --> LicOverageCkSession["Stripe's checkout\npage opens"]
    LicOverageCkSession --> LicOverageCkCompleted(["Stripe confirms:\ncheckout complete"]):::webhook
    LicOverageCkCompleted --> HandleLicOverageCk["Process the\ncompleted purchase"]
    HandleLicOverageCk --> GrantOverageLic["Extra credits\nare added"]:::credit
    GrantOverageLic --> IntentComplete["Purchase marked\ncomplete"]
    OverageMethodChoice -- "requesting it be paid\nfor offline" --> OfflineOverageRequest["Request is submitted\nfor review"]
    OfflineOverageRequest --> SuperadminDecision{"Our team's\ndecision"}
    SuperadminDecision -- "approve" --> ApproveOffline["Extra credits are added,\nrecorded by hand"]:::credit
    SuperadminDecision -- "reject" --> RejectOffline["Request denied"]:::error
    OverageMethodChoice -- "a free goodwill grant\nfrom our team" --> ManualGrant["Extra credits are added\nimmediately, no charge —\nworks the same for either\nbilling method"]:::credit
    ApproveOffline --> LicenseActiveState
    ManualGrant --> LicenseActiveState
    IntentComplete --> LicenseActiveState
```

**How a school's shared credit pool works:** every seat gets its own monthly amount, but the
whole school also shares one overall monthly budget (seats × the plan's monthly amount).
Enrolling a new teacher partway through the month draws from whatever's left in that shared
budget, not a fresh allocation — so a school that's already used up its pool sees new
teachers start at zero credits until the next renewal or monthly top-up. Extra credits bought
separately, for any one teacher, never expire and don't count against that shared pool.
