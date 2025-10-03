"""Pick from quality side and place on assembly side with new D behaviour."""

from __future__ import annotations

from typing import Any

from ._precision_workflow import run_workflow


def run(arm) -> Any:
    """Execute the quality→assembly workflow using the shared runner."""

    steps = [
        ("Start at home",        {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
        ("Rotate to quality",    {"A": "open",   "B": "min",   "C": "max",  "D": "quality"}),
        ("Approach",             {"A": "open",   "B": "pick",  "C": "max",  "D": "quality"}),
        ("Descend",              {"A": "open",   "B": "pick",  "C": "pick", "D": "quality"}),
        ("Grip",                 {"A": "closed", "B": "pick",  "C": "pick", "D": "quality"}),
        ("Lift",                 {"A": "closed", "B": "pick",  "C": "max",  "D": "quality"}),
        ("Return over centre",   {"A": "closed", "B": "min",   "C": "max",  "D": "neutral"}),
        ("Rotate to assembly",   {"A": "closed", "B": "min",   "C": "max",  "D": "assembly"}),
        ("Approach assembly",    {"A": "closed", "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Descend to place",     {"A": "closed", "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Release",              {"A": "open",   "B": "pick",  "C": "pick", "D": "assembly"}),
        ("Clear",                {"A": "open",   "B": "pick",  "C": "max",  "D": "assembly"}),
        ("Final home",           {"A": "open",   "B": "min",   "C": "max",  "D": "neutral"}),
    ]

    return run_workflow(arm, steps)


__all__ = ["run"]

