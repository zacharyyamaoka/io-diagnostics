"""Fans and the hwmon reader (Zach, 2026-11-10: "It would be helpful to get the fan speed for like all of these things ... if the
temperature is that then are the fans at maximum?").

The hwmon reader is LiveProbe.hwmon() over a fake sysfs tree (tmp_path/class/hwmon): every chip, its temperature channels with the
limits the chip reports, its fans (RPM) and PWM outputs (duty). WHY not psutil.sensors_temperatures: it merges chips that share a name,
numbers an unlabelled sensor by its position (gigabyte_wmi hides a channel it cannot read, so its 4th sensor can be temp5) and reads no
fan duty. Board fans are a Fans check per chip; the GPU's fans are NVML's, on the GPU's own check.
"""

from __future__ import annotations

from pathlib import Path

from test_computer import FakeProbe, chip, measure, run

from contract.record import OK, WARN
import computer_diagnostics as computer


def hwmon_tree(root: Path) -> Path:
    """A sysfs root with five chips: the CPU, gigabyte_wmi without temp4, an ITE fan chip, an NVMe drive and a chip with no reading."""

    def write(folder: Path, files: dict[str, str]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (folder / name).write_text(text + "\n")

    hwmon = root / "class" / "hwmon"
    write(hwmon / "hwmon0", {"name": "k10temp", "temp1_input": "79500", "temp1_label": "Tctl", "temp3_input": "77250", "temp3_label": "Tccd1"})
    write(hwmon / "hwmon3", {"name": "gigabyte_wmi", "temp1_input": "44000", "temp2_input": "57000", "temp3_input": "79000",
                             "temp5_input": "93000", "temp6_input": "70000"})
    write(hwmon / "hwmon10", {"name": "it8689", "temp1_input": "41000", "temp1_max": "127000",
                              "fan1_input": "1210", "fan1_min": "300", "fan1_alarm": "0", "pwm1": "255", "pwm1_enable": "2",
                              "fan2_input": "0", "fan2_min": "300", "fan2_alarm": "1", "pwm2": "128", "pwm2_enable": "2",
                              "fan3_input": "640", "fan3_label": "SYS_FAN2", "fan3_max": "2000"})
    drive = root / "devices" / "pci0000:00" / "0000:02:00.0" / "nvme" / "nvme0"
    write(drive, {"model": "Samsung SSD 980 PRO 1TB                 "})
    write(hwmon / "hwmon1", {"name": "nvme", "temp1_input": "56850", "temp1_label": "Composite", "temp1_max": "81850", "temp1_crit": "84850",
                             "temp2_input": "56850", "temp2_label": "Sensor 1", "temp2_max": "65261850"})
    (hwmon / "hwmon1" / "device").symlink_to(drive)
    write(hwmon / "hwmon5", {"name": "broken", "temp1_input": "garbage"})
    return root


def test_the_hwmon_reader_names_channels_as_sysfs_does_and_keeps_each_chip(tmp_path):
    chips = {item["hwmon"]: item for item in computer.LiveProbe(hwmon_tree(tmp_path)).hwmon()}
    assert [item["hwmon"] for item in computer.LiveProbe(tmp_path).hwmon()] == ["hwmon0", "hwmon1", "hwmon3", "hwmon5", "hwmon10"]
    cpu = chips["hwmon0"]
    assert cpu["name"] == "k10temp" and [(t["channel"], t["label"], t["current"]) for t in cpu["temps"]] == [("temp1", "Tctl", 79.5), ("temp3", "Tccd1", 77.25)]
    # gigabyte_wmi hides temp4: the fourth sensor is temp5, never "temp 4"
    assert [t["channel"] for t in chips["hwmon3"]["temps"]] == ["temp1", "temp2", "temp3", "temp5", "temp6"]
    nvme = chips["hwmon1"]
    assert nvme["model"] == "Samsung SSD 980 PRO 1TB" and nvme["device"].endswith("/nvme/nvme0")
    assert (nvme["temps"][0]["max"], nvme["temps"][0]["crit"]) == (81.85, 84.85) and nvme["temps"][1]["max"] == 65261.85
    assert chips["hwmon5"]["temps"] == [{"channel": "temp1", "label": "", "current": None, "max": None, "crit": None, "emergency": None}]
    # WHY no tempN_min: nothing uses it, and on an NVMe drive every tempN_min / tempN_max read is an admin command to the drive
    # (drivers/nvme/host/hwmon.c, nvme_get_temp_thresh), which follow would send every second
    assert all("min" not in sensor for item in chips.values() for sensor in item["temps"])
    fans = chips["hwmon10"]["fans"]
    assert [(f["channel"], f["rpm"], f["min"], f["alarm"]) for f in fans] == [("fan1", 1210, 300, False), ("fan2", 0, 300, True), ("fan3", 640, None, None)]
    assert fans[2]["label"] == "SYS_FAN2" and fans[2]["max"] == 2000
    assert [(p["channel"], p["duty_percent"], p["enable"]) for p in chips["hwmon10"]["pwms"]] == [("pwm1", 100.0, 2), ("pwm2", 50.2, 2)]


def test_a_machine_without_hwmon_reads_no_chips(tmp_path):
    assert computer.LiveProbe(tmp_path).hwmon() == []


def test_each_fan_reads_its_speed_against_its_maximum(tmp_path):
    chips = computer.LiveProbe(hwmon_tree(tmp_path)).hwmon()
    fans = run(chips=chips)["Board: Fans it8689"]
    assert measure(fans, "fan1").value == 1210 and measure(fans, "fan1").unit == "rpm"  # OpenTelemetry hw.fan.speed and Home Assistant say "rpm"
    assert measure(fans, "fan1 duty").value == 100.0 and measure(fans, "fan1 duty").unit == "%"
    assert measure(fans, "SYS_FAN2 (fan3)").value == 640 and measure(fans, "SYS_FAN2 (fan3) of max").value == 32.0
    assert "fan1 1210 RPM at 100 % duty (full speed)" in fans.message
    # the chip's own alarm (fan2 under its 300 RPM minimum while driven at 50 %) is the only thing that raises a fan's level
    assert fans.level == WARN and fans.code == "FanAlarm" and "fan2" in fans.message
    values = dict(fans.values)
    assert "fan2_alarm" in values["threshold_source"] and "chip" in values["threshold_source"]
    assert values["control"] == "pwm1 automatic · pwm2 automatic"


def test_fans_that_turn_with_no_alarm_are_ok():
    quiet = chip("nct6798", fans=[{"channel": "fan1", "label": "", "rpm": 900, "min": 200, "max": None, "target": None, "alarm": False}],
                 pwms=[{"channel": "pwm1", "duty_percent": 40.0, "enable": 5}])
    fans = run(chips=[quiet])["Board: Fans nct6798"]
    assert fans.level == OK and fans.message == "fan1 900 RPM at 40 % duty"


def test_no_board_fan_says_so_in_plain_words():
    """This machine (2026-11-10): no hwmon chip has a fan: the A620M S2H's fan chip has no driver in kernel 6.8."""

    fans = run()["Board: Fans"]
    assert fans.level == OK and fans.message.startswith("not available: no fan sensor in /sys/class/hwmon")
    assert not fans.measurements


def test_gpu_fans_are_percent_of_the_gpus_maximum():
    stopped = run()["GPU 0: NVIDIA GeForce RTX 4080 SUPER"]
    assert measure(stopped, "fan 1").value == 0 and measure(stopped, "fan 2").unit == "%" and "fans 0 % (stopped)" in stopped.message
    gpu = FakeProbe().gpu["gpus"][0]
    flat_out = {"gpus": [{**gpu, "fans": [{"fan": 0, "percent": 100, "target_percent": 100}, {"fan": 1, "percent": 100, "target_percent": 100}]}]}
    full = run(gpu=flat_out)["GPU 0: NVIDIA GeForce RTX 4080 SUPER"]
    assert "fans 100 % (full speed)" in full.message
    uneven = {"gpus": [{**gpu, "fans": [{"fan": 0, "percent": 45, "target_percent": 45}, {"fan": 1, "percent": 47, "target_percent": 45}]}]}
    assert "fans 45 / 47 %" in run(gpu=uneven)["GPU 0: NVIDIA GeForce RTX 4080 SUPER"].message
    values = dict(full.values)
    assert "maximum" in values["fan_scale"]  # NVML: "a percentage of the product's maximum noise tolerance fan speed"
