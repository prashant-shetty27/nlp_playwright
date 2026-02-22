#!/usr/bin/env python3
"""
Script to help you clean up old/duplicate VS Code snippet files.
Keeps only automation-snippets.code-snippets and removes others.
"""
import os
import glob

SNIPPET_DIR = os.path.expanduser("~/Library/Application Support/Code/User/snippets")
KEEP_FILE = "automation-snippets.code-snippets"

def clean_snippet_files():
    files = glob.glob(os.path.join(SNIPPET_DIR, "*.code-snippets"))
    for f in files:
        if not f.endswith(KEEP_FILE):
            print(f"Deleting: {f}")
            os.remove(f)
    print(f"✅ Only {KEEP_FILE} kept in {SNIPPET_DIR}")

if __name__ == "__main__":
    clean_snippet_files()
