import json
import os

def load_sites():
    path = os.path.join(os.path.dirname(__file__), "sites.json")

    if not os.path.exists(path):
        raise FileNotFoundError(f"sites.json not found at {path}")

    with open(path, "r") as f:
        return json.load(f)

# Load once at import time
SITES = load_sites()
