"""Pick from assembly side and place on quality side using named points.
Easy-to-debug version: strictly sequential, validates each joint move, and
inserts pauses between both joint moves and poses.
"""

import logging
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# ---------- Tunables ----------
SPEED = 100                  # normal move speed
POSE_PAUSE_S = 10.0          # pause between poses (as you expected)
JOINT_PAUSE_S = 10.0         # pause after each individual joint move
REPEAT_PER_JOINT = 2         # repeat the same joint command N times (settling/backlash)
SETTLE_TIMEOUT_S = 8.0       # how long to wait for a joint to settle at target
SETTLE_TOL = {"A": 2.0, "B": 3.0, "C": 3.0, "D": 3.0}  # per-joint tolerance in degrees
POLL_INTERVAL_S = 0.05       # verify polling interval
RETRY_ON_FAIL = 1            # extra attempts if a joint doesn't settle
RETRY_SLOWDOWN = 0.5         # retry at slower speed (fraction of SPEED)
# ------------------------------


def _wait_until_settled(arm, target: Dict[str, float], timeout_s: float) -> Tuple[bool, Dict[str, float]]:
    """Poll arm.verify_at(target) until within tolerance or timeout."""
    t0 = time.time()
    ok, errs = arm.verify_at(target, SETTLE_TOL)
    while not ok and (time.time() - t0) < timeout_s:
        time.sleep(POLL_INTERVAL_S)
        ok, errs = arm.verify_at(target, SETTLE_TOL)
    return ok, errs


def _move_joint_and_validate(arm, joint: str, target_deg: float, speed: int) -> Dict[str, Any]:
    """One joint → absolute degrees → verify → optional retry → return last summary."""
    last_summary: Dict[str, Any] = {}
    attempt = 0
    while True:
        attempt += 1
        logger.info(">>> Move %s to %.2f° (attempt %d) at speed %d", joint, target_deg, attempt, speed)
        last_summary = arm.move(
            "absolute",
            {joint: target_deg},
            speed=speed,
            units="degrees",
            finalize=True,
        )

        # Wait until we’re actually there (based on arm.current_abs / get_degrees)
        ok, errs = _wait_until_settled(arm, {joint: target_deg}, timeout_s=SETTLE_TIMEOUT_S)
        err = errs.get(joint, 0.0)
        logger.info("... %s settle check: ok=%s err=%.2f°", joint, ok, err)

        if ok:
            return last_summary

        if attempt <= RETRY_ON_FAIL:
            # Retry once at a slower speed to sneak up on the target
            speed = max(20, int(SPEED * RETRY_SLOWDOWN))
            logger.warning("Retrying %s (err=%.2f°); new speed=%d", joint, err, speed)
            continue

        # Give up for this joint
        raise RuntimeError(f"Joint {joint} failed to settle at {target_deg:.2f}° after {attempt} attempt(s); last err={err:.2f}°")


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow using point names, with strict sequencing and pauses."""
    speed = SPEED

    steps = [
        ("Start at home",        {"A": "open",   "B": "min",  "C": "max", "D": "neutral"}),
        ("Pick Right S1",        {"A": "open",   "B": "pick", "C": "max", "D": "assembly"}),
        ("Pick Right S2",        {"A": "open",   "B": "pick", "C": "max", "D": "assembly"}),
        ("Grab",                 {"A": "closed", "B": "pick", "C": "max", "D": "assembly"}),
        ("Pick Right S3",        {"A": "closed", "B": "pick", "C": "max", "D": "assembly"}),
        ("Go Home (except A)",   {"A": "closed", "B": "min",  "C": "min", "D": "neutral"}),
        ("Drop Left S1",         {"A": "closed", "B": "pick", "C": "max", "D": "quality"}),
        ("Drop Left S2",         {"A": "closed", "B": "pick", "C": "max", "D": "quality"}),
        ("Release",              {"A": "open",   "B": "pick", "C": "max", "D": "quality"}),
        ("Drop Left S3",         {"A": "open",   "B": "pick", "C": "max", "D": "quality"}),
        ("Final home",           {"A": "open",   "B": "min",  "C": "max", "D": "neutral"}),
    ]

    # Resolve named points → absolute degrees once
    abs_steps = [(name, arm.resolve_pose(pose)) for name, pose in steps]

    result = None
    prev_pose = None
    joint_order = ("D", "C", "B", "A")

    for idx, (pose_name, target_pose) in enumerate(abs_steps):
        logger.info("=== Pose %d/%d: %s ===", idx + 1, len(abs_steps), pose_name)

        # If we just finished a pose, verify we’re still at it before proceeding
        if prev_pose is not None:
            ok, errs = arm.verify_at(prev_pose, SETTLE_TOL)
            if not ok:
                # If D drift is huge, recover; otherwise nudge back once.
                if errs.get("D", 0) >= 720:
                    logger.error("D reference drift (%.1f°) before step %d; recovering", errs["D"], idx)
                    arm.recover_to_home(speed=30, timeout_s=90.0)
                else:
                    logger.warning("DRIFT before step %d: %s; nudging back", idx, errs)
                    arm.move("absolute", prev_pose, speed=40, units="degrees", finalize=True, finalize_deadband_deg=2.0)
                    ok2, _ = arm.verify_at(prev_pose, SETTLE_TOL)
                    if not ok2:
                        logger.error("DRIFT persists before step %d; recovering", idx)
                        arm.recover_to_home(speed=30, timeout_s=90.0)

        # Move each joint in strict order, verifying after each
        for joint in joint_order:
            if joint not in target_pose:
                continue
            target_deg = float(target_pose[joint])

            for rep in range(1, REPEAT_PER_JOINT + 1):
                logger.info("-- %s → %.2f° (pass %d/%d)", joint, target_deg, rep, REPEAT_PER_JOINT)
                result = _move_joint_and_validate(arm, joint, target_deg, speed)

                # Optional pause between individual joint moves (for observation)
                if JOINT_PAUSE_S > 0:
                    logger.info("Pausing %.1fs after %s move", JOINT_PAUSE_S, joint)
                    time.sleep(JOINT_PAUSE_S)

        logger.info("Reached pose: %s", pose_name)

        # Pause between poses so you can validate visually/with sensors/logs
        if idx < len(abs_steps) - 1 and POSE_PAUSE_S > 0:
            logger.info("Pausing %.1fs between poses", POSE_PAUSE_S)
            time.sleep(POSE_PAUSE_S)

        prev_pose = target_pose

    return result
