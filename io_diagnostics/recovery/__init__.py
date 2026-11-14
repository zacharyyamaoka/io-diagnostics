"""Device reset and automatic recovery (docs/device-reset.md): every device's way back, its own reset first, then the USB
port's, then a human, by itself on a deployed robot or for a person who presses Reset….

    profiles/   one reset ladder per device class (frozen dataclasses, one per file)
    ladder      the engine: detect, each rung once per incident, holders and owner locks, evidence, the page
    __main__    check | plan | recover | act | udev-rules | ...

Stdlib only, like the rest of the host (it runs under the tray's system python too).
"""
