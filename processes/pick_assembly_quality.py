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
        {"A": "open", "B": "min+22", "C": "max-84", "D": "assembly"},  # Pick Right S1
        {"A": "open-4", "B": "min", "C": "max-124", "D": "assembly"},  # Pick Right S2
        {"A": "closed+1", "B": "min", "C": "max-129", "D": "assembly"},  # Grab
        {"A": "closed+1", "B": "min+22", "C": "max-84", "D": "assembly"},  # Pick Right S3
        {"A": "closed+1", "B": "min", "C": "min", "D": "neutral"},  # Go Home for all but A
        {"A": "closed+1", "B": "min+22", "C": "max-84", "D": "quality"},  # Drop Left S1
        {"A": "closed+1", "B": "min", "C": "max-124", "D": "quality"},  # Drop Left S2
        {"A": "open", "B": "min", "C": "max-129", "D": "quality"},  # Release
        {"A": "open", "B": "min+22", "C": "max-84", "D": "quality"},  # Drop Left S3
        {"A": "open", "B": "min", "C": "max", "D": "neutral"},  # final home
    ]

    last_target = None
    result = None
    for i, pose in enumerate(steps):
        if last_target:
            ok, errs = arm.verify_at(last_target)
            if not ok:
                logger.warning("DRIFT before step %d: %s", i, errs)
                arm.move("absolute", last_target, speed=40, timeout_s=60)
                ok2, errs2 = arm.verify_at(last_target)
                if not ok2:
                    logger.error("DRIFT persists before step %d: %s; recovering to home", i, errs2)
                    arm.recover_to_home(speed=30, timeout_s=90.0)
        result = arm.move("absolute", pose, speed=speed)
        last_target = arm.resolve_pose(pose)
    return result
