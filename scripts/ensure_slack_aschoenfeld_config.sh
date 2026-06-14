#!/usr/bin/env bash
# Write SensorLinx Slack config on Forest Home HA (/config/sensorlinx/*.txt).
# Uses direct Slack Web API — no Home Assistant notify integration required.
set -euo pipefail

HA_HOST="${HA_HOST:-172.16.255.250}"
HA_USER="${HA_USER:-root}"
REMOTE_DIR="/config/sensorlinx"
CHANNEL="${SLACK_CHANNEL:-#sensorlinx-reports}"
MENTION="${SLACK_MENTION:-@aschoenfeld}"

ssh_cmd() {
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${HA_USER}@${HA_HOST}" "$@"
}

echo "=== Ensure SensorLinx Slack config on ${HA_USER}@${HA_HOST} ==="
ssh_cmd "mkdir -p ${REMOTE_DIR}"
ssh_cmd "printf '%s\n' '${CHANNEL}' > ${REMOTE_DIR}/slack_channel.txt"
ssh_cmd "printf '%s\n' '${MENTION}' > ${REMOTE_DIR}/slack_mention.txt"
echo "Wrote channel + mention. Add slack_bot_token.txt or slack_webhook.txt separately."
ssh_cmd "cat ${REMOTE_DIR}/slack_channel.txt ${REMOTE_DIR}/slack_mention.txt"
echo "Done."
