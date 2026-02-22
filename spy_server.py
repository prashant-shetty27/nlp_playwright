import json
import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_FILE = "recorded_elements.json"

def generate_custom_xpath(element_dna):
    """Generates a robust heuristic XPath for Playwright execution."""
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})
    
    # 1. Developer IDs
    if attrs.get("id"): return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"): return f"//{tag}[@name='{attrs['name']}']"
        
    # 2. Accessibility & Image Semantic Tags (NEW PIPELINE)
    if attrs.get("aria-label"): return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"): return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"): return f"//{tag}[@alt='{attrs['alt']}']"
        
    # 3. Class Logic
    classes = attrs.get("class", "")
    if classes:
        class_list = classes.split()
        valid_classes = [c for c in class_list if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"
            
    # 4. Text Logic
    text = element_dna.get("innerText")
    if text and len(text) < 40:
        clean_text = text.replace("'", "\\'")
        return f"//{tag}[normalize-space(text())='{clean_text}']"
        
    # 5. Ultimate Fallback
    return f"//{tag}"

def save_to_database(element_dna):
    """Saves the element DNA into a hierarchical Page -> Locator JSON structure."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    # 1. Pop the user-defined names
    page_name = element_dna.pop("userPageName", "unnamed_page")
    locator_name = element_dna.pop("userLocatorName", "unnamed_locator")

    if page_name not in data:
        data[page_name] = {}

    # 2. Generate the Custom ML XPath
    element_dna["custom_xpath"] = generate_custom_xpath(element_dna)

    # 3. Trust the Human (No more aggressive XPath deduplication)
    # If the locator_name already exists, we consider this an intentional update by the user.
    is_update = locator_name in data[page_name]

    # 4. Save the element explicitly under the Human-provided names
    data[page_name][locator_name] = element_dna

    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return f"{page_name} -> {locator_name}", is_update

# Ensure this route is ONLY defined ONCE in the file
@app.route('/record', methods=['POST'])
def record_element():
    element_dna = request.json
    saved_name, is_update = save_to_database(element_dna)
    
    print("\n" + "="*40)
    if is_update:
        print(f"🔄 UPDATED EXISTING ELEMENT: {saved_name}")
    else:
        print(f"💾 SAVED NEW ELEMENT: {saved_name}")
    print(f"XPath: {element_dna.get('custom_xpath')}")
    print("="*40 + "\n")
    
    return jsonify({"status": "Success", "message": f"Processed {saved_name}"})

if __name__ == '__main__':
    print("🚀 Spy Server listening on port 5050... Ready to record.")
    app.run(port=5050)