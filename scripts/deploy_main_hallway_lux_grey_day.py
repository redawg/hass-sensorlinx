#!/usr/bin/env python3
"""Deploy main hallway grey-day lux — delegates to deploy_grey_day_lux_lights.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).resolve().parent / "deploy_grey_day_lux_lights.py"
    raise SystemExit(subprocess.call([sys.executable, str(script)]))
