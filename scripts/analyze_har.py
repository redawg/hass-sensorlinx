#!/usr/bin/env python3
"""Summarize SensorLinx API calls from a HAR export.

Use this when you need to extend the integration beyond what pysensorlinx
already covers. Export a HAR file from mitmproxy, HTTP Toolkit, or Chrome
DevTools while using the SensorLinx Android app.

Usage:
  python scripts/analyze_har.py captures/session.har
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from urllib.parse import urlparse


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/analyze_har.py <file.har>", file=sys.stderr)
        return 1

    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        har = json.load(fh)

    entries = har.get("log", {}).get("entries", [])
    hosts = Counter()
    endpoints = Counter()
    methods = Counter()
    sensorlinx_calls: list[dict] = []

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        method = req.get("method", "")
        parsed = urlparse(url)
        hosts[parsed.netloc] += 1
        methods[method] += 1

        if "sensorlinx" in parsed.netloc:
            path_only = parsed.path
            endpoints[f"{method} {path_only}"] += 1

            post_data = req.get("postData", {})
            body = post_data.get("text")
            sensorlinx_calls.append(
                {
                    "method": method,
                    "url": url,
                    "body": body[:300] if body else None,
                    "status": entry.get("response", {}).get("status"),
                }
            )

    print(f"Total requests: {len(entries)}\n")
    print("Top hosts:")
    for host, count in hosts.most_common(10):
        print(f"  {host}: {count}")

    print("\nSensorLinx endpoints:")
    for endpoint, count in endpoints.most_common():
        print(f"  {endpoint}: {count}")

    print("\nSample SensorLinx calls:")
    for call in sensorlinx_calls[:15]:
        print(f"  {call['status']} {call['method']} {call['url']}")
        if call["body"]:
            print(f"    body: {call['body']}")

    print(
        "\nKnown API base: https://mobile.sensorlinx.co"
        "\nAuth endpoint:  POST /account/login"
        "\nDevices:        GET  /buildings/{id}/devices"
        "\nUpdates:        PATCH /buildings/{id}/devices/{syncCode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
