"""Load a prototype's binding by path (prototype dirs are not packages)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROTOTYPES = Path(__file__).resolve().parents[1] / "prototypes"


def binding(variant: str):
    name = f"binding_{variant.replace('-', '_')}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PROTOTYPES / variant / "binding.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def v2_drivers() -> list:
    """The drivers as the naming-convention binding finds them (the views are binding-independent)."""

    return binding("v2-plug-journal").find_drivers()
