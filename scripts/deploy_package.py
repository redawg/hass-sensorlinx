#!/usr/bin/env python3
"""Deploy the outdoor_reset.yaml package to Forest Home via SSH."""
import subprocess
import os
import sys

HA_HOST = "172.16.255.250"
HA_USER = "root"
LOCAL_FILE = os.path.join(os.path.dirname(__file__), "..", "packages", "outdoor_reset.yaml")
REMOTE_DIR = "/config/packages"
REMOTE_FILE = f"{REMOTE_DIR}/outdoor_reset.yaml"

def run_ssh(cmd, timeout=15):
    """Run a command over SSH."""
    full_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                f"{HA_USER}@{HA_HOST}", cmd]
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr

def run_scp(local, remote, timeout=30):
    """SCP a file to the remote host."""
    full_cmd = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                local, f"{HA_USER}@{HA_HOST}:{remote}"]
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr

# Step 1: Create packages directory on HA
print("=== Creating /config/packages directory ===")
rc, out, err = run_ssh(f"mkdir -p {REMOTE_DIR}")
print(f"  rc={rc} out={out.strip()} err={err.strip()}")

# Step 2: Check if packages: is in configuration.yaml
print("\n=== Checking configuration.yaml for packages directive ===")
rc, out, err = run_ssh("grep -c 'packages:' /config/configuration.yaml")
if rc == 0 and out.strip() != "0":
    print("  packages: already present in configuration.yaml")
else:
    print("  Adding packages: !include_dir_named packages to configuration.yaml")
    rc, out, err = run_ssh(
        "echo '' >> /config/configuration.yaml && "
        "echo 'homeassistant:' >> /config/configuration.yaml && "
        "echo '  packages: !include_dir_named packages' >> /config/configuration.yaml"
    )
    print(f"  rc={rc} err={err.strip()}")

# Step 3: SCP the package file
print(f"\n=== Uploading {LOCAL_FILE} -> {REMOTE_FILE} ===")
rc, out, err = run_scp(os.path.abspath(LOCAL_FILE), REMOTE_FILE)
print(f"  rc={rc} out={out.strip()} err={err.strip()}")
if rc != 0:
    print("  ERROR: SCP failed!")
    sys.exit(1)

# Step 4: Verify file exists on remote
print("\n=== Verifying uploaded file ===")
rc, out, err = run_ssh(f"wc -l {REMOTE_FILE}")
print(f"  {out.strip()}")

print("\n=== Done! Restart HA to load the package. ===")
