"""Write each proposed driver's per-direction binding artifacts (prototype stand-ins).

  V1  drivers_proposed/<pkg>/src/<pkg>/io-diagnostics.json   manifest shipped in the driver
  V3  prototypes/v3-hub-row/bin/<pkg>-diagnostics           the console script an install creates
  V4  prototypes/v4-registry/site/<pkg>-0.1.0.dist-info/     the metadata an install writes
  all drivers_proposed/<pkg>/pyproject.snippet.toml          the lines V3/V4 add to the driver's pyproject

    /usr/bin/python3 /home/bam/io-diagnostics/tools/make_bindings.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = {
    "moteus_servo": {"title": "moteus servo bus", "usb": [{"vid": "0483", "pid": "5740"}], "icon": "bus"},
    "intel_rgbd": {"title": "RealSense camera", "usb": [{"vid": "8086", "pid": "0b5b"}, {"vid": "8086", "pid": "0b07"}], "icon": "camera"},
    "obsbot_ptz_camera": {"title": "OBSBOT PTZ camera", "usb": [{"vid": "3564"}], "icon": "camera"},
}


def main() -> None:
    for package, info in DRIVERS.items():
        package_dir = ROOT / "drivers_proposed" / package / "src" / package
        manifest = {"name": package, "title": info["title"], "usb": info["usb"], "icon": info["icon"],
                    "entry": f"{package}.diagnostics:DIAGNOSTICS"}
        (package_dir / "io-diagnostics.json").write_text(json.dumps(manifest, indent=1) + "\n")

        script = ROOT / "prototypes" / "v3-hub-row" / "bin" / f"{package}-diagnostics"
        script.write_text(
            "#!/usr/bin/python3\n"
            "# Stand-in for the console script an install creates from the driver's [project.scripts].\n"
            "import sys\n"
            f"sys.path[:0] = ['{ROOT}', '{ROOT / 'drivers_proposed' / package / 'src'}']\n"
            f"from {package}.diagnostics import DIAGNOSTICS\n"
            "from contract.cli import run\n"
            "raise SystemExit(run(DIAGNOSTICS))\n"
        )
        script.chmod(0o755)

        dist_info = ROOT / "prototypes" / "v4-registry" / "site" / f"{package}-0.1.0.dist-info"
        dist_info.mkdir(parents=True, exist_ok=True)
        (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package.replace('_', '-')}\nVersion: 0.1.0\n")
        (dist_info / "entry_points.txt").write_text(f"[io_diagnostics.drivers]\n{package} = {package}.diagnostics:DIAGNOSTICS\n")

        (ROOT / "drivers_proposed" / package / "pyproject.snippet.toml").write_text(
            f"# Lines this driver's pyproject.toml gains (only the direction you pick needs its lines)\n\n"
            f"# V3 hub row: the CLI the hub spawns\n[project.scripts]\n{package}-diagnostics = \"{package}.diagnostics:main\"\n\n"
            f"# V4 registry: the entry point the registry discovers\n[project.entry-points.\"io_diagnostics.drivers\"]\n"
            f"{package} = \"{package}.diagnostics:DIAGNOSTICS\"\n"
        )
    print("bindings written for", ", ".join(DRIVERS))


if __name__ == "__main__":
    main()
