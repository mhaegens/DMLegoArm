"""Process registry.

Each entry maps an endpoint name to a callable that accepts the global
`ArmController` instance. New processes should be registered here.
"""

from .pick_assembly_quality import run as pick_assembly_quality
from .pick_quality_assembly import run as pick_quality_assembly

PROCESS_MAP = {
    "pick-assembly-quality": pick_assembly_quality,
    "pick-quality-assembly": pick_quality_assembly,
}

__all__ = ["PROCESS_MAP"]

