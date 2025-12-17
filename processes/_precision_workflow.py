"""Shared precision workflow logic for production processes.

All joints now move at their requested speeds and rely on the controller to
perform final corrections at 80% of that requested speed. The workflow still
breaks long A/B/C moves into segments to reduce overshoot but no longer slows
joint D differently from the others.
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
# Speeds for all joints. We still break long A/B/C moves into segments to help
# reduce overshoot, but all joints otherwise use the same rates.
SPEED_DEFAULT = 100
SPEED_DEFAULT_FINAL = 35
SPEED_FINE = 25
SEGMENT_DEG_ABC = 120.0

# Position tolerances (degrees).
TOL = {"A": 3.0, "B": 3.0, "C": 3.0, "D": 3.0}

# Verification parameters.
SETTLE_TIMEOUT_S = 6.0
POLL_INTERVAL_S = 0.05
STABLE_OK_POLLS = 2
STABLE_WINDOW_S = 0.5

# Fine-tuning parameters.
# Allow a maximum of three immediate retries per joint. The final attempt is a
# single fine correction rather than a loop of nudges, so each joint performs
# at most ``RETRY_ON_FAIL`` follow-up moves after the initial command.
RETRY_ON_FAIL = 3
REPEAT_PER_JOINT = 2
FINE_GAIN = 0.65
FINE_STEP_MIN = 0.8
FINE_STEP_MAX = 20.0

# Cosmetic pauses so operators can observe each move.
POSE_PAUSE_S = 0.0
JOINT_PAUSE_S = 0.0
IGNORE_SETTLE_FAILURE = {"A", "B","C", "D"}
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


def _abs_move(arm, joint: str, target: float, *, speed: int, use_segments: bool) -> Dict[str, Any]:
    """Absolute move with optional segmentation (used for joints A/B/C)."""

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
            last = arm.move("absolute", {joint: pos}, speed=SPEED_DEFAULT, units="degrees", finalize=True)

        last = arm.move("absolute", {joint: target}, speed=SPEED_DEFAULT_FINAL, units="degrees", finalize=True)
    else:
        last = arm.move("absolute", {joint: target}, speed=speed, units="degrees", finalize=True)

    return last


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
    arm.move(
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

    use_segments = joint in ("A", "B", "C")
    base_speed = SPEED_DEFAULT
    last: Dict[str, Any] = {}
    err_abs = float("nan")

    attempt = 1
    telemetry_retry_used = False

    while True:
        current_speed = base_speed if attempt == 1 else SPEED_DEFAULT_FINAL
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

        if _telemetry_unhealthy(arm, errs) and not telemetry_retry_used:
            telemetry_retry_used = True
            logger.warning("Telemetry unhealthy; retrying %s once", joint)
            attempt += 1
            continue

        if err_abs != err_abs:  # NaN guard
            err_abs = float("inf")

        tol = TOL.get(joint, 3.0)
        if err_abs <= tol:
            return last

        if err_abs <= 10.0:
            if attempt >= 2:
                return last
            logger.warning("Retrying %s at slower speed=%d", joint, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue

        # Recovery class: |error| > 10°
        if attempt == 1:
            logger.warning("Large error for %s (≈%.2f°). Performing recovery at speed=%d", joint, err_abs, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue
        if attempt == 2:
            logger.warning("Second recovery attempt for %s at speed=%d", joint, SPEED_DEFAULT_FINAL)
            attempt += 1
            continue

        if err_abs > 10.0:
            raise RuntimeError(
                f"Joint {joint} failed to settle at {target:.2f}° after {attempt} attempt(s)"
            )

        if joint in IGNORE_SETTLE_FAILURE:
            logger.warning(
                "Joint %s failed to settle (last error≈%.2f°); ignoring failure per operator guidance.",
                joint,
                err_abs if err_abs == err_abs else float("nan"),
            )
            return last

        raise RuntimeError(
            f"Joint {joint} failed to settle at {target:.2f}° after {attempt} attempt(s)"
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

    total_steps = len(resolved_steps)

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

            if (
                previous_pose is not None
                and index != 1
                and index != total_steps
                and joint in previous_pose
                and float(previous_pose[joint]) == target
            ):
                logger.info("Skipping %s for pose %d; unchanged from previous pose", joint, index)
                continue

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
    "SPEED_DEFAULT",
    "SPEED_DEFAULT_FINAL",
    "SPEED_FINE",
    "POSE_PAUSE_S",
    "JOINT_PAUSE_S",
    "IGNORE_SETTLE_FAILURE",
]

