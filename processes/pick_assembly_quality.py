"""
Pick from assembly side and place on quality side using named points.

Assumptions after mechanical change:
- Joint D: near-direct drive, minimal lash, short travel (< 2 rev from neutral to either side).
- Requirement for D: always move at speed <= 6; fine tune at speed 1–2; tighter tolerance.
- Joints A/B/C unchanged: normal speed 100, segment long moves, slow final approach, fine tune if needed.

Design:
- Absolute → verify → optional retry (slower) → fine correction.
- D uses no backlash catch spins, no long-move segmentation, just slow/precise control.
- Stable verification (a few consecutive OK polls) to avoid flicker.
"""

import logging
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger("process.pick_assembly_quality")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ================= Tunables =================
# Speeds (deg/s or device units; match your driver’s meaning)
SPEED_ABC = 100                 # normal speed for A/B/C
SPEED_ABC_FINAL = 35            # final approach for A/B/C
SPEED_ABC_FINE = 25             # fine corrections for A/B/C
SEGMENT_DEG_ABC = 120.0         # break up long A/B/C moves into chunks of this size

# D is always slow + precise
SPEED_D_MAX = 6                 # hard cap for any D move
SPEED_D_FINAL = 2               # final approach for D
SPEED_D_FINE = 1                # fine corrections for D (1–2 per requirement)

# Tolerances (degrees)
TOL = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 1.0}  # D tightened

# Verification
SETTLE_TIMEOUT_S = 6.0
POLL_INTERVAL_S = 0.05
STABLE_OK_POLLS = 2             # need 2 consecutive OK reads within window
STABLE_WINDOW_S = 0.5

# Retry/finetune
RETRY_ON_FAIL = 1               # one slower retry before fine
REPEAT_PER_JOINT = 2            # run each joint command twice (helps A/B/C settle)
FINE_MAX_STEPS = 5
FINE_GAIN_ABC = 0.65
FINE_STEP_MIN_ABC = 0.8
FINE_STEP_MAX_ABC = 20.0
FINE_GAIN_D = 0.70
FINE_STEP_MIN_D = 0.4
FINE_STEP_MAX_D = 8.0

# Pauses for observation (keep as before)
POSE_PAUSE_S = 10.0
JOINT_PAUSE_S = 10.0
# ===========================================


def _verify_stable(arm, target: Dict[str, float], timeout_s: float) -> Tuple[bool, Dict[str, float]]:
    """Require a couple of consecutive OK verifications within a small window."""
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


def _read_pos(arm, j: str) -> float:
    try:
        return float(arm.read_position().get(j))
    except Exception:
        return float("nan")


def _abs_move(arm, j: str, target: float, speed: int, use_segments: bool) -> Dict[str, Any]:
    """Absolute move with optional segmentation (A/B/C use, D does not)."""
    if j == "D":
        speed = min(speed, SPEED_D_MAX)

    if not use_segments:
        return arm.move("absolute", {j: target}, speed=speed, units="degrees", finalize=True)

    cur = _read_pos(arm, j)
    if cur == cur:
        delta = target - cur
    else:
        delta = 0.0

    last = {}
    if cur == cur and abs(delta) > SEGMENT_DEG_ABC:
        step = SEGMENT_DEG_ABC if delta > 0 else -SEGMENT_DEG_ABC
        pos = cur
        while abs(target - pos) > SEGMENT_DEG_ABC:
            pos += step
            last = arm.move("absolute", {j: pos}, speed=SPEED_ABC, units="degrees", finalize=True)
        # final approach slower
        last = arm.move("absolute", {j: target}, speed=SPEED_ABC_FINAL, units="degrees", finalize=True)
    else:
        # small move: one shot (A/B/C)
        last = arm.move("absolute", {j: target}, speed=speed, units="degrees", finalize=True)

    return last


def _fine_correct(arm, j: str, target: float) -> bool:
    """Small signed nudges toward target; D uses tighter steps/speeds."""
    tol = TOL.get(j, 3.0)

    # quick early-out
    ok0, _ = _verify_stable(arm, {j: target}, timeout_s=0.8)
    if ok0:
        return True

    if j == "D":
        gain = FINE_GAIN_D
        step_min = FINE_STEP_MIN_D
        step_max = FINE_STEP_MAX_D
        speed = SPEED_D_FINE
    else:
        gain = FINE_GAIN_ABC
        step_min = FINE_STEP_MIN_ABC
        step_max = FINE_STEP_MAX_ABC
        speed = SPEED_ABC_FINE

    for k in range(1, FINE_MAX_STEPS + 1):
        pos = _read_pos(arm, j)
        if pos == pos:
            err = target - pos
            if abs(err) <= tol:
                return True
            step = max(step_min, min(step_max, abs(err) * gain))
            corr = +step if err > 0 else -step
        else:
            # If we can’t read, bias toward target with a minimal nudge
            corr = step_min

        logger.warning("%s fine %d/%d: nudging %+0.2f° @%d", j, k, FINE_MAX_STEPS, corr, speed)
        arm.move("relative", {j: corr}, speed=(min(speed, SPEED_D_MAX) if j == "D" else speed),
                 units="degrees", finalize=True)

        ok, _ = _verify_stable(arm, {j: target}, timeout_s=1.0 if j == "D" else 0.8)
        if ok:
            return True

    return False


def _move_joint(arm, j: str, target: float) -> Dict[str, Any]:
    """Absolute (segmented for A/B/C; direct for D) → verify (+retry) → fine correction."""
    use_segments = (j in ("A", "B", "C"))
    base_speed = SPEED_ABC if j in ("A", "B", "C") else SPEED_D_MAX

    last = {}
    attempt = 0
    cur_speed = base_speed

    while True:
        attempt += 1
        logger.info(">>> Move %s to %.2f° (attempt %d) @%d", j, target, attempt, cur_speed)
        last = _abs_move(arm, j, target, speed=cur_speed, use_segments=use_segments)

        ok, errs = _verify_stable(arm, {j: target}, timeout_s=SETTLE_TIMEOUT_S)
        err_abs = float(abs(errs.get(j, 0.0))) if isinstance(errs, dict) and j in errs else float("nan")
        logger.info("... %s settle: ok=%s err≈%.2f°", j, ok, (err_abs if err_abs == err_abs else float("nan")))
        if ok:
            return last

        if attempt <= RETRY_ON_FAIL:
            # Retry slower (A/B/C: go to final speed; D: go to final speed cap)
            cur_speed = SPEED_ABC_FINAL if j in ("A", "B", "C") else SPEED_D_FINAL
            logger.warning("Retrying %s at slower speed=%d", j, cur_speed)
            continue
        break

    # Fine correction
    good = _fine_correct(arm, j, target)
    if not good:
        raise RuntimeError(f"Joint {j} failed to settle at {target:.2f}° after {attempt} absolute attempt(s) + fine correction")
    return last


def run(arm) -> Any:
    """Execute the workflow using point names, with simplified D behavior."""
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

    # Resolve named points once
    abs_steps = [(name, arm.resolve_pose(pose)) for name, pose in steps]

    result = None
    prev_pose = None
    joint_order = ("D", "C", "B", "A")  # keep D first to orient the part before vertical moves, etc.

    for idx, (pose_name, target_pose) in enumerate(abs_steps):
        logger.info("=== Pose %d/%d: %s ===", idx + 1, len(abs_steps), pose_name)

        # If we drifted since last pose, gently restore (simple nudge; no special D recovery now)
        if prev_pose is not None:
            ok_prev, errs_prev = _verify_stable(arm, prev_pose, timeout_s=1.0)
            if not ok_prev:
                logger.warning("Drift before step %d (%s). Nudging back.", idx, errs_prev)
                arm.move("absolute", prev_pose, speed=40, units="degrees", finalize=True)
                _verify_stable(arm, prev_pose, timeout_s=1.0)

        # Move each joint with possible repeats for settling
        for j in joint_order:
            if j not in target_pose:
                continue
            target = float(target_pose[j])

            for rep in range(1, REPEAT_PER_JOINT + 1):
                logger.info("-- %s → %.2f° (pass %d/%d)", j, target, rep, REPEAT_PER_JOINT)
                result = _move_joint(arm, j, target)
                if JOINT_PAUSE_S > 0:
                    logger.info("Pausing %.1fs after %s", JOINT_PAUSE_S, j)
                    time.sleep(JOINT_PAUSE_S)

        logger.info("Reached pose: %s", pose_name)
        if idx < len(abs_steps) - 1 and POSE_PAUSE_S > 0:
            logger.info("Pausing %.1fs between poses", POSE_PAUSE_S)
            time.sleep(POSE_PAUSE_S)

        prev_pose = target_pose

    logger.info("Process complete.")
    return result
