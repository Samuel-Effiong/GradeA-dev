"""
Builds the downloadable PDF for an assignment.

Kept apart from the viewset because producing this document needs nothing
from an HTTP request - only the assignment and which of the two views
(teacher, with rubrics and model answers; student, without) to build. Two
callers depend on that: download_pdf serves it, and the publish-time
pre-render task warms the cache with it, and both must produce identical
bytes or the cache would serve one caller's document to the other.
"""

from django.utils.html import escape as escape_html

from .pdf_renderer import ensure_capacity, render_html_to_pdf
from .services import AssignmentProcessingService, _strip_html_from_title


def render_assignment_pdf(assignment, include_rubric: bool) -> bytes:
    """
    Build this assignment's PDF from scratch: assemble the HTML, then
    render it through Chromium.

    Lives here rather than on the viewset because it needs nothing
    from the request - only the assignment and which view to build. That
    lets the publish-time pre-render task (assignments/tasks.py) produce
    exactly the same bytes a download would, and lets the whole expensive
    path be handed to pdf_cache.get_or_render() as one callable so
    concurrent callers share a single render.
    """
    # Refuse before assembling anything if the renderer is already at
    # capacity: this document costs real CPU to build and would only be
    # discarded at the render call a moment later.
    ensure_capacity()

    # Prepare data for the assignment (common to both views)
    data = {
        "title": assignment.title,
        "instructions": assignment.instructions,
        "total_points": assignment.total_points,
        "due_date": (assignment.due_date.isoformat() if assignment.due_date else None),
        "questions": assignment.questions,
    }

    # Generate the assignment HTML without hidden teacher content.
    # include_document_header=False: the PDF template below already
    # renders its own title/instructions/meta header, so the shared
    # formatter shouldn't render them a second time.
    html_body = AssignmentProcessingService.format_assignment_standard_html(
        data, include_rubric=include_rubric, include_document_header=False
    )

    # Extract course and teacher info. Escaped before embedding below:
    # under the previous WeasyPrint pipeline a stray "<script>" or
    # "onerror=" here was inert (WeasyPrint has no JS engine at all),
    # but Chromium actually executes JavaScript while rendering this
    # document to PDF - the same raw interpolation that was harmless
    # before is a real script-injection path now, so every value that
    # isn't already known-safe (a server-formatted date, a plain int)
    # needs escaping here, matching what format_assignment_standard_html
    # already does for the fields it renders itself.
    course_name = escape_html(assignment.course.name if assignment.course else "Course")
    teacher_name = escape_html(
        assignment.course.teacher.get_full_name()
        if assignment.course and assignment.course.teacher
        else "Instructor"
    )

    due_date_str = (
        assignment.due_date.strftime("%B %d, %Y at %I:%M %p")
        if assignment.due_date
        else "Not set"
    )
    display_title = escape_html(
        _strip_html_from_title(assignment.title) or "Assignment"
    )
    # This endpoint calls format_assignment_standard_html with
    # include_document_header=False specifically so it can render its
    # own instructions box below instead - but that also means the
    # shared formatter's own sanitize_ai_html(instructions) call never
    # runs against the version rendered here. Assignment.instructions
    # is stored as whatever raw HTML the AI/extraction pipeline
    # produced (see AssignmentProcessingService.format_assignment_standard_html,
    # which sanitizes it lazily at render time, not at write time), so
    # it must be sanitized here too before being embedded.
    sanitized_instructions = AssignmentProcessingService.sanitize_ai_html(
        assignment.instructions or ""
    )
    instructions_html = (
        f'<div class="instructions-box"><strong>Instructions:</strong> '
        f"{sanitized_instructions}</div>"
        if sanitized_instructions
        else ""
    )

    # Build the full HTML with enhanced styling
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{display_title}</title>
        <style>
            /* --- Page setup ---
               Editorial layout patterned after the ground-truth
               benchmark PDFs (ai_processor/benchmark/render.py):
               quiet serif typesetting instead of a dashboard look.
               Running headers/footers (title top-center, page count
               bottom-center, brand mark bottom-right) are rendered via
               Chromium's header_template/footer_template PDF options
               (see download_pdf below), not CSS @page margin boxes -
               Chromium's print-to-PDF doesn't support those. Page
               margins are likewise set via page.pdf()'s margin option
               rather than here, so they stay in sync with the space
               those templates need. */
            @page {{
                size: A4;
            }}

            /* --- Global styles --- */
            body {{
                font-family: 'Georgia', 'Times New Roman', serif;
                margin: 0;
                padding: 0;
                line-height: 1.65;
                color: #20242b;
                background: #ffffff;
                font-size: 11.5pt;
            }}

            .container {{
                max-width: 100%;
            }}

            /* --- Title block --- */
            .assignment-header {{
                text-align: center;
                margin-bottom: 28px;
                padding-bottom: 18px;
                border-bottom: 3px double #1a3a5c;
            }}
            .assignment-header .course-name {{
                font-size: 10.5pt;
                font-weight: 400;
                letter-spacing: 1.5px;
                text-transform: uppercase;
                color: #96895f;
                margin-bottom: 6px;
            }}
            .assignment-header .assignment-title {{
                font-size: 25pt;
                font-weight: 700;
                color: #1a3a5c;
                margin: 6px 0 10px 0;
            }}
            .assignment-header .meta {{
                font-size: 10.5pt;
                color: #5a6472;
                font-style: italic;
            }}
            .assignment-header .meta span:not(:last-child)::after {{
                content: " \\00b7 ";
                font-style: normal;
                color: #b5ab8f;
            }}

            /* --- Instructions --- */
            .instructions-box {{
                background: #f7f6f2;
                border-left: 3px solid #1a3a5c;
                padding: 14px 20px;
                margin-bottom: 28px;
                font-size: 11pt;
            }}
            .instructions-box strong {{
                color: #1a3a5c;
            }}

            /* --- Section heading (services.py emits "Assignment
               Questions" as an h2 followed by a bare hr) --- */
            h2 {{
                font-size: 13.5pt;
                color: #1a3a5c;
                text-transform: uppercase;
                letter-spacing: 1px;
                border-bottom: 1px solid #ddd8c8;
                padding-bottom: 6px;
                margin: 0 0 18px;
            }}
            h2 + hr {{
                display: none;
            }}

            /* --- Questions ---
               services.py renders each question as a plain div with
               inline styles (shared with the ProseMirror editor
               pipeline), so styling here targets the tags it
               actually emits rather than card classes. */
            strong {{
                color: #1a3a5c;
            }}
            .container > div {{
                page-break-inside: avoid;
            }}

            /* Images */
            img {{
                max-width: 100%;
                height: auto;
                display: block;
                margin: 12px auto;
                border: 1px solid #ddd8c8;
            }}
            /* Tables (rubric, etc.) */
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 14px 0;
                font-size: 10.5pt;
            }}
            th, td {{
                border: 1px solid #ddd8c8;
                padding: 7px 10px;
                text-align: left;
                vertical-align: top;
            }}
            th {{
                background-color: #f0eee7;
                font-weight: 700;
                color: #1a3a5c;
            }}
            /* Lists */
            ul, ol {{
                padding-left: 25px;
                margin: 10px 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header Block -->
            <div class="assignment-header">
                <div class="course-name">{course_name}</div>
                <div class="assignment-title">{display_title}</div>
                <div class="meta">
                    <span>{teacher_name}</span>
                    <span>Due {due_date_str}</span>
                    <span>{assignment.total_points or 'N/A'} marks total</span>
                </div>
            </div>

            <!-- Instructions -->
            {instructions_html}

            <!-- Questions -->
            {html_body}
        </div>
    </body>
    </html>
    """

    # Replaces the old @page @top-center/@bottom-center/@bottom-right
    # margin boxes: Chromium's print-to-PDF has no equivalent CSS
    # support, so the running title/page-count/brand mark are built as
    # Chromium's own header/footer templates instead. Padding matches
    # the page margins below so the text lines up with the body content
    # (Chromium's templates span the full page width by default,
    # ignoring left/right margins unless padding compensates).
    # class="title" is filled in by Chromium from the document's own
    # <title> tag (set above), so it never needs to be re-escaped here.
    header_template = """
    <div style="width:100%; box-sizing:border-box;
                padding:0 2cm 5px 2cm; margin:0;
                font-family: Georgia, 'Times New Roman', serif;
                font-style:italic; font-size:9px; color:#7a8188;
                text-align:center; border-bottom:1px solid #ddd8c8;">
        <span class="title"></span>
    </div>
    """
    footer_template = """
    <div style="width:100%; box-sizing:border-box;
                padding:5px 2cm 0 2cm; margin:0;
                font-family: Georgia, 'Times New Roman', serif;
                font-size:8.5px; color:#7a8188;
                border-top:1px solid #ddd8c8;
                display:flex; align-items:center; justify-content:space-between;">
        <span style="flex:1;"></span>
        <span style="flex:1; text-align:center;">
            Page <span class="pageNumber"></span> of <span class="totalPages"></span>
        </span>
        <span style="flex:1; text-align:right; letter-spacing:0.5px; color:#b5ab8f;">
            Grade A+
        </span>
    </div>
    """

    return render_html_to_pdf(
        full_html,
        header_template=header_template,
        footer_template=footer_template,
        margins={
            "top": "2.5cm",
            "right": "2cm",
            "bottom": "2.2cm",
            "left": "2cm",
        },
    )
