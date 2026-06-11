#!/usr/bin/env python3
"""Push file to GitHub via API."""
import requests
import base64
import pathlib
import json

GITHUB_TOKEN = None  # Will use MCP, but let's try direct API
OWNER = "redawg"
REPO = "hbx-sensorlinx-ha"

# Read the file
file_path = pathlib.Path(r"C:\Users\andre\Projects\hbx-sensorlinx-ha\custom_components\sensorlinx\outdoor_reset.py")
content = file_path.read_text(encoding="utf-8")

# We need to get the current file SHA first, then update
# But since we're using the MCP, let's just output the content length
print(f"File: {file_path.name}")
print(f"Size: {len(content)} chars, {len(content.encode('utf-8'))} bytes")
print("Ready for MCP push")
