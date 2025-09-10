"""Pick from assembly side and place on quality side using named points."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow using point names."""
    speed = 100  # top speed

    # Each pose references previously calibrated point names rather than raw
    # numeric values.  Offsets are expressed relative to those points and are
    # resolved by :meth:`ArmController.resolve_point`.
    steps = [
        {"A": "open", "B": "min", "C": "max", "D": "home"},  # Start at home
        {"A": "open", "B": "min+22", "C": "max-84", "D": "assembly"},  # Pick Right S1
        {"A": "open-4", "B": "min", "C": "max-124", "D": "assembly"},  # Pick Right S2
        {"A": "closed+1", "B": "min", "C": "max-129", "D": "assembly"},  # Grab
        {"A": "closed+1", "B": "min+22", "C": "max-84", "D": "assembly"},  # Pick Right S3
        {"A": "closed+1", "B": "min", "C": "min", "D": "home"},  # Go Home for all but A
        {"A": "closed+1", "B": "min+22", "C": "max-84", "D": "quality"},  # Drop Left S1
        {"A": "closed+1", "B": "min", "C": "max-124", "D": "quality"},  # Drop Left S2
        {"A": "open", "B": "min", "C": "max-129", "D": "quality"},  # Release
        {"A": "open", "B": "min+22", "C": "max-84", "D": "quality"},  # Drop Left S3
        {"A": "open", "B": "min", "C": "max", "D": "home"},  # final home
    ]

    result = None
    for pose in steps:
        result = arm.move("absolute", pose, speed=speed)
    return result
