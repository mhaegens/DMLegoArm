"""Gracefully park the arm then shut down the host."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any


LOGGER = logging.getLogger(__name__)

# Delay after reaching the shutdown pose before powering off (seconds)
SHUTDOWN_DELAY_S = 3.0


def _schedule_shutdown():
    """Trigger OS shutdown in a background thread."""

    def _do_shutdown():
        time.sleep(SHUTDOWN_DELAY_S)
        try:
            subprocess.Popen(["sudo", "shutdown", "-h", "now"])
            LOGGER.info("Shutdown command issued after %.1fs delay", SHUTDOWN_DELAY_S)
        except Exception:  # pragma: no cover - best-effort safety
            LOGGER.exception("Failed to execute shutdown command")

    threading.Thread(target=_do_shutdown, daemon=True).start()


def run(arm) -> Any:
    """Move to a safe pose, pause, then power off the Pi.

    The shutdown pose is:
    - A: open
    - B: min
    - C: min
    - D: neutral
    """

    target_pose = {"A": "open", "B": "min", "C": "min", "D": "neutral"}
    LOGGER.info("Shutdown: moving to pose %s", target_pose)

    resolved = arm.resolve_pose(target_pose)
    move_result = arm.move(
        "absolute", resolved, speed=30, units="degrees", finalize=True, timeout_s=120
    )

    LOGGER.info("Shutdown: pose reached, scheduling power off")
    _schedule_shutdown()

    return {
        "pose": resolved,
        "move_result": move_result,
        "shutdown_delay_s": SHUTDOWN_DELAY_S,
    }
