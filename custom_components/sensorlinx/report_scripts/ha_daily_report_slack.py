#!/usr/bin/env python3
"""Run daily performance report and post summary to Slack via the Slack Web API.

Designed to run on Home Assistant at:
  /config/sensorlinx/ha_daily_report_slack.py

Slack credentials (one of):
  /config/sensorlinx/slack_bot_token.txt  (xoxb- bot token, preferred)
  /config/sensorlinx/slack_webhook.txt      (incoming webhook URL)
  SLACK_BOT_TOKEN / SLACK_WEBHOOK_URL env vars

Also runnable locally from the repo for testing.
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Support both HA deploy path and local repo path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from daily_hvac_report import main as run_report  # noqa: E402

SLACK_CHANNEL_FILE = Path("/config/sensorlinx/slack_channel.txt")
SLACK_MENTION_FILE = Path("/config/sensorlinx/slack_mention.txt")
SLACK_BOT_TOKEN_FILE = Path("/config/sensorlinx/slack_bot_token.txt")
SLACK_WEBHOOK_FILE = Path("/config/sensorlinx/slack_webhook.txt")
REPORT_ARCHIVE = Path("/config/sensorlinx/daily_report_latest.txt")
AGENT_LOG_PATH = (
    Path("/config/sensorlinx/agent_adjustments.jsonl")
    if Path("/config").exists()
    else SCRIPT_DIR / "agent_adjustments.jsonl"
)
DEFAULT_SLACK_CHANNEL = "#sensorlinx-reports"
DEFAULT_SLACK_MENTION = "@aschoenfeld"

ENTITY_LABELS = {
    "switch.sensorlinx_outdoor_reset_supply_water_reset_enabled": "Supply water reset",
    "water_heater.main_water_heater": "Tankless setpoint",
    "number.sensorlinx_outdoor_reset_supply_water_min_temp_mild": "Supply min (mild)",
    "number.sensorlinx_outdoor_reset_supply_water_max_temp_cold": "Supply max (cold)",
}
# Slack text limit is ~4000 chars; keep chunks smaller for code fences + titles.
SLACK_CHUNK_MAX = 2800
SLACK_POST_DELAY = 1.0
# Match numbered report sections (e.g. "  1. SYSTEM OVERVIEW"), not list items.
SECTION_RE = re.compile(r"^\s*(\d+)\.\s+([A-Z][A-Z0-9\s/&().'-]*)$")


def load_slack_bot_token():
    if os.environ.get("SLACK_BOT_TOKEN"):
        return os.environ["SLACK_BOT_TOKEN"].strip()
    if SLACK_BOT_TOKEN_FILE.exists():
        return SLACK_BOT_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return None


def load_slack_webhook():
    if os.environ.get("SLACK_WEBHOOK_URL"):
        return os.environ["SLACK_WEBHOOK_URL"].strip()
    if SLACK_WEBHOOK_FILE.exists():
        return SLACK_WEBHOOK_FILE.read_text(encoding="utf-8").strip()
    return None


def slack_configured():
    return bool(load_slack_bot_token() or load_slack_webhook())


def load_slack_mention():
    if os.environ.get("SLACK_MENTION"):
        return os.environ["SLACK_MENTION"].strip()
    if SLACK_MENTION_FILE.exists():
        return SLACK_MENTION_FILE.read_text(encoding="utf-8").strip()
    return DEFAULT_SLACK_MENTION


def load_slack_channel():
    if os.environ.get("SLACK_CHANNEL"):
        ch = os.environ["SLACK_CHANNEL"].strip()
    elif SLACK_CHANNEL_FILE.exists():
        ch = SLACK_CHANNEL_FILE.read_text(encoding="utf-8").strip()
    else:
        ch = DEFAULT_SLACK_CHANNEL
    if ch and not ch.startswith(("#", "@")):
        ch = f"#{ch}"
    return ch


def _slack_post_api(message, channel, title=None):
    """Post a message using chat.postMessage and a bot token."""
    token = load_slack_bot_token()
    text = f"*{title}*\n{message}" if title else message
    payload = {"channel": channel, "text": text, "mrkdwn": True}
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        return 400, body.get("error", "unknown_error")
    return 200, ""


def _slack_post_webhook(message, title=None):
    """Post a message using an incoming webhook URL."""
    webhook = load_slack_webhook()
    text = f"*{title}*\n{message}" if title else message
    payload = json.dumps({"text": text, "mrkdwn": True}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read().decode("utf-8")


def extract_report_body(report_text):
    """Return the printable report body between header and END OF REPORT."""
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
    return "\n".join(keep) if keep else report_text


def split_into_sections(body):
    """Split report body into (section_title, section_text) tuples."""
    lines = body.splitlines()
    sections = []
    preamble = []
    current_title = None
    current_lines = []

    for line in lines:
        match = SECTION_RE.match(line)
        if match:
            if current_title is not None:
                sections.append((current_title, "\n".join(current_lines).strip()))
            elif preamble:
                sections.append(("Overview", "\n".join(preamble).strip()))
                preamble = []
            current_title = f"{match.group(1)}. {match.group(2).strip()}"
            current_lines = [line]
        elif current_title is None:
            preamble.append(line)
        else:
            current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, "\n".join(current_lines).strip()))
    elif preamble:
        sections.append(("Report", "\n".join(preamble).strip()))

    return sections


def chunk_text(text, max_len):
    """Split text into chunks at line boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks


def entity_label(entity_id):
    return ENTITY_LABELS.get(entity_id, entity_id)


def format_adjustment_line(adj):
    label = entity_label(adj.get("entity", ""))
    old = adj.get("old")
    new = adj.get("new")
    reason = adj.get("reason", "")
    change = f"{old} -> {new}"
    return f"• *{label}*: `{change}`\n  _{reason}_"


def log_adjustments(result):
    """Persist applied agent actions for audit trail."""
    if not result:
        return
    actions = []
    for key in ("applied", "orchestrator_applied"):
        for a in result.get(key) or []:
            if a.get("applied"):
                actions.append(a)
    if not actions:
        return
    entry = {
        "ts": datetime.now().isoformat(),
        "overall_score": result.get("overall_score"),
        "hydronic_score": result.get("hydronic_score"),
        "actions": [
            {
                "entity": a.get("entity"),
                "old": a.get("old"),
                "new": a.get("new"),
                "reason": a.get("reason"),
                "applied": a.get("applied"),
            }
            for a in actions
        ],
    }
    try:
        AGENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"Could not write agent log: {exc}")


def build_andrew_changes_message(result):
    """Slack message for Andrew listing agent changes applied this run."""
    mention = load_slack_mention()
    lines = [
        f"{mention} *SensorLinx agent run* "
        f"({datetime.now().strftime('%b %d %I:%M %p')})",
        "_Supply water adjustments are applied automatically on every report run._",
        "",
    ]
    if not result:
        lines.append("No result returned from report.")
        return "\n".join(lines)

    lines.append(
        f"Scores: overall *{result.get('overall_score', '?')}/100* | "
        f"hydronic *{result.get('hydronic_score', '?')}/100*"
    )
    lines.append("")

    applied = result.get("applied") or []
    ok = [a for a in applied if a.get("applied")]
    failed = [a for a in applied if not a.get("applied")]

    if ok:
        lines.append(f"*Applied ({len(ok)}):*")
        for adj in ok:
            lines.append(format_adjustment_line(adj))
        lines.append("")
    elif result.get("adjustments"):
        lines.append("*No changes applied* (API call failed or blocked).")
        lines.append("")
    else:
        lines.append("*No supply water adjustments needed* — parameters look optimal.")
        lines.append("")

    if failed:
        lines.append(f"*Failed ({len(failed)}):*")
        for adj in failed:
            lines.append(format_adjustment_line(adj))

    return "\n".join(lines)


def build_report_header(result):
    header = (
        f"*HBX SensorLinx Daily Report*\n"
        f"{datetime.now().strftime('%A %b %d %Y %I:%M %p')}\n"
        f"Agent mode: *LIVE* (adjustments applied automatically)\n"
    )
    if result:
        header += (
            f"Overall: *{result.get('overall_score', '?')}/100* | "
            f"Hydronic: *{result.get('hydronic_score', '?')}/100*\n"
        )
        applied = result.get("applied") or []
        applied_ok = [a for a in applied if a.get("applied")]
        if applied_ok:
            header += f"Adjustments applied: *{len(applied_ok)}* (see message for {load_slack_mention()})\n"
        else:
            header += "No supply water adjustments applied this run\n"
    return header


def build_slack_messages(report_text, result):
    """Build a list of Slack messages — header plus one or more per report section."""
    body = extract_report_body(report_text)
    sections = split_into_sections(body)
    messages = [
        build_report_header(result),
        build_andrew_changes_message(result),
    ]

    if not sections:
        for chunk in chunk_text(body, SLACK_CHUNK_MAX):
            messages.append(f"```\n{chunk}\n```")
        return messages

    total = len(sections)
    messages[0] += f"\nFull report split into *{total}* sections below."

    for index, (title, content) in enumerate(sections, 1):
        chunks = chunk_text(content, SLACK_CHUNK_MAX)
        for part, chunk in enumerate(chunks, 1):
            label = f"*{title}* ({index}/{total})"
            if len(chunks) > 1:
                label += f" part {part}/{len(chunks)}"
            messages.append(f"{label}\n```\n{chunk}\n```")

    return messages


def send_slack(message, channel=None, title="SensorLinx Daily Report"):
    channel = channel or load_slack_channel()
    if load_slack_bot_token():
        return _slack_post_api(message, channel, title=title)
    if load_slack_webhook():
        return _slack_post_webhook(message, title=title)
    return 503, "Slack not configured"


def send_slack_messages(messages, channel=None):
    channel = channel or load_slack_channel()
    sent = 0
    for i, message in enumerate(messages):
        if i == 0:
            title = "SensorLinx Daily Report"
        elif i == 1:
            title = "SensorLinx Agent Changes"
        else:
            title = f"SensorLinx Report ({i + 1}/{len(messages)})"
        status, body = send_slack(message, channel=channel, title=title)
        if status != 200:
            return status, body, sent
        sent += 1
        if i + 1 < len(messages):
            time.sleep(SLACK_POST_DELAY)
    return 200, "", sent


def archive_report(report_text):
    try:
        REPORT_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_ARCHIVE.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"Could not archive report: {exc}")


def main():
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        result = run_report(apply_supply=True)
    finally:
        sys.stdout = old_stdout

    report_text = buf.getvalue()
    archive_report(report_text)
    log_adjustments(result)
    messages = build_slack_messages(report_text, result)

    if not slack_configured():
        print(
            "Slack not configured — report archived only. "
            "Set /config/sensorlinx/slack_bot_token.txt or slack_webhook.txt"
        )
        return 0

    print(f"Posting {len(messages)} Slack message(s)")
    for i, message in enumerate(messages, 1):
        print(f"\n--- Slack message {i}/{len(messages)} ---\n{message[:500]}...")

    try:
        status, body, sent = send_slack_messages(messages)
        print(f"Slack post: HTTP {status}, sent {sent}/{len(messages)}")
        if status != 200:
            print(body)
            return 1
        if sent != len(messages):
            return 1
    except urllib.error.HTTPError as exc:
        print(f"Slack post failed: HTTP {exc.code} {exc.read().decode('utf-8')[:300]}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Slack post failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
