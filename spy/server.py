"""
spy/server.py
Spy API server for recording element DNA from extension content scripts.
Moved from root spy_server.py.
"""
import json
import os
import sys

# Allow running as `python spy/server.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS
from config.settings import RECORDED_ELEMENTS_FILE

app = Flask(__name__)
CORS(app)

DB_FILE = RECORDED_ELEMENTS_FILE


def generate_custom_xpath(element_dna):
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})

    if attrs.get("id"):
        return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"):
        return f"//{tag}[@name='{attrs['name']}']"
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"):
        return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"):
        return f"//{tag}[@alt='{attrs['alt']}']"

    classes = attrs.get("class", "")
    if classes:
        class_list = classes.split()
        valid_classes = [c for c in class_list if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"

    text = element_dna.get("innerText")
    if text and len(text) < 40:
        clean_text = text.replace("'", "\\'")
        return f"//{tag}[normalize-space(text())='{clean_text}']"

    return f"//{tag}"


def save_to_database(element_dna):
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    page_name = element_dna.pop("userPageName", "unnamed_page")
    locator_name = element_dna.pop("userLocatorName", "unnamed_locator")

    if page_name not in data:
        data[page_name] = {}

    element_dna["custom_xpath"] = generate_custom_xpath(element_dna)
    is_update = locator_name in data[page_name]
    data[page_name][locator_name] = element_dna

    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return f"{page_name} -> {locator_name}", is_update


@app.route('/api/record-element', methods=['POST'])
def record_element():
    element_dna = request.json
    saved_name, is_update = save_to_database(element_dna)

    print("\n" + "=" * 40)
    if is_update:
        print(f"🔄 UPDATED EXISTING ELEMENT: {saved_name}")
    else:
        print(f"💾 SAVED NEW ELEMENT: {saved_name}")
    print(f"XPath: {element_dna.get('custom_xpath')}")
    print("=" * 40 + "\n")

    return jsonify({"status": "Success", "message": f"Processed {saved_name}"})


@app.route('/api/get-database-schema', methods=['GET'])
def get_database_schema():
    """Return the full locator DB so the extension popup can display it."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}
    return jsonify(data)


if __name__ == '__main__':
    print("🚀 Spy Server listening on port 5050... Ready to record.")
    app.run(port=5050)
