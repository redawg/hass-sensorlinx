#!/usr/bin/env bash
# Write SensorLinx Slack targets on Forest Home HA (/config/sensorlinx/*.txt).
# Matches ha_daily_report_slack.py defaults: @aschoenfeld + notify.schoenfeld
set -euo pipefail

HA_HOST="${HA_HOST:-172.16.255.250}"
HA_USER="${HA_USER:-root}"
REMOTE_DIR="/config/sensorlinx"
CHANNEL="${SLACK_CHANNEL:-@aschoenfeld}"
MENTION="${SLACK_MENTION:-@aschoenfeld}"
NOTIFY="${SLACK_NOTIFY_SERVICE:-schoenfeld}"

ssh_cmd() {
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${HA_USER}@${HA_HOST}" "$@"
}

echo "=== Ensure SensorLinx Slack config on ${HA_USER}@${HA_HOST} ==="
ssh_cmd "mkdir -p ${REMOTE_DIR}"
ssh_cmd "printf '%s\n' '${CHANNEL}' > ${REMOTE_DIR}/slack_channel.txt"
ssh_cmd "printf '%s\n' '${MENTION}' > ${REMOTE_DIR}/slack_mention.txt"
ssh_cmd "printf '%s\n' '${NOTIFY}' > ${REMOTE_DIR}/slack_notify_service.txt"
echo "Wrote:"
ssh_cmd "cat ${REMOTE_DIR}/slack_channel.txt ${REMOTE_DIR}/slack_mention.txt ${REMOTE_DIR}/slack_notify_service.txt"
echo "Done."
