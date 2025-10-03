"""Shared precision workflow logic for production processes.

The recent mechanical change made joint D (the wrist rotation) almost
direct-drive. That removes the need for the aggressive backlash
compensation we previously employed, but it also means we must cap the
speed at 6 and use very small, deliberate nudges when fine-tuning.  The
helpers in this module encapsulate that behaviour so both production
flows can reuse it without duplicating logic.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

logger = logging.getLogger("process.precision_workflow")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ================= Tunables =================
# Speeds for the unchanged joints.  We still break long moves into two
# phases (full speed then a slower final approach) because their gear
# trains did not change.
SPEED_ABC = 100
SPEED_ABC_FINAL = 35
SPEED_ABC_FINE = 25
SEGMENT_DEG_ABC = 120.0

# Joint D: always slow and precise.
SPEED_D_MAX = 6
SPEED_D_FINAL = 2
SPEED_D_FINE = 1

# Position tolerances (degrees).
TOL = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 1.0}

# Verification parameters.
SETTLE_TIMEOUT_S = 6.0
POLL_INTERVAL_S = 0.05
STABLE_OK_POLLS = 2
STABLE_WINDOW_S = 0.5

# Fine-tuning parameters.
RETRY_ON_FAIL = 1
REPEAT_PER_JOINT = 2
FINE_MAX_STEPS = 5
FINE_GAIN_ABC = 0.65
FINE_STEP_MIN_ABC = 0.8
FINE_STEP_MAX_ABC = 20.0
FINE_GAIN_D = 0.70
FINE_STEP_MIN_D = 0.4
FINE_STEP_MAX_D = 8.0

# Cosmetic pauses so operators can observe each move.
POSE_PAUSE_S = 10.0
JOINT_PAUSE_S = 10.0
# ===========================================


def _verify_stable(arm, target: Dict[str, float], timeout_s: float) -> Tuple[bool, Dict[str, float]]:
    """Require a couple of consecutive OK reads inside ``timeout_s``."""

    t0 = time.time()
    ok, errs = arm.verify_at(target, TOL)
    stable = 1 if ok else 0
    last_change = time.time()

    while (time.time() - t0) < timeout_s:
        time.sleep(POLL_INTERVAL_S)
        ok, errs = arm.verify_at(target, TOL)
        if ok:
            stable += 1
            if stable >= STABLE_OK_POLLS and (time.time() - last_change) >= STABLE_WINDOW_S:
                return True, errs
        else:
            stable = 0
            last_change = time.time()
    return False, errs


def _read_pos(arm, joint: str) -> float:
    try:
        return float(arm.read_position().get(joint))
    except Exception:  # pragma: no cover - defensive; depends on device driver
        return float("nan")


def _abs_move(arm, joint: str, target: float, *, speed: int, use_segments: bool) -> Dict[str, Any]:
    """Absolute move with optional segmentation (used for joints A/B/C)."""

    if joint == "D":
        speed = min(speed, SPEED_D_MAX)

    if not use_segments:
        return arm.move("absolute", {joint: target}, speed=speed, units="degrees", finalize=True)

    current = _read_pos(arm, joint)
    delta = target - current if current == current else 0.0
    last: Dict[str, Any] = {}

    if current == current and abs(delta) > SEGMENT_DEG_ABC:
        step = SEGMENT_DEG_ABC if delta > 0 else -SEGMENT_DEG_ABC
        pos = current
        while abs(target - pos) > SEGMENT_DEG_ABC:
            pos += step
            last = arm.move("absolute", {joint: pos}, speed=SPEED_ABC, units="degrees", finalize=True)

        last = arm.move("absolute", {joint: target}, speed=SPEED_ABC_FINAL, units="degrees", finalize=True)
    else:
        last = arm.move("absolute", {joint: target}, speed=speed, units="degrees", finalize=True)

    return last


def _fine_correct(arm, joint: str, target: float) -> bool:
    """Perform small relative nudges toward ``target``."""

    tol = TOL.get(joint, 3.0)
    ok, _ = _verify_stable(arm, {joint: target}, timeout_s=0.8)
    if ok:
        return True

    if joint == "D":
        gain = FINE_GAIN_D
        step_min = FINE_STEP_MIN_D
        step_max = FINE_STEP_MAX_D
        speed = SPEED_D_FINE
    else:
        gain = FINE_GAIN_ABC
        step_min = FINE_STEP_MIN_ABC
        step_max = FINE_STEP_MAX_ABC
        speed = SPEED_ABC_FINE

    for attempt in range(1, FINE_MAX_STEPS + 1):
        current = _read_pos(arm, joint)
        if current == current:
            error = target - current
            if abs(error) <= tol:
                return True
            step = max(step_min, min(step_max, abs(error) * gain))
            correction = step if error > 0 else -step
        else:
            correction = step_min

        logger.warning(
            "%s fine %d/%d: %+0.2f° @%d",
            joint,
            attempt,
            FINE_MAX_STEPS,
            correction,
            speed,
        )
        arm.move(
            "relative",
            {joint: correction},
            speed=min(speed, SPEED_D_MAX) if joint == "D" else speed,
            units="degrees",
            finalize=True,
        )

        ok, _ = _verify_stable(arm, {joint: target}, timeout_s=1.0 if joint == "D" else 0.8)
        if ok:
            return True

    return False


def _move_joint(arm, joint: str, target: float) -> Dict[str, Any]:
    """Move one joint with verification, retries, and fine correction."""

    use_segments = joint in ("A", "B", "C")
    base_speed = SPEED_ABC if use_segments else SPEED_D_MAX
    last: Dict[str, Any] = {}
    attempt = 0
    current_speed = base_speed

    while True:
        attempt += 1
        logger.info(
            ">>> Move %s to %.2f° (attempt %d) @%d",
            joint,
            target,
            attempt,
            current_speed,
        )
        last = _abs_move(arm, joint, target, speed=current_speed, use_segments=use_segments)

        ok, errs = _verify_stable(arm, {joint: target}, timeout_s=SETTLE_TIMEOUT_S)
        err_abs = float(abs(errs.get(joint, 0.0))) if isinstance(errs, dict) and joint in errs else float("nan")
        logger.info("... %s settle: ok=%s err≈%.2f°", joint, ok, err_abs if err_abs == err_abs else float("nan"))
        if ok:
            return last

        if attempt <= RETRY_ON_FAIL:
            current_speed = SPEED_ABC_FINAL if use_segments else SPEED_D_FINAL
            logger.warning("Retrying %s at slower speed=%d", joint, current_speed)
            continue
        break

    good = _fine_correct(arm, joint, target)
    if not good:
        raise RuntimeError(
            f"Joint {joint} failed to settle at {target:.2f}° after {attempt} absolute attempt(s) + fine correction"
        )
    return last


def run_workflow(
    arm,
    steps: Sequence[Tuple[str, Dict[str, float]]],
    *,
    joint_order: Iterable[str] = ("D", "C", "B", "A"),
    pose_pause_s: float = POSE_PAUSE_S,
    joint_pause_s: float = JOINT_PAUSE_S,
) -> Dict[str, Any]:
    """Execute the provided ``steps`` using the precision movement rules."""

    resolved_steps: List[Tuple[str, Dict[str, float]]] = [
        (name, arm.resolve_pose(pose)) for name, pose in steps
    ]

    result: Dict[str, Any] = {}
    previous_pose: Dict[str, float] | None = None

    for index, (pose_name, target_pose) in enumerate(resolved_steps, start=1):
        logger.info("=== Pose %d/%d: %s ===", index, len(resolved_steps), pose_name)

        if previous_pose is not None:
            ok_prev, errs_prev = _verify_stable(arm, previous_pose, timeout_s=1.0)
            if not ok_prev:
                logger.warning("Drift before step %d (%s). Nudging back.", index, errs_prev)
                arm.move("absolute", previous_pose, speed=40, units="degrees", finalize=True)
                _verify_stable(arm, previous_pose, timeout_s=1.0)

        for joint in joint_order:
            if joint not in target_pose:
                continue
            target = float(target_pose[joint])

            for rep in range(1, REPEAT_PER_JOINT + 1):
                logger.info("-- %s → %.2f° (pass %d/%d)", joint, target, rep, REPEAT_PER_JOINT)
                result = _move_joint(arm, joint, target)
                if joint_pause_s > 0:
                    logger.info("Pausing %.1fs after %s", joint_pause_s, joint)
                    time.sleep(joint_pause_s)

        logger.info("Reached pose: %s", pose_name)
        if index < len(resolved_steps) and pose_pause_s > 0:
            logger.info("Pausing %.1fs between poses", pose_pause_s)
            time.sleep(pose_pause_s)

        previous_pose = target_pose

    logger.info("Process complete.")
    return result


__all__ = [
    "run_workflow",
    "SPEED_ABC",
    "SPEED_ABC_FINAL",
    "SPEED_ABC_FINE",
    "SPEED_D_MAX",
    "SPEED_D_FINAL",
    "SPEED_D_FINE",
    "POSE_PAUSE_S",
    "JOINT_PAUSE_S",
]

