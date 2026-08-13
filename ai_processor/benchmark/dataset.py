"""
The benchmark dataset: 3 assignments, 7 students, known-correct grades.

PURE DATA. No Django imports, no ai_processor.services import — so
dataset integrity can be validated without a database or an
OPENROUTER_API_KEY.

## How the expected grades work

Objective questions and computational maths/chemistry have one right
answer, so their expectation is `exact=True` and the assertion is an
exact score match.

Essays and open short-answers do not. Two competent human markers
routinely disagree by one rubric level on the same essay, so an
exact-match assertion there would fail on *correct* behaviour and the
suite would rightly be ignored. Those answers are authored to sit
squarely on one rubric level, and the assertion is "that level or an
adjacent one" (`exact=False`).

## The authoring constraint that bites

AIProcessor._finalize_grading_result snaps every LLM score to the
nearest rubric level. An expected score that is not itself a rubric
level value is therefore UNREACHABLE — the grader could never produce
it no matter how right it was. `iter_expectation_errors()` enforces
this, and tests_grading_benchmark fails loudly if it is ever violated.

## Deliberate probes

Beyond ordinary performance spread, specific cases are planted:

- STUDENTS["fluent_wrong"] writes confident, well-structured, factually
  WRONG answers. This is the failure mode that matters most: a grader
  that rewards style over correctness looks fine on every other student.
- STUDENTS["twin"] shares byte-identical answers with STUDENTS["strong"]
  on two essay questions (see IDENTICAL_ANSWER_PROBES). Two identical
  answers must receive identical scores; today they are two independent
  model calls with no shared state, and nothing in the pipeline would
  notice if they diverged.
- Objective answers are given sometimes as a bare letter, sometimes as
  option text, sometimes as text with the letter prefix — exercising
  ai_processor/objective_grading.py's letter/text handling.
- One answer is correct but paraphrased so heavily it shares almost no
  vocabulary with model_answer (see PARAPHRASE_PROBES), separating
  "understands the content" from "matched keywords".
"""

from dataclasses import dataclass, field

# ── Structures ────────────────────────────────────────────────────────────

OBJECTIVE = "OBJECTIVE"
SHORT_ANSWER = "SHORT-ANSWER"
ESSAY = "ESSAY"


@dataclass(frozen=True)
class AnswerSpec:
    """One student's answer to one question, plus its known-correct grade."""

    question_number: int
    answer_html: str
    expected_points: float
    #: True when there is exactly one defensible score (objective
    #: questions, and computational work with a single right result).
    #: False for open responses, where an adjacent rubric level is also
    #: an acceptable grade.
    exact: bool = False
    #: Why this grade is the right one — read by a human reviewing a
    #: benchmark failure, so be specific.
    note: str = ""


@dataclass(frozen=True)
class BenchmarkAssignment:
    key: str
    subject: str
    title: str
    instructions: str
    questions: list = field(default_factory=list)

    @property
    def total_points(self):
        return sum(q["points"] for q in self.questions)

    def question(self, number):
        for q in self.questions:
            if q["question_number"] == number:
                return q
        raise KeyError(f"{self.key} has no question {number}")

    def as_assignment_questions(self):
        """The JSON that would live in Assignment.questions."""
        return [dict(q) for q in self.questions]


@dataclass(frozen=True)
class BenchmarkStudent:
    key: str
    name: str
    profile: str


def _rubric(*pairs):
    """rubric(("excellent", 20, "..."), ("good", 15, "..."), ...)"""
    return [
        {"level": level, "points": points, "description": description}
        for level, points, description in pairs
    ]


def _objective(number, text, points, options, model_answer, blooms="Apply"):
    return {
        "question_number": number,
        "question_text": text,
        "question_type": OBJECTIVE,
        "question_image": "",
        "points": points,
        "blooms_level": blooms,
        "options": list(options),
        "additional_notes": "",
        # OBJECTIVE questions are scored all-or-nothing and carry no
        # rubric, per the grading prompt's contract.
        "rubric": [],
        "model_answer": model_answer,
    }


def _open(number, text, qtype, points, rubric, model_answer, blooms="Analyze"):
    return {
        "question_number": number,
        "question_text": text,
        "question_type": qtype,
        "question_image": "",
        "points": points,
        "blooms_level": blooms,
        "options": [],
        "additional_notes": "",
        "rubric": rubric,
        "model_answer": model_answer,
    }


# ── Students ──────────────────────────────────────────────────────────────

STUDENTS = [
    BenchmarkStudent(
        "excellent", "Ada Okonkwo", "Consistently top-level work across subjects."
    ),
    BenchmarkStudent(
        "strong", "Bola Adeyemi", "Solid understanding; small slips and omissions."
    ),
    BenchmarkStudent(
        "middling",
        "Chidi Nwosu",
        "Partial grasp; correct method, incomplete execution.",
    ),
    BenchmarkStudent(
        "weak", "Dayo Balogun", "Attempts everything, understands little."
    ),
    BenchmarkStudent(
        "partial", "Efe Idris", "Leaves several questions blank; attempts the rest."
    ),
    BenchmarkStudent(
        "fluent_wrong",
        "Femi Alabi",
        "Fluent, confident, well-structured prose that is factually WRONG. "
        "The adversarial case: a grader fooled by style scores this highly.",
    ),
    BenchmarkStudent(
        "twin",
        "Grace Mensah",
        "Shares byte-identical answers with 'strong' on two essays, to test "
        "cross-student scoring consistency.",
    ),
]

STUDENTS_BY_KEY = {s.key: s for s in STUDENTS}

#: (assignment_key, question_number) pairs where 'twin' and 'strong'
#: submit byte-identical answers. Their scores must match exactly.
IDENTICAL_ANSWER_PROBES = [("maths", 6), ("chemistry", 5)]

#: (assignment_key, student_key, question_number) answers that are fully
#: correct but share almost no wording with model_answer.
PARAPHRASE_PROBES = [("chemistry", "excellent", 6)]


# ── Assignment 1: Advanced Mathematics (8 questions -> BATCHED path) ──────

MATHS = BenchmarkAssignment(
    key="maths",
    subject="Mathematics",
    title="<h1>Advanced Calculus and Algebra — Assessment 3</h1>",
    instructions=(
        "<p>Answer <b>all</b> questions. Show your working: full marks require "
        "justified steps, not just a final answer. Calculators are permitted.</p>"
    ),
    questions=[
        _objective(
            1,
            r"<p>Let $f(x) = x^3 \ln(x)$ for $x > 0$. Which expression gives "
            r"$f'(x)$?</p>",
            3,
            [
                r"A) $3x^2 \ln(x) + x^2$",
                r"B) $3x^2 \ln(x)$",
                r"C) $3x^2 + \frac{1}{x}$",
                r"D) $x^2 \ln(x) + 3x^2$",
            ],
            r"A) $3x^2 \ln(x) + x^2$",
        ),
        _objective(
            2,
            r"<p>Evaluate $\displaystyle\lim_{x \to 0} \frac{\sin(5x)}{3x}$.</p>",
            3,
            [r"A) $\frac{5}{3}$", r"B) $1$", r"C) $\frac{3}{5}$", r"D) $0$"],
            r"A) $\frac{5}{3}$",
        ),
        _open(
            3,
            r"<p>Solve $2x^2 - 7x + 3 = 0$ exactly, showing your method. State "
            r"both roots.</p>",
            SHORT_ANSWER,
            10,
            _rubric(
                (
                    "excellent",
                    10,
                    "<p>Both roots correct ($x = 3$ and $x = \\tfrac{1}{2}$) with "
                    "a complete, valid method shown.</p>",
                ),
                (
                    "good",
                    7,
                    "<p>Correct method and both roots, but working is thin, or one "
                    "root stated without justification.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Valid method started but an arithmetic error yields wrong "
                    "root(s), or only one correct root found.</p>",
                ),
                ("poor", 0, "<p>No valid method, or no response.</p>"),
            ),
            r"<p>Factorising: $2x^2 - 7x + 3 = (2x - 1)(x - 3) = 0$, so "
            r"$x = \tfrac{1}{2}$ or $x = 3$. Equivalently by the quadratic "
            r"formula with $a=2, b=-7, c=3$: "
            r"$x = \frac{7 \pm \sqrt{49 - 24}}{4} = \frac{7 \pm 5}{4}$.</p>",
        ),
        _open(
            4,
            r"<p>Evaluate $\displaystyle\int_{0}^{2} x\,e^{x^2}\,dx$ using a "
            r"substitution. Show the substitution explicitly and give an exact "
            r"answer.</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>Substitution $u = x^2$ stated, limits correctly changed to "
                    "$0 \\to 4$, exact answer $\\tfrac{1}{2}(e^4 - 1)$.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct substitution and answer, but limits handled loosely "
                    "or a step omitted.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Substitution attempted but $du$ mishandled or limits not "
                    "changed, giving a wrong result.</p>",
                ),
                ("poor", 0, "<p>No valid substitution, or no response.</p>"),
            ),
            r"<p>Let $u = x^2$, so $du = 2x\,dx$ and $x\,dx = \tfrac{1}{2}du$. "
            r"When $x=0, u=0$; when $x=2, u=4$. The integral becomes "
            r"$\tfrac{1}{2}\int_0^4 e^u du = \tfrac{1}{2}[e^u]_0^4 = "
            r"\tfrac{1}{2}(e^4 - 1)$.</p>",
        ),
        _open(
            5,
            r"<p>A spherical balloon is inflated so its volume increases at "
            r"$100\ \text{cm}^3/\text{s}$. Find the rate at which the radius is "
            r"increasing when $r = 5\ \text{cm}$. Give units.</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>$V = \\tfrac{4}{3}\\pi r^3$ differentiated correctly, chain "
                    "rule applied, answer $\\tfrac{1}{\\pi}\\ \\text{cm/s}$ "
                    "(≈0.318) with units.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct setup and method, right answer but units omitted or "
                    "arithmetic slightly slipped.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Correct volume formula but chain rule misapplied or "
                    "$dV/dt$ and $dr/dt$ confused.</p>",
                ),
                ("poor", 0, "<p>No valid setup, or no response.</p>"),
            ),
            r"<p>$V = \tfrac{4}{3}\pi r^3 \Rightarrow \frac{dV}{dt} = "
            r"4\pi r^2 \frac{dr}{dt}$. At $r=5$: $100 = 4\pi(25)\frac{dr}{dt}$, "
            r"so $\frac{dr}{dt} = \frac{100}{100\pi} = \frac{1}{\pi} \approx "
            r"0.318\ \text{cm/s}$.</p>",
        ),
        _open(
            6,
            r"<p>State the Mean Value Theorem precisely. Explain why <em>both</em> "
            r"of its hypotheses are necessary, giving a specific counterexample "
            r"for each hypothesis when it is dropped.</p>",
            ESSAY,
            20,
            _rubric(
                (
                    "excellent",
                    20,
                    "<p>MVT stated precisely (continuity on $[a,b]$, "
                    "differentiability on $(a,b)$, conclusion $\\exists c$ with "
                    "$f'(c) = \\frac{f(b)-f(a)}{b-a}$). Both hypotheses explained "
                    "with valid, specific counterexamples.</p>",
                ),
                (
                    "good",
                    15,
                    "<p>Statement correct; both hypotheses discussed but one "
                    "counterexample is vague, or the open/closed interval "
                    "distinction is blurred.</p>",
                ),
                (
                    "fair",
                    8,
                    "<p>Rough statement of the theorem; only one hypothesis "
                    "addressed, or counterexamples missing/invalid.</p>",
                ),
                (
                    "poor",
                    0,
                    "<p>Theorem misstated or absent; no meaningful explanation.</p>",
                ),
            ),
            r"<p>MVT: if $f$ is continuous on $[a,b]$ and differentiable on "
            r"$(a,b)$, there exists $c \in (a,b)$ with $f'(c) = "
            r"\frac{f(b)-f(a)}{b-a}$. Continuity is needed: $f(x)=\lfloor x "
            r"\rfloor$ on $[0,1]$ has a jump and no such $c$. Differentiability "
            r"is needed: $f(x)=|x|$ on $[-1,1]$ is continuous with average slope "
            r"$0$, but $f'$ is never $0$ where it exists.</p>",
            blooms="Evaluate",
        ),
        _objective(
            7,
            r"<p>What is the determinant of $\begin{pmatrix} 2 & -1 \\ 4 & 3 "
            r"\end{pmatrix}$?</p>",
            3,
            ["A) $2$", "B) $10$", "C) $6$", "D) $-10$"],
            "B) $10$",
        ),
        _open(
            8,
            r"<p>Determine whether $\displaystyle\sum_{n=1}^{\infty} "
            r"\frac{n!}{n^n}$ converges. Name the test you use and justify the "
            r"conclusion.</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>Ratio test applied correctly, limit shown to be "
                    "$\\tfrac{1}{e} < 1$, conclusion 'converges' stated.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct test and conclusion, but the limit evaluation is "
                    "asserted rather than justified.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>A plausible test named but misapplied, or correct "
                    "conclusion with no supporting work.</p>",
                ),
                (
                    "poor",
                    0,
                    "<p>Wrong conclusion with no valid reasoning, or no "
                    "response.</p>",
                ),
            ),
            r"<p>Ratio test: $\frac{a_{n+1}}{a_n} = \frac{(n+1)!}{(n+1)^{n+1}} "
            r"\cdot \frac{n^n}{n!} = \frac{n^n}{(n+1)^n} = "
            r"\left(\frac{n}{n+1}\right)^n \to \frac{1}{e} \approx 0.368 < 1$. "
            r"Since the limit is less than 1, the series converges.</p>",
        ),
        # Q9 exists to push this assignment's LLM-BOUND question count past
        # GRADING_QUESTIONS_PER_CHUNK (5) so it takes the BATCHED path.
        # Without it, tier 0 claims the 3 objectives, leaving exactly 5 for
        # the model — and every assignment in the benchmark would quietly
        # take the single-pass path, leaving batching (per-batch retries,
        # out-of-batch filtering, per-batch completeness) untested.
        _open(
            9,
            r"<p>The curve $x^3 + y^3 = 6xy$ (the folium of Descartes) passes "
            r"through the point $(3,3)$. Find $\frac{dy}{dx}$ at that point "
            r"using implicit differentiation.</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>Implicit differentiation carried out correctly including "
                    "the product rule on $6xy$, rearranged for "
                    "$\\frac{dy}{dx}$, evaluated to $-1$ at $(3,3)$.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct differentiation and correct value, but the "
                    "rearrangement is compressed or the general expression is "
                    "not stated before substituting.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Implicit differentiation attempted but the product rule "
                    "on $6xy$ is mishandled, or $\\frac{dy}{dx}$ terms are not "
                    "collected, giving a wrong value.</p>",
                ),
                (
                    "poor",
                    0,
                    "<p>Differentiates as if $y$ were constant, or no " "response.</p>",
                ),
            ),
            r"<p>Differentiating both sides with respect to $x$: "
            r"$3x^2 + 3y^2\frac{dy}{dx} = 6y + 6x\frac{dy}{dx}$. "
            r"Collecting: $\frac{dy}{dx}(3y^2 - 6x) = 6y - 3x^2$, so "
            r"$\frac{dy}{dx} = \frac{6y - 3x^2}{3y^2 - 6x} = "
            r"\frac{2y - x^2}{y^2 - 2x}$. At $(3,3)$: "
            r"$\frac{6 - 9}{9 - 6} = \frac{-3}{3} = -1$.</p>",
        ),
    ],
)


# ── Assignment 2: Chemistry (6 questions -> BATCHED path) ─────────────────

CHEMISTRY = BenchmarkAssignment(
    key="chemistry",
    subject="Chemistry",
    title="<h1>Physical and Organic Chemistry — Assessment 2</h1>",
    instructions=(
        "<p>Answer <b>all</b> questions. Show working and include units where "
        "applicable. Give equations in balanced form.</p>"
    ),
    questions=[
        _objective(
            1,
            r"<p>What is the conjugate base of sulfuric acid, $H_2SO_4$?</p>",
            3,
            [
                r"A) $SO_4^{2-}$",
                r"B) $HSO_4^{-}$",
                r"C) $H_3SO_4^{+}$",
                r"D) $H_2SO_3$",
            ],
            r"B) $HSO_4^{-}$",
            blooms="Understand",
        ),
        _objective(
            2,
            r"<p>For the exothermic equilibrium $N_2(g) + 3H_2(g) \rightleftharpoons "
            r"2NH_3(g)$, what happens to the yield of $NH_3$ if the temperature is "
            r"increased at constant pressure?</p>",
            3,
            [
                "A) It increases",
                "B) It decreases",
                "C) It is unchanged",
                "D) The reaction stops",
            ],
            "B) It decreases",
            blooms="Apply",
        ),
        _open(
            3,
            r"<p>$5.00\ \text{g}$ of $CaCO_3$ reacts with $50.0\ \text{cm}^3$ of "
            r"$1.00\ \text{mol dm}^{-3}$ $HCl$. Identify the limiting reagent and "
            r"calculate the mass of $CO_2$ produced. "
            r"($M_r$: $CaCO_3 = 100.1$, $CO_2 = 44.0$)</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>Balanced equation, both mole quantities correct, HCl "
                    "correctly identified as limiting, mass of $CO_2$ ≈"
                    "$1.10\\ \\text{g}$ with working.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct limiting reagent and approach; minor arithmetic or "
                    "rounding slip, or equation unbalanced.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Moles calculated but limiting reagent wrongly identified, "
                    "giving an incorrect mass.</p>",
                ),
                ("poor", 0, "<p>No valid stoichiometric method, or no response.</p>"),
            ),
            r"<p>$CaCO_3 + 2HCl \rightarrow CaCl_2 + H_2O + CO_2$. "
            r"$n(CaCO_3) = 5.00/100.1 = 0.0500\ \text{mol}$; "
            r"$n(HCl) = 0.0500 \times 1.00 = 0.0500\ \text{mol}$. "
            r"$HCl$ requires 2 mol per mol $CaCO_3$, so $0.0500$ mol HCl reacts "
            r"with only $0.0250$ mol $CaCO_3$ — HCl is limiting. "
            r"$n(CO_2) = 0.0250\ \text{mol}$, mass $= 0.0250 \times 44.0 = "
            r"1.10\ \text{g}$.</p>",
        ),
        _open(
            4,
            r"<p>At equilibrium a $2.00\ \text{dm}^3$ vessel contains "
            r"$0.200\ \text{mol}$ $H_2$, $0.200\ \text{mol}$ $I_2$ and "
            r"$1.60\ \text{mol}$ $HI$ for $H_2 + I_2 \rightleftharpoons 2HI$. "
            r"Calculate $K_c$ and state its units.</p>",
            SHORT_ANSWER,
            12,
            _rubric(
                (
                    "excellent",
                    12,
                    "<p>Concentrations computed from moles/volume, correct $K_c$ "
                    "expression, $K_c = 64$, correctly stated as dimensionless.</p>",
                ),
                (
                    "good",
                    8,
                    "<p>Correct value but units mishandled, or concentrations used "
                    "without dividing by volume yet the ratio still resolves.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Correct expression but arithmetic error, or moles used "
                    "directly with an incorrect result.</p>",
                ),
                ("poor", 0, "<p>Wrong expression entirely, or no response.</p>"),
            ),
            r"<p>$[H_2] = [I_2] = 0.100\ \text{mol dm}^{-3}$, "
            r"$[HI] = 0.800\ \text{mol dm}^{-3}$. "
            r"$K_c = \frac{[HI]^2}{[H_2][I_2]} = \frac{0.800^2}{0.100 \times 0.100} "
            r"= \frac{0.64}{0.01} = 64$. Units cancel, so $K_c$ is "
            r"dimensionless.</p>",
        ),
        _open(
            5,
            r"<p>Compare the $S_N1$ and $S_N2$ mechanisms. Address rate equations, "
            r"stereochemical outcome, and the effect of substrate structure, and "
            r"explain <em>why</em> each difference arises.</p>",
            ESSAY,
            20,
            _rubric(
                (
                    "excellent",
                    20,
                    "<p>Both mechanisms correct; rate = k[substrate] vs "
                    "k[substrate][nucleophile]; racemisation via planar carbocation "
                    "vs inversion via backside attack; tertiary favours $S_N1$ and "
                    "primary favours $S_N2$, with causal explanation throughout.</p>",
                ),
                (
                    "good",
                    15,
                    "<p>All three axes covered accurately but one explanation is "
                    "descriptive rather than causal.</p>",
                ),
                (
                    "fair",
                    8,
                    "<p>Mechanisms distinguished but with a significant error, or "
                    "only one or two axes addressed.</p>",
                ),
                (
                    "poor",
                    0,
                    "<p>Mechanisms confused or reversed; no valid comparison.</p>",
                ),
            ),
            r"<p>$S_N1$ is two-step: rate-determining ionisation to a carbocation "
            r"gives rate $= k[\text{substrate}]$, independent of nucleophile. The "
            r"planar intermediate is attacked from either face, so a chiral centre "
            r"racemises. Tertiary substrates are favoured because alkyl groups "
            r"stabilise the carbocation. $S_N2$ is concerted: rate $= "
            r"k[\text{substrate}][\text{Nu}]$, backside attack inverts "
            r"configuration (Walden inversion), and primary substrates are favoured "
            r"because bulky groups block the trajectory.</p>",
            blooms="Analyze",
        ),
        _open(
            6,
            r"<p>Given $\Delta H_f^\circ$: $CH_4(g) = -74.8$, $CO_2(g) = -393.5$, "
            r"$H_2O(l) = -285.8\ \text{kJ mol}^{-1}$, use Hess's law to calculate "
            r"$\Delta H^\circ$ for the complete combustion of methane.</p>",
            SHORT_ANSWER,
            10,
            _rubric(
                (
                    "excellent",
                    10,
                    "<p>Balanced combustion equation, correct Hess cycle "
                    "(products − reactants), answer $-890.3\\ \\text{kJ mol}^{-1}$ "
                    "with sign and units.</p>",
                ),
                (
                    "good",
                    7,
                    "<p>Correct method and magnitude; sign or units slipped, or the "
                    "$\\times 2$ on water omitted from the shown working but "
                    "present in the result.</p>",
                ),
                (
                    "fair",
                    4,
                    "<p>Hess's law invoked but reactants and products reversed, or "
                    "stoichiometric coefficients ignored.</p>",
                ),
                ("poor", 0, "<p>No valid method, or no response.</p>"),
            ),
            r"<p>$CH_4 + 2O_2 \rightarrow CO_2 + 2H_2O$. "
            r"$\Delta H^\circ = \sum \Delta H_f^\circ(\text{products}) - "
            r"\sum \Delta H_f^\circ(\text{reactants}) = "
            r"[(-393.5) + 2(-285.8)] - [(-74.8) + 0] = -965.1 + 74.8 = "
            r"-890.3\ \text{kJ mol}^{-1}$.</p>",
        ),
    ],
)


# ── Assignment 3: History & Literature (4 questions -> SINGLE-PASS path) ──

HUMANITIES = BenchmarkAssignment(
    key="humanities",
    subject="History and Literature",
    title="<h1>History and Literature — Comparative Essay Paper</h1>",
    instructions=(
        "<p>Answer <b>all</b> questions. Essays are marked on argument, use of "
        "evidence, and engagement with counter-positions — not length.</p>"
    ),
    questions=[
        _open(
            1,
            "<p>“The First World War was the result of systemic alliance "
            "structures, not the decisions of individuals.” Evaluate this claim "
            "with reference to the historiographical debate.</p>",
            ESSAY,
            25,
            _rubric(
                (
                    "excellent",
                    25,
                    "<p>Sustained argument engaging both structural and "
                    "individual-agency positions; names specific historiography "
                    "(e.g. Fischer, Clark); uses concrete evidence; reaches a "
                    "defended judgement.</p>",
                ),
                (
                    "good",
                    18,
                    "<p>Clear argument with real evidence and some awareness of the "
                    "debate, but historiography is thin or the counter-position is "
                    "handled briefly.</p>",
                ),
                (
                    "fair",
                    10,
                    "<p>Narrative account of the war's outbreak with limited "
                    "argument; little engagement with the claim itself.</p>",
                ),
                (
                    "poor",
                    0,
                    "<p>Largely irrelevant, factually incorrect, or no response.</p>",
                ),
            ),
            "<p>A strong answer weighs structural explanations (the alliance "
            "system, mobilisation timetables, the security dilemma) against "
            "agency-focused ones (Fischer's thesis of deliberate German war aims; "
            "Clark's <em>Sleepwalkers</em> emphasising contingent decisions by "
            "identifiable actors), uses the July Crisis as evidence, and reaches a "
            "defended judgement rather than asserting a balance.</p>",
            blooms="Evaluate",
        ),
        _open(
            2,
            "<p>In <em>Macbeth</em>, is Macbeth's downfall driven by his own "
            "ambition or by forces beyond his control? Argue a case using close "
            "reference to the text.</p>",
            ESSAY,
            25,
            _rubric(
                (
                    "excellent",
                    25,
                    "<p>Clear thesis; close textual reference including quotation; "
                    "engages the witches/fate counter-reading; analyses language "
                    "rather than retelling plot.</p>",
                ),
                (
                    "good",
                    18,
                    "<p>Clear argument with textual support, but analysis is "
                    "occasionally descriptive or the counter-reading is asserted "
                    "rather than examined.</p>",
                ),
                (
                    "fair",
                    10,
                    "<p>Mostly plot summary with an implied argument; minimal "
                    "quotation or analysis.</p>",
                ),
                ("poor", 0, "<p>Misreads the play, or no response.</p>"),
            ),
            "<p>A strong answer argues a position — for instance that the witches "
            "supply opportunity rather than compulsion, since Macbeth's "
            "“vaulting ambition” and Lady Macbeth's goading precede any "
            "irreversible act — and reads the language of the dagger soliloquy "
            "and “tomorrow, and tomorrow” closely rather than narrating events.</p>",
            blooms="Evaluate",
        ),
        _open(
            3,
            "<p>Define <em>Realpolitik</em> and explain its significance in "
            "nineteenth-century European statecraft, with one concrete "
            "example.</p>",
            SHORT_ANSWER,
            15,
            _rubric(
                (
                    "excellent",
                    15,
                    "<p>Accurate definition (policy driven by practical power "
                    "considerations rather than ideology or morality), clear "
                    "significance, and a precise, correct example.</p>",
                ),
                (
                    "good",
                    10,
                    "<p>Sound definition and example; significance stated but "
                    "underdeveloped.</p>",
                ),
                (
                    "fair",
                    5,
                    "<p>Vague or partially incorrect definition; example missing or "
                    "only loosely relevant.</p>",
                ),
                ("poor", 0, "<p>Incorrect definition, or no response.</p>"),
            ),
            "<p><em>Realpolitik</em> is politics conducted on the basis of "
            "practical power and material circumstance rather than ideological or "
            "moral principle. Bismarck exemplifies it: he engineered the "
            "Austro-Prussian and Franco-Prussian wars to unify Germany under "
            "Prussian leadership, then reversed course to preserve the resulting "
            "balance through alliances.</p>",
            blooms="Understand",
        ),
        _open(
            4,
            "<p>Two accounts of the same 1848 uprising survive: a government "
            "dispatch describing “a criminal mob”, and a participant's diary "
            "describing “the people in arms”. Explain how a historian should use "
            "these sources.</p>",
            SHORT_ANSWER,
            15,
            _rubric(
                (
                    "excellent",
                    15,
                    "<p>Treats both as evidence of perspective rather than ranking "
                    "one as true; identifies each author's position and interest; "
                    "explains corroboration against independent evidence.</p>",
                ),
                (
                    "good",
                    10,
                    "<p>Recognises bias in both and suggests cross-checking, but "
                    "the method is stated generally.</p>",
                ),
                (
                    "fair",
                    5,
                    "<p>Notes that sources disagree and picks one as more reliable "
                    "without adequate justification.</p>",
                ),
                ("poor", 0, "<p>No source-critical reasoning, or no response.</p>"),
            ),
            "<p>Neither is simply true or false: each is reliable evidence of its "
            "author's position. The historian establishes provenance and interest "
            "(an official justifying suppression; a participant justifying "
            "action), reads the loaded language as data about perspective, and "
            "corroborates factual claims — timing, numbers, casualties — against "
            "independent records.</p>",
            blooms="Analyze",
        ),
    ],
)


ASSIGNMENTS = [MATHS, CHEMISTRY, HUMANITIES]
ASSIGNMENTS_BY_KEY = {a.key: a for a in ASSIGNMENTS}


# ── Integrity validation ──────────────────────────────────────────────────


def allowed_scores(question):
    """
    Every score the grader can actually produce for this question.

    For OBJECTIVE this is {0, points} — all-or-nothing. For anything else
    it is the rubric's level values plus 0, because
    _finalize_grading_result snaps to the nearest rubric level and always
    permits 0 (a skipped answer must stay expressible even when the
    rubric floor is non-zero).
    """
    if question["question_type"] == OBJECTIVE:
        return {0.0, float(question["points"])}
    values = {float(level["points"]) for level in question["rubric"]}
    values.add(0.0)
    return values


def iter_dataset_errors():
    """
    Yield a human-readable string per structural problem in the dataset.

    Imported by tests_grading_benchmark. Kept here (rather than in the
    test) so `manage.py grading_benchmark` can refuse to run a paid live
    pass against a dataset that is internally inconsistent.
    """
    required = {
        "question_number",
        "question_text",
        "question_type",
        "question_image",
        "points",
        "blooms_level",
        "options",
        "additional_notes",
        "rubric",
        "model_answer",
    }

    for assignment in ASSIGNMENTS:
        seen_numbers = set()
        for question in assignment.questions:
            number = question.get("question_number")
            where = f"{assignment.key} Q{number}"

            missing = required - set(question)
            if missing:
                yield f"{where}: missing field(s) {sorted(missing)}"
            if number in seen_numbers:
                yield f"{where}: duplicate question_number"
            seen_numbers.add(number)

            qtype = question.get("question_type")
            if qtype not in (OBJECTIVE, SHORT_ANSWER, ESSAY):
                yield f"{where}: bad question_type {qtype!r}"
            if not question.get("points"):
                yield f"{where}: points must be > 0"

            if qtype == OBJECTIVE:
                if question["rubric"]:
                    yield f"{where}: OBJECTIVE must carry an empty rubric"
                options = question.get("options") or []
                if len(options) < 2:
                    yield f"{where}: OBJECTIVE needs at least 2 options"
                if question["model_answer"] not in options:
                    yield (
                        f"{where}: model_answer is not one of the options — "
                        "deterministic matching would defer every answer"
                    )
                # Two options that are the same string make the question
                # unanswerable and would silently poison the metric.
                if len(set(options)) != len(options):
                    yield f"{where}: duplicate options"
            else:
                if question.get("options"):
                    yield f"{where}: non-OBJECTIVE must have empty options"
                levels = question.get("rubric") or []
                if len(levels) != 4:
                    yield (f"{where}: expected a 4-level rubric, got {len(levels)}")
                if (
                    levels
                    and max(level["points"] for level in levels) != question["points"]
                ):
                    yield (
                        f"{where}: top rubric level must equal the question's "
                        f"points ({question['points']})"
                    )
                level_points = [level["points"] for level in levels]
                if level_points != sorted(level_points, reverse=True):
                    yield f"{where}: rubric levels must descend by points"
                if len(set(level_points)) != len(level_points):
                    yield f"{where}: duplicate rubric level point values"


def iter_expectation_errors():
    """
    Yield a string per unreachable or mismatched expected grade.

    The load-bearing check is the first one: an expected score that is
    not a value the grader can produce (because scores are snapped to
    rubric levels) would fail forever, and the failure would look like a
    model problem rather than an authoring mistake.
    """
    # Imported lazily: submissions imports this module, so a top-level
    # import here would be circular.
    from ai_processor.benchmark import submissions as _submissions

    for assignment in ASSIGNMENTS:
        numbers = {q["question_number"] for q in assignment.questions}
        for student in STUDENTS:
            try:
                specs = _submissions.answers_for(assignment.key, student.key)
            except KeyError:
                yield f"{assignment.key}: no answers for student {student.key!r}"
                continue

            answered = [spec.question_number for spec in specs]
            if set(answered) != numbers:
                yield (
                    f"{assignment.key}/{student.key}: answers cover "
                    f"{sorted(set(answered))}, questions are {sorted(numbers)}"
                )
            if len(answered) != len(set(answered)):
                yield f"{assignment.key}/{student.key}: duplicate answers"

            for spec in specs:
                if spec.question_number not in numbers:
                    continue
                question = assignment.question(spec.question_number)
                where = f"{assignment.key}/{student.key} Q{spec.question_number}"
                permitted = allowed_scores(question)
                if float(spec.expected_points) not in permitted:
                    yield (
                        f"{where}: expected {spec.expected_points} is not a "
                        f"reachable score — scores snap to rubric levels, so "
                        f"only {sorted(permitted)} can ever be produced"
                    )
                if question["question_type"] == OBJECTIVE and not spec.exact:
                    yield f"{where}: OBJECTIVE expectations must be exact=True"
                if not spec.answer_html.strip() and spec.expected_points != 0:
                    yield f"{where}: blank answer expects non-zero points"
                if not spec.note:
                    yield f"{where}: every expectation needs a note explaining it"

    # The consistency probe is only meaningful if the answers really are
    # byte-identical — a stray edit to one copy would turn a hard
    # assertion into a silently vacuous one.
    for assignment_key, number in IDENTICAL_ANSWER_PROBES:
        pair = []
        for student_key in ("strong", "twin"):
            specs = _submissions.answers_for(assignment_key, student_key)
            match = [s for s in specs if s.question_number == number]
            if not match:
                yield (
                    f"{assignment_key}/{student_key} Q{number}: identical-answer "
                    "probe references a question this student did not answer"
                )
                continue
            pair.append(match[0])
        if len(pair) == 2:
            if pair[0].answer_html != pair[1].answer_html:
                yield (
                    f"{assignment_key} Q{number}: 'strong' and 'twin' answers are "
                    "NOT byte-identical, so the consistency probe proves nothing"
                )
            if pair[0].expected_points != pair[1].expected_points:
                yield (
                    f"{assignment_key} Q{number}: identical answers must carry "
                    "the same expected score"
                )


def iter_all_errors():
    yield from iter_dataset_errors()
    yield from iter_expectation_errors()
