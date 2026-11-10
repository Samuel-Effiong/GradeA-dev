"""The extraction prompt the two batch upload routes send (B1b-5, SM Q2).

Moved here byte for byte from students/views.py (teacher batch) and
assignments/views.py (assignment batch), where it was written out twice
identically, so a retried upload item (students/item_retry.py) relaunches
with exactly what its route sent. A test pins it verbatim.
"""

BATCH_UPLOAD_PROMPT = """
            Analyze the image of an educational assignment and return a JSON

            IMPORTANT: Return only valid JSON matching the required structure.
            Do not include any explanatory text before or after the JSON
            """
