"""Pick from assembly side and place on quality side using absolute poses."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow."""
    speed = 100  # top speed

    # Use existing backlash calibration configured on the arm
    # rather than overriding it here.

    # Poses expressed in rotations for each motor.
    steps = [
        {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},  # home
        {"A": 0.0, "B": 22.0, "C": -84.0, "D": 113.0},  # Pick Right S1
        {"A": -4.0, "B": 0.0, "C": -124.0, "D": 113.0},  # Pick Right S2
        {"C": -129.0, "A": -6.0, "B": 0.0, "D": 113.0},  # Grab
        {"A": -4.0, "B": 22.0, "C": -84.0, "D": 113.0},  # Pick Right S3
        {"A": -6.0, "B": 0.0, "C": 0.0, "D": 0.0},  # Go Home for all but A
        {"A": -6.0, "B": 22.0, "C": -84.0, "D": -113.0},  # Drop Left S1
        {"A": -6.0, "B": 0.0, "C": -124.0, "D": -113.0},  # Drop Left S2
        {"A": -6.0, "B": 0.0, "C": -129.0, "D": -113.0},  # Release
        {"A": -4.0, "B": 22.0, "C": -84.0, "D": -113.0},  # Drop Left S3
        {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},  # final home
    ]

    result = None
    # Sequentially move each motor to its target for every step.
    for pose in steps:
        for motor in ("A", "B", "C", "D"):
            target = pose.get(motor)
            if target is None:
                continue
            while True:
                target_deg = target * 360.0
                result = arm.move("absolute", {motor: target_deg}, speed=speed, units="degrees")
                # arm.move returns degrees; compare against degree target
                new_deg = result["new_abs"][motor]
                if abs(new_deg - target_deg) <= 1e-6:
                    break
    return result
