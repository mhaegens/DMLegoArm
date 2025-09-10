"""Pick from assembly side and place on quality side using absolute poses."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow."""
    speed = 100  # top speed

    # Use existing backlash calibration configured on the arm
    # rather than overriding it here.

    # Poses expressed directly in rotations for each motor.
    steps = [
        {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},  # home
        {"A": 0.0, "B": 22.0, "C": -84.0, "D": 113.0},  # Pick Right S1
        {"A": -4.0, "B": 0.0, "C": -124.0, "D": 113.0},  # Pick Right S2
        {"A": -6.0, "B": 0.0, "C": -129.0, "D": 113.0},  # Grab
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
                result = arm.move("absolute", {motor: target}, speed=speed, units="rotations")
                # arm.move returns degrees; convert to rotations for comparison
                new_rot = result["new_abs"][motor] / 360.0
                if abs(new_rot - target) <= 1e-6:
                    break
    return result
