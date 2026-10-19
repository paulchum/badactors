"""The castle as it ships: the map, the cast, the balance numbers, the frozen
qualification suite, and the output-token limits models are given.

They live inside the package and are found through :mod:`importlib.resources`, so
an installed wheel reads them just as a checkout does. Every command that reads
one takes a path to read instead (``--map``, ``--cast``, ``--balance``,
``--suite``, ``--outputs``).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def shipped(name: str) -> Path:
    """The path of a file that ships in this package."""
    resource = files(__name__) / name
    if not isinstance(resource, Path):
        raise RuntimeError(
            f"Can't find {name}: badactors has to be installed unzipped to read it."
        )
    return resource


MAP = shipped("map.json")
CAST = shipped("cast.json")
BALANCE = shipped("balance.json")
SUITE = shipped("qualification.json.xz")
OUTPUTS = shipped("outputs.json")

__all__ = ["BALANCE", "CAST", "MAP", "OUTPUTS", "SUITE", "shipped"]
