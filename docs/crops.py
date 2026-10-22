"""Crop the real sandbox renders (out/native/) into small WebP exhibits for the gallery.

    /usr/bin/python3 /home/bam/io-diagnostics/docs/crops.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "out" / "native"
CROPS = ROOT / "out" / "crops"

# name: (source, crop w:h:x:y, output width)
SPECS = {
    "v1-quick": ("v1-F1-device.png", "430:660:1170:0", 300),
    "v1-banner": ("v1-F1-banner.png", "520:120:540:28", 400),
    "v1-now": ("v1-F3-menu.png", "430:560:1170:0", 300),
    "v2-menu": ("v2-F1-device.png", "640:640:960:0", 380),
    "v2-banner": ("v2-F1-banner.png", "520:120:540:28", 400),
    "v4-bar": ("v4-F1-bar.png", "560:32:1040:0", 380),
    "v4-menu": ("v4-F1-menu.png", "640:420:960:0", 380),
    "v5-bar": ("v5-F3-bar.png", "560:32:1040:0", 380),
    "v5-menu": ("v5-F3-menu.png", "640:330:960:0", 380),
}


def main() -> None:
    CROPS.mkdir(parents=True, exist_ok=True)
    for name, (source, crop, width) in SPECS.items():
        target = CROPS / f"{name}.webp"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(NATIVE / source), "-vf", f"crop={crop},scale={width}:-1:flags=lanczos",
                        "-quality", "78", str(target)], check=True)
        print(f"{target.name} {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
