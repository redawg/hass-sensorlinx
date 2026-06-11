"""Schedule and service handler for daily performance reports to Slack."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REPORT_SCRIPT = Path(__file__).parent / "report_scripts" / "ha_daily_report_slack.py"
SCHEDULE_HOUR = 7
SCHEDULE_MINUTE = 0


def _run_report_script() -> tuple[int, str]:
    """Execute the report script in a subprocess (blocking)."""
    if not REPORT_SCRIPT.exists():
        return 1, f"Report script not found: {REPORT_SCRIPT}"
    result = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPORT_SCRIPT.parent),
        timeout=300,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output


async def async_run_daily_report(hass: HomeAssistant) -> None:
    """Run the daily report and post to Slack."""
    _LOGGER.info("Starting daily performance report")
    try:
        returncode, output = await hass.async_add_executor_job(_run_report_script)
    except asyncio.TimeoutError:
        _LOGGER.error("Daily report timed out after 300s")
        await hass.services.async_call(
            "notify",
            "schoenfeld",
            {"message": "SensorLinx daily report FAILED: timed out after 5 minutes"},
        )
        return

    if returncode != 0:
        _LOGGER.error("Daily report failed (rc=%s): %s", returncode, output[-500:])
        await hass.services.async_call(
            "notify",
            "schoenfeld",
            {
                "message": (
                    f"SensorLinx daily report FAILED (exit {returncode}):\n"
                    f"```{output[-1500:]}```"
                ),
            },
        )
        return

    _LOGGER.info("Daily report completed successfully")


async def _handle_run_daily_report(call: ServiceCall) -> None:
    await async_run_daily_report(call.hass)


async def async_setup_daily_report(hass: HomeAssistant) -> None:
    """Register the daily report service and 7 AM schedule."""
    if REPORT_SCRIPT.exists():
        _LOGGER.info("Daily report script: %s", REPORT_SCRIPT)
    else:
        _LOGGER.warning("Daily report script missing: %s", REPORT_SCRIPT)

    async def scheduled_report(now):
        await async_run_daily_report(hass)

    async_track_time_change(
        hass,
        scheduled_report,
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
    )

    hass.services.async_register(
        DOMAIN,
        "run_daily_report",
        _handle_run_daily_report,
    )
    _LOGGER.info(
        "Daily report scheduled at %02d:%02d, service: %s.run_daily_report",
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
        DOMAIN,
    )
