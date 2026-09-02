#!/usr/bin/env python3
"""Deploy SensorLinx openings_guard.py to Forest Home and reload integration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HA_HOST = os.environ.get("HA_HOST", "172.16.255.250")
HA_USER = os.environ.get("HA_USER", "root")
HA_HTTP = f"http://{HA_HOST}:8123"
REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILES = [
    (
        REPO_ROOT / "custom_components/sensorlinx/openings_guard.py",
        "/config/custom_components/sensorlinx/openings_guard.py",
    ),
    (
        REPO_ROOT / "custom_components/sensorlinx/manifest.json",
        "/config/custom_components/sensorlinx/manifest.json",
    ),
]
DOMAIN = "sensorlinx"


def load_token() -> str:
    token = (os.environ.get("HA_TOKEN") or os.environ.get("API_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    repos = Path(os.environ.get("REPOS_ROOT", "/home/aatom/repos"))
    sys.path.insert(0, str(repos / ".cursor/scripts"))
    import importlib.util

    path = repos / ".cursor/scripts/morning-ha-weather-forecast.py"
    spec = importlib.util.spec_from_file_location("morning_ha", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    _, token = mod.load_ha_config()
    return token


def run_ssh(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    full = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        f"{HA_USER}@{HA_HOST}",
        cmd,
    ]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def run_scp(local: Path, remote: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    full = [
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        str(local),
        f"{HA_USER}@{HA_HOST}:{remote}",
    ]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def ha_request(method: str, path: str, token: str, data: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"{HA_HTTP}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return resp.status, json.loads(raw) if raw else {}


def find_config_entry_id(token: str) -> str:
    _, entries = ha_request("GET", "/api/config/config_entries/entry", token)
    for entry in entries:
        if entry.get("domain") == DOMAIN:
            return entry["entry_id"]
    raise RuntimeError(f"No config entry for domain {DOMAIN}")


def verify_openings(token: str) -> None:
    time.sleep(3)
    _, status = ha_request("GET", "/api/states/sensor.sensorlinx_openings_guard_openings_status", token)
    attrs = status.get("attributes") or {}
    print("openings_status:", status.get("state"))
    print("open list:", attrs.get("open"))
    for rd in attrs.get("readings") or []:
        if "tb56" in rd.get("entity_id", "") or "front" in rd.get("entity_id", ""):
            print(" front reading:", rd)
    _, floor = ha_request("GET", "/api/states/switch.radiant_floor_contoller", token)
    print("radiant_floor_contoller:", floor.get("state"))


def main() -> int:
    token = load_token()
    print(f"=== Deploy openings guard → {HA_USER}@{HA_HOST} ===\n")

    print("1. Upload custom component files")
    for local, remote in LOCAL_FILES:
        if not local.is_file():
            print(f"   MISSING {local}")
            return 1
        proc = run_scp(local, remote)
        ok = proc.returncode == 0
        print(f"   {local.name} -> {remote} ... {'OK' if ok else 'FAIL'}")
        if not ok:
            print(proc.stderr.strip())
            return proc.returncode

    print("\n2. Reload SensorLinx config entry")
    entry_id = find_config_entry_id(token)
    ha_request(
        "POST",
        "/api/services/homeassistant/reload_config_entry",
        token,
        {"entry_id": entry_id},
    )
    print(f"   reload_config_entry {entry_id} OK")

    print("\n3. Refresh openings + verify")
    ha_request("POST", f"/api/services/{DOMAIN}/refresh_openings", token, {})
    verify_openings(token)

    print("\n4. Update legacy floor automation contacts (REST)")
    deploy_auto = REPO_ROOT / "scripts/deploy_open_contact_floor_disable.py"
    proc = subprocess.run([sys.executable, str(deploy_auto)], check=False)
    if proc.returncode != 0:
        print("   WARN: deploy_open_contact_floor_disable.py failed")
        return proc.returncode

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
