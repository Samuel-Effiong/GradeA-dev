def assignment_to_prosemirror(assignment: dict) -> dict:
    """
    Converts assignment JSON structure into ProseMirror document schema
    """

    content = []

    # Title
    if assignment.get("title"):
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": assignment["title"]}],
            }
        )

    # Instructions
    if assignment.get("instructions"):
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": assignment["instructions"]}],
            }
        )

    # Spacer
    content.append({"type": "paragraph", "content": []})

    # Questions
    if assignment.get("questions"):
        list_items = []

        for q in assignment["questions"]:
            item_content = []

            # Question text
            item_content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": q["question_text"]}],
                }
            )

            # Points
            if q.get("points") is not None:
                item_content.append(
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "marks": [{"type": "italic"}],
                                "text": f"({q['points']} points)",
                            }
                        ],
                    }
                )

            # Options (for objective questions)
            if q.get("options"):
                bullet_items = []
                for opt in q["options"]:
                    bullet_items.append(
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": opt}],
                                }
                            ],
                        }
                    )

                item_content.append({"type": "bulletList", "content": bullet_items})

            list_items.append({"type": "listItem", "content": item_content})

        content.append({"type": "orderedList", "content": list_items})

    return {"type": "doc", "content": content}


# assignment = assignment_to_prosemirror(assignment)
# pprint(assignment)
