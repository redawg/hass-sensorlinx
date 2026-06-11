#!/usr/bin/env python3
"""Add repo to HACS and download, then restart HA."""
import json
import os
import sys
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
REPO = "redawg/hass-sensorlinx"


def post(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# Step 1: Add custom repository to HACS
print("=== Adding custom repo to HACS ===")
code, resp = post("/api/services/hacs/repository_add", {
    "repository": REPO,
    "category": "integration",
})
print(f"  repository_add: {code}")
if code >= 400:
    print(f"  response: {resp[:500]}")

# Step 2: Download/install via HACS
print("\n=== Downloading via HACS ===")
code, resp = post("/api/services/hacs/download", {
    "repository": REPO,
})
print(f"  download: {code}")
if code >= 400:
    print(f"  response: {resp[:500]}")

# Step 3: Restart HA to load updated code
print("\n=== Restarting Home Assistant ===")
code, resp = post("/api/services/homeassistant/restart", {})
print(f"  restart: {code}")
if code >= 400:
    print(f"  response: {resp[:500]}")
else:
    print("  HA restarting... integration will reload with fixed code.")
