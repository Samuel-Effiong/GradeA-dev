# from prosemirror.html_diff import HtmlParser
import json
import string
from datetime import datetime

# from bs4 import BeautifulSoup
from lxml import html

# 2. Use the Parser to create a document object
from prosemirror.model import DOMParser, Schema
from prosemirror.schema.basic import schema as basic_schema
from prosemirror.schema.list import add_list_nodes

# 1. Define your schema (standard basic schema with lists)
my_schema = Schema(
    {
        "nodes": add_list_nodes(
            basic_schema.spec["nodes"], "paragraph block*", "block"
        ),
        "marks": basic_schema.spec["marks"],
    }
)


html_string = """


"""
dom = html.fromstring(html_string)

# Parse the DOM into a ProseMirror Document object
doc = DOMParser.from_schema(my_schema).parse(dom)

# 3. Convert the Document object to a JSON-compatible Dictionary
pm_json = str(doc.to_json())

json_file = json.dumps(pm_json, indent=4)
with open("prosemirror.json", "w") as file:
    file.write(pm_json)

# print(pm_json)


def format_assignment_standard_html(data: dict) -> str:
    """
    Converts structured assignment data into a globally recognized academic format.
    Preserves ALL HTML and displays questions, rubrics, and model answers professionally.
    """

    title_html = data.get("title", "")
    instructions_html = data.get("instructions", "")
    due_date = data.get("due_date")
    total_points = data.get("total_points", 0)
    questions = data.get("questions", [])

    if due_date:
        due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00")).strftime(
            "%B %d, %Y"
        )

    html_output = []

    # Title
    html_output.append(
        f"""
    <div style="text-align:center; margin-bottom:25px;">
        {title_html}
    </div>
    """
    )

    # Instructions
    html_output.append(
        f"""
    <div style="margin-bottom:20px;">
        {instructions_html}
    </div>
    """
    )

    # Meta
    html_output.append(
        f"""
    <div style="margin-bottom:30px;">
        <p><strong>Total Marks:</strong> {total_points}</p>
        {"<p><strong>Due Date:</strong> " + due_date + "</p>" if due_date else ""}
    </div>
    """
    )

    # Questions Header
    html_output.append(
        """
    <h2>Assignment Questions</h2>
    <hr>
    """
    )

    # Questions
    for q in questions:
        q_no = q.get("question_number")
        q_points = q.get("points")
        q_text = q.get("question_text", "")
        q_type = q.get("question_type", "").upper()
        options = q.get("options", [])
        rubric = q.get("rubric", [])
        model_answer = q.get("model_answer", "")
        image_url = q.get("question_image", "")

        html_output.append(
            f"""
        <div style="margin-bottom:40px;">
            <p><strong>Question {q_no} ({q_points} marks)</strong></p>
            {q_text}
        """
        )

        # Question Image
        if image_url:
            html_output.append(
                f"""
            <div style="margin-top:12px; margin-bottom:12px; text-align:center;">
                <img src={image_url!r} style="max-width:100%; height:auto;" alt="Question {q_no} image">
            </div>
            """
            )

        # Objective Options
        if q_type == "OBJECTIVE" and options:
            html_output.append(
                """
            <div style="margin-top:12px; padding-left:25px;">
            """
            )

            for idx, opt in enumerate(options):
                letter = string.ascii_uppercase[idx]
                html_output.append(
                    f"""
                    <p><strong>{letter}.</strong> {opt}</p>
                """
                )

            html_output.append("</div>")

        # Rubric (for essay & short answer)
        if rubric:
            html_output.append(
                """
            <div style="margin-top:15px;">
                <p><strong>Marking Guide / Rubric:</strong></p>
                <table border="1" cellpadding="6" cellspacing="0" width="100%" style="border-collapse:collapse;">
                    <thead>
                        <tr>
                            <th align="left">Performance Level</th>
                            <th align="center">Marks</th>
                            <th align="left">Criteria</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            )

            for r in rubric:
                level = r.get("level", "").title()
                points = r.get("points", "")
                desc = r.get("description", "")

                html_output.append(
                    f"""
                    <tr>
                        <td>{level}</td>
                        <td align="center">{points}</td>
                        <td>{desc}</td>
                    </tr>
                """
                )

            html_output.append(
                """
                    </tbody>
                </table>
            </div>
            """
            )

        # Model Answer
        if model_answer:
            html_output.append(
                f"""
            <div style="margin-top:15px;">
                <p><strong>Model Answer / Expected Response:</strong></p>
                <div style="padding-left:15px;">
                    {model_answer}
                </div>
            </div>
            """
            )

        html_output.append("</div>")

    return "\n".join(html_output)


def format_assignment_standard_html_2(data: dict) -> str:
    """
    Converts structured assignment data into a globally recognized academic format.
    Preserves ALL HTML and displays questions, rubrics, and model answers professionally
    WITHOUT using tabular structures.
    """

    title_html = data.get("title", "")
    instructions_html = data.get("instructions", "")
    due_date = data.get("due_date")
    total_points = data.get("total_points", 0)
    questions = data.get("questions", [])

    if due_date:
        due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00")).strftime(
            "%B %d, %Y"
        )

    html_output = []

    # Title
    html_output.append(
        f"""
    <div style="text-align:center; margin-bottom:25px;">
        {title_html}
    </div>
    """
    )

    # Instructions
    html_output.append(
        f"""
    <div style="margin-bottom:20px;">
        {instructions_html}
    </div>
    """
    )

    # Meta
    html_output.append(
        f"""
    <div style="margin-bottom:30px;">
        <p><strong>Total Marks:</strong> {total_points}</p>
        {"<p><strong>Due Date:</strong> " + due_date + "</p>" if due_date else ""}
    </div>
    """
    )

    # Questions Header
    html_output.append(
        """
    <h2>Assignment Questions</h2>
    <hr>
    """
    )

    # Questions
    for q in questions:
        q_no = q.get("question_number")
        q_points = q.get("points")
        q_text = q.get("question_text", "")
        q_type = q.get("question_type", "").upper()
        options = q.get("options", [])
        rubric = q.get("rubric", [])
        model_answer = q.get("model_answer", "")

        html_output.append(
            f"""
        <div style="margin-bottom:40px;">
            <p><strong>Question {q_no} ({q_points} marks)</strong></p>
            {q_text}
        """
        )

        # Objective Options
        if q_type == "OBJECTIVE" and options:
            html_output.append(
                """
            <div style="margin-top:12px; padding-left:25px;">
            """
            )

            for idx, opt in enumerate(options):
                letter = string.ascii_uppercase[idx]
                html_output.append(
                    f"""
                    <p><strong>{letter}.</strong> {opt}</p>
                """
                )

            html_output.append("</div>")

        # Rubric (Essay / Short Answer)
        if rubric:
            html_output.append(
                """
            <div style="margin-top:18px;">
                <p><strong>Marking Guide / Rubric:</strong></p>
                <div style="padding-left:15px;">
            """
            )

            for r in rubric:
                level = r.get("level", "").title()
                points = r.get("points", "")
                desc = r.get("description", "")

                html_output.append(
                    f"""
                    <p>
                        <strong>{level} ({points} marks):</strong><br>
                        {desc}
                    </p>
                """
                )

            html_output.append(
                """
                </div>
            </div>
            """
            )

        # Model Answer
        if model_answer:
            html_output.append(
                f"""
            <div style="margin-top:18px;">
                <p><strong>Model Answer / Expected Response:</strong></p>
                <div style="padding-left:15px;">
                    {model_answer}
                </div>
            </div>
            """
            )

        html_output.append("</div>")

    return "\n".join(html_output)


data = {}


# output = format_assignment_standard_html(data)
# with open('assignent.html', 'w') as file:
#     file.write(output)

print("Finished")
# print(output)
