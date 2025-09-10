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
        {"A": "open", "B": "pick", "C": "max-30", "D": "assembly"},  # Pick Right S1
        {"A": "open-2", "B": "pick", "C": "max-80", "D": "assembly"},  # Pick Right S2
        {"A": "closed+1", "B": "pick", "C": "max-81", "D": "assembly"},  # Grab
        {"A": "closed+1", "B": "pick", "C": "max-81", "D": "assembly"},  # Pick Right S3
        {"A": "closed+1", "B": "min", "C": "min", "D": "neutral"},  # Go Home for all but A
        {"A": "closed+1", "B": "pick", "C": "max-81", "D": "quality"},  # Drop Left S1
        {"A": "closed+1", "B": "pick", "C": "max-80", "D": "quality"},  # Drop Left S2
        {"A": "open", "B": "pick", "C": "max-80", "D": "quality"},  # Release
        {"A": "open", "B": "pick", "C": "max-30", "D": "quality"},  # Drop Left S3
        {"A": "open", "B": "min", "C": "max", "D": "neutral"},  # final home
    ]

    # Pre-resolve the pose names once and reuse the exact coordinates.
    abs_steps = [arm.resolve_pose(p) for p in steps]

    result = None
    for i, target in enumerate(abs_steps):
        if i > 0:
            ok, errs = arm.verify_at(abs_steps[i - 1])
            if not ok:
                # If D is wildly off, treat as bad reference and recover.
                if errs.get("D", 0) >= 720:
                    logger.error(
                        "D reference drift (%.1f°) before step %d; recovering", errs["D"], i
                    )
                    arm.recover_to_home(speed=30, timeout_s=90.0)
                else:
                    logger.warning("DRIFT before step %d: %s; nudging back", i, errs)
                    arm.move("absolute", abs_steps[i - 1], speed=40, timeout_s=60)
                    ok2, _ = arm.verify_at(abs_steps[i - 1])
                    if not ok2:
                        logger.error("DRIFT persists before step %d; recovering", i)
                        arm.recover_to_home(speed=30, timeout_s=90.0)
        result = arm.move("absolute", target, speed=speed)
    return result
