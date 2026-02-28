from flask import Flask, request, jsonify
from flask_cors import CORS

# 1. Initialize the Server
app = Flask(__name__)

# 2. Enable CORS (Security Bypass)
# Chrome strictly blocks websites from sending data to localhost. 
# CORS tells the browser, "It is safe, I am allowing this connection."
CORS(app)

# 3. Create the API Endpoint
@app.route('/record', methods=['POST'])
def record_element():
    # A. Catch the payload sent by background.js
    element_dna = request.json
    
    # B. Extract key data to prove we got it
    tag = element_dna.get('tagName')
    xpath = element_dna.get('absoluteXPath')
    
    print("\n" + "="*40)
    print(f"🐍 PYTHON RECEIVED NEW ELEMENT DNA!")
    print(f"Tag: {tag}")
    print(f"XPath: {xpath}")
    print("="*40 + "\n")
    
    # C. Send the JSON Receipt back to the extension
    # This prevents the "Unexpected end of JSON input" error you just saw
    return jsonify({"status": "Success", "message": "Python secured the payload."})

# 4. Start the Engine
if __name__ == '__main__':
    # Running on port 5050 to avoid MacOS/Windows system conflicts
    print("🚀 Spy Server listening on port 5050...")
    app.run(port=5050)