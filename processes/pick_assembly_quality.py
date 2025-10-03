"""
Pick from assembly side and place on quality side using named points.

Robust + simplified:
- Single-direction final approach (takes up lash consistently).
- Segment long moves; finish with slow pass.
- Fine correction uses sensor-signed error; stops if no measurable improvement.
- On no-improvement, do a backoff + slow absolute re-approach (once).
- D has explicit reversal catch spins before moves (as required).
- Stable verification (consecutive OK polls) avoids flicker.
"""

import logging
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger("process.pick_assembly_quality")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ======== Tunables (pared down) ========
SPEED = 100
POSE_PAUSE_S = 10.0
JOINT_PAUSE_S = 10.0
REPEAT_PER_JOINT = 2

# Tolerances (deg)
TOL = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 3.0}

# Verification
SETTLE_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.05
STABLE_OK_POLLS = 2
STABLE_WINDOW_S = 0.5

# Absolute command retry
RETRY_ON_FAIL = 1
RETRY_SLOWDOWN = 0.5

# Segmentation + final approach
SEGMENT_DEG = 120.0
SEGMENT_SPEED = max(20, int(SPEED * 0.8))
FINAL_APPROACH_SPEED = max(15, int(SPEED * 0.35))

# Fine correction (A/B/C)
FINE_MAX_STEPS = 5
FINE_SPEED = max(15, int(SPEED * 0.35))
FINE_GAIN = 0.65                # step ≈ gain * |err|
FINE_STEP_MIN = 0.8
FINE_STEP_MAX = 20.0
FINE_BACKOFF = 3.0              # ensures re-approach from the same side
NO_IMPROVE_LIMIT = 2            # consecutive rechecks without enough improvement
IMPROVE_EPS = 0.5               # must reduce |err| by at least this much

# D-specific
D_CATCH_TURNS = 2.0             # full turns on direction reversal (hardware reality)
D_CATCH_SPEED = 35
D_FINE_SPEED = max(15, int(SPEED * 0.35))
D_FINE_GAIN = 0.60
D_FINE_STEP_MIN = 1.0
D_FINE_STEP_MAX = 20.0
D_BACKOFF = 6.0
D_RECHECK_S = 0.8

# Extreme drift guard
D_RECOVER_DRIFT_DEG = 720.0
RECOVER_SPEED = 30
RECOVER_TIMEOUT_S = 120.0
# =======================================


def _verify_stable(arm, target: Dict[str, float], timeout_s: float) -> Tuple[bool, Dict[str, float]]:
    t0 = time.time()
    ok, errs = arm.verify_at(target, TOL)
    stable = 1 if ok else 0
    last_flip = time.time()

    while (time.time() - t0) < timeout_s:
        time.sleep(POLL_INTERVAL_S)
        ok, errs = arm.verify_at(target, TOL)
        if ok:
            stable += 1
            if stable >= STABLE_OK_POLLS and (time.time() - last_flip) >= STABLE_WINDOW_S:
                return True, errs
        else:
            stable = 0
            last_flip = time.time()
    return False, errs


def _read_pos(arm, j: str) -> float:
    try:
        return float(arm.read_position().get(j))
    except Exception:
        return float("nan")


def _signed_err(arm, j: str, target: float) -> float:
    pos = _read_pos(arm, j)
    return target - pos if pos == pos else float("nan")


def _segmented_absolute(arm, j: str, current: float, target: float, speed: int):
    delta = target - current
    if abs(delta) <= SEGMENT_DEG:
        return arm.move("absolute", {j: target}, speed=speed, units="degrees", finalize=True)

    step = SEGMENT_DEG if delta > 0 else -SEGMENT_DEG
    pos = current
    last = {}
    while abs(target - pos) > SEGMENT_DEG:
        pos += step
        last = arm.move("absolute", {j: pos}, speed=SEGMENT_SPEED, units="degrees", finalize=True)
    last = arm.move("absolute", {j: target}, speed=FINAL_APPROACH_SPEED, units="degrees", finalize=True)
    return last


def _backoff_then_slow_abs(arm, j: str, approach_dir: int, target: float, backoff_deg: float, speed: int):
    arm.move("relative", {j: -approach_dir * abs(backoff_deg)}, speed=speed, units="degrees", finalize=True)
    arm.move("absolute", {j: target}, speed=speed, units="degrees", finalize=True)


def _catch_spin_if_reversed(arm, last_dir: int, new_dir: int):
    if last_dir is not None and new_dir != 0 and new_dir != last_dir:
        spin = new_dir * D_CATCH_TURNS * 360.0
        logger.warning("D reversal: catch spin %+0.1f° @%d", spin, D_CATCH_SPEED)
        arm.move("relative", {"D": spin}, speed=D_CATCH_SPEED, units="degrees", finalize=True)


def _fine_correct(arm, j: str, target: float, approach_dir: int) -> bool:
    """Generic fine for A/B/C with 'measured-improvement' guard."""
    tol = TOL.get(j, 3.0)
    ok, _ = _verify_stable(arm, {j: target}, timeout_s=0.8)
    if ok:
        return True

    no_improve = 0
    prev_err_abs = None

    for k in range(1, FINE_MAX_STEPS + 1):
        err = _signed_err(arm, j, target)
        # Choose direction: sensor sign if valid, else approach_dir
        step_dir = (1 if (err == err and err > 0) else (-1 if (err == err and err < 0) else approach_dir))
        # If we ended up on the wrong side of target, restore approach side:
        if err == err and step_dir != approach_dir:
            logger.warning("%s fine %d: wrong side (err=%.2f°) → backoff %.2f°", j, k, err, FINE_BACKOFF)
            _backoff_then_slow_abs(arm, j, approach_dir, target, FINE_BACKOFF, FINE_SPEED)
        else:
            step_mag = max(FINE_STEP_MIN, min(FINE_STEP_MAX, abs(err if err == err else tol) * FINE_GAIN))
            corr = approach_dir * step_mag
            logger.warning("%s fine %d/%d: err≈%.2f° → rel %+0.2f° @%d", j, k, FINE_MAX_STEPS,
                           (err if err == err else float("nan")), corr, FINE_SPEED)
            arm.move("relative", {j: corr}, speed=FINE_SPEED, units="degrees", finalize=True)

        time.sleep(0.8)
        ok2, errs2 = _verify_stable(arm, {j: target}, timeout_s=0.8)
        cur_err_abs = float(abs(errs2.get(j, 0.0))) if isinstance(errs2, dict) and j in errs2 else float("nan")

        if ok2:
            return True

        # Measured-improvement guard (based on verify_at error)
        improved = (prev_err_abs is None) or (cur_err_abs != cur_err_abs) or (prev_err_abs - cur_err_abs >= IMPROVE_EPS)
        if not improved:
            no_improve += 1
            logger.warning("%s fine %d: no improvement (prev≈%.2f°, now≈%.2f°) [%d/%d]",
                           j, k, (prev_err_abs if prev_err_abs == prev_err_abs else float("nan")), cur_err_abs,
                           no_improve, NO_IMPROVE_LIMIT)
            if no_improve >= NO_IMPROVE_LIMIT:
                logger.warning("%s fine: escalating → backoff %.2f° + slow abs", j, FINE_BACKOFF)
                _backoff_then_slow_abs(arm, j, approach_dir, target, FINE_BACKOFF, FINE_SPEED)
                ok3, _ = _verify_stable(arm, {j: target}, timeout_s=1.0)
                return ok3
        else:
            no_improve = 0

        prev_err_abs = cur_err_abs

    return False


def _fine_correct_d(arm, target: float, approach_dir: int) -> bool:
    tol = TOL.get("D", 3.0)

    # If on wrong side, restore approach side
    err = _signed_err(arm, "D", target)
    if err == err and (1 if err > 0 else -1) != approach_dir:
        logger.warning("D: wrong side before fine (err=%.2f°) → backoff %.2f°", err, D_BACKOFF)
        _backoff_then_slow_abs(arm, "D", approach_dir, target, D_BACKOFF, D_FINE_SPEED)

    no_improve = 0
    prev_err_abs = None

    for k in range(1, FINE_MAX_STEPS + 1):
        err = _signed_err(arm, "D", target)
        step_mag = max(D_FINE_STEP_MIN, min(D_FINE_STEP_MAX, abs(err if err == err else tol) * D_FINE_GAIN))
        corr = approach_dir * step_mag
        logger.warning("D fine %d/%d: err≈%.2f° → rel %+0.2f° @%d",
                       k, FINE_MAX_STEPS, (err if err == err else float("nan")), corr, D_FINE_SPEED)
        arm.move("relative", {"D": corr}, speed=D_FINE_SPEED, units="degrees", finalize=True)

        ok2, errs2 = _verify_stable(arm, {"D": target}, timeout_s=D_RECHECK_S)
        cur_err_abs = float(abs(errs2.get("D", 0.0))) if isinstance(errs2, dict) and "D" in errs2 else float("nan")
        if ok2:
            return True

        improved = (prev_err_abs is None) or (cur_err_abs != cur_err_abs) or (prev_err_abs - cur_err_abs >= IMPROVE_EPS)
        if not improved:
            no_improve += 1
            logger.warning("D fine: no improvement (prev≈%.2f°, now≈%.2f°) [%d/%d]",
                           (prev_err_abs if prev_err_abs == prev_err_abs else float("nan")), cur_err_abs,
                           no_improve, NO_IMPROVE_LIMIT)
            if no_improve >= NO_IMPROVE_LIMIT:
                logger.warning("D fine: escalating → backoff %.2f° + slow abs", D_BACKOFF)
                _backoff_then_slow_abs(arm, "D", approach_dir, target, D_BACKOFF, D_FINE_SPEED)
                ok3, _ = _verify_stable(arm, {"D": target}, timeout_s=D_RECHECK_S)
                return ok3
        else:
            no_improve = 0

        prev_err_abs = cur_err_abs

    return False


def _move_joint(arm, j: str, target: float, speed: int, d_state: Dict[str, int]) -> Dict[str, Any]:
    """Absolute (segmented) → verify (+retry) → fine-correct. Enforces approach-dir and D catch spins."""
    # Approach direction from current sensor
    cur = _read_pos(arm, j)
    if cur == cur:
        approach_dir = 1 if (target - cur) > 0 else (-1 if (target - cur) < 0 else (d_state.get("D", 1) if j == "D" else 1))
    else:
        approach_dir = d_state.get("D", 1) if j == "D" else 1

    # D reversal catch spin before any absolute move
    if j == "D":
        _catch_spin_if_reversed(arm, d_state.get("D"), approach_dir)

    # Segmented absolute with one retry at slower speed if needed
    last = {}
    attempt = 0
    cur_speed = speed
    while True:
        attempt += 1
        logger.info(">>> Move %s to %.2f° (attempt %d) @%d", j, target, attempt, cur_speed)
        last = _segmented_absolute(arm, j, (cur if cur == cur else target), target, cur_speed)

        ok, errs = _verify_stable(arm, {j: target}, timeout_s=SETTLE_TIMEOUT_S)
        err_abs = float(abs(errs.get(j, 0.0))) if isinstance(errs, dict) and j in errs else float("nan")
        logger.info("... %s settle: ok=%s err≈%.2f°", j, ok, (err_abs if err_abs == err_abs else float("nan")))
        if ok or attempt > RETRY_ON_FAIL:
            break

        cur_speed = max(20, int(SPEED * RETRY_SLOWDOWN))
        logger.warning("Retrying %s absolute at slower speed=%d", j, cur_speed)
        cur2 = _read_pos(arm, j)
        if cur2 == cur2:
            cur = cur2

    if not ok:
        # Fine correction
        good = _fine_correct_d(arm, target, approach_dir) if j == "D" else _fine_correct(arm, j, target, approach_dir)
        if not good:
            raise RuntimeError(f"Joint {j} failed to settle at {target:.2f}° after {attempt} absolute attempt(s) + fine correction")

    if j == "D":
        d_state["D"] = approach_dir
    return last


def run(arm) -> Any:
    speed = SPEED

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

    abs_steps = [(name, arm.resolve_pose(pose)) for name, pose in steps]

    result = None
    prev_pose = None
    joint_order = ("D", "C", "B", "A")
    d_state: Dict[str, int] = {"D": +1}

    for idx, (pose_name, target_pose) in enumerate(abs_steps):
        logger.info("=== Pose %d/%d: %s ===", idx + 1, len(abs_steps), pose_name)

        # If we drifted from last pose, restore gently; if D drift insane, recover
        if prev_pose is not None:
            ok_prev, errs_prev = _verify_stable(arm, prev_pose, timeout_s=2.0)
            if not ok_prev:
                d_err = float(errs_prev.get("D", 0.0))
                if d_err >= D_RECOVER_DRIFT_DEG:
                    logger.error("D drift %.1f° before step %d → recover_to_home", d_err, idx)
                    arm.recover_to_home(speed=RECOVER_SPEED, timeout_s=RECOVER_TIMEOUT_S)
                else:
                    logger.warning("Drift before step %d (%s). Nudging back.", idx, errs_prev)
                    arm.move("absolute", prev_pose, speed=40, units="degrees", finalize=True)
                    ok_prev2, _ = _verify_stable(arm, prev_pose, timeout_s=2.0)
                    if not ok_prev2:
                        logger.error("Drift persists → recover_to_home")
                        arm.recover_to_home(speed=RECOVER_SPEED, timeout_s=RECOVER_TIMEOUT_S)

        for j in joint_order:
            if j not in target_pose:
                continue
            target = float(target_pose[j])

            for rep in range(1, REPEAT_PER_JOINT + 1):
                logger.info("-- %s → %.2f° (pass %d/%d)", j, target, rep, REPEAT_PER_JOINT)
                result = _move_joint(arm, j, target, speed, d_state)
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
