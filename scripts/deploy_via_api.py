#!/usr/bin/env python3
"""Deploy outdoor_reset.yaml to Forest Home via Supervisor API or alternative methods."""
import json
import os
import urllib.request
import urllib.error

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def api_get(path):
    req = urllib.request.Request(f"{BASE}{path}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def api_post(path, data=None):
    body = json.dumps(data).encode() if data else b""
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


# Check what addons are installed (supervisor API)
print("=== Checking Supervisor addons ===")
code, data = api_get("/api/hassio/addons")
if code == 200:
    addons = data.get("data", {}).get("addons", [])
    for addon in addons:
        name = addon.get("name", "")
        slug = addon.get("slug", "")
        state = addon.get("state", "")
        if any(k in name.lower() for k in ["ssh", "terminal", "file", "samba"]):
            print(f"  {slug}: {name} (state={state})")
else:
    print(f"  Supervisor API returned {code}: {data}")

# Try SSH addon info specifically
print("\n=== SSH addon details ===")
for slug in ["core_ssh", "a0d7b954_ssh", "a0d7b954_terminal"]:
    code, data = api_get(f"/api/hassio/addons/{slug}/info")
    if code == 200:
        info = data.get("data", {})
        print(f"  {slug}: state={info.get('state')} port={info.get('network', {})}")
    else:
        pass

# Check if Samba backup share exists
print("\n=== Checking config folder listing via supervisor ===")
# The hassio proxy allows file operations in some setups
for path in ["/api/hassio/core/api/config", "/api/config/core"]:
    code, data = api_get(path)
    if code == 200:
        print(f"  {path} -> {json.dumps(data)[:200]}")
