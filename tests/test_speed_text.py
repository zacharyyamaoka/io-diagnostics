"""A link speed is named by its USB generation only on a USB link (2026-10-24: a 10 Gb/s network card read "USB 3.2 Gen 2")."""

from io_diagnostics.tree import speed_text


def test_a_usb_link_carries_its_generation_name():
    assert speed_text(480).endswith("480 Mb/s") and speed_text(480) != "480 Mb/s"
    assert speed_text(10000).endswith("10 Gb/s") and speed_text(10000) != "10 Gb/s"


def test_a_network_link_is_only_its_rate():
    assert speed_text(10000, usb=False) == "10 Gb/s"
    assert speed_text(1000, usb=False) == "1 Gb/s"
    assert speed_text(100, usb=False) == "100 Mb/s"


def test_no_speed_is_no_text():
    assert speed_text(None) == "" and speed_text(0, usb=False) == ""
