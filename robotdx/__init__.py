"""robotdx: a compatibility alias. The diagnostics library moved to its own package, ``robot_diagnostics`` (PyPI ``robot-diagnostics``,
/home/bam/robot_diagnostics, github.com/zacharyyamaoka/robot_diagnostics) on 2026-11-23; io-diagnostics depends on it.

``import robotdx.diagnostics.X`` keeps working and gives the very module object ``robot_diagnostics.X`` (one registry, one module, never a
second copy), so code outside this repo that still says ``robotdx.diagnostics`` (bam_rack_twin, data-ui's gallery builders) runs unchanged.
New code imports ``robot_diagnostics``.

WHY a meta-path finder, not ``sys.modules['robotdx.diagnostics'] = robot_diagnostics``: that alone aliases the package, but a submodule import
(``robotdx.diagnostics.stock.health``) would then be found through the package's ``__path__`` and executed a second time under the old name,
registering every stock diagnostic twice. The finder answers every ``robotdx.diagnostics[.X]`` name with the already-imported new module.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

OLD, NEW = "robotdx.diagnostics", "robot_diagnostics"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name == OLD or name.startswith(OLD + "."):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(NEW + spec.name[len(OLD):])  # the new module itself, its own __name__ and __spec__ untouched

    def exec_module(self, module):
        pass  # already executed once, under its real name


if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
