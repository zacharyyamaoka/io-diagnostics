"""Where every limit comes from (Zach, 2026-11-10: "I want to be able to click this expand it. and I want to be able to like go to the
source for that threshold values as to why it was set at that value... Ideally it should link to like a datasheet"), and the gigabyte_wmi
temp5 decision ("For the Gigabyte wmi temperature it says like critical temperature.... ok I want to try to fix that").

Every status with a limit carries the value ``threshold_source`` (plain words: whose number, and why) and, where a page that states it
exists, ``threshold_url``: a datasheet or spec page, opened and its number confirmed on 2026-11-10.
"""

from __future__ import annotations

from test_computer import A620M_S2H, FakeProbe, chip, gigabyte, measure, run

from contract.record import ERROR, OK, WARN
import computer_diagnostics as computer

AMD_7900X = "https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7900x.html"
NVIDIA_4080 = "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4080-family/"
ROS = "https://github.com/ros/diagnostics/blob/ros2/diagnostic_common_diagnostics/diagnostic_common_diagnostics/"


def limited(status) -> bool:
    return any(limit is not None for measurement in status.measurements
               for limit in (measurement.warn_low, measurement.warn_high, measurement.error_low, measurement.error_high))


def test_every_status_with_a_limit_says_whose_limit_it_is():
    statuses = computer.diagnose(FakeProbe(chips=[*FakeProbe().chips, gigabyte()]), computer.Options(), host="rig")
    with_limits = [status for status in statuses if limited(status)]
    assert len(with_limits) >= 8
    for status in with_limits:
        values = dict(status.values)
        assert values.get("threshold_source", "").strip(), status.name
        url = values.get("threshold_url")
        assert url is None or url.startswith("https://"), (status.name, url)


def test_ros_limits_link_to_the_ros_monitor_that_sets_them():
    found = run()
    for name, monitor in (("CPU: CPU Information", "cpu_monitor.py"), ("RAM: RAM Information", "ram_monitor.py"),
                          ("Disks: HD Usage", "hd_monitor.py"), ("NTP offset", "ntp_monitor.py")):
        values = dict(found[name].values)
        assert values["threshold_url"].startswith(ROS + monitor), (name, values["threshold_url"])
        assert "ROS" in values["threshold_source"]
    usb = dict(found["USB Bandwidth"].values)
    assert usb["threshold_url"] == "https://www.usb.org/document-library/usb-20-specification" and "5.6.4" in usb["threshold_source"]
    network = dict(found["Network: Network Usage"].values)
    assert "RobotDx" in network["threshold_source"] and "threshold_url" not in network  # our own choice: no page to link


def test_the_cpu_limit_is_the_parts_published_tjmax_when_known():
    cpu = run()["CPU: Temperature CPU (k10temp)"]
    values = dict(cpu.values)
    assert values["threshold_url"] == AMD_7900X
    assert "AMD Ryzen 9 7900X" in values["threshold_source"] and "Tjmax" in values["threshold_source"] and "95 °C" in values["threshold_source"]
    assert "RobotDx" in values["threshold_source"]  # the 85 °C warning is ours, and says so


def test_an_unknown_cpu_keeps_the_robotdx_default_and_says_it_is_one():
    cpu = run(cpu="Some Future CPU 9000")["CPU: Temperature CPU (k10temp)"]
    assert (measure(cpu, "Tctl").warn_high, measure(cpu, "Tctl").error_high) == (85.0, 95.0)
    values = dict(cpu.values)
    assert "RobotDx default" in values["threshold_source"] and "Some Future CPU 9000" in values["threshold_source"]
    assert "threshold_url" not in values


def test_a_cpu_sensor_with_its_own_limit_uses_it():
    intel = run(chips=[chip("coretemp", [("temp1", "Package id 0", 70.0, 80.0, 100.0)])])["CPU: Temperature CPU (coretemp)"]
    assert (measure(intel, "Package id 0").warn_high, measure(intel, "Package id 0").error_high) == (80.0, 100.0)
    assert "temp1_crit" in dict(intel.values)["threshold_source"]


def test_the_drive_uses_its_own_thresholds_and_links_its_datasheet():
    nvme = run()["Disks: Temperature NVMe (nvme)"]
    values = dict(nvme.values)
    assert "drive" in values["threshold_source"] and "84.85" in values["threshold_source"] and "Sensor 1" in values["threshold_source"]
    assert values["threshold_url"].startswith("https://download.semiconductor.samsung.com/") and values["model"] == "Samsung SSD 980 PRO 1TB"


def test_the_gpu_limits_come_from_the_gpu_and_link_its_spec_page():
    gpu = run()["GPU 0: NVIDIA GeForce RTX 4080 SUPER"]
    values = dict(gpu.values)
    assert values["threshold_url"] == NVIDIA_4080
    source = values["threshold_source"]
    assert "NVML" in source and "90 °C" in source and "96 °C" in source and "101 °C" in source
    other = {"gpus": [{**FakeProbe().gpu["gpus"][0], "name": "NVIDIA RTX A2000"}]}
    unknown = dict(run(gpu=other)["GPU 0: NVIDIA RTX A2000"].values)
    assert unknown["threshold_url"] == "https://github.com/NVIDIA/go-nvml/blob/main/gen/nvml/nvml.h"  # what each threshold means


# ----- gigabyte_wmi temp5 ------------------------------------------------------------------------------------------------
def test_gigabyte_temp5_at_97_degrees_is_not_an_error():
    """Live on 2026-11-10: ERROR "gigabyte_wmi temp 5 97 °C: Critical Temperature (above 95 °C, default: the sensor reports none)".
    The 95 °C was the CPU's Tjmax, borrowed by a board sensor that reports no limit; temp5 is the board's VRM, which has none published."""

    board = run(chips=[gigabyte(97.0)])["Board: Temperature gigabyte_wmi"]
    assert board.level == OK and board.code == ""
    assert all(m.warn_high is None and m.error_high is None for m in board.measurements)
    assert board.message == "gigabyte_wmi temp5 97 °C (probably the VRM MOS) · no limit known"


def test_on_the_a620m_s2h_each_gigabyte_channel_has_its_probable_name():
    board = run(chips=[gigabyte()], board_info=A620M_S2H)["Board: Temperature gigabyte_wmi"]
    assert [m.key for m in board.measurements] == ["temp1 System 1", "temp2 Chipset", "temp3 CPU", "temp4 PCIEX16", "temp5 VRM MOS",
                                                    "temp6 VSOC MOS"]
    values = dict(board.values)
    assert values["labels_source"].startswith("probable")
    assert "temp3" in values["labels_source"] and "Tctl" in values["labels_source"]  # how it was checked on this board
    assert values["labels_url"].startswith("https://github.com/lm-sensors/lm-sensors/")
    assert values["threshold_source"].startswith("none") and "gigabyte-wmi.c" in values["threshold_url"]


def test_on_another_gigabyte_board_the_channels_stay_unnamed():
    """The order differs between boards (a B550 AORUS ELITE V2 reads temp2 "System 2", temp6 "PCH"): only a checked board is named."""

    board = run(chips=[gigabyte(skip=("temp4",))], board_info={"vendor": "Gigabyte Technology Co., Ltd.", "name": "B550 AORUS ELITE V2"})
    found = board["Board: Temperature gigabyte_wmi"]
    assert [m.key for m in found.measurements] == ["temp1", "temp2", "temp3", "temp5", "temp6"]
    assert "labels_source" not in dict(found.values) and found.message == "gigabyte_wmi temp5 93 °C · no limit known"


def test_a_sensor_without_a_limit_never_borrows_one():
    hot = [chip("amdgpu", [("temp1", "edge", 99.0, None, None)]), chip("mt7921_phy0", [("temp1", "", 99.0, None, None)]),
           chip("acpitz", [("temp1", "", 99.0, None, None)])]
    for name, status in run(chips=hot).items():
        if name.endswith(("(amdgpu)", "mt7921_phy0", "(acpitz)")):
            assert status.level == OK and "no limit known" in status.message, name
            assert dict(status.values)["threshold_source"].startswith("none"), name


def test_a_sensor_that_reports_its_own_limit_still_errors():
    dgpu = run(chips=[chip("amdgpu", [("temp1", "edge", 101.0, None, 100.0)])])["GPU (amdgpu): Temperature GPU (amdgpu)"]
    assert dgpu.level == ERROR and "its own limit" in dgpu.message
    assert "temp1_crit" in dict(dgpu.values)["threshold_source"]
    wifi = run(chips=[chip("mt7921_phy0", [("temp1", "", 125.0, 120.0, None)])])["Network: Temperature mt7921_phy0"]
    assert wifi.level == WARN


def test_the_probe_reads_the_board_and_cpu_names_once_from_dmi_and_cpuinfo(tmp_path):
    """The names CPU_SPECS and BOARD_SENSOR_NAMES are keyed by, read from fake /sys and /proc trees."""

    dmi = tmp_path / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "board_vendor").write_text("Gigabyte Technology Co., Ltd.\n")
    (dmi / "board_name").write_text("A620M S2H\n")
    (tmp_path / "proc").mkdir()
    cpuinfo = tmp_path / "proc" / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9 7900X 12-Core Processor\n\n"
                       "processor\t: 1\nmodel name\t: AMD Ryzen 9 7900X 12-Core Processor\n")
    probe = computer.LiveProbe(tmp_path / "sys", proc_root=tmp_path / "proc")
    assert probe.board() == A620M_S2H and probe.cpu_model() == "AMD Ryzen 9 7900X 12-Core Processor"
    cpuinfo.write_text("model name\t: Something Else\n")
    assert probe.cpu_model() == "AMD Ryzen 9 7900X 12-Core Processor"  # read once per process: follow never re-reads it
    assert computer.LiveProbe(tmp_path / "nothing", proc_root=tmp_path / "nothing").board() == {"vendor": "", "name": ""}
