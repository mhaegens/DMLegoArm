"""Pick from assembly side and place on quality side using named points."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow using point names."""
    speed = 100  # top speed

    # Each pose references previously calibrated point names rather than raw
    # numeric values.  Offsets are expressed relative to those points and are
    # resolved by :meth:`ArmController.resolve_point`.
    steps = [
        {"A": "open", "B": "min", "C": "max", "D": "neutral"},  # Start at home
        {"A": "open", "B": "pick", "C": "max", "D": "assembly"},  # Pick Right S1
        {"A": "open", "B": "pick", "C": "max", "D": "assembly"},  # Pick Right S2
        {"A": "closed", "B": "pick", "C": "max", "D": "assembly"},  # Grab
        {"A": "closed", "B": "pick", "C": "max", "D": "assembly"},  # Pick Right S3
        {"A": "closed", "B": "min", "C": "min", "D": "neutral"},  # Go Home for all but A
        {"A": "closed", "B": "pick", "C": "max", "D": "quality"},  # Drop Left S1
        {"A": "closed", "B": "pick", "C": "max", "D": "quality"},  # Drop Left S2
        {"A": "open", "B": "pick", "C": "max", "D": "quality"},  # Release
        {"A": "open", "B": "pick", "C": "max", "D": "quality"},  # Drop Left S3
        {"A": "open", "B": "min", "C": "max", "D": "neutral"},  # final home
    ]

    # Pre-resolve the pose names once and reuse the exact coordinates.
    abs_steps = [arm.resolve_pose(p) for p in steps]

    result = None
    prev_pose = None
    joint_order = ("D", "C", "B", "A")

    for idx, target in enumerate(abs_steps):
        if prev_pose is not None:
            ok, errs = arm.verify_at(prev_pose)
            if not ok:
                if errs.get("D", 0) >= 720:
                    logger.error(
                        "D reference drift (%.1f°) before step %d; recovering", errs["D"], idx
                    )
                    arm.recover_to_home(speed=30, timeout_s=90.0)
                else:
                    logger.warning("DRIFT before step %d: %s; nudging back", idx, errs)
                    arm.move("absolute", prev_pose, speed=40, units="degrees", timeout_s=60)
                    ok2, _ = arm.verify_at(prev_pose)
                    if not ok2:
                        logger.error("DRIFT persists before step %d; recovering", idx)
                        arm.recover_to_home(speed=30, timeout_s=90.0)

        for joint in joint_order:
            if joint not in target:
                continue
            joint_target = {joint: target[joint]}
            for _ in range(2):
                result = arm.move("absolute", joint_target, speed=speed, units="degrees")

        prev_pose = target

    return result
