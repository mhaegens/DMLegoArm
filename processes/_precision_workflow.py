"""Shared precision workflow logic for production processes.

All joints now move at their requested speeds and rely on the controller to
perform final corrections at 80% of that requested speed. The workflow still
breaks long A/B/C moves into segments to reduce overshoot but no longer slows
joint D differently from the others.
"""

from __future__ import annotations

import logging
import os
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
# Speeds for all joints. We still break long A/B/C moves into segments to help
# reduce overshoot, but all joints otherwise use the same rates.
SPEED_DEFAULT = 100
SPEED_DEFAULT_FINAL = 35
SPEED_FINE = 25
SEGMENT_DEG_ABC = 180.0

# Position tolerances (degrees).
TOL = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 3.0}

# Verification parameters.
SETTLE_TIMEOUT_S = 6.0
SETTLE_TIMEOUT_BASE_S = 1.5
SETTLE_TIMEOUT_PER_DEG_S = 0.02
SETTLE_TIMEOUT_MIN_S = 1.2
POLL_INTERVAL_S = 0.12
STABLE_OK_POLLS = 2
STABLE_WINDOW_S = 0.2

# Fine-tuning parameters.
# Allow a maximum of three immediate retries per joint. The final attempt is a
# single fine correction rather than a loop of nudges, so each joint performs
# at most ``RETRY_ON_FAIL`` follow-up moves after the initial command.
RETRY_ON_FAIL = 3
REPEAT_PER_JOINT = 1
FINE_GAIN = 0.65
FINE_STEP_MIN = 0.8
FINE_STEP_MAX = 20.0

# Cosmetic pauses so operators can observe each move.
POSE_PAUSE_S = 0.0
JOINT_PAUSE_S = 0.0
IGNORE_SETTLE_FAILURE = set()
if os.getenv("ARM_IGNORE_SETTLE_FAILURE", "0") == "1":
    IGNORE_SETTLE_FAILURE = {"A", "B", "C", "D"}
# ===========================================

_MOVE_LOCK = threading.Lock()


def _issue_move(arm, mode: str, values: Dict[str, float], *, speed: int, units: str, finalize: bool) -> Dict[str, Any]:
    with _MOVE_LOCK:
        return arm.move(mode, values, speed=speed, units=units, finalize=finalize)


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


def _normalize_target(joint: str, current: float, target: float) -> float:
    if joint not in ("A", "B", "C") or current != current:
        return target
    offset = round((current - target) / 360.0)
    candidates = [target + 360.0 * (offset + delta) for delta in (-1, 0, 1)]
    return min(candidates, key=lambda v: abs(v - current))


def _settle_timeout(joint: str, target: float, current: float) -> float:
    if current != current:
        return SETTLE_TIMEOUT_S
    distance = abs(target - current)
    joint_factor = 0.8 if joint == "D" else 1.0
    timeout = SETTLE_TIMEOUT_BASE_S + distance * SETTLE_TIMEOUT_PER_DEG_S * joint_factor
    timeout = max(SETTLE_TIMEOUT_MIN_S, min(timeout, SETTLE_TIMEOUT_S))
    return timeout


def _telemetry_unhealthy(arm, errs: Any) -> bool:
    """Best-effort check for unhealthy telemetry sources."""

    # Missing/NaN errors usually mean we could not read positions.
    if not isinstance(errs, dict) or any(v != v for v in errs.values()):
        return True

    # Allow arm implementations to expose a health probe.
    for attr in ("telemetry_healthy", "telemetry_ok", "telemetry_status"):
        probe = getattr(arm, attr, None)
        try:
            if callable(probe):
                result = probe()
            else:
                result = probe
        except Exception:  # pragma: no cover - defensive
            continue

        if result is None:
            continue

        if isinstance(result, dict):
            healthy = bool(result.get("healthy", result.get("ok", True)))
        else:
            healthy = bool(result)

        if not healthy:
            return True

    return False


def _abs_move(
    arm,
    joint: str,
    target: float,
    *,
    speed: int,
    use_segments: bool,
) -> Tuple[Dict[str, Any], bool, float]:
    """Absolute move with optional segmentation (used for joints A/B/C)."""

    current = _read_pos(arm, joint)
    target = _normalize_target(joint, current, target)
    delta = target - current if current == current else 0.0
    last: Dict[str, Any] = {}
    segmented = False

    if not use_segments:
        last = _issue_move(arm, "absolute", {joint: target}, speed=speed, units="degrees", finalize=True)
        return last, segmented, target

    if current == current and abs(delta) > SEGMENT_DEG_ABC:
        step = SEGMENT_DEG_ABC if delta > 0 else -SEGMENT_DEG_ABC
        pos = current
        while abs(target - pos) > SEGMENT_DEG_ABC:
            pos += step
            segmented = True
            last = _issue_move(arm, "absolute", {joint: pos}, speed=SPEED_DEFAULT, units="degrees", finalize=True)
            ok, _ = arm.verify_at({joint: target}, TOL)
            if ok:
                return last, segmented, target

        last = _issue_move(arm, "absolute", {joint: target}, speed=SPEED_DEFAULT_FINAL, units="degrees", finalize=True)
        segmented = True
    else:
        last = _issue_move(arm, "absolute", {joint: target}, speed=speed, units="degrees", finalize=True)

    return last, segmented, target


def _single_fine_correct(arm, joint: str, target: float) -> bool:
    """Perform a single small relative nudge toward ``target``."""

    tol = TOL.get(joint, 3.0)
    ok, _ = _verify_stable(arm, {joint: target}, timeout_s=0.8)
    if ok:
        return True

    current = _read_pos(arm, joint)
    gain = FINE_GAIN
    step_min = FINE_STEP_MIN
    step_max = FINE_STEP_MAX
    speed = SPEED_FINE

    if current == current:
        error = target - current
        if abs(error) <= tol:
            return True
        step = max(step_min, min(step_max, abs(error) * gain))
        correction = step if error > 0 else -step
    else:
        correction = step_min

    logger.warning("%s fine 1/1: %+0.2f° @%d", joint, correction, speed)
    _issue_move(
        arm,
        "relative",
        {joint: correction},
        speed=speed,
        units="degrees",
        finalize=True,
    )

    ok, _ = _verify_stable(arm, {joint: target}, timeout_s=0.8)
    return ok


def _move_joint(arm, joint: str, target: float) -> Dict[str, Any]:
    """Move one joint with verification, retries, and a single fine correction."""

    pre_ok, _ = arm.verify_at({joint: target}, TOL)
    if pre_ok:
        logger.info("%s already within tolerance; skipping move", joint)
        return {
            "skipped": True,
            "settled_ok": True,
            "telemetry_unhealthy": False,
            "telemetry_retry_used": False,
            "segmented": False,
            "target": target,
            "commanded_target": target,
            "attempts": 0,
        }

    use_segments = joint in ("A", "B", "C")
    base_speed = SPEED_DEFAULT
    last: Dict[str, Any] = {}
    err_abs = float("nan")

    attempt = 1
    telemetry_retry_used = False
    telemetry_unhealthy = False
    segmented = False
    settle_ok = False
    commanded_target = target

    while True:
        current_for_timeout = _read_pos(arm, joint)
        current_speed = base_speed if attempt == 1 else SPEED_DEFAULT_FINAL
        logger.info(
            ">>> Move %s to %.2f° (attempt %d) @%d",
            joint,
            target,
            attempt,
            current_speed,
        )
        last, segmented, commanded_target = _abs_move(
            arm,
            joint,
            target,
            speed=current_speed,
            use_segments=use_segments,
        )

        settle_timeout = _settle_timeout(joint, commanded_target, current_for_timeout)
        ok, errs = _verify_stable(arm, {joint: commanded_target}, timeout_s=settle_timeout)
        settle_ok = ok
        err_abs = float(abs(errs.get(joint, 0.0))) if isinstance(errs, dict) and joint in errs else float("nan")
        logger.info("... %s settle: ok=%s err≈%.2f°", joint, ok, err_abs if err_abs == err_abs else float("nan"))

        if ok:
            break

        telemetry_unhealthy = _telemetry_unhealthy(arm, errs)
        if telemetry_unhealthy and not telemetry_retry_used:
            telemetry_retry_used = True
            logger.warning("Telemetry unhealthy; retrying %s once", joint)
            attempt += 1
            continue

        if err_abs != err_abs:  # NaN guard
            err_abs = float("inf")

        tol = TOL.get(joint, 3.0)
        if err_abs <= tol:
            break

        if err_abs <= 10.0:
            if attempt >= 2:
                break
            logger.warning("Retrying %s at slower speed=%d", joint, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue

        # Recovery class: |error| > 10°
        if attempt == 1:
            logger.warning("Large error for %s (≈%.2f°). Performing recovery at speed=%d", joint, err_abs, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue
        if attempt == 2 and RETRY_ON_FAIL >= 3:
            logger.warning("Second recovery attempt for %s at speed=%d", joint, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue

        break

    if not settle_ok and joint not in IGNORE_SETTLE_FAILURE:
        raise RuntimeError(
            f"Joint {joint} failed to settle at {target:.2f}° after {attempt} attempt(s)"
        )

    if not settle_ok and joint in IGNORE_SETTLE_FAILURE:
        logger.warning(
            "Joint %s failed to settle (last error≈%.2f°); ignoring failure per operator guidance.",
            joint,
            err_abs if err_abs == err_abs else float("nan"),
        )

    result = dict(last) if isinstance(last, dict) else {"move_result": last}
    result.update(
        {
            "settled_ok": settle_ok,
            "telemetry_unhealthy": telemetry_unhealthy,
            "telemetry_retry_used": telemetry_retry_used,
            "segmented": segmented,
            "target": target,
            "commanded_target": commanded_target,
            "attempts": attempt,
        }
    )
    return result

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
    previous_pose_flags = {
        "settle_failed": False,
        "telemetry_unhealthy": False,
        "segmented": False,
    }

    total_steps = len(resolved_steps)
    allow_repeat = REPEAT_PER_JOINT > 0

    for index, (pose_name, target_pose) in enumerate(resolved_steps, start=1):
        logger.info("=== Pose %d/%d: %s ===", index, len(resolved_steps), pose_name)

        pose_flags = {
            "settle_failed": False,
            "telemetry_unhealthy": False,
            "segmented": False,
        }

        if previous_pose is not None and any(previous_pose_flags.values()):
            ok_prev, errs_prev = _verify_stable(arm, previous_pose, timeout_s=0.5)
            if not ok_prev:
                tol_pad = {j: TOL.get(j, 3.0) + 1.0 for j in previous_pose}
                needs_nudge = any(
                    abs(errs_prev.get(j, 0.0)) > tol_pad.get(j, 4.0) for j in previous_pose
                )
                if needs_nudge:
                    logger.warning("Drift before step %d (%s). Nudging back.", index, errs_prev)
                    _issue_move(arm, "absolute", previous_pose, speed=40, units="degrees", finalize=True)
                    _verify_stable(arm, previous_pose, timeout_s=0.5)
                else:
                    logger.info("Minor drift before step %d; skipping nudge.", index)

        for joint in joint_order:
            if joint not in target_pose:
                continue
            target = float(target_pose[joint])

            if (
                previous_pose is not None
                and index != 1
                and index != total_steps
                and joint in previous_pose
                and float(previous_pose[joint]) == target
            ):
                logger.info("Skipping %s for pose %d; unchanged from previous pose", joint, index)
                continue

            logger.info("-- %s → %.2f° (pass 1)", joint, target)
            result = _move_joint(arm, joint, target)
            if joint_pause_s > 0:
                logger.info("Pausing %.1fs after %s", joint_pause_s, joint)
                time.sleep(joint_pause_s)

            needs_repeat = (
                not result.get("settled_ok", True)
                or result.get("telemetry_retry_used", False)
            )
            if needs_repeat and allow_repeat:
                logger.info("-- %s → %.2f° (pass 2/2)", joint, target)
                result = _move_joint(arm, joint, target)
                if joint_pause_s > 0:
                    logger.info("Pausing %.1fs after %s", joint_pause_s, joint)
                    time.sleep(joint_pause_s)

            pose_flags["settle_failed"] = pose_flags["settle_failed"] or not result.get("settled_ok", True)
            pose_flags["telemetry_unhealthy"] = pose_flags["telemetry_unhealthy"] or result.get(
                "telemetry_unhealthy",
                False,
            )
            pose_flags["segmented"] = pose_flags["segmented"] or result.get("segmented", False)

        logger.info("Reached pose: %s", pose_name)
        if index < len(resolved_steps) and pose_pause_s > 0:
            logger.info("Pausing %.1fs between poses", pose_pause_s)
            time.sleep(pose_pause_s)

        previous_pose_flags = pose_flags
        previous_pose = target_pose

    logger.info("Process complete.")
    return result


__all__ = [
    "run_workflow",
    "SPEED_DEFAULT",
    "SPEED_DEFAULT_FINAL",
    "SPEED_FINE",
    "POSE_PAUSE_S",
    "JOINT_PAUSE_S",
    "IGNORE_SETTLE_FAILURE",
]
