#!/usr/bin/env python3
"""One-off probe for Forest Home radiant floor entities."""
import json
import sys
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
if not TOKEN:
    print("usage: probe_ha.py TOKEN", file=sys.stderr)
    sys.exit(1)

headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get(path: str):
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    print("=== API root ===")
    print(get("/api/"))

    states = get("/api/states")
    keywords = ["radiant", "heated", "floor", "pump", "garage", "sensorlinx", "zon", "thm", "hbx"]
    matches = []
    for s in states:
        eid = s["entity_id"].lower()
        name = (s.get("attributes", {}).get("friendly_name") or "").lower()
        text = eid + " " + name
        if any(k in text for k in keywords):
            attrs = s.get("attributes", {})
            matches.append(
                {
                    "entity_id": s["entity_id"],
                    "state": s["state"],
                    "friendly_name": attrs.get("friendly_name"),
                    "device_class": attrs.get("device_class"),
                }
            )
    print("\n=== Matching states ===")
    print(json.dumps(matches, indent=2))
    print("TOTAL", len(matches))

    try:
        entries = get("/api/config/config_entries/entry")
        print("\n=== Config entries (sensorlinx) ===")
        for e in entries:
            if e.get("domain") in ("sensorlinx", "hbx", "hbxcontrols"):
                print(json.dumps(e, indent=2))
    except Exception as err:
        print("config_entries error:", err)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
