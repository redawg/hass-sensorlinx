#!/usr/bin/env python3
"""Set GitHub repository topics for HACS validation."""
import subprocess
import requests

# Get token from git credential store
proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True,
)
token = None
for line in proc.stdout.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1]
        break

if not token:
    print("ERROR: Could not retrieve GitHub token from git credentials")
    exit(1)

print(f"Token retrieved (length={len(token)})")

# Set topics via GitHub API
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

topics = ["hacs-integration", "homeassistant", "home-assistant", "hacs", "radiant-floor-heating", "sensorlinx", "hbx-controls"]

r = requests.put(
    "https://api.github.com/repos/redawg/hass-sensorlinx/topics",
    headers=headers,
    json={"names": topics},
)

print(f"Status: {r.status_code}")
if r.status_code == 200:
    print(f"Topics set: {r.json().get('names')}")
else:
    print(f"Error: {r.text[:300]}")
