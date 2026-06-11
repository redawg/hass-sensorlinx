#!/usr/bin/env python3
"""Daily Performance Agent.

Evaluates radiant floor + hydronic system performance, adjusts main water
temperature for optimal delivery, and produces a full specialist report
including supply water changes and efficiency metrics.

Usage:
    python scripts/daily_report_agent.py          # analyze + apply adjustments
    python scripts/daily_report_agent.py --dry-run  # report only, no changes
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_hvac_report import main as run_report

LOG_PATH = Path(__file__).parent / "agent_adjustments.jsonl"


def log_adjustments(result):
    """Persist agent actions for audit trail."""
    if not result or not result.get("applied"):
        return
    entry = {
        "ts": datetime.now().isoformat(),
        "overall_score": result.get("overall_score"),
        "hydronic_score": result.get("hydronic_score"),
        "actions": [
            {
                "entity": a["entity"],
                "old": a["old"],
                "new": a["new"],
                "reason": a["reason"],
                "applied": a["applied"],
            }
            for a in result["applied"]
        ],
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Daily performance agent - evaluate, adjust supply water, report"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate report and recommendations without applying changes",
    )
    args = parser.parse_args()

    print("=" * 78)
    print("  DAILY PERFORMANCE AGENT")
    print(f"  Mode: {'DRY RUN (no changes)' if args.dry_run else 'LIVE (apply adjustments)'}")
    print("=" * 78)
    print()

    result = run_report(apply_supply=not args.dry_run)

    if result and not args.dry_run:
        log_adjustments(result)
        applied_count = sum(1 for a in (result.get("applied") or []) if a.get("applied"))
        if applied_count:
            print(f"  Agent applied {applied_count} supply water adjustment(s).")
            print(f"  Log: {LOG_PATH}")
            print()


if __name__ == "__main__":
    main()
