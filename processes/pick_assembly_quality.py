"""Pick from assembly side and place on quality side."""

from typing import Any


def run(arm) -> Any:
    """Execute the pick-assembly-quality workflow."""
    arm.goto_pose("home", speed=60)
    arm.move("relative", {"A": -90}, speed=50)
    arm.move("relative", {"D": 30}, speed=30)  # open claw
    arm.move("relative", {"B": -20}, speed=40)  # move down
    arm.move("relative", {"D": -30}, speed=30)  # close claw
    arm.move("relative", {"B": 20}, speed=40)  # move up
    arm.move("relative", {"A": 180}, speed=50)  # rotate to quality side
    arm.move("relative", {"B": -20}, speed=40)  # move down
    arm.move("relative", {"D": 30}, speed=30)  # open claw
    arm.move("relative", {"B": 20}, speed=40)  # move up
    arm.move("relative", {"D": -30}, speed=30)  # close claw
    return arm.goto_pose("home", speed=60)

