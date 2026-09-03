# flake8: noqa: B907
#
# B907 ("manually surrounded by quotes, consider !r") is suppressed for this
# whole module. Every hit is a double-quoted HTML or SVG attribute — markup
# this file exists to emit — and !r would produce Python repr quoting, which
# is not valid markup. The check is right in general and wrong here.
"""
A self-contained HTML report of benchmark trends.

Everything is inlined — CSS, and charts drawn as hand-written SVG. There are
no <script> tags, no web fonts and no CDN links, so the file opens correctly
with networking switched off and can be emailed to someone who does not use a
terminal. A test asserts that self-containment rather than trusting it.

SVG by hand rather than a charting library: the project's standing convention
(scoring.py) is to avoid new dependencies for small jobs, and these are line
charts of a dozen points.

THE CHART IS A CONTROL CHART. The shaded band behind each line is the metric's
normal range, so "is this run unusual?" is answered by whether the last point
sits inside the shading — visible at a glance, without comparing numbers. That
is the whole question this report exists to answer, so it gets the strongest
visual treatment on the page.
"""

import html
from datetime import datetime, timezone

from ai_processor.benchmark import analysis

_CSS = """
:root {
  --ground:#f6f7f9; --surface:#ffffff; --line:#e3e6ec;
  --ink:#16181d; --muted:#666d78; --faint:#8a919c;
  --band:#dfe4ec; --accent:#1f5fd0; --accent-soft:#e8effb;
  --good:#1d6b45; --good-bg:#e6f2eb;
  --warn:#b4471f; --warn-bg:#fbeae3;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground:#101317; --surface:#171b21; --line:#262c35;
    --ink:#e6e9ee; --muted:#98a0ad; --faint:#79818e;
    --band:#232935; --accent:#6fa2ff; --accent-soft:#1b2740;
    --good:#5fbf8e; --good-bg:#15291f;
    --warn:#e8845c; --warn-bg:#2e1c15;
  }
}
:root[data-theme="dark"] {
  --ground:#101317; --surface:#171b21; --line:#262c35;
  --ink:#e6e9ee; --muted:#98a0ad; --faint:#79818e;
  --band:#232935; --accent:#6fa2ff; --accent-soft:#1b2740;
  --good:#5fbf8e; --good-bg:#15291f;
  --warn:#e8845c; --warn-bg:#2e1c15;
}

* { box-sizing:border-box; }
body {
  margin:0; padding:3rem 1.25rem 5rem;
  background:var(--ground); color:var(--ink);
  font:15px/1.6 var(--sans);
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:900px; margin:0 auto; }

.eyebrow {
  font:600 11px/1 var(--mono); letter-spacing:.14em; text-transform:uppercase;
  color:var(--faint); margin:0 0 .85rem;
}
h1 {
  font-size:1.75rem; line-height:1.15; letter-spacing:-.02em;
  margin:0 0 .5rem; text-wrap:balance;
}
.lede { color:var(--muted); margin:0 0 2.5rem; max-width:62ch; }
h2 {
  font-size:.95rem; letter-spacing:-.005em; margin:3rem 0 .35rem;
  padding-bottom:.6rem; border-bottom:1px solid var(--line);
}
h2 + .lede { margin:.7rem 0 1.25rem; font-size:.9rem; }

.rows { display:flex; flex-direction:column; gap:.6rem; }
.row {
  background:var(--surface); border:1px solid var(--line);
  border-radius:8px; padding:.9rem 1.05rem 1rem;
}
.row-head {
  display:flex; justify-content:space-between; align-items:baseline;
  gap:1rem; margin-bottom:.65rem;
}
.row-name { font-weight:600; font-size:.95rem; }
.chip {
  font:600 10px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase;
  padding:.3rem .5rem; border-radius:4px; white-space:nowrap;
}
.chip-good { background:var(--good-bg); color:var(--good); }
.chip-warn { background:var(--warn-bg); color:var(--warn); }
.chip-none { background:var(--accent-soft); color:var(--muted); }

.readout {
  font:12px/1.5 var(--mono); color:var(--muted);
  font-variant-numeric:tabular-nums; margin-top:.6rem;
}
.readout b { color:var(--ink); font-weight:600; }
.readout .out { color:var(--warn); font-weight:600; }

table { border-collapse:collapse; width:100%; font-size:.85rem; }
th, td {
  text-align:left; padding:.5rem .65rem; border-bottom:1px solid var(--line);
  white-space:nowrap;
}
th {
  font:600 10px/1 var(--mono); letter-spacing:.09em; text-transform:uppercase;
  color:var(--faint);
}
td { font-variant-numeric:tabular-nums; }
td.mono, th.mono { font-family:var(--mono); font-size:.8rem; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr.flagged td:first-child { box-shadow:inset 2px 0 0 var(--warn); }
a { color:var(--accent); }

.note {
  background:var(--warn-bg); border:1px solid var(--warn);
  border-left-width:3px; border-radius:6px;
  padding:.8rem 1rem; margin-bottom:1.75rem; font-size:.88rem;
}
.note b { color:var(--warn); }
.empty { color:var(--muted); font-style:italic; font-size:.9rem; }
.scroll { overflow-x:auto; }
svg { display:block; width:100%; height:auto; }

/* Chart styling lives here, NOT in SVG presentation attributes: browsers do
   not resolve var() inside attributes like fill="", so the colours would
   silently fail to apply and the chart would render unstyled. */
.c-band { fill:var(--band); }
.c-mean { stroke:var(--faint); stroke-width:1; stroke-dasharray:3 3; opacity:.6; }
.c-series { fill:none; stroke:var(--accent); stroke-width:1.75;
            stroke-linejoin:round; stroke-linecap:round; }
.c-dot { fill:var(--surface); stroke:var(--accent); stroke-width:1.5; }
.c-last { fill:var(--accent); }
.c-last.out { fill:var(--warn); }
.c-axis { fill:var(--faint); font-family:var(--mono); font-size:10px; }
footer {
  margin-top:3.5rem; padding-top:1.25rem; border-top:1px solid var(--line);
  color:var(--faint); font-size:.82rem; max-width:62ch;
}
"""


def _esc(value):
    return html.escape("" if value is None else str(value))


def _fmt(value, kind):
    if value is None:
        return "n/a"
    if kind == "rate":
        return f"{value * 100:.1f}%"
    if kind == "count":
        return f"{value:,.0f}"
    return f"{value:.4g}"


def _control_chart(values, band, kind, width=820, height=104):
    """
    A control chart: the normal range as a shaded band, the run history as a
    line, the most recent point emphasised and coloured by whether it sits
    inside the band. Reading it requires no numbers.
    """
    points = [v for v in values if v is not None]
    if len(points) < 2:
        return '<p class="empty">Not enough runs to plot yet.</p>'

    pad_y, pad_x = 14, 10
    # The vertical scale must cover the data AND the band, or an out-of-range
    # point would be clipped off the chart — exactly the case worth seeing.
    lows = [min(points)] + ([band["normal_low"]] if band else [])
    highs = [max(points)] + ([band["normal_high"]] if band else [])
    low, high = min(lows), max(highs)

    flat = high == low
    if flat:
        low, high = low - 1, high + 1
    span = high - low

    def y_of(value):
        return height - pad_y - ((value - low) / span) * (height - 2 * pad_y)

    step = (width - 2 * pad_x) / (len(points) - 1)
    coords = [(pad_x + i * step, y_of(v)) for i, v in enumerate(points)]

    parts = []

    if band and not flat:
        top, bottom = y_of(band["normal_high"]), y_of(band["normal_low"])
        parts.append(
            f'<rect class="c-band" x="0" y="{top:.1f}" width="{width}" '
            f'height="{max(1.0, bottom - top):.1f}"/>'
        )
        mean_y = y_of(band["mean"])
        parts.append(
            f'<line class="c-mean" x1="0" y1="{mean_y:.1f}" '
            f'x2="{width}" y2="{mean_y:.1f}"/>'
        )

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords)
    )
    parts.append(f'<path class="c-series" d="{path}"/>')

    for x, y in coords[:-1]:
        parts.append(f'<circle class="c-dot" cx="{x:.1f}" cy="{y:.1f}" r="2.5"/>')

    last_x, last_y = coords[-1]
    outside = bool(
        band and not (band["normal_low"] <= points[-1] <= band["normal_high"])
    )
    parts.append(
        f'<circle class="c-last{" out" if outside else ""}" '
        f'cx="{last_x:.1f}" cy="{last_y:.1f}" r="4.5"/>'
    )

    if flat:
        parts.append(
            f'<text class="c-axis" x="{pad_x}" y="12">'
            f"{_esc(_fmt(points[0], kind))} — unchanged every run</text>"
        )
    else:
        parts.append(
            f'<text class="c-axis" x="{pad_x}" y="10">'
            f"{_esc(_fmt(high, kind))}</text>"
            f'<text class="c-axis" x="{pad_x}" y="{height - 3}">'
            f"{_esc(_fmt(low, kind))}</text>"
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="control chart, {len(points)} runs">' + "".join(parts) + "</svg>"
    )


def _chip(assessment):
    if assessment is None:
        return '<span class="chip chip-none">no verdict yet</span>'
    if assessment["unusual"]:
        return '<span class="chip chip-warn">outside normal</span>'
    return '<span class="chip chip-good">normal</span>'


def _readout(entry, kind):
    band, verdict = entry.get("band"), entry.get("assessment")
    if band:
        range_text = (
            f'normal {_fmt(band["normal_low"], kind)}–'
            f'{_fmt(band["normal_high"], kind)} over {band["runs"]} runs'
        )
    else:
        range_text = (
            f"needs {analysis.MIN_RUNS_FOR_BAND}+ runs before a normal range means "
            "anything"
        )

    if verdict is None:
        return f'<div class="readout">{range_text}</div>'

    multiple = (
        ""
        if verdict["sigmas"] is None
        else f' · {abs(verdict["sigmas"]):.1f}× the usual spread'
    )
    latest_class = "out" if verdict["unusual"] else "b"
    latest = (
        f'<span class="{latest_class}">{_fmt(verdict["latest"], kind)}</span>'
        if verdict["unusual"]
        else f'<b>{_fmt(verdict["latest"], kind)}</b>'
    )
    return (
        f'<div class="readout">latest {latest} '
        f'vs {_fmt(verdict["baseline_mean"], kind)} average of the previous '
        f'{verdict["baseline_runs"]}{multiple} · {range_text}</div>'
    )


def _metric_rows(trend_report):
    rows = []
    for metric, _label, kind in analysis.TRENDED_METRICS:
        entry = trend_report["metrics"].get(metric) or {}
        values = [value for _run_id, value in entry.get("series") or []]
        if not values:
            continue
        rows.append(
            '<div class="row"><div class="row-head">'
            f'<span class="row-name">{_esc(entry.get("label") or metric)}</span>'
            f'{_chip(entry.get("assessment"))}</div>'
            f'{_control_chart(values, entry.get("band"), kind)}'
            f"{_readout(entry, kind)}</div>"
        )
    return '<div class="rows">' + "".join(rows) + "</div>"


def _runs_table(runs):
    if not runs:
        return '<p class="empty">No runs recorded yet.</p>'
    body = []
    for run in sorted(runs, key=lambda r: r.get("run_id") or "", reverse=True):
        metrics = run.get("metrics") or {}
        archive = run.get("archive_url")
        body.append(
            "<tr>"
            f'<td class="mono">{_esc(run.get("run_id"))}</td>'
            f'<td class="mono">{_esc(run.get("mode"))}</td>'
            f'<td class="num">{_esc(run.get("questions_graded"))}</td>'
            f'<td class="num">{_fmt(metrics.get("exact_rate"), "rate")}</td>'
            f'<td class="num">'
            f'{_fmt(metrics.get("within_one_level_rate"), "rate")}</td>'
            f'<td class="num">'
            f'{_fmt(metrics.get("evidence_verified_rate"), "rate")}</td>'
            f'<td class="num">{_fmt(metrics.get("total_tokens"), "count")}</td>'
            f'<td>{f"<a href=\"{_esc(archive)}\">raw data</a>" if archive else "—"}</td>'
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>run</th><th>mode</th>"
        '<th class="num">questions</th><th class="num">exact</th>'
        '<th class="num">within 1</th><th class="num">evidence</th>'
        '<th class="num">tokens</th><th>archive</th>'
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def _stability_table(findings, limit=25):
    if not findings:
        return (
            '<p class="empty">No question has been graded in more than one '
            "comparable run yet, so consistency cannot be judged.</p>"
        )
    body = []
    for finding in findings[:limit]:
        levels = " → ".join(
            "?" if level is None else str(level) for level in finding["levels"]
        )
        body.append(
            f'<tr class="{"flagged" if finding["unstable"] else ""}">'
            f'<td class="mono">{_esc(finding["assignment_key"])}/'
            f'{_esc(finding["student_key"])} Q{_esc(finding["question_number"])}</td>'
            f'<td>{_esc(finding["question_type"])}</td>'
            f'<td class="num">{finding["runs"]}</td>'
            f'<td class="num">{finding["distinct_levels"]}</td>'
            f'<td class="mono">{levels}</td>'
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>question</th><th>type</th>"
        '<th class="num">runs</th><th class="num">grades seen</th>'
        "<th>grade per run (0 = best)</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def render(runs, question_rows, include_replay=False, include_partial=False):
    """Build the complete, self-contained HTML document."""
    trend_report = analysis.trends(runs, include_replay, include_partial)
    comparable = analysis.comparable_runs(runs, include_replay, include_partial)
    stability = analysis.question_stability(
        question_rows, run_ids={r.get("run_id") for r in comparable}
    )
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    mixed_note = (
        '<div class="note"><b>Not like-for-like.</b> These runs do not all use '
        "the same grading prompt, so a change in the numbers may reflect the "
        "prompt changing rather than the grader behaving differently.</div>"
        if trend_report["mixed_prompt_versions"]
        else ""
    )

    unstable_count = sum(1 for f in stability if f["unstable"])
    counted = trend_report["runs_considered"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grading Benchmark Trends</title>
<style>{_CSS}</style>
</head><body><div class="wrap">

<p class="eyebrow">Grading benchmark</p>
<h1>Is the change real, or is it noise?</h1>
<p class="lede">
  Across {counted} comparable run{"" if counted == 1 else "s"}. The shaded band
  on each chart is that measure's normal range; the filled dot is the latest
  run. A dot inside the band is ordinary variation, not a result.
  <br><span class="readout">Generated {_esc(generated)}</span>
</p>
{mixed_note}

{_metric_rows(trend_report)}

<h2>Questions graded inconsistently</h2>
<p class="lede">
  The same student answer, marked on different runs. A question whose grade
  moves is one the grader is unreliable on — {unstable_count} of
  {len(stability)} tracked question{"" if len(stability) == 1 else "s"} moved.
</p>
{_stability_table(stability)}

<h2>Every run</h2>
{_runs_table(runs)}

<footer>
  Replay runs and partial runs are left out of these statistics by default.
  A replay re-reads fixed recorded responses, so its numbers never vary and
  including them would make the normal range look far narrower than it is; a
  partial run grades only some of the dataset, so its rates are not comparable.
</footer>
</div></body></html>
"""
