"""The Computer's checks grouped by device (Zach, 2026-11-10: "the metrics here [on] the computer ... grouped by device on the
computer. RAM, CPU, GPU, etc").

WHY by the check's NAME and not by part entities: the entity page lists an entity's OWN records (host.node -> rows.own), and the
Diagnostics list folds them into folders by the "<who>: <what>" of the name (web/detail/d6-rows.js folderPath). A status moved onto
a ``part_of`` entity leaves the Computer's list for a page of its own (measured with a scratch host, 2026-11-10: the host's checks lost
it and the host row read OK while its part was ERROR), and the live RealSense groups under "D405 usb-1-7" for the same reason: that is
its checks' name, while its stream parts' checks are on the stream rows, not in the camera's list. So the device is the name's "who",
the status stays on the Computer (path "") and the "what" keeps its ROS name.
"""

from __future__ import annotations

from test_computer import A620M_S2H, FakeProbe, chip, gigabyte, run

import computer_diagnostics as computer


def folder_of(name: str) -> str:
    """web/detail/d6-rows.js folderPath, for the names this plugin writes: the text before ": " is the folder."""

    return name.split(": ", 1)[0] if ": " in name else ""


def test_each_check_sits_in_its_devices_folder_and_keeps_its_ros_name():
    statuses = computer.diagnose(FakeProbe(chips=[*FakeProbe().chips, gigabyte(), chip("mt7921_phy0", [("temp1", "", 52.0, None, None)],
                                                                                         device="/sys/devices/pci0000:00/0000:05:00.0/ieee80211/phy0")]),
                                 computer.Options(), host="bam-GPU")
    folders = {status.name.split(": ", 1)[1]: folder_of(status.name) for status in statuses}
    assert folders == {"CPU Information": "CPU", "Temperature CPU (k10temp)": "CPU", "RAM Information": "RAM", "HD Usage": "Disks",
                       "Temperature NVMe (nvme)": "Disks", "NVIDIA GeForce RTX 4080 SUPER": "GPU 0", "Temperature gigabyte_wmi": "Board",
                       "Fans": "Board", "Network Usage": "Network", "Temperature mt7921_phy0": "Network",
                       # the machine's own: the clock, the uptime and the USB buses are no one device's
                       "NTP offset": "bam-GPU", "Uptime": "bam-GPU", "USB Bandwidth": "bam-GPU"}
    # every check is still the Computer's own record: one entity, one hardware id, the "what" readable by a ROS tool
    assert {status.path for status in statuses} == {""} and {status.hardware_id for status in statuses} == {"bam-GPU"}


def test_the_devices_are_few_folders_not_one_per_check():
    found = run(chips=[*FakeProbe().chips, gigabyte()])
    folders = {folder_of(name) for name in found if ": " in name}
    assert folders == {"CPU", "RAM", "Disks", "GPU 0", "Board", "Network"}


def test_each_gpu_and_hwmon_chip_lands_in_its_own_device():
    two = {"gpus": [FakeProbe().gpu["gpus"][0], {**FakeProbe().gpu["gpus"][0], "index": "1", "name": "NVIDIA RTX A2000", "bus_id": "00000000:02:00.0"}]}
    chips = [chip("amdgpu", [("temp1", "edge", 58.0, None, None)], device="/sys/devices/pci0000:00/0000:00:08.1/0000:0f:00.0"),
             chip("spd5118", [("temp1", "", 41.0, 55.0, 85.0)], hwmon="hwmon6"),
             chip("r8169_0_600:00", [("temp1", "", 50.0, 120.0, None)], hwmon="hwmon7"),
             chip("acpitz", [("temp1", "", 16.8, None, None)], device="/sys/devices/virtual/thermal/thermal_zone0", hwmon="hwmon8"),
             chip("mystery_chip", [("temp1", "", 30.0, None, None)], hwmon="hwmon9")]
    found = run(gpu=two, chips=chips)
    assert "GPU 0: NVIDIA GeForce RTX 4080 SUPER" in found and "GPU 1: NVIDIA RTX A2000" in found
    assert "GPU (amdgpu): Temperature GPU (amdgpu)" in found and "RAM: Temperature spd5118" in found
    assert "Network: Temperature r8169_0_600:00" in found and "Board: Temperature ACPI zone (acpitz)" in found
    # a chip nobody mapped keeps its own name as its folder: never guessed onto a device
    assert "mystery_chip: Temperature mystery_chip" in found


def test_two_chips_with_one_name_stay_two_checks():
    """psutil merged them into one "nvme" list (and named the second drive's sensors "Composite #4"); each drive is its own check."""

    drives = [chip("nvme", [("temp1", "Composite", 40.0, 81.85, 84.85)], model="Samsung SSD 980 PRO 1TB", hwmon="hwmon1"),
              chip("nvme", [("temp1", "Composite", 45.0, 79.85, 82.85)], model="WD Blue SN580 1TB", hwmon="hwmon2")]
    found = run(chips=drives)
    assert "Disks: Temperature NVMe (nvme)" in found and "Disks: Temperature NVMe (nvme) #2" in found
    assert dict(found["Disks: Temperature NVMe (nvme) #2"].values)["model"] == "WD Blue SN580 1TB"


def test_board_identity_is_the_board_checks_own_value():
    board = run(chips=[gigabyte()])["Board: Temperature gigabyte_wmi"]
    assert dict(board.values)["board"] == f"{A620M_S2H['vendor']} {A620M_S2H['name']}"
