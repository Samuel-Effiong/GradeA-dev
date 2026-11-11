# Credit requests: what teachers and school admins will see

Nothing changes until we switch it on. When it is on:

## 1. Teachers

- A teacher on a school license who is running low can **ask their school admin for more credits**, with a short note (up to 500 characters).
- They can have **one open request at a time**. They can withdraw it while it is still waiting.
- They get an **email** when the admin answers: credits added, or declined (with the admin's reason).
- If their grading was waiting in the queue for credits, it **starts by itself** when the credits arrive.

## 2. School admins

- They get an **email** for each new request, and see a list of requests with a count of the ones waiting.
- **Approve** opens the normal payment page (card), or sends the normal offline request to us. The credits go to that teacher when the payment goes through, the same as buying extra credits today.
- **Decline** asks for a reason, which the teacher sees.
- If the admin leaves the payment page without paying, the request goes back to "waiting", so it is not lost.
- If a teacher leaves the license, or the license ends, their open request is **closed** automatically.
- If two payments for the same request ever both go through, we keep one and **refund the other automatically**, and email the admin.

## 3. Questions for you

- **Q1.** When a request is closed because the teacher left or the license ended, should the teacher get an email (W4)? *Suggested: yes.*
- **Q2.** Should we email the teacher when the admin approves but has not paid yet? *Suggested: no, only when the credits arrive.*
- **Q3.** Should super admins (us) see and answer every school's requests? *Suggested: yes, as the plan says.*
- **Q4.** Archiving a school, or locking a teacher's account, does **not** end their license today. Should those also close open requests? *Suggested: no, leave as is.*
- **Q5.** Should a request remind the admin if no one answers it? *Suggested: not now.*

## 4. New wording to approve (drafts)

| # | Where | Text |
|---|---|---|
| W1 | Email to admin | Subject: "{Teacher} asked for more credits". Body: "{Teacher} asked for {n} more credit blocks for {School}. Their note: "{note}". Open your credit requests to approve or decline." |
| W2 | Email to teacher | Subject: "Your extra credits are ready". Body: "{Admin} approved your request. {n} credit blocks have been added to your account." |
| W3 | Email to teacher | Subject: "Your credit request was declined". Body: "{Admin} declined your request for {n} credit blocks. Their reason: "{reason}"." |
| W4 | Email to teacher | Subject: "Your credit request was closed". Body: "Your request for {n} credit blocks was closed because you are no longer on {School}'s license." |
| W5 | Email to admin | Subject: "We refunded a duplicate payment". Body: "Two payments went through for the same credit request. We kept one and refunded the other ({amount}) to your card." |
| W6 | Error | "You already have a credit request waiting. Withdraw it first if you want to change it." |
| W7 | Error | "You need to be on a school license to ask for credits." |
| W8 | Error | "This request has already been answered." |
| W9 | Error | "You can only withdraw a request while it is waiting." |
| W10 | Status names | Waiting, Approved, Credits added, Declined, Withdrawn, Closed |
| W11 | Note on a request | "The payment page closed before payment. The request is waiting again." |
| W12 | Note on a request | "Our team could not confirm the offline payment: {reason}. The request is waiting again." |
| W13 | Note on a request | "Closed: the teacher left the license." / "Closed: the license ended." |
