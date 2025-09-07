"""Pick from assembly side and place on quality side using absolute poses."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow."""
    speed = 100  # top speed
    arm.set_backlash({"D": 5 * 360})  # 5 rotations backlash on motor D
    arm.move("absolute", {"A": 0, "B": 0, "C": 0, "D": 0}, speed=speed)
    arm.move(
        "absolute",
        {"A": -20160, "B": 0, "C": -37800, "D": 43920},
        speed=speed,
    )
    arm.move(
        "absolute",
        {"A": -21600, "B": 0, "C": -34920, "D": 43920},
        speed=speed,
    )
    arm.move(
        "absolute",
        {"A": -21600, "B": 14400, "C": -9720, "D": 3600},
        speed=speed,
    )
    arm.move(
        "absolute",
        {"A": -21600, "B": 3240, "C": -29880, "D": -39600},
        speed=speed,
    )
    arm.move(
        "absolute",
        {"A": -20880, "B": 3240, "C": 2520, "D": -39600},
        speed=speed,
    )
    return arm.move("absolute", {"A": 0, "B": 0, "C": 0, "D": 0}, speed=speed)

