"""
Pick from assembly side and place on quality side using named points.
Easy-to-debug version: strictly sequential, validates each joint move, and
inserts pauses between both joint moves and poses.
"""

import logging
import time
from typing import Any, Dict, Tuple

# --------------------------------------------------------------------
# Logging: make sure our messages are visible in the logs you're tailing
# --------------------------------------------------------------------
logger = logging.getLogger("process.pick_assembly_quality")
if not logging.getLogger().handlers:  # don't duplicate if app already configures logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ---------- Tunables ----------
SPEED = 100                  # normal move speed
POSE_PAUSE_S = 10.0          # pause between poses
JOINT_PAUSE_S = 10.0         # pause after each individual joint move
REPEAT_PER_JOINT = 2         # repeat the same joint command N times (settling/backlash)
SETTLE_TIMEOUT_S = 8.0       # how long to wait for a joint to settle at target
SETTLE_TOL = {"A": 2.0, "B": 3.0, "C": 3.0, "D": 3.0}  # per-joint tolerance in degrees
POLL_INTERVAL_S = 0.05       # verify polling interval
RETRY_ON_FAIL = 1            # extra attempts if a joint doesn't settle
RETRY_SLOWDOWN = 0.5         # retry at slower speed (fraction of SPEED)

# Backlash-aware fine approach for D (keeps tight tolerance, adds gentle nudges)
D_FINE_RETRIES = 4           # extra chances after the first settle check
D_FINE_SPEED_FRAC = 0.35     # slow, gentle corrections
D_FINE_STEP_MIN = 1.0        # degrees, minimum nudge size
D_FINE_STEP_MAX = 20.0       # degrees, cap if error is large
D_FINE_STEP_FACTOR = 0.6     # scale of measured error to step by
# ------------------------------


def _wait_until_settled(arm, target: Dict[str, float], timeout_s: float) -> Tuple[bool, Dict[str, float]]:
    """Poll arm.verify_at(target, tol) until within tolerance or timeout."""
    t0 = time.time()
    ok, errs = arm.verify_at(target, SETTLE_TOL)
    while not ok and (time.time() - t0) < timeout_s:
        time.sleep(POLL_INTERVAL_S)
        ok, errs = arm.verify_at(target, SETTLE_TOL)
    return ok, errs


def _d_fine_correct(arm, target_deg: float) -> Tuple[bool, float]:
    """
    Backlash-aware, gentle 'creep' onto target for D only.
    Returns (ok, err_after) based on verify_at().
    """
    fine_speed = max(15, int(SPEED * D_FINE_SPEED_FRAC))

    ok, errs = _wait_until_settled(arm, {"D": target_deg}, timeout_s=SETTLE_TIMEOUT_S)
    err = float(errs.get("D", 0.0))
    if ok:
        return True, err

    for k in range(1, D_FINE_RETRIES + 1):
        # Step in the direction that reduces the error; avoid direction flips that reintroduce lash
        step = max(D_FINE_STEP_MIN, min(D_FINE_STEP_MAX, abs(err) * D_FINE_STEP_FACTOR))
        correction = -step if err > 0.0 else step  # if we're high (+err), move negative; if low (-err), move positive
        logger.warning("D fine-correction %d/%d: err=%.2f°, step=%.2f° @%d",
                       k, D_FINE_RETRIES, err, correction, fine_speed)

        arm.move(
            "relative",
            {"D": correction},
            speed=fine_speed,
            units="degrees",
            finalize=True,
        )

        ok, errs = _wait_until_settled(arm, {"D": target_deg}, timeout_s=SETTLE_TIMEOUT_S)
        err = float(errs.get("D", 0.0))
        logger.info("... D re-check after fine-correction %d: ok=%s err=%.2f°", k, ok, err)
        if ok:
            break

    return ok, err


def _move_joint_and_validate(arm, joint: str, target_deg: float, speed: int) -> Dict[str, Any]:
    """
    One joint → absolute degrees → verify → optional retry → backlash-aware D fine-corrections → return last summary.
    Raises RuntimeError if the joint cannot settle within tolerance.
    """
    last_summary: Dict[str, Any] = {}
    attempt = 0
    current_speed = speed

    while True:
        attempt += 1
        logger.info(">>> Move %s to %.2f° (attempt %d) at speed %d", joint, target_deg, attempt, current_speed)

        # Execute a blocking move (finalize=True so the motor's own correction runs)
        last_summary = arm.move(
            "absolute",
            {joint: target_deg},
            speed=current_speed,
            units="degrees",
            finalize=True,
        )

        # Primary settle check
        ok, errs = _wait_until_settled(arm, {joint: target_deg}, timeout_s=SETTLE_TIMEOUT_S)
        err = float(errs.get(joint, 0.0))
        logger.info("... %s settle check: ok=%s err=%.2f°", joint, ok, err)

        if ok:
            return last_summary

        # For D only: try backlash-aware fine corrections before we consider another "retry" or failure
        if joint == "D":
            ok_d, err_d = _d_fine_correct(arm, target_deg)
            if ok_d:
                return last_summary
            err = err_d  # update last error for logging/exception below

        if attempt <= RETRY_ON_FAIL:
            # Retry once at a slower speed to sneak up on the target
            current_speed = max(20, int(SPEED * RETRY_SLOWDOWN))
            logger.warning("Retrying %s (err=%.2f°); new speed=%d", joint, err, current_speed)
            continue

        # Give up for this joint
        raise RuntimeError(
            f"Joint {joint} failed to settle at {target_deg:.2f}° after {attempt} attempt(s); last err={err:.2f}°"
        )


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow using point names, with strict sequencing and pauses."""
    speed = SPEED

    # NOTE: use "closed" (not "close") to match typical point naming
    steps = [
        ("Start at home",        {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
        ("Pick Right S1",        {"A": "open",   "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Pick Right S2",        {"A": "open",   "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Grab",                 {"A": "closed", "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Pick Right S3",        {"A": "closed", "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Go Home (except A)",   {"A": "closed", "B": "min",   "C": "max",  "D": "neutral"}),
        ("Drop Left S1",         {"A": "closed", "B": "pick",  "C": "max",  "D": "quality"}),
        ("Drop Left S2",         {"A": "closed", "B": "pick",  "C": "pick", "D": "quality"}),
        ("Release",              {"A": "open",   "B": "pick",  "C": "pick", "D": "quality"}),
        ("Drop Left S3",         {"A": "open",   "B": "pick",  "C": "max",  "D": "quality"}),
        ("Final home",           {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
    ]

    # Resolve named points → absolute degrees once (so every repeat uses the same targets)
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
                if float(errs.get("D", 0.0)) >= 720.0:
                    logger.error("D reference drift (%.1f°) before step %d; recovering", errs["D"], idx)
                    arm.recover_to_home(speed=30, timeout_s=90.0)
                else:
                    logger.warning("DRIFT before step %d: %s; nudging back", idx, errs)
                    arm.move(
                        "absolute",
                        prev_pose,
                        speed=40,
                        units="degrees",
                        finalize=True,
                    )
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

    logger.info("Process complete.")
    return result
