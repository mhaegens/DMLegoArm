"""Pick from quality side and place on assembly side."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-quality-assembly workflow."""
    arm.goto_pose("home", speed=60)
    arm.move("relative", {"A": 90}, speed=50)
    arm.move("relative", {"D": 30}, speed=30)  # open claw
    arm.move("relative", {"B": -20}, speed=40)  # move down
    arm.move("relative", {"D": -30}, speed=30)  # close claw
    arm.move("relative", {"B": 20}, speed=40)  # move up
    arm.move("relative", {"A": -180}, speed=50)  # rotate to assembly side
    arm.move("relative", {"B": -20}, speed=40)  # move down
    arm.move("relative", {"D": 30}, speed=30)  # open claw
    arm.move("relative", {"B": 20}, speed=40)  # move up
    arm.move("relative", {"D": -30}, speed=30)  # close claw
    return arm.goto_pose("home", speed=60)

