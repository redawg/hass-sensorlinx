#!/usr/bin/env python3
"""Deploy daily report scripts and HA package to Forest Home."""
import os
import subprocess
import sys
import time

import requests

HA_HOST = "172.16.255.250"
HA_USER = "root"
HA_HTTP = f"http://{HA_HOST}:8123"
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ."
    "Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

REMOTE_SENSORLINX = "/config/sensorlinx"
REMOTE_PACKAGES = "/config/packages"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(SCRIPT_DIR, "..")

FILES_TO_DEPLOY = [
    ("scripts/ha_daily_report_slack.py", f"{REMOTE_SENSORLINX}/ha_daily_report_slack.py"),
    ("scripts/daily_hvac_report.py", f"{REMOTE_SENSORLINX}/daily_hvac_report.py"),
    ("scripts/daily_report_agent.py", f"{REMOTE_SENSORLINX}/daily_report_agent.py"),
    ("packages/daily_report.yaml", f"{REMOTE_PACKAGES}/daily_report.yaml"),
]


def run_ssh(cmd, timeout=30):
    full = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        f"{HA_USER}@{HA_HOST}", cmd,
    ]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def run_scp(local, remote, timeout=60):
    full = [
        "scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        local, f"{HA_USER}@{HA_HOST}:{remote}",
    ]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def ha_reload():
    for endpoint in ("shell_command", "script", "automation"):
        r = requests.post(
            f"{HA_HTTP}/api/services/{endpoint}/reload",
            headers=HEADERS,
            timeout=30,
        )
        print(f"  reload {endpoint}: {r.status_code}")


def ha_run_script():
    r = requests.post(
        f"{HA_HTTP}/api/services/script/turn_on",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"entity_id": "script.sensorlinx_daily_performance_report"},
        timeout=10,
    )
    print(f"  trigger script: {r.status_code} {r.text[:200]}")


def main():
    print("=== Deploy SensorLinx Daily Report ===\n")

    print("1. Create remote directories")
    rc, out, err = run_ssh(f"mkdir -p {REMOTE_SENSORLINX} {REMOTE_PACKAGES}")
    print(f"   rc={rc} err={err.strip()}")

    print("\n1b. Ensure Slack config (@aschoenfeld)")
    ensure = os.path.join(SCRIPT_DIR, "ensure_slack_aschoenfeld_config.sh")
    if os.path.isfile(ensure):
        proc = subprocess.run(["bash", ensure], capture_output=True, text=True, timeout=60)
        print(f"   ensure script rc={proc.returncode}")
        if proc.stdout.strip():
            print(f"   {proc.stdout.strip()[:300]}")
        if proc.returncode != 0 and proc.stderr.strip():
            print(f"   (SSH optional) {proc.stderr.strip()[:200]}")

    print("\n2. Upload files")
    for local_rel, remote in FILES_TO_DEPLOY:
        local = os.path.abspath(os.path.join(REPO_ROOT, local_rel))
        rc, out, err = run_scp(local, remote)
        status = "OK" if rc == 0 else f"FAIL rc={rc}"
        print(f"   {local_rel} -> {remote} ... {status}")
        if rc != 0:
            print(f"     {err.strip()}")
            sys.exit(1)

    print("\n3. Ensure packages in configuration.yaml")
    rc, out, err = run_ssh("grep -c 'packages:' /config/configuration.yaml")
    if rc == 0 and out.strip() != "0":
        print("   packages: already configured")
    else:
        run_ssh(
            "printf '\\nhomeassistant:\\n  packages: !include_dir_named packages\\n' "
            ">> /config/configuration.yaml"
        )
        print("   added packages directive")

    print("\n4. Reload HA components")
    ha_reload()

    print("\n5. Verify entities")
    time.sleep(3)
    r = requests.get(f"{HA_HTTP}/api/states", headers=HEADERS, timeout=15)
    states = {s["entity_id"]: s for s in r.json()}
    for eid in (
        "script.sensorlinx_daily_performance_report",
        "automation.sensorlinx_daily_report_schedule",
    ):
        if eid in states:
            print(f"   {eid}: found")
        else:
            print(f"   {eid}: NOT FOUND (may need HA restart)")

    print("\n6. Trigger test run")
    ha_run_script()
    print("\nDone. Check Slack @aschoenfeld for the report message.")


if __name__ == "__main__":
    main()
