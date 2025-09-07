"""Pick from assembly side and place on quality side using absolute poses."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow."""
    speed = 100  # top speed

    # Backlash is measured in degrees so 5 rotations == 5 * 360
    arm.set_backlash({"D": 5 * 360})

    # The provided pose values are in raw motor degrees and are all multiples of
    # 360.  Large degree values appear to be ignored by the Build HAT firmware,
    # resulting in tiny movements.  Converting to rotations and using the
    # ``units="rotations"`` mode keeps the commands within the supported range
    # and produces the expected motion.
    steps = [
        {"A": 0, "B": 0, "C": 0, "D": 0},
        {"A": -20160 / 360, "B": 0, "C": -37800 / 360, "D": 43920 / 360},
        {"A": -21600 / 360, "B": 0, "C": -34920 / 360, "D": 43920 / 360},
        {"A": -21600 / 360, "B": 14400 / 360, "C": -9720 / 360, "D": 3600 / 360},
        {"A": -21600 / 360, "B": 3240 / 360, "C": -29880 / 360, "D": -39600 / 360},
        {"A": -20880 / 360, "B": 3240 / 360, "C": 2520 / 360, "D": -39600 / 360},
        {"A": 0, "B": 0, "C": 0, "D": 0},
    ]

    result = None
    for pose in steps:
        result = arm.move("absolute", pose, speed=speed, units="rotations")
    return result

