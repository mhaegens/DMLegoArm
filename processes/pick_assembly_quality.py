"""Pick from assembly side and place on quality side with new D behaviour."""

from __future__ import annotations

from typing import Any

from ._precision_workflow import run_workflow


def run(arm) -> Any:
    """Execute the assembly→quality workflow using the shared runner."""

    steps = [
        ("Start at home",        {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
        ("Approach S1",          {"A": "open",   "B": "pick",  "C": "max",  "D": "quality"}),
        ("Approach S2",          {"A": "open",   "B": "pick",  "C": "pick", "D": "quality"}),
        ("Grip",                 {"A": "closed", "B": "pick",  "C": "pick", "D": "quality"}),
        ("Lift clear",           {"A": "closed", "B": "pick",  "C": "max",  "D": "quality"}),
        ("Return over centre",   {"A": "closed", "B": "min",   "C": "max",  "D": "neutral"}),
        ("Rotate to assembly",   {"A": "closed", "B": "min",   "C": "max",  "D": "assembly"}),
        ("Lower to place",       {"A": "closed", "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Place",                {"A": "closed", "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Release",              {"A": "open",   "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Clear",                {"A": "open",   "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Final home",           {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
    ]

    return run_workflow(arm, steps)


__all__ = ["run"]

