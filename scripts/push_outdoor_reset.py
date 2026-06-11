#!/usr/bin/env python3
"""Push outdoor_reset.py to GitHub via contents API."""
import requests
import base64
import pathlib

OWNER = "redawg"
REPO = "hbx-sensorlinx-ha"
FILE_PATH = "custom_components/sensorlinx/outdoor_reset.py"
BRANCH = "main"

# Read GitHub token from git credential
# Using the token from previous successful pushes
TOKEN = None

# Try to get token from git config
import subprocess
result = subprocess.run(
    ["git", "config", "--get", "credential.helper"],
    capture_output=True, text=True, cwd=r"C:\Users\andre\Projects\hbx-sensorlinx-ha"
)
print(f"Git credential helper: {result.stdout.strip()}")

# Read the file
local_path = pathlib.Path(r"C:\Users\andre\Projects\hbx-sensorlinx-ha") / FILE_PATH
content = local_path.read_bytes()
content_b64 = base64.b64encode(content).decode()
print(f"File size: {len(content)} bytes, base64: {len(content_b64)} chars")

# Get GitHub token from environment or use the MCP token approach
import os
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

if not TOKEN:
    # Try gh CLI
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if result.returncode == 0:
        TOKEN = result.stdout.strip()
        print("Got token from gh CLI")

if not TOKEN:
    print("ERROR: No GitHub token found. Set GITHUB_TOKEN env var.")
    exit(1)

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
}

# Get current file SHA
r = requests.get(
    f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}?ref={BRANCH}",
    headers=headers,
)
if r.status_code == 200:
    sha = r.json()["sha"]
    print(f"Current SHA: {sha}")
else:
    sha = None
    print(f"File not found (will create): {r.status_code}")

# Update the file
payload = {
    "message": "feat: add supply water temperature reset control",
    "content": content_b64,
    "branch": BRANCH,
}
if sha:
    payload["sha"] = sha

r = requests.put(
    f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}",
    headers=headers,
    json=payload,
)
if r.status_code in (200, 201):
    print(f"SUCCESS: {r.json()['commit']['sha']}")
else:
    print(f"FAILED: {r.status_code} {r.text[:500]}")
