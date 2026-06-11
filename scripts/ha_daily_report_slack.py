#!/usr/bin/env python3
"""Run daily performance report and post summary to Slack via HA notify.schoenfeld.

Designed to run on Home Assistant at:
  /config/sensorlinx/ha_daily_report_slack.py

Also runnable locally from the repo for testing.
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Support both HA deploy path and local repo path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from daily_hvac_report import main as run_report  # noqa: E402

if Path("/config").exists():
    _DEFAULT_HA = "http://127.0.0.1:8123"
else:
    _DEFAULT_HA = "http://172.16.255.250:8123"
HA_HOST = os.environ.get("HA_HOST", _DEFAULT_HA)
TOKEN_FILE = Path("/config/sensorlinx/.ha_token")
DEFAULT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ."
    "Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
)
SLACK_MAX = 3500


def load_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return os.environ.get("HA_TOKEN", DEFAULT_TOKEN)


def ha_post(path, payload, token):
    url = f"{HA_HOST.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read().decode("utf-8")


def build_slack_summary(report_text, result):
    """Condense the full report into a Slack-friendly summary."""
    lines = report_text.splitlines()
    keep = []
    capture = False
    for line in lines:
        if line.strip().startswith("DAILY RADIANT FLOOR SYSTEM REPORT"):
            capture = True
        if capture:
            keep.append(line)
        if line.strip() == "END OF REPORT":
            break

    body = "\n".join(keep) if keep else report_text
    if len(body) > SLACK_MAX:
        body = body[:SLACK_MAX] + "\n...(truncated)"

    header = (
        f"*HBX SensorLinx Daily Report* - "
        f"{datetime.now().strftime('%A %b %d %Y %I:%M %p')}\n"
    )
    if result:
        header += (
            f"Overall: *{result.get('overall_score', '?')}/100* | "
            f"Hydronic: *{result.get('hydronic_score', '?')}/100*\n"
        )
        applied = result.get("applied") or []
        applied_ok = [a for a in applied if a.get("applied")]
        if applied_ok:
            header += f"Adjustments applied: *{len(applied_ok)}*\n"
        elif result.get("adjustments"):
            header += "Adjustments recommended (see full report)\n"
        else:
            header += "No supply water adjustments needed\n"

    return header + "```\n" + body + "\n```"


def send_slack(message, token):
    status, body = ha_post(
        "/api/services/notify/schoenfeld",
        {
            "message": message,
            "title": "SensorLinx Daily Report",
        },
        token,
    )
    return status, body


def main():
    token = load_token()
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        result = run_report(apply_supply=True)
    finally:
        sys.stdout = old_stdout

    report_text = buf.getvalue()
    summary = build_slack_summary(report_text, result)

    # Also print locally for shell_command logs
    print(summary)

    try:
        status, body = send_slack(summary, token)
        print(f"Slack notify: HTTP {status}")
        if status != 200:
            print(body)
            return 1
    except urllib.error.HTTPError as exc:
        print(f"Slack notify failed: HTTP {exc.code} {exc.read().decode('utf-8')[:300]}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Slack notify failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
