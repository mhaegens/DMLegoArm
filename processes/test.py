"""Simple test process exercising all joints."""
from typing import Any


def run(arm) -> Any:
    """Run joint movements across predefined points."""
    speed = 100
    arm.goto_pose("home", speed=speed)

    # Joint A: open then closed
    arm.move("absolute", {"A": "open"}, speed=speed)
    arm.move("absolute", {"A": "closed"}, speed=speed)

    # Joint B: min -> pick -> max
    for pos in ["min", "pick", "max"]:
        arm.move("absolute", {"B": pos}, speed=speed)

    # Joint C: min -> max
    for pos in ["min", "max"]:
        arm.move("absolute", {"C": pos}, speed=speed)

    # Joint D: neutral -> assembly -> quality -> neutral
    for pos in ["neutral", "assembly", "quality", "neutral"]:
        arm.move("absolute", {"D": pos}, speed=speed)

    return arm.goto_pose("home", speed=speed)
