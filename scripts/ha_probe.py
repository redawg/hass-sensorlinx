#!/usr/bin/env python3
"""Probe Forest Home API capabilities for custom integration install."""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("HA_URL", "http://172.16.255.250:8123")
TOKEN = os.environ["HA_TOKEN"]


def get(path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return e.code, data


def main() -> int:
    checks = [
        "/api/",
        "/api/config",
        "/api/hassio/info",
        "/api/hassio/addons",
        "/api/config/config_entries/entry",
        "/api/services",
    ]
    for path in checks:
        code, data = get(path)
        print(f"\n=== {path} -> {code} ===")
        if path.endswith("/entry") and isinstance(data, list):
            domains = sorted({e.get("domain") for e in data})
            print("domains:", ", ".join(domains[:40]), ("..." if len(domains) > 40 else ""))
            if any(e.get("domain") == "sensorlinx" for e in data):
                print("sensorlinx: ALREADY CONFIGURED")
        elif path.endswith("/addons") and isinstance(data, dict):
            addons = data.get("data", {}).get("addons") or data.get("addons") or []
            for a in addons[:15]:
                print(f"  - {a.get('name')} slug={a.get('slug')} state={a.get('state')}")
        elif isinstance(data, dict):
            print(json.dumps(data, indent=2)[:1200])
        else:
            print(str(data)[:500])
    return 0


if __name__ == "__main__":
    if not TOKEN:
        print("Set HA_TOKEN", file=sys.stderr)
        sys.exit(1)
    raise SystemExit(main())
