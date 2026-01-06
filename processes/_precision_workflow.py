"""Shared precision workflow logic for production processes.

Simplified flow: for each joint in each pose, move to the target once, check
error, and retry once if the absolute error exceeds 10 degrees.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

logger = logging.getLogger("process.precision_workflow")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ================= Tunables =================
SPEED_DEFAULT = 100
ERROR_RETRY_THRESHOLD = 10.0

POSE_PAUSE_S = 0.0
JOINT_PAUSE_S = 0.0
# ===========================================

_MOVE_LOCK = threading.Lock()


def _issue_move(arm, mode: str, values: Dict[str, float], *, speed: int, units: str, finalize: bool) -> Dict[str, Any]:
    with _MOVE_LOCK:
        return arm.move(mode, values, speed=speed, units=units, finalize=finalize)


def _move_joint(arm, joint: str, target: float) -> Dict[str, Any]:
    """Move one joint and retry once if the error is too large."""

    result = _issue_move(arm, "absolute", {joint: target}, speed=SPEED_DEFAULT, units="degrees", finalize=True)
    err = 0.0
    if isinstance(result, dict):
        err = abs(float(result.get("final_error_deg", {}).get(joint, 0.0)))

    if err > ERROR_RETRY_THRESHOLD:
        logger.info("%s error %.2f° > %.1f°; retrying", joint, err, ERROR_RETRY_THRESHOLD)
        result = _issue_move(arm, "absolute", {joint: target}, speed=SPEED_DEFAULT, units="degrees", finalize=True)

    return result


def run_workflow(
    arm,
    steps: Sequence[Tuple[str, Dict[str, float]]],
    *,
    joint_order: Iterable[str] = ("D", "C", "B", "A"),
    pose_pause_s: float = POSE_PAUSE_S,
    joint_pause_s: float = JOINT_PAUSE_S,
) -> Dict[str, Any]:
    """Execute the provided ``steps`` using the simplified movement rules."""

    resolved_steps: List[Tuple[str, Dict[str, float]]] = [
        (name, arm.resolve_pose(pose)) for name, pose in steps
    ]

    result: Dict[str, Any] = {}

    for index, (pose_name, target_pose) in enumerate(resolved_steps, start=1):
        logger.info("=== Pose %d/%d: %s ===", index, len(resolved_steps), pose_name)

        for joint in joint_order:
            if joint not in target_pose:
                continue
            target = float(target_pose[joint])
            logger.info("-- %s → %.2f°", joint, target)
            result = _move_joint(arm, joint, target)
            if joint_pause_s > 0:
                logger.info("Pausing %.1fs after %s", joint_pause_s, joint)
                time.sleep(joint_pause_s)

        logger.info("Reached pose: %s", pose_name)
        if index < len(resolved_steps) and pose_pause_s > 0:
            logger.info("Pausing %.1fs between poses", pose_pause_s)
            time.sleep(pose_pause_s)

    logger.info("Process complete.")
    return result


__all__ = [
    "run_workflow",
    "SPEED_DEFAULT",
    "ERROR_RETRY_THRESHOLD",
    "POSE_PAUSE_S",
    "JOINT_PAUSE_S",
]
