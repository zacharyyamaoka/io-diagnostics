"""The GPU through NVML, NVIDIA's own library (nvidia-ml-py's ``pynvml``; nvidia-smi is built on it), with a fake NVML here, and the
nvidia-smi path kept for an env without the binding (Zach, 2026-11-10: "Please search into how we can reliably get more info about the
GPU"). One real run, under strace, proves what the plugin process opens below /dev with NVML in it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from test_computer import PLUGIN_DIR, ROOT, FakeProbe, measure, run

from contract.record import ERROR, OK, WARN
import computer_diagnostics as computer


# ----- a fake NVML: the pynvml calls the plugin makes, answered as this machine's RTX 4080 SUPER did on 2026-11-10 --------
class NVMLError(Exception):
    def __init__(self, value):
        super().__init__(f"NVML error {value}")
        self.value = value


class FakeNvml:
    NVMLError = NVMLError
    NVML_ERROR_NOT_SUPPORTED, NVML_ERROR_NO_PERMISSION, NVML_ERROR_DRIVER_NOT_LOADED = 3, 4, 9
    NVML_ERROR_LIBRARY_NOT_FOUND, NVML_ERROR_FUNCTION_NOT_FOUND, NVML_ERROR_GPU_IS_LOST = 12, 13, 15
    NVML_TEMPERATURE_GPU = 0
    NVML_TEMPERATURE_THRESHOLD_SHUTDOWN, NVML_TEMPERATURE_THRESHOLD_SLOWDOWN, NVML_TEMPERATURE_THRESHOLD_MEM_MAX = 0, 1, 2
    NVML_TEMPERATURE_THRESHOLD_GPU_MAX, NVML_TEMPERATURE_THRESHOLD_ACOUSTIC_MIN, NVML_TEMPERATURE_THRESHOLD_ACOUSTIC_CURR = 3, 4, 5
    NVML_TEMPERATURE_THRESHOLD_ACOUSTIC_MAX = 6
    NVML_CLOCK_GRAPHICS, NVML_CLOCK_SM, NVML_CLOCK_MEM = 0, 1, 2

    def __init__(self, **override):
        self.inits = self.shutdowns = 0
        self.init_error = None
        self.temperature, self.fans, self.reasons = 52, [0, 0], 0x1
        self.thresholds = {0: 101, 1: 96, 3: 90, 4: 65, 5: 84, 6: 88}  # MEM_MAX (2): not supported on this card
        self.lost = False
        self.__dict__.update(override)

    def _check(self):
        if self.lost:
            raise NVMLError(self.NVML_ERROR_GPU_IS_LOST)

    def nvmlInit(self):
        self.inits += 1
        if self.init_error is not None:
            raise NVMLError(self.init_error)

    def nvmlShutdown(self):
        self.shutdowns += 1

    def nvmlDeviceGetCount(self):
        return 1

    def nvmlDeviceGetHandleByIndex(self, index):
        return ("handle", index)

    def nvmlDeviceGetName(self, handle):
        return "NVIDIA GeForce RTX 4080 SUPER"

    def nvmlDeviceGetUUID(self, handle):
        return "GPU-c6b97139-209d-b215-f5f9-3e448385bca1"

    def nvmlDeviceGetPciInfo(self, handle):
        return SimpleNamespace(busId=b"00000000:01:00.0")  # older bindings hand back bytes

    def nvmlDeviceGetTemperature(self, handle, sensor):
        self._check()
        return self.temperature

    def nvmlDeviceGetTemperatureThreshold(self, handle, kind):
        if kind not in self.thresholds:
            raise NVMLError(self.NVML_ERROR_NOT_SUPPORTED)
        return self.thresholds[kind]

    def nvmlDeviceGetNumFans(self, handle):
        return len(self.fans)

    def nvmlDeviceGetFanSpeed_v2(self, handle, fan):
        return self.fans[fan]

    def nvmlDeviceGetTargetFanSpeed(self, handle, fan):
        return 30

    def nvmlDeviceGetMinMaxFanSpeed(self, handle):
        return (30, 100)

    def nvmlDeviceGetPowerUsage(self, handle):
        return 38543  # mW

    def nvmlDeviceGetEnforcedPowerLimit(self, handle):
        return 320000

    def nvmlDeviceGetClockInfo(self, handle, clock):
        return {1: 420, 2: 5001, 0: 420}[clock]

    def nvmlDeviceGetMaxClockInfo(self, handle, clock):
        return {1: 3105, 2: 11501, 0: 3105}[clock]

    def nvmlDeviceGetCurrentClocksEventReasons(self, handle):
        return self.reasons

    def nvmlDeviceGetUtilizationRates(self, handle):
        return SimpleNamespace(gpu=34, memory=11)

    def nvmlDeviceGetMemoryInfo(self, handle):
        return SimpleNamespace(used=4094033920, total=17171480576, free=0)


def test_nvml_reads_temperature_its_own_limits_fans_power_clocks_and_reasons():
    [gpu] = computer.read_nvml(FakeNvml())
    assert gpu["source"] == "nvml" and gpu["bus_id"] == "00000000:01:00.0" and gpu["name"] == "NVIDIA GeForce RTX 4080 SUPER"
    assert (gpu["temperature"], gpu["max_operating"], gpu["slowdown"], gpu["shutdown"], gpu["acoustic_target"]) == (52, 90, 96, 101, 84)
    assert gpu["fans"] == [{"fan": 0, "percent": 0, "target_percent": 30}, {"fan": 1, "percent": 0, "target_percent": 30}]
    assert (gpu["fan_min_percent"], gpu["fan_max_percent"]) == (30, 100)
    assert (gpu["power_w"], gpu["power_limit_w"]) == (38.543, 320.0)
    assert (gpu["clock_sm_mhz"], gpu["clock_sm_max_mhz"], gpu["clock_mem_mhz"], gpu["clock_mem_max_mhz"]) == (420, 3105, 5001, 11501)
    assert gpu["reasons"] == 0x1 and gpu["utilization"] == 34
    assert gpu["memory_used_mib"] == pytest.approx(3904.4, abs=0.1) and gpu["memory_total_mib"] == pytest.approx(16376.0, abs=0.1)
    assert "mem_max" not in gpu  # not supported on a GeForce card: absent, never a made-up number


def test_a_reading_the_gpu_does_not_support_is_none_and_the_rest_still_reads():
    class NoFans(FakeNvml):
        def nvmlDeviceGetNumFans(self, handle):
            raise NVMLError(self.NVML_ERROR_NOT_SUPPORTED)

        def nvmlDeviceGetEnforcedPowerLimit(self, handle):
            raise NVMLError(self.NVML_ERROR_NO_PERMISSION)

    [gpu] = computer.read_nvml(NoFans(thresholds={}))
    assert gpu["fans"] == [] and gpu["power_limit_w"] is None and gpu["temperature"] == 52
    assert (gpu["max_operating"], gpu["slowdown"], gpu["shutdown"]) == (None, None, None)


def test_an_older_binding_without_the_clock_events_call_reads_the_throttle_reasons():
    class Older(FakeNvml):
        nvmlDeviceGetCurrentClocksEventReasons = None

        def nvmlDeviceGetCurrentClocksThrottleReasons(self, handle):
            return 0x4

    [gpu] = computer.read_nvml(Older())
    assert gpu["reasons"] == 0x4


def test_a_lost_gpu_is_an_error_not_an_empty_reading():
    with pytest.raises(NVMLError):
        computer.read_nvml(FakeNvml(lost=True))


# ----- what the status says ------------------------------------------------------------------------------------------------
def gpu_with(**changes) -> dict:
    return {"gpus": [{**FakeProbe().gpu["gpus"][0], **changes}]}


NAME = "GPU 0: NVIDIA GeForce RTX 4080 SUPER"


def test_the_gpu_status_carries_fans_power_clocks_and_what_holds_its_clocks_back():
    found = run()[NAME]
    keys = {m.key for m in found.measurements}
    assert {"temperature", "fan 1", "fan 2", "power", "power limit", "SM clock", "memory clock", "utilization", "memory used"} <= keys
    assert measure(found, "power limit").value == 320.0 and measure(found, "SM clock").unit == "MHz"
    values = dict(found.values)
    assert values["held_back_by"] == "idle" and values["reading"] == "NVML (nvidia-ml-py)"
    assert values["clocks"] == "SM 420 of 3105 MHz · memory 5001 of 11501 MHz"


def test_a_thermal_slowdown_the_gpu_reports_is_a_warning():
    hot = run(gpu=gpu_with(reasons=0x20, temperature=89.0, fans=[{"fan": 0, "percent": 100, "target_percent": 100}]))[NAME]
    assert hot.level == WARN and hot.code == "GpuSlowingDown"
    assert hot.message.startswith("GPU 0 is slowing itself: heat (software slowdown)") and "fan 100 % (full speed)" in hot.message
    brake = run(gpu=gpu_with(reasons=0x80 | 0x4))[NAME]
    assert brake.level == WARN and "power brake" in brake.message


def test_a_power_cap_is_the_gpu_working_as_configured():
    capped = run(gpu=gpu_with(reasons=0x4, power_w=319.0))[NAME]
    assert capped.level == OK and dict(capped.values)["held_back_by"] == "power cap"


def test_a_temperature_above_slowdown_stays_an_error_with_the_reasons_beside_it():
    critical = run(gpu=gpu_with(temperature=97.0, reasons=0x40))[NAME]
    assert critical.level == ERROR and critical.code == "CriticalTemperature" and "slowdown 96 °C" in critical.message


def test_the_nvidia_smi_fallback_still_reads():
    smi = {"gpus": [{"index": "0", "name": "NVIDIA GeForce RTX 4080 SUPER", "uuid": "GPU-1", "bus_id": "00000000:01:00.0", "utilization": 30.0,
                     "memory_used_mib": 2000.0, "memory_total_mib": 16376.0, "temperature": 45.0, "max_operating": 90.0, "slowdown": 96.0,
                     "shutdown": 101.0, "source": "nvidia-smi"}]}
    found = run(gpu=smi)[NAME]
    assert found.level == OK and found.message == "30 % · 2000 of 16376 MiB · 45 °C"
    assert dict(found.values)["reading"].startswith("nvidia-smi") and "nvidia-smi" in dict(found.values)["threshold_source"]


# ----- the probe: NVML first, nvidia-smi without the binding ------------------------------------------------------------------
def test_the_probe_reads_nvml_and_shuts_it_down_after_one_diagnose():
    nvml = FakeNvml()
    found = computer.LiveProbe(nvml_loader=lambda: nvml).gpus()
    assert found["gpus"][0]["source"] == "nvml" and (nvml.inits, nvml.shutdowns) == (1, 1)


def test_follow_initialises_nvml_once_and_shuts_it_down_on_close():
    nvml = FakeNvml()
    probe = computer.LiveProbe(follow=True, nvml_loader=lambda: nvml)
    for _ in range(3):
        assert probe.gpus()["gpus"][0]["temperature"] == 52
    assert (nvml.inits, nvml.shutdowns) == (1, 0)
    probe.close()
    assert nvml.shutdowns == 1


def test_without_the_binding_the_probe_falls_back_to_nvidia_smi(monkeypatch):
    calls = []

    def fake_run(argv):
        calls.append(argv)
        if "--query-gpu" in argv[1]:
            return SimpleNamespace(returncode=0, stdout="0, NVIDIA GeForce RTX 4080 SUPER, GPU-1, 00000000:01:00.0, 30, 2000, 16376, 45\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="GPU 00000000:01:00.0\n        GPU Slowdown Temp : 96 C\n", stderr="")

    monkeypatch.setattr(computer, "_run", fake_run)
    found = computer.LiveProbe(nvml_loader=lambda: None).gpus()
    assert found["gpus"][0]["source"] == "nvidia-smi" and found["gpus"][0]["slowdown"] == 96.0 and calls[0][0] == "nvidia-smi"


def test_no_nvidia_library_and_no_nvidia_smi_is_not_available(monkeypatch):
    monkeypatch.setattr(computer, "_run", lambda argv: None)
    monkeypatch.setattr(computer.shutil, "which", lambda name: None)
    found = computer.LiveProbe(nvml_loader=lambda: FakeNvml(init_error=FakeNvml.NVML_ERROR_LIBRARY_NOT_FOUND)).gpus()
    assert "missing" in found
    assert run(gpu=found)["GPU"].level == OK


def test_a_driver_that_is_not_loaded_reads_as_not_answering():
    found = computer.LiveProbe(nvml_loader=lambda: FakeNvml(init_error=FakeNvml.NVML_ERROR_DRIVER_NOT_LOADED)).gpus()
    assert "error" in found and "NVML" in found["error"]
    status = run(gpu=found)["GPU"]
    assert status.level == WARN and status.code == "GpuDriverNotAnswering"


def test_follow_asks_the_gpus_constants_once_per_nvml_session():
    """A GPU's thresholds, name, PCI id, maximum clocks and fan range never change while NVML is open: asked once per session
    (each NVML call is a driver ioctl, ~7 ms of CPU per tick for all of them on 2026-11-10), asked again after a restart."""

    asked = []

    class Counting(FakeNvml):
        def nvmlDeviceGetTemperatureThreshold(self, handle, kind):
            asked.append(kind)
            return super().nvmlDeviceGetTemperatureThreshold(handle, kind)

    nvml = Counting()
    probe = computer.LiveProbe(follow=True, nvml_loader=lambda: nvml)
    clock = [100.0]
    probe.monotonic = lambda: clock[0]
    first = probe.gpus()["gpus"][0]
    for _ in range(4):
        again = probe.gpus()["gpus"][0]
    assert len(asked) == 4 and (again["slowdown"], again["clock_sm_max_mhz"], again["fan_max_percent"]) == (96, 3105, 100)
    assert again["temperature"] == first["temperature"] == 52  # the readings themselves are read every time
    nvml.temperature = 60
    assert probe.gpus()["gpus"][0]["temperature"] == 60
    nvml.lost = True
    probe.gpus()
    nvml.lost = False
    clock[0] += computer.GPU_RESTART_S
    probe.gpus()
    assert len(asked) == 8  # a new session asks again: the driver may have changed under it


def test_one_diagnose_reads_everything_once():
    asked = []

    class Counting(FakeNvml):
        def nvmlDeviceGetTemperatureThreshold(self, handle, kind):
            asked.append(kind)
            return super().nvmlDeviceGetTemperatureThreshold(handle, kind)

    nvml = Counting()
    computer.LiveProbe(nvml_loader=lambda: nvml).gpus()
    computer.LiveProbe(nvml_loader=lambda: nvml).gpus()
    assert len(asked) == 8


def test_a_gpu_lost_while_following_is_an_error_and_nvml_starts_again_later():
    nvml = FakeNvml()
    probe = computer.LiveProbe(follow=True, nvml_loader=lambda: nvml)
    clock = [100.0]
    probe.monotonic = lambda: clock[0]
    assert "gpus" in probe.gpus()
    nvml.lost = True
    assert "error" in probe.gpus() and nvml.shutdowns == 1
    nvml.lost = False
    assert "error" in probe.gpus() and nvml.inits == 1  # not before GPU_RESTART_S
    clock[0] += computer.GPU_RESTART_S
    assert "gpus" in probe.gpus() and nvml.inits == 2


# ----- the real plugin, in its env, under strace --------------------------------------------------------------------------------
ALLOWED_DEV = ("/dev/null", "/dev/urandom", "/dev/random")


@pytest.mark.skipif(shutil.which("uv") is None or shutil.which("strace") is None or shutil.which("nvidia-smi") is None,
                    reason="needs uv, strace and an NVIDIA driver")
def test_the_real_plugin_opens_only_nvidias_own_nodes_below_dev(tmp_path):
    """The Python audit hook (computer_audit.py) cannot see NVML's opens: libnvidia-ml opens its nodes in C. strace sees every process
    of the run. WHY these are fine: /dev/nvidiactl, /dev/nvidia0, /dev/nvidia-uvm and /dev/nvidia-caps are the GPU's shared control
    nodes, the same ones nvidia-smi (what this plugin ran before) and every CUDA program open; no rack port, camera or CAN node."""

    trace = tmp_path / "trace"
    completed = subprocess.run(["strace", "-f", "-qq", "-e", "trace=open,openat", "-o", str(trace), "uv", "run", "--quiet", "--project",
                                str(PLUGIN_DIR), "python", "-m", "computer_diagnostics", "diagnose", "--device", "host"],
                               capture_output=True, text=True, timeout=180, cwd=PLUGIN_DIR,
                               env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"})
    assert completed.returncode == 0, completed.stderr[-600:]
    opened = sorted({line.split('"')[1] for line in trace.read_text().splitlines()
                     if '"/dev/' in line and "ENOENT" not in line})
    assert opened and all(path in ALLOWED_DEV or path.startswith("/dev/nvidia") for path in opened), opened
    assert any(path.startswith("/dev/nvidia") for path in opened)  # NVML read the GPU in-process
    lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    gpu = next(line for line in lines if line["name"].startswith("GPU 0: "))
    values = {pair["key"]: pair["value"] for pair in gpu.get("values", [])}
    assert values["reading"] == "NVML (nvidia-ml-py)" and "NVML" in values["threshold_source"]
    assert {pair["key"] for pair in gpu["measurements"]} >= {"fan 1", "power", "SM clock"}
    assert ROOT.exists()
