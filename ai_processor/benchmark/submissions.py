"""
The 7 benchmark students' answers, with their known-correct grades.

Split out of dataset.py purely for navigability — this is the bulk of the
data. Imports from dataset.py; dataset.py must NOT import this, so there
is no cycle.

Every AnswerSpec.expected_points MUST be one of that question's rubric
level point values (or full/0 for OBJECTIVE), because
_finalize_grading_result snaps scores to the nearest rubric level and an
off-level expectation is unreachable by construction.
tests_grading_benchmark enforces this.

`exact=True` means there is exactly one defensible score — objective
questions and computational work with a single right answer. Everything
else is graded within +/- one rubric level.
"""

from ai_processor.benchmark.dataset import AnswerSpec

# ── Shared answers for the cross-student consistency probe ────────────────
# 'strong' and 'twin' submit these byte-for-byte. Two identical answers
# must receive identical scores; today they are two independent model
# calls with no shared state and nothing would notice if they diverged.

SHARED_MVT_ANSWER = (
    "<p>The Mean Value Theorem says that if a function $f$ is continuous on "
    "the closed interval $[a,b]$ and differentiable on the open interval "
    "$(a,b)$, then there is some point $c$ between $a$ and $b$ where "
    "$f'(c) = \\frac{f(b)-f(a)}{b-a}$. In words, the instantaneous rate of "
    "change equals the average rate of change somewhere.</p>"
    "<p>Continuity matters because without it the function can jump. Take "
    "the floor function on $[0,1]$: it jumps at $1$, and its derivative is "
    "$0$ everywhere it exists, so it never matches the average slope.</p>"
    "<p>Differentiability matters because of corners. The function "
    "$f(x)=|x|$ on $[-1,1]$ is continuous and its average slope is $0$, but "
    "the derivative is $-1$ or $+1$ and never $0$. The corner at the origin "
    "is exactly where a tangent would have to be.</p>"
)

SHARED_SN1_SN2_ANSWER = (
    "<p>$S_N1$ happens in two steps. The leaving group departs first to make "
    "a carbocation, and because that first step is the slow one the rate is "
    "$k[\\text{substrate}]$ — the nucleophile does not appear in the rate "
    "equation. The carbocation is flat, so the nucleophile can come in from "
    "either side and you get a mixture of both configurations.</p>"
    "<p>$S_N2$ is one concerted step, so the rate is "
    "$k[\\text{substrate}][\\text{Nu}]$. The nucleophile attacks from behind "
    "the leaving group and the molecule inverts, like an umbrella in the "
    "wind.</p>"
    "<p>Tertiary halides go by $S_N1$ because the extra alkyl groups make "
    "the carbocation more stable. Primary halides go by $S_N2$ because there "
    "is room for the nucleophile to get in at the back.</p>"
)


# ── Assignment 1: Mathematics ─────────────────────────────────────────────

MATHS_SUBMISSIONS = {
    "excellent": [
        AnswerSpec(
            1,
            "<p>A</p>",
            3,
            exact=True,
            note="Correct, given as a bare letter — exercises letter matching.",
        ),
        AnswerSpec(
            2,
            r"<p>$\frac{5}{3}$</p>",
            3,
            exact=True,
            note="Correct, given as option TEXT with no letter prefix.",
        ),
        AnswerSpec(
            3,
            r"<p>Factorise: $2x^2 - 7x + 3 = (2x-1)(x-3)$. Setting each factor "
            r"to zero, $2x - 1 = 0$ gives $x = \frac{1}{2}$, and $x - 3 = 0$ "
            r"gives $x = 3$. Check: $2(9) - 21 + 3 = 0$. Both roots verified.</p>",
            10,
            exact=True,
            note="Both roots correct with complete method and a check.",
        ),
        AnswerSpec(
            4,
            r"<p>Let $u = x^2$. Then $du = 2x\,dx$, so $x\,dx = \frac{1}{2}du$. "
            r"Limits: $x=0 \Rightarrow u=0$, $x=2 \Rightarrow u=4$. "
            r"$\int_0^2 xe^{x^2}dx = \frac{1}{2}\int_0^4 e^u du = "
            r"\frac{1}{2}\left[e^u\right]_0^4 = \frac{1}{2}(e^4 - 1)$.</p>",
            12,
            exact=True,
            note="Substitution stated, limits changed, exact answer.",
        ),
        AnswerSpec(
            5,
            r"<p>$V = \frac{4}{3}\pi r^3$, so $\frac{dV}{dt} = 4\pi r^2 "
            r"\frac{dr}{dt}$ by the chain rule. Substituting $\frac{dV}{dt} = "
            r"100$ and $r = 5$: $100 = 4\pi(25)\frac{dr}{dt} = "
            r"100\pi \frac{dr}{dt}$. Therefore $\frac{dr}{dt} = \frac{1}{\pi} "
            r"\approx 0.318$ cm/s.</p>",
            12,
            exact=True,
            note="Correct throughout, units given.",
        ),
        AnswerSpec(
            6,
            "<p>The Mean Value Theorem states that if $f$ is continuous on "
            "$[a,b]$ and differentiable on $(a,b)$, then there exists at least "
            "one $c \\in (a,b)$ such that $f'(c) = \\frac{f(b)-f(a)}{b-a}$. "
            "Geometrically, some tangent is parallel to the chord joining the "
            "endpoints.</p>"
            "<p>Continuity on the closed interval is necessary. Consider "
            "$f(x) = \\lfloor x \\rfloor$ on $[0,1]$. The average rate of change "
            "is $\\frac{1-0}{1-0} = 1$, but wherever $f'$ exists it is $0$, so "
            "no such $c$ exists. The discontinuity at the endpoint breaks the "
            "link between the chord and the tangents.</p>"
            "<p>Differentiability on the open interval is separately necessary. "
            "Consider $f(x) = |x|$ on $[-1,1]$. This is continuous everywhere, "
            "and the average rate of change is $\\frac{1-1}{2} = 0$. But "
            "$f'(x) = -1$ for $x<0$ and $+1$ for $x>0$, and $f'(0)$ does not "
            "exist, so no point has derivative $0$. Note the hypothesis is only "
            "on the <em>open</em> interval, which is why functions with vertical "
            "tangents at the endpoints, such as $\\sqrt{1-x^2}$ on $[-1,1]$, "
            "still satisfy the theorem.</p>",
            20,
            note="Precise statement, both hypotheses, two valid counterexamples, "
            "and the open/closed distinction handled explicitly.",
        ),
        AnswerSpec(
            7,
            "<p>B) $10$</p>",
            3,
            exact=True,
            note="Correct, given as letter plus text.",
        ),
        AnswerSpec(
            8,
            r"<p>Use the ratio test. $\frac{a_{n+1}}{a_n} = "
            r"\frac{(n+1)!}{(n+1)^{n+1}} \cdot \frac{n^n}{n!} = "
            r"\frac{(n+1) n^n}{(n+1)^{n+1}} = \frac{n^n}{(n+1)^n} = "
            r"\left(\frac{n}{n+1}\right)^n = "
            r"\left(1 + \frac{1}{n}\right)^{-n} \to e^{-1}$. "
            r"Since $\frac{1}{e} \approx 0.368 < 1$, the series converges.</p>",
            12,
            exact=True,
            note="Ratio test applied with the limit genuinely derived.",
        ),
        AnswerSpec(
            9,
            r"<p>Differentiate both sides with respect to $x$, remembering the "
            r"product rule on the right: $3x^2 + 3y^2\frac{dy}{dx} = 6y + "
            r"6x\frac{dy}{dx}$.</p><p>Collect the $\frac{dy}{dx}$ terms: "
            r"$3y^2\frac{dy}{dx} - 6x\frac{dy}{dx} = 6y - 3x^2$, so "
            r"$\frac{dy}{dx} = \frac{6y-3x^2}{3y^2-6x} = "
            r"\frac{2y-x^2}{y^2-2x}$.</p><p>At $(3,3)$: "
            r"$\frac{6-9}{9-6} = -1$.</p>",
            12,
            exact=True,
            note="Product rule handled, general expression derived, evaluated "
            "correctly to -1.",
        ),
    ],
    "strong": [
        AnswerSpec(1, "<p>A</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, r"<p>A) $\frac{5}{3}$</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            3,
            r"<p>$x = \frac{1}{2}$ and $x = 3$. I used the quadratic formula "
            r"with $a=2$, $b=-7$, $c=3$.</p>",
            7,
            note="Both roots correct but the working is asserted, not shown — "
            "'good' rather than 'excellent'.",
        ),
        AnswerSpec(
            4,
            r"<p>Substituting $u = x^2$ gives $du = 2x\,dx$. So the integral is "
            r"$\frac{1}{2}\int e^u du = \frac{1}{2}e^{x^2}$ evaluated from $0$ "
            r"to $2$, which is $\frac{1}{2}(e^4 - 1)$.</p>",
            8,
            note="CORRECTED after the first live run. This was originally "
            "authored as 12 on the reasoning that back-substituting instead of "
            "changing limits is mathematically valid — which it is. But this "
            "rubric's 'excellent' descriptor explicitly requires 'limits "
            "correctly changed to $0 \\to 4$', and 'good' is precisely "
            "'correct substitution and answer, but limits handled loosely'. "
            "The grader awarded 8 and was RIGHT; the ground truth was wrong. "
            "Kept as a deliberate rubric-adherence probe: the grader must "
            "follow the teacher's rubric even when a mathematically defensible "
            "alternative would earn more.",
        ),
        AnswerSpec(
            5,
            r"<p>$V = \frac{4}{3}\pi r^3$ so $\frac{dV}{dt} = 4\pi r^2 "
            r"\frac{dr}{dt}$. With $r=5$, $100 = 100\pi \frac{dr}{dt}$, so "
            r"$\frac{dr}{dt} = \frac{1}{\pi}$.</p>",
            8,
            note="Correct method and value but no units — the 'good' descriptor.",
        ),
        AnswerSpec(
            6,
            SHARED_MVT_ANSWER,
            15,
            note="CONSISTENCY PROBE (shared with 'twin'). Correct statement and "
            "both counterexamples, but the open/closed distinction is blurred "
            "and the floor-function example is loosely argued — 'good'.",
        ),
        AnswerSpec(7, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            8,
            r"<p>By the ratio test the limit is $\frac{1}{e}$, which is less "
            r"than 1, so the series converges.</p>",
            8,
            note="Correct test and conclusion but the limit is asserted with no "
            "derivation — exactly the 'good' descriptor.",
        ),
        AnswerSpec(
            9,
            r"<p>$3x^2 + 3y^2 y' = 6y + 6xy'$. Rearranging and putting in "
            r"$x=3$, $y=3$: $27 + 27y' = 18 + 18y'$, so $9y' = -9$ and "
            r"$y' = -1$.</p>",
            8,
            note="Correct differentiation and correct value, but substituted "
            "immediately instead of deriving the general expression first — "
            "the 'good' descriptor.",
        ),
    ],
    "middling": [
        AnswerSpec(
            1,
            r"<p>$x^2 + 3x^2\ln x$</p>",
            3,
            exact=True,
            note="CORRECT + DEFER PROBE. This is option A "
            "($3x^2 \\ln(x) + x^2$) written mathematically identically but "
            "textually differently: terms reordered and \\ln x without "
            "parentheses. Tier 0 matches exact strings only, so it must "
            "return AMBIGUOUS and hand this to the LLM rather than guess "
            "— that defer is the safety invariant working, and it is the "
            "only live coverage of the defer path in the dataset. The LLM "
            "must then award full marks: the grading prompt's OBJECTIVE "
            "section requires comparing mathematical equivalence, not "
            "strings. A 0 here means the grader is string-matching. "
            "(Replaced the earlier whitespace-based defer probe, which "
            "collapse_math_whitespace now correctly claims.)",
        ),
        AnswerSpec(
            2,
            "<p>B) $1$</p>",
            0,
            exact=True,
            note="Wrong: forgot the coefficients, answered the standard "
            "$\\sin x / x$ limit.",
        ),
        AnswerSpec(
            3,
            r"<p>Using the quadratic formula: $x = \frac{7 \pm \sqrt{49 - 24}}"
            r"{4} = \frac{7 \pm \sqrt{25}}{4}$. So $x = \frac{7+5}{4} = 3$ and "
            r"$x = \frac{7-5}{4} = \frac{2}{4}$. So $x = 3$ or $x = 2$.</p>",
            4,
            note="Valid method, correct discriminant, but botched the final "
            "simplification of $2/4$ to give a wrong second root — 'fair'.",
        ),
        AnswerSpec(
            4,
            r"<p>Let $u = x^2$, $du = 2x\,dx$. Then $\int_0^2 e^u du = "
            r"e^4 - 1$.</p>",
            4,
            note="Substitution set up but the $\\frac{1}{2}$ from $du$ was "
            "dropped and limits not changed — 'fair'.",
        ),
        AnswerSpec(
            5,
            r"<p>$V = \frac{4}{3}\pi r^3$. Differentiating, $\frac{dV}{dt} = "
            r"4\pi r^2\frac{dr}{dt}$. Putting $r = 5$ and $\frac{dV}{dt} = 100$ "
            r"gives $\frac{dr}{dt} = \frac{100}{100\pi}$.</p>",
            8,
            note="Correct method and expression, left unsimplified and no units.",
        ),
        AnswerSpec(
            6,
            "<p>The Mean Value Theorem says that for a function on an interval "
            "there is a point where the derivative equals the average slope "
            "$\\frac{f(b)-f(a)}{b-a}$.</p>"
            "<p>The function has to be smooth. If it has a corner like $|x|$ "
            "then it does not work, because at the corner there is no "
            "derivative.</p>",
            8,
            note="Rough statement missing the interval hypotheses; only one "
            "hypothesis addressed — 'fair'.",
        ),
        AnswerSpec(7, "<p>B) $10$</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            8,
            "<p>I think it converges because the factorial on top grows slower "
            "than $n^n$ on the bottom.</p>",
            4,
            note="Correct conclusion and a real intuition, but no named test and "
            "no work — 'fair'.",
        ),
        AnswerSpec(
            9,
            r"<p>$3x^2 + 3y^2\frac{dy}{dx} = 6y$. So "
            r"$\frac{dy}{dx} = \frac{6y-3x^2}{3y^2}$, which at $(3,3)$ is "
            r"$\frac{18-27}{27} = -\frac{1}{3}$.</p>",
            4,
            note="Implicit differentiation attempted on the left but the product "
            "rule on $6xy$ is mishandled (the $6x\\frac{dy}{dx}$ term is "
            "dropped), giving a wrong value — 'fair'.",
        ),
    ],
    "weak": [
        AnswerSpec(
            1,
            "<p>B) $3x^2 \\ln(x)$</p>",
            0,
            exact=True,
            note="Wrong: differentiated $x^3$ but forgot the product rule term.",
        ),
        AnswerSpec(2, "<p>D</p>", 0, exact=True, note="Wrong: assumed the limit is 0."),
        AnswerSpec(
            3,
            r"<p>$2x^2 - 7x + 3 = 0$. I divided by 2 to get $x^2 - 3.5x + 1.5 = "
            r"0$. Then $x = 3.5$ or $x = 1.5$.</p>",
            4,
            note="Started a valid rearrangement then read roots straight off the "
            "coefficients — method attempted, roots wrong. 'fair'.",
        ),
        AnswerSpec(
            4,
            r"<p>$\int xe^{x^2} dx = \frac{x^2}{2}e^{x^2}$, so the answer is "
            r"$2e^4$.</p>",
            0,
            note="Invented a product-rule-style antiderivative; no substitution.",
        ),
        AnswerSpec(
            5,
            r"<p>The volume is $\frac{4}{3}\pi r^3$. When $r = 5$ that is "
            r"$\frac{500}{3}\pi$. So the radius grows at $100$ divided by that.</p>",
            4,
            note="Correct volume formula but confused volume with its rate — the "
            "chain rule is not applied. 'fair'.",
        ),
        AnswerSpec(
            6,
            "<p>The Mean Value Theorem is about the average of a function over "
            "an interval. You add the values at the ends and divide by two. It "
            "is used to find averages in calculus.</p>",
            0,
            note="Confuses MVT with the average value of a function — the "
            "theorem is misstated. 'poor'.",
        ),
        AnswerSpec(
            7,
            "<p>C) $6$</p>",
            0,
            exact=True,
            note="Wrong: multiplied the diagonal only.",
        ),
        AnswerSpec(
            8,
            "<p>It diverges because factorials get very big very quickly.</p>",
            0,
            note="Wrong conclusion, no valid reasoning.",
        ),
        AnswerSpec(
            9,
            r"<p>$3x^2 + 3y^2 = 6y + 6x$. At $(3,3)$ that gives "
            r"$27 + 27 = 18 + 18$, so $54 = 36$.</p>",
            0,
            note="Differentiated as if $y$ were a constant — no $\\frac{dy}{dx}$ "
            "appears anywhere. 'poor'.",
        ),
    ],
    "partial": [
        AnswerSpec(1, "<p>A</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, "", 0, exact=True, note="BLANK — not attempted."),
        AnswerSpec(3, "", 0, note="BLANK — not attempted."),
        AnswerSpec(
            4,
            r"<p>Let $u = x^2$ so $du = 2x dx$.</p>",
            4,
            note="Substitution correctly identified then abandoned — 'fair'.",
        ),
        AnswerSpec(5, "", 0, note="BLANK — not attempted."),
        AnswerSpec(
            6,
            "<p>The Mean Value Theorem needs the function to be continuous on "
            "$[a,b]$ and differentiable on $(a,b)$, and then some $c$ has "
            "$f'(c)$ equal to the average slope. If it is not differentiable, "
            "for example $|x|$, it can fail.</p>",
            8,
            note="Statement essentially right but no real explanation and only "
            "one hypothesis illustrated — 'fair'.",
        ),
        AnswerSpec(7, "", 0, exact=True, note="BLANK — not attempted."),
        AnswerSpec(8, "", 0, note="BLANK — not attempted."),
        AnswerSpec(9, "", 0, note="BLANK — not attempted."),
    ],
    "fluent_wrong": [
        AnswerSpec(
            1,
            r"<p>D) $x^2\ln(x) + 3x^2$</p>",
            0,
            exact=True,
            note="ADVERSARIAL + LATEX-WHITESPACE PROBE: wrong option D. The "
            "retyped LaTeX omits the space in option D's '$x^2 \\ln(x)$'. "
            "This used to defer to the LLM — letter said D, text matched "
            "nothing — costing a billed call to reach an answer tier 0 "
            "already had. collapse_math_whitespace now treats whitespace "
            "inside '$...$' as insignificant (it is, in LaTeX), so the "
            "letter and the text agree and tier 0 claims it as INCORRECT "
            "for free. Realistic case: students retype notation "
            "inconsistently.",
        ),
        AnswerSpec(
            2,
            r"<p>C) $\frac{3}{5}$</p>",
            0,
            exact=True,
            note="ADVERSARIAL: inverted the ratio.",
        ),
        AnswerSpec(
            3,
            r"<p>Applying the quadratic formula in its standard form, we "
            r"identify $a = 2$, $b = -7$ and $c = 3$. The discriminant "
            r"$b^2 - 4ac$ evaluates to $49 - 24 = 25$, a perfect square, which "
            r"guarantees rational roots. Proceeding, $x = \frac{-7 \pm 5}{4}$, "
            r"yielding $x = -\frac{1}{2}$ and $x = -3$. Both roots are "
            r"negative, consistent with the positive constant term.</p>",
            4,
            note="ADVERSARIAL: immaculate presentation and correct discriminant, "
            "but the sign of $b$ is mishandled so both roots are wrong, and the "
            "closing justification is nonsense. Method is genuinely visible, so "
            "'fair' — a grader seduced by the prose will award far more.",
        ),
        AnswerSpec(
            4,
            r"<p>This integral yields elegantly to the substitution "
            r"$u = e^{x^2}$, whence $du = e^{x^2}dx$ and the integrand reduces "
            r"to $x\,du$. Integrating gives $\frac{x^2}{2}u$, and evaluating "
            r"between the stated limits produces $2e^4$, an exact closed form "
            r"as required.</p>",
            0,
            note="ADVERSARIAL: fluent and confident, but the substitution is "
            "invalid ($x$ is left inside) and the answer is wrong. 'poor'.",
        ),
        AnswerSpec(
            5,
            r"<p>By the standard related-rates argument we differentiate the "
            r"volume with respect to the radius, obtaining $4\pi r^2$, the "
            r"surface area — a pleasing result. At $r = 5$ this is $100\pi$, and "
            r"since the volume grows at $100$ cm³/s the radius must therefore "
            r"grow at $100\pi$ cm/s.</p>",
            4,
            note="ADVERSARIAL: correct differentiation, correct observation about "
            "surface area, then multiplies where it should divide. 'fair'.",
        ),
        AnswerSpec(
            6,
            "<p>The Mean Value Theorem is one of the central results of "
            "differential calculus. It asserts that for any function $f$ defined "
            "on an interval $[a,b]$, there exists a point $c$ in that interval "
            "at which the function attains its mean value, that is "
            "$f(c) = \\frac{f(a)+f(b)}{2}$.</p>"
            "<p>The first hypothesis, continuity, is essential: a discontinuous "
            "function may simply skip over its mean value. The second "
            "hypothesis, differentiability, guarantees that the function is "
            "sufficiently well behaved for the mean to be attained smoothly "
            "rather than at a cusp.</p>"
            "<p>Together these conditions make the theorem one of the most "
            "widely applied tools in analysis, underpinning both Taylor's "
            "theorem and L'Hôpital's rule.</p>",
            0,
            note="THE KEY ADVERSARIAL CASE: three well-structured paragraphs, "
            "confident register, correct-sounding surrounding claims — and the "
            "theorem itself is flatly wrong (that is the Intermediate Value "
            "Theorem applied to the mean, not the MVT). Rubric 'poor' says "
            "'theorem misstated'. A grader rewarding fluency scores this high.",
        ),
        AnswerSpec(
            7,
            "<p>A) $2$</p>",
            0,
            exact=True,
            note="ADVERSARIAL: wrong.",
        ),
        AnswerSpec(
            8,
            "<p>The series is a textbook application of the comparison test. "
            "Since $n! \\le n^n$ for all $n \\ge 1$, each term is bounded above "
            "by $1$, and a series whose terms are bounded necessarily "
            "converges.</p>",
            0,
            note="ADVERSARIAL: correct inequality, correct-sounding test name, "
            "and a conclusion that does not follow at all (bounded terms do not "
            "imply convergence). Right answer, invalid reasoning — 'poor' per "
            "the rubric's 'no valid reasoning'.",
        ),
        AnswerSpec(
            9,
            r"<p>Implicit differentiation proceeds term by term. The left side "
            r"yields $3x^2 + 3y^2\frac{dy}{dx}$ directly. The right side, "
            r"$6xy$, is a product, and by the product rule its derivative is "
            r"$6x\frac{dy}{dx} \cdot 6y = 36xy\frac{dy}{dx}$.</p>"
            r"<p>Substituting the point $(3,3)$ and solving gives "
            r"$\frac{dy}{dx} = \frac{1}{3}$, a positive slope consistent with "
            r"the curve rising through this region.</p>",
            4,
            note="ADVERSARIAL: correctly names the product rule and gets the "
            "left side right, then multiplies the two product terms instead of "
            "adding them, and closes with a confident geometric justification "
            "that is also wrong (the slope at (3,3) is -1). 'fair'.",
        ),
    ],
    "twin": [
        AnswerSpec(1, "<p>A</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, r"<p>$\frac{5}{3}$</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            3,
            r"<p>$(2x-1)(x-3) = 0$ so $x = 0.5$ or $x = 3$.</p>",
            7,
            note="Correct roots, factorisation shown but no verification.",
        ),
        AnswerSpec(
            4,
            r"<p>With $u = x^2$, $du = 2x\,dx$, the integral becomes "
            r"$\frac{1}{2}\int_0^4 e^u du = \frac{1}{2}(e^4-1)$.</p>",
            12,
            exact=True,
            note="Correct with limits changed.",
        ),
        AnswerSpec(
            5,
            r"<p>$\frac{dV}{dt} = 4\pi r^2 \frac{dr}{dt}$, so at $r=5$, "
            r"$\frac{dr}{dt} = \frac{100}{100\pi} = \frac{1}{\pi}$ cm/s.</p>",
            12,
            exact=True,
            note="Correct with units.",
        ),
        AnswerSpec(
            6,
            SHARED_MVT_ANSWER,
            15,
            note="CONSISTENCY PROBE: byte-identical to 'strong' Q6. Must receive "
            "the same score as 'strong' did.",
        ),
        AnswerSpec(7, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            8,
            r"<p>Ratio test: $\left(\frac{n}{n+1}\right)^n \to \frac{1}{e} < 1$, "
            r"so it converges.</p>",
            8,
            note="Correct with the ratio shown but the limit not derived.",
        ),
        AnswerSpec(
            9,
            r"<p>$3x^2 + 3y^2\frac{dy}{dx} = 6y + 6x\frac{dy}{dx}$, so "
            r"$\frac{dy}{dx} = \frac{2y-x^2}{y^2-2x} = \frac{-3}{3} = -1$ at "
            r"$(3,3)$.</p>",
            8,
            note="Correct differentiation and value, working compressed into one "
            "line — 'good'.",
        ),
    ],
}


# ── Assignment 2: Chemistry ───────────────────────────────────────────────

CHEMISTRY_SUBMISSIONS = {
    "excellent": [
        AnswerSpec(1, r"<p>B) $HSO_4^{-}$</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, "<p>B) It decreases</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            3,
            r"<p>$CaCO_3 + 2HCl \rightarrow CaCl_2 + H_2O + CO_2$.</p>"
            r"<p>$n(CaCO_3) = \frac{5.00}{100.1} = 0.0500$ mol. "
            r"$n(HCl) = 0.0500 \times 1.00 = 0.0500$ mol.</p>"
            r"<p>The equation needs 2 mol HCl per mol $CaCO_3$, so $0.0500$ mol "
            r"$CaCO_3$ would require $0.100$ mol HCl. Only $0.0500$ mol is "
            r"available, so <b>HCl is limiting</b>.</p>"
            r"<p>$n(CO_2) = \frac{0.0500}{2} = 0.0250$ mol, so "
            r"$m(CO_2) = 0.0250 \times 44.0 = 1.10$ g.</p>",
            12,
            exact=True,
            note="Balanced equation, both moles, correct limiting reagent, "
            "correct mass with units.",
        ),
        AnswerSpec(
            4,
            r"<p>Concentrations: $[H_2] = \frac{0.200}{2.00} = 0.100$, "
            r"$[I_2] = 0.100$, $[HI] = \frac{1.60}{2.00} = 0.800$ mol dm⁻³.</p>"
            r"<p>$K_c = \frac{[HI]^2}{[H_2][I_2]} = "
            r"\frac{(0.800)^2}{(0.100)(0.100)} = \frac{0.640}{0.0100} = 64.0$.</p>"
            r"<p>The concentration units cancel because there are two moles of "
            r"gas on each side, so $K_c$ is dimensionless.</p>",
            12,
            exact=True,
            note="Correct value with the dimensionless justification explained.",
        ),
        AnswerSpec(
            5,
            "<p>The two mechanisms differ in molecularity, and every other "
            "difference follows from that.</p>"
            "<p><b>Rate.</b> $S_N1$ proceeds through a rate-determining "
            "ionisation to a carbocation; since the nucleophile enters only "
            "after that step, it cannot appear in the rate law, giving "
            "rate $= k[\\text{RX}]$. $S_N2$ is concerted — bond forming and "
            "bond breaking occur in one transition state containing both "
            "species — so rate $= k[\\text{RX}][\\text{Nu}^-]$.</p>"
            "<p><b>Stereochemistry.</b> The $S_N1$ carbocation is $sp^2$ "
            "hybridised and planar, so the nucleophile attacks either face with "
            "near-equal probability and a single enantiomer racemises. In "
            "$S_N2$ the nucleophile must attack anti-periplanar to the leaving "
            "group, along the back lobe of the C–LG antibonding orbital, so "
            "configuration inverts (Walden inversion).</p>"
            "<p><b>Substrate.</b> Tertiary substrates favour $S_N1$ because "
            "adjacent alkyl groups donate electron density and hyperconjugate "
            "into the empty p orbital, stabilising the carbocation. The same "
            "bulk sterically blocks backside attack, which is why primary "
            "substrates — with an accessible rear face and no carbocation "
            "stabilisation — go by $S_N2$.</p>",
            20,
            note="All three axes with genuinely causal explanations throughout.",
        ),
        AnswerSpec(
            6,
            "<p>Burning methane completely gives one carbon dioxide and two "
            "waters per methane: $CH_4 + 2O_2 \\rightarrow CO_2 + 2H_2O$.</p>"
            "<p>Hess's law lets me take the enthalpy of the products minus that "
            "of the reactants, since enthalpy is a state function and the route "
            "does not matter. Oxygen is an element in its standard state so "
            "contributes nothing.</p>"
            "<p>Products: $-393.5 + 2(-285.8) = -965.1$. Reactants: $-74.8$. "
            "Difference: $-965.1 - (-74.8) = -890.3$ kJ mol⁻¹. Negative, as "
            "expected for a combustion.</p>",
            10,
            exact=True,
            note="PARAPHRASE PROBE: entirely correct, but written in plain "
            "language sharing almost no vocabulary with model_answer (no "
            "'standard enthalpy of formation', no summation notation, no "
            "'cycle'). Tests understanding vs keyword matching.",
        ),
    ],
    "strong": [
        AnswerSpec(1, r"<p>$HSO_4^{-}$</p>", 3, exact=True, note="Correct as text."),
        AnswerSpec(2, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            3,
            r"<p>$CaCO_3 + 2HCl \rightarrow CaCl_2 + H_2O + CO_2$. "
            r"$n(CaCO_3) = 0.0500$ mol and $n(HCl) = 0.0500$ mol. HCl is "
            r"limiting because you need twice as much of it. "
            r"$n(CO_2) = 0.025$ mol so the mass is $1.1$ g.</p>",
            8,
            note="Correct limiting reagent and answer, but the reasoning is "
            "compressed and significant figures are loose — 'good'.",
        ),
        AnswerSpec(
            4,
            r"<p>$[H_2] = 0.100$, $[I_2] = 0.100$, $[HI] = 0.800$. "
            r"$K_c = \frac{0.800^2}{0.100 \times 0.100} = 64$. "
            r"Units: mol dm⁻³.</p>",
            8,
            note="Correct value but the units are wrong — $K_c$ here is "
            "dimensionless. Exactly the 'good' descriptor.",
        ),
        AnswerSpec(
            5,
            SHARED_SN1_SN2_ANSWER,
            15,
            note="CONSISTENCY PROBE (shared with 'twin'). All three axes correct, "
            "but the explanations are descriptive rather than causal — 'good'.",
        ),
        AnswerSpec(
            6,
            r"<p>$CH_4 + 2O_2 \rightarrow CO_2 + 2H_2O$. "
            r"$\Delta H = [(-393.5) + 2(-285.8)] - (-74.8) = -890.3$ kJ/mol.</p>",
            7,
            note="Correct answer and method but stated in one line with no "
            "explanation of the cycle — 'good'.",
        ),
    ],
    "middling": [
        AnswerSpec(1, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            2,
            "<p>A) It increases</p>",
            0,
            exact=True,
            note="Wrong: applied Le Chatelier backwards for an exothermic " "reaction.",
        ),
        AnswerSpec(
            3,
            r"<p>$n(CaCO_3) = 5.00/100.1 = 0.05$ mol. $n(HCl) = 0.05$ mol. "
            r"They are equal so $CaCO_3$ is limiting. $n(CO_2) = 0.05$ mol, "
            r"mass $= 0.05 \times 44 = 2.2$ g.</p>",
            4,
            note="Moles correct but ignored the 1:2 ratio, so the wrong reagent "
            "is limiting and the mass is doubled — 'fair'.",
        ),
        AnswerSpec(
            4,
            r"<p>$K_c = \frac{[HI]^2}{[H_2][I_2]}$. Using the moles directly: "
            r"$\frac{1.60^2}{0.200 \times 0.200} = \frac{2.56}{0.04} = 64$.</p>",
            8,
            note="Right expression and right value, but reached by using moles "
            "instead of concentrations — the volume happens to cancel. Matches "
            "the 'good' descriptor explicitly.",
        ),
        AnswerSpec(
            5,
            "<p>$S_N1$ has one step and $S_N2$ has two steps. In $S_N1$ the "
            "rate only depends on the halogenoalkane, and in $S_N2$ it depends "
            "on both reactants. $S_N1$ happens with tertiary and $S_N2$ with "
            "primary. The $S_N2$ one inverts the molecule.</p>",
            8,
            note="Correct on rate, substrate and inversion, but the step counts "
            "are stated backwards and there is no explanation of why — 'fair'.",
        ),
        AnswerSpec(
            6,
            r"<p>$\Delta H = -393.5 + (-285.8) - (-74.8) = -604.5$ kJ/mol.</p>",
            4,
            note="Hess's law applied in the right direction but the $\\times 2$ "
            "on water is missing — 'fair'.",
        ),
    ],
    "weak": [
        AnswerSpec(
            1,
            r"<p>A) $SO_4^{2-}$</p>",
            0,
            exact=True,
            note="Wrong: removed both protons.",
        ),
        AnswerSpec(2, "<p>C) It is unchanged</p>", 0, exact=True, note="Wrong."),
        AnswerSpec(
            3,
            r"<p>$5.00 / 100.1 = 0.05$ moles of calcium carbonate. So there are "
            r"$0.05$ moles of $CO_2$ and the mass is $2.2$ g.</p>",
            4,
            note="Correct first mole calculation, no HCl consideration at all — "
            "'fair' by the 'limiting reagent wrongly identified' descriptor.",
        ),
        AnswerSpec(
            4,
            r"<p>$K_c = \frac{[H_2][I_2]}{[HI]^2}$ which is "
            r"$\frac{0.01}{0.64} = 0.0156$.</p>",
            0,
            note="Expression inverted — 'poor'.",
        ),
        AnswerSpec(
            5,
            "<p>They are both ways that molecules react. $S_N1$ is faster than "
            "$S_N2$ because it has a lower number. Both of them swap one group "
            "for another group.</p>",
            0,
            note="No valid comparison; the '1 means faster' claim is invented.",
        ),
        AnswerSpec(
            6,
            "<p>You add up all the numbers given: $-74.8 - 393.5 - 285.8 = "
            "-754.1$ kJ/mol.</p>",
            0,
            note="No Hess cycle, just summed the data — 'poor'.",
        ),
    ],
    "partial": [
        AnswerSpec(1, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, "", 0, exact=True, note="BLANK — not attempted."),
        AnswerSpec(
            3,
            r"<p>$CaCO_3 + 2HCl \rightarrow CaCl_2 + H_2O + CO_2$. "
            r"$n(CaCO_3) = 0.0500$ mol, $n(HCl) = 0.0500$ mol. Since you need 2 "
            r"HCl for every carbonate, the acid runs out first, so HCl limits "
            r"it. That gives $0.025$ mol of $CO_2$.</p>",
            8,
            note="Correct reasoning and limiting reagent but stops before "
            "converting to mass — 'good' (approach correct, answer incomplete).",
        ),
        AnswerSpec(4, "", 0, note="BLANK — not attempted."),
        AnswerSpec(
            5,
            "<p>$S_N2$ is a one step mechanism where the nucleophile attacks "
            "from the opposite side to the leaving group, so the molecule is "
            "inverted. The rate depends on both the substrate and the "
            "nucleophile. I ran out of time to cover $S_N1$.</p>",
            8,
            note="What is written is entirely correct, but only one mechanism is "
            "covered so there is no comparison — 'fair'.",
        ),
        AnswerSpec(6, "", 0, note="BLANK — not attempted."),
    ],
    "fluent_wrong": [
        AnswerSpec(
            1,
            r"<p>C) $H_3SO_4^{+}$</p>",
            0,
            exact=True,
            note="ADVERSARIAL: gave the conjugate acid instead.",
        ),
        AnswerSpec(
            2,
            "<p>A) It increases</p>",
            0,
            exact=True,
            note="ADVERSARIAL: wrong direction.",
        ),
        AnswerSpec(
            3,
            r"<p>We begin by balancing: $CaCO_3 + 2HCl \rightarrow CaCl_2 + "
            r"H_2O + CO_2$. The molar quantities are $n(CaCO_3) = 0.0500$ mol "
            r"and $n(HCl) = 0.0500$ mol. Since these are numerically equal, "
            r"neither reagent is in excess and the reaction proceeds "
            r"stoichiometrically to completion, consuming both entirely. "
            r"Accordingly $n(CO_2) = 0.0500$ mol and $m = 2.20$ g.</p>",
            4,
            note="ADVERSARIAL: balanced equation and both mole values correct, "
            "then 'numerically equal means neither is limiting' — plausible-"
            "sounding and wrong, since the ratio is 1:2. 'fair'.",
        ),
        AnswerSpec(
            4,
            r"<p>Equilibrium constants are defined in terms of concentrations, "
            r"so we first divide by the volume: $[H_2] = [I_2] = 0.100$ and "
            r"$[HI] = 0.800$ mol dm⁻³. The expression for this equilibrium is "
            r"$K_c = \frac{[HI]}{[H_2][I_2]} = \frac{0.800}{0.0100} = 80.0$, "
            r"with units of dm³ mol⁻¹.</p>",
            4,
            note="ADVERSARIAL: correct concentrations and a confident preamble, "
            "but the square on $[HI]$ is dropped, so both value and units are "
            "wrong. Right expression shape, arithmetic wrong — 'fair'.",
        ),
        AnswerSpec(
            5,
            "<p>The distinction between these two mechanisms is one of the most "
            "elegant in physical organic chemistry.</p>"
            "<p>$S_N1$ is the bimolecular pathway: the nucleophile and the "
            "substrate collide in a single concerted transition state, so the "
            "rate is $k[\\text{RX}][\\text{Nu}^-]$ and the stereochemistry is "
            "cleanly inverted. Because the transition state is crowded, "
            "unhindered primary substrates react fastest.</p>"
            "<p>$S_N2$, by contrast, is unimolecular. The substrate first "
            "ionises to a planar carbocation, giving rate $= k[\\text{RX}]$ and "
            "racemisation, and tertiary centres dominate because they stabilise "
            "the intermediate.</p>",
            0,
            note="THE KEY ADVERSARIAL CASE for chemistry: every individual fact "
            "is correct, the prose is confident and well-organised — but the two "
            "labels are systematically SWAPPED. Rubric 'poor' says 'mechanisms "
            "confused or reversed'. A grader matching keywords sees all the "
            "right terms and scores this highly.",
        ),
        AnswerSpec(
            6,
            "<p>By Hess's law, enthalpy change is path independent, so we may "
            "sum the formation enthalpies of all species involved: "
            "$(-74.8) + (-393.5) + 2(-285.8) = -1039.9$ kJ mol⁻¹. The strongly "
            "negative value confirms combustion is exothermic.</p>",
            0,
            note="ADVERSARIAL: correctly states Hess's law, then adds reactants "
            "and products instead of subtracting. 'poor'.",
        ),
    ],
    "twin": [
        AnswerSpec(1, "<p>B</p>", 3, exact=True, note="Correct."),
        AnswerSpec(2, "<p>B) It decreases</p>", 3, exact=True, note="Correct."),
        AnswerSpec(
            3,
            r"<p>$CaCO_3 + 2HCl \rightarrow CaCl_2 + H_2O + CO_2$. Both are "
            r"$0.0500$ mol but HCl is needed 2:1, so HCl limits. "
            r"$n(CO_2) = 0.0250$ mol, $m = 1.10$ g.</p>",
            8,
            note="Correct throughout but very compressed.",
        ),
        AnswerSpec(
            4,
            r"<p>$[HI] = 0.800$, $[H_2] = [I_2] = 0.100$. "
            r"$K_c = \frac{0.64}{0.01} = 64$, no units.</p>",
            8,
            note="Correct value and correctly dimensionless, minimal working.",
        ),
        AnswerSpec(
            5,
            SHARED_SN1_SN2_ANSWER,
            15,
            note="CONSISTENCY PROBE: byte-identical to 'strong' Q5. Must receive "
            "the same score as 'strong' did.",
        ),
        AnswerSpec(
            6,
            r"<p>$CH_4 + 2O_2 \rightarrow CO_2 + 2H_2O$. Products minus "
            r"reactants: $[-393.5 + 2(-285.8)] - [-74.8] = -890.3$ kJ mol⁻¹.</p>",
            7,
            note="Correct with the cycle direction stated but not explained.",
        ),
    ],
}


# ── Assignment 3: History & Literature ────────────────────────────────────

HUMANITIES_SUBMISSIONS = {
    "excellent": [
        AnswerSpec(
            1,
            "<p>The claim is half right, and the historiography shows why the "
            "other half matters.</p>"
            "<p>The structural case is strong. The alliance system converted a "
            "Balkan dispute into a continental war, and mobilisation timetables "
            "— the Schlieffen Plan above all — compressed the decision window "
            "to days. Once Russia began general mobilisation on 30 July, German "
            "planning admitted no partial response.</p>"
            "<p>But structures do not sign ultimatums. Fritz Fischer's "
            "<em>Griff nach der Weltmacht</em> argued from the September "
            "Programme that Germany pursued deliberate expansionist aims, "
            "making the July Crisis a chosen war rather than an accident. "
            "Christopher Clark's <em>The Sleepwalkers</em> reaches an almost "
            "opposite conclusion, but note that it too is a story about "
            "<em>decisions</em> — men who were, in his phrase, watchful but "
            "unseeing. Both major positions locate causation in agency; they "
            "disagree only about intent.</p>"
            "<p>The blank cheque of 5 July is the decisive evidence. It was not "
            "required by any treaty. Austria-Hungary's ultimatum was designed "
            "to be rejected. These are choices made inside a structure, not "
            "outputs of one.</p>"
            "<p>I therefore judge the claim inverted: the alliance system set "
            "the price of war, but individuals decided to pay it.</p>",
            25,
            note="Sustained argument, named historiography used accurately, "
            "concrete evidence, defended judgement.",
        ),
        AnswerSpec(
            2,
            "<p>Macbeth's downfall is self-authored; the supernatural supplies "
            "occasion, not compulsion.</p>"
            "<p>The witches never instruct. They predict — “thou shalt be king "
            "hereafter” — and it is Macbeth who immediately supplies the means, "
            "recoiling at a “horrid image” before any external pressure. That "
            "the thought of murder arrives unbidden, in the same scene as the "
            "prophecy, is the play's clearest evidence that the ambition "
            "precedes the temptation.</p>"
            "<p>The dagger soliloquy makes the point formally. “Art thou not, "
            "fatal vision, sensible to feeling as to sight?” The dagger is "
            "explicitly interrogated as a projection — “a dagger of the mind, a "
            "false creation” — so even the play's most supernatural-seeming "
            "prompt is internally sourced.</p>"
            "<p>The fatalist reading has real support: the apparitions' "
            "equivocations do come true, and Macbeth dies believing himself "
            "cheated by fiends that “palter with us in a double sense”. But "
            "equivocation only ensnares him because he chooses to hear "
            "guarantees. Birnam Wood moves because armed men choose to carry "
            "it.</p>"
            "<p>By the “tomorrow, and tomorrow, and tomorrow” speech the "
            "language has emptied of agency altogether — life is “a tale told "
            "by an idiot” — which reads less as fate's verdict than as the "
            "terminal condition of a man who has spent his own will.</p>",
            25,
            note="Clear thesis, close quotation, counter-reading genuinely "
            "examined and answered, language analysed rather than plot retold.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> is statecraft grounded in practical power "
            "and material circumstance rather than in ideology, legitimism or "
            "moral principle. Its significance is that it displaced the "
            "Metternichian assumption that dynastic legitimacy should govern "
            "European order, replacing it with calculation of interest.</p>"
            "<p>Bismarck is the standing example. He engineered war with "
            "Austria in 1866 and then imposed strikingly lenient terms at "
            "Nikolsburg — not from generosity, but because a resentful Austria "
            "would be useless to him later. The same man who unified Germany by "
            "war spent the following two decades constructing alliances to "
            "prevent one.</p>",
            15,
            note="Precise definition, real significance, and a specific example "
            "that demonstrates the concept rather than merely naming it.",
        ),
        AnswerSpec(
            4,
            "<p>Neither source is “the true one”, and choosing between them is "
            "the wrong operation. Each is reliable evidence of its author's "
            "position, and only conditionally reliable about events.</p>"
            "<p>First, establish provenance and interest. The dispatch is "
            "written by an official upward through a chain of command, with a "
            "career stake in having suppression appear necessary; “criminal "
            "mob” performs that justification. The diary is written by a "
            "participant, plausibly for himself, with a stake in the rising "
            "being legitimate; “the people in arms” performs that. The loaded "
            "vocabulary is not contamination to be filtered out — it is the "
            "evidence, telling us how each side needed the event understood.</p>"
            "<p>Second, separate claim types. Assertions about motive are "
            "interpretation. Assertions about time, place, numbers and "
            "casualties are checkable, and should be corroborated against "
            "independent records — municipal registers, hospital returns, "
            "newspaper reports from outside the city.</p>"
            "<p>Where the two agree despite opposite interests, confidence is "
            "high, because agreement against interest is the strongest form of "
            "corroboration.</p>",
            15,
            note="Treats both as perspectival evidence, identifies interest, "
            "separates checkable from interpretive claims, and gets the "
            "agreement-against-interest principle right.",
        ),
    ],
    "strong": [
        AnswerSpec(
            1,
            "<p>The alliance system was clearly important. Because of the "
            "treaties, a quarrel between Austria-Hungary and Serbia dragged in "
            "Russia, then Germany, then France and Britain. The mobilisation "
            "schedules also meant that once one country started, the others "
            "felt they had to follow or lose the advantage.</p>"
            "<p>However, people still made the decisions. Fischer argued that "
            "Germany had planned for expansion and wanted the war, which puts "
            "the responsibility on German leaders rather than on the system. "
            "The blank cheque given to Austria-Hungary was a choice, and the "
            "ultimatum was written so that it would be refused.</p>"
            "<p>Overall I think both mattered, but the alliances made the war "
            "bigger rather than making it happen in the first place.</p>",
            18,
            note="Clear argument with real evidence and one named historian, but "
            "the historiographical debate is thin and the counter-position gets "
            "one paragraph — 'good'.",
        ),
        AnswerSpec(
            2,
            "<p>Macbeth's ambition is the main cause of his downfall, though "
            "the witches play a part.</p>"
            "<p>When the witches tell him he will be king, Macbeth immediately "
            "starts thinking about murder, which shows the idea was already "
            "there. Lady Macbeth then pushes him when he hesitates, questioning "
            "his manhood. The witches never actually tell him to kill Duncan.</p>"
            "<p>Later the apparitions make him overconfident, telling him no "
            "man born of woman can harm him. This is a trick, and it does "
            "contribute to his death because he stops being careful.</p>"
            "<p>So fate sets things up but Macbeth's own choices are what "
            "destroy him.</p>",
            18,
            note="Clear argument with accurate textual reference, but reference "
            "is paraphrased rather than quoted and the analysis is descriptive "
            "— 'good'.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> means politics based on practical "
            "considerations and power rather than on morality or ideology. It "
            "mattered in the nineteenth century because it changed how states "
            "made decisions.</p>"
            "<p>Bismarck is the main example — he was willing to go to war or "
            "make alliances depending on what was useful for Prussia at the "
            "time.</p>",
            10,
            note="Sound definition and a correct example, but significance is "
            "asserted rather than developed — 'good'.",
        ),
        AnswerSpec(
            4,
            "<p>A historian should not simply pick one. Both are biased, but "
            "the bias is itself useful information about how each side saw the "
            "uprising.</p>"
            "<p>The government dispatch calls them a criminal mob because the "
            "official needs to justify putting the rising down. The diary calls "
            "them the people in arms because the writer was part of it and "
            "believed in it. So the wording tells you about the author's "
            "position.</p>"
            "<p>For the actual facts, like how many people were there and what "
            "happened when, the historian should look for other sources to "
            "check against, such as newspapers or official records from "
            "elsewhere.</p>",
            15,
            note="Treats both as perspectival, identifies each interest, and "
            "proposes concrete corroboration — reaches 'excellent' despite plain "
            "prose.",
        ),
    ],
    "middling": [
        AnswerSpec(
            1,
            "<p>There were many causes of the First World War. The "
            "assassination of Franz Ferdinand in Sarajevo in 1914 started it. "
            "Austria-Hungary blamed Serbia and declared war. Russia supported "
            "Serbia and Germany supported Austria-Hungary. Then France and "
            "Britain joined in.</p>"
            "<p>The alliances were important because they meant more countries "
            "joined the war. There was also the arms race and nationalism and "
            "imperialism.</p>"
            "<p>So the alliance system was a big cause of the war.</p>",
            10,
            note="Accurate narrative but almost no argument and no engagement "
            "with the claim or the debate — 'fair'.",
        ),
        AnswerSpec(
            2,
            "<p>Macbeth meets the witches who tell him he will be king. He "
            "tells Lady Macbeth and she persuades him to kill Duncan when he "
            "comes to stay. He becomes king but then has to kill Banquo as "
            "well, and later Macduff's family.</p>"
            "<p>He goes back to the witches and they tell him he is safe until "
            "Birnam Wood moves. In the end Malcolm's army uses branches from "
            "the wood and Macduff kills him.</p>"
            "<p>I think it was mostly his ambition that caused it.</p>",
            10,
            note="Plot summary with the argument tacked on at the end; no "
            "quotation or analysis — 'fair'.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> is a German word about being realistic in "
            "politics. It means leaders did what was practical. Bismarck used "
            "it in Germany.</p>",
            5,
            note="Roughly right but vague, and the example is named without any "
            "content — 'fair'.",
        ),
        AnswerSpec(
            4,
            "<p>The two sources disagree because they come from different "
            "sides. The government one is biased against the protesters and the "
            "diary is biased for them.</p>"
            "<p>The historian should compare them and try to find out what "
            "really happened, maybe by using other sources too.</p>",
            10,
            note="Recognises bias in both and suggests cross-checking, but the "
            "method stays general — precisely the 'good' descriptor.",
        ),
    ],
    "weak": [
        AnswerSpec(
            1,
            "<p>The First World War happened because of the alliances. "
            "Countries had agreements with each other so when one went to war "
            "they all did. It was also because of the assassination.</p>"
            "<p>Lots of people died in the trenches. It lasted from 1914 to "
            "1918 and Germany lost.</p>",
            10,
            note="Some accurate content and a gestured claim, but drifts into "
            "irrelevance — sits at the top of 'fair'.",
        ),
        AnswerSpec(
            2,
            "<p>Macbeth is a play by Shakespeare about a Scottish king. "
            "Macbeth is told by three witches that he will become king. He kills "
            "the king and takes over. At the end he is killed.</p>"
            "<p>It was the witches' fault because they told him.</p>",
            10,
            note="Bare summary with an unsupported one-line claim — 'fair'.",
        ),
        AnswerSpec(
            3,
            "<p>Realpolitik is when politicians are realistic about things and "
            "do not promise what they cannot do.</p>",
            5,
            note="Partially right in spirit but imprecise, and no example — " "'fair'.",
        ),
        AnswerSpec(
            4,
            "<p>The historian should use the government one because official "
            "records are more reliable than someone's personal diary.</p>",
            5,
            note="Ranks one source as true without justification — exactly the "
            "'fair' descriptor.",
        ),
    ],
    "partial": [
        AnswerSpec(1, "", 0, note="BLANK — not attempted."),
        AnswerSpec(
            2,
            "<p>Macbeth's ambition is the main reason he falls. The witches "
            "give him the idea that he could be king, but he is the one who "
            "decides to kill Duncan, and Lady Macbeth encourages him. He could "
            "have waited to see if the prophecy came true on its own.</p>"
            "<p>Later he becomes overconfident because of what the apparitions "
            "say.</p>",
            10,
            note="A real argument, briefly made, with no quotation and little "
            "development — 'fair'.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> is policy based on practical power rather "
            "than moral or ideological principles. Bismarck is the classic "
            "example, unifying Germany through calculated wars.</p>",
            10,
            note="Accurate definition and correct example, but significance is "
            "not addressed — 'good'.",
        ),
        AnswerSpec(4, "", 0, note="BLANK — not attempted."),
    ],
    "fluent_wrong": [
        AnswerSpec(
            1,
            "<p>The historiographical consensus on this question was settled "
            "decisively by A.J.P. Taylor in <em>The Origins of the First World "
            "War</em> (1961), in which he demonstrated that the conflict was "
            "the deliberate product of a Franco-Russian conspiracy to encircle "
            "Germany, a thesis subsequently confirmed by Fritz Fischer.</p>"
            "<p>The alliance system, on this reading, was not a neutral "
            "structure but an instrument of that encirclement. The Triple "
            "Entente of 1907 was explicitly offensive in character, and the "
            "Schlieffen Plan was drafted in 1905 as a purely defensive response "
            "to it.</p>"
            "<p>It follows that the claim under evaluation is correct: "
            "structures, not individuals, produced the war — the individuals "
            "concerned were merely executing commitments entered into decades "
            "earlier.</p>",
            10,
            note="ADVERSARIAL: authoritative register, real book titles, real "
            "historians' names — and the substance is fabricated. Taylor argued "
            "close to the opposite; Fischer blamed Germany, not a Franco-Russian "
            "conspiracy; the chronology of the Entente and Schlieffen Plan is "
            "inverted. Some genuine material (alliance structure, plan names) "
            "keeps it off the floor — 'fair'.",
        ),
        AnswerSpec(
            2,
            "<p>Shakespeare's tragedy turns on the doctrine of predestination, "
            "and Macbeth's fall is best read as a study in its inexorability.</p>"
            "<p>The decisive evidence is the banquet scene, in which Banquo's "
            "ghost appears to the entire court, confirming that the "
            "supernatural operates as an objective force in the play's world "
            "rather than as psychological projection. Macbeth's own words — "
            "“the fault, dear Brutus, is not in our stars, but in ourselves” — "
            "are heavy with irony precisely because the play refutes them.</p>"
            "<p>Once the prophecy is uttered in Act I, the sequence is fixed. "
            "Macbeth's protestations of reluctance are the tragic hero's "
            "obligatory resistance to a fate he cannot alter.</p>",
            10,
            note="ADVERSARIAL: confident literary register and a genuine thesis, "
            "resting on two false claims — the ghost is visible only to Macbeth, "
            "and the quotation is from <em>Julius Caesar</em>, not "
            "<em>Macbeth</em>. Argument structure is real, so 'fair'.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> denotes the nineteenth-century doctrine, "
            "associated above all with Metternich and the Congress of Vienna, "
            "that international order should rest on the moral principle of "
            "dynastic legitimacy, and that statesmen are bound to uphold "
            "treaties irrespective of national advantage.</p>"
            "<p>Its significance lies in restraining the ambitions of "
            "individual powers; Bismarck's later career is conventionally "
            "understood as its repudiation.</p>",
            0,
            note="ADVERSARIAL: fluent, correctly cites Metternich and Vienna, "
            "and defines Realpolitik as its own opposite — legitimism and moral "
            "principle are exactly what it displaced. Rubric 'poor' says "
            "'incorrect definition'.",
        ),
        AnswerSpec(
            4,
            "<p>Source criticism requires the historian to establish which "
            "account is authoritative and to discard the other. Here the "
            "criterion is proximity to the institutional record: the government "
            "dispatch was produced contemporaneously within a formal "
            "administrative process, and is therefore the primary source, "
            "whereas a personal diary constitutes a secondary and inherently "
            "unreliable recollection.</p>"
            "<p>The participant's account may nonetheless be cited for "
            "colour.</p>",
            5,
            note="ADVERSARIAL: methodological vocabulary deployed confidently to "
            "reach the wrong conclusion, including the plain error that a "
            "participant's diary is a secondary source. Some engagement with "
            "provenance keeps it at 'fair'.",
        ),
    ],
    "twin": [
        AnswerSpec(
            1,
            "<p>The alliance system mattered a great deal — it turned a "
            "regional dispute into a general war, and the mobilisation "
            "timetables left very little room for anyone to step back once "
            "Russia began mobilising.</p>"
            "<p>But Fischer's work on German war aims suggests this was not "
            "simply drift. The blank cheque was issued, and the ultimatum to "
            "Serbia was drafted to be rejected. Those are decisions.</p>"
            "<p>My judgement is that the structures raised the cost of the "
            "crisis, but individuals chose to run it.</p>",
            18,
            note="Clear argument, one named historian, real evidence, but "
            "compressed and the debate is only sketched — 'good'.",
        ),
        AnswerSpec(
            2,
            "<p>Macbeth's ambition drives his downfall. The witches only "
            "predict; they never order him to do anything, and his mind goes to "
            "murder straight away in Act I, before anyone has pressured him.</p>"
            "<p>Lady Macbeth's goading matters too, but she is working on "
            "material that is already there. The apparitions later make him "
            "reckless rather than doomed — he chooses to believe the "
            "guarantees.</p>"
            "<p>So the supernatural creates the opportunity and Macbeth "
            "supplies the will.</p>",
            18,
            note="Clear thesis and accurate reference, but paraphrase rather "
            "than quotation and limited close analysis — 'good'.",
        ),
        AnswerSpec(
            3,
            "<p><em>Realpolitik</em> is politics driven by practical power "
            "rather than ideology or morality. It marked a shift away from the "
            "legitimist order of the Congress of Vienna.</p>"
            "<p>Bismarck is the example: he provoked wars when they served "
            "Prussian unification and then switched to alliance-building to "
            "keep what he had won.</p>",
            10,
            note="Good definition, correct contrast and example, but "
            "significance stays brief — 'good'.",
        ),
        AnswerSpec(
            4,
            "<p>Both sources are biased and that is what makes them useful. The "
            "official calls them a criminal mob because he has to justify "
            "putting the rising down; the participant calls them the people in "
            "arms because he was part of it.</p>"
            "<p>For facts like numbers and timing the historian should check "
            "both against independent records rather than trusting either.</p>",
            10,
            note="Recognises bias in both and proposes corroboration, but the "
            "method is stated generally — 'good'.",
        ),
    ],
}


SUBMISSIONS = {
    "maths": MATHS_SUBMISSIONS,
    "chemistry": CHEMISTRY_SUBMISSIONS,
    "humanities": HUMANITIES_SUBMISSIONS,
}


def answers_for(assignment_key, student_key):
    """The AnswerSpec list for one (assignment, student) pair."""
    return SUBMISSIONS[assignment_key][student_key]


def submission_answers_json(assignment_key, student_key):
    """The JSON that would live in StudentSubmission.answers."""
    return [
        {"question_number": spec.question_number, "answer_html": spec.answer_html}
        for spec in answers_for(assignment_key, student_key)
    ]


def expected_total(assignment_key, student_key):
    return sum(
        spec.expected_points for spec in answers_for(assignment_key, student_key)
    )
