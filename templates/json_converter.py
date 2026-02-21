import json

# The text you want to convert
text = """
{

}
"""

# Create JSON structure
data = {
    "course": "",
    "topic": "",
    "content": text,
}

# Save to file
with open("output.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)

print("JSON saved to output.json")
