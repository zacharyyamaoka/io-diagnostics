"""Test-run shadow of pyrealsense2: the real extension (enums, intrinsics) with a device-less context.

WHY: intel_rgbd's conftest and test_real call find_device_serials() at import, and its shared contract suite starts
real pipelines when a camera is found. On the rack PC two D405s are plugged in and a test run must never open a
camera, so every context lists no device and a pipeline refuses to exist. Used by tools/safe_pytest_intel_rgbd.py.
"""

import glob
import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_real_spec = importlib.machinery.PathFinder.find_spec("pyrealsense2", [path for path in sys.path
                                                                       if os.path.abspath(path or ".") != _HERE])
if _real_spec is None or not _real_spec.submodule_search_locations:
    raise ImportError("shadow pyrealsense2: the real pyrealsense2 is not installed in this environment")
[_SO] = glob.glob(os.path.join(list(_real_spec.submodule_search_locations)[0], "pyrealsense2.cpython-*.so"))
_loader = importlib.machinery.ExtensionFileLoader("pyrealsense2.pyrealsense2", _SO)
_spec = importlib.util.spec_from_file_location("pyrealsense2.pyrealsense2", _SO, loader=_loader)
_real = importlib.util.module_from_spec(_spec)
sys.modules["pyrealsense2.pyrealsense2"] = _real
_loader.exec_module(_real)
globals().update({name: getattr(_real, name) for name in dir(_real) if not name.startswith("__")})
__version__ = _real.__version__
SHADOW = True


class context:  # noqa: N801 - the SDK's spelling
    def __init__(self, *args, **kwargs):
        pass

    def query_devices(self):
        return []

    @property
    def devices(self):
        return []


class pipeline:  # noqa: N801
    def __init__(self, *args, **kwargs):
        raise RuntimeError("shadow pyrealsense2: no streaming in this test run")
