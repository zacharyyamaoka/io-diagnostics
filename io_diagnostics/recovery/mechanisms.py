"""Run one rung's reset, bounded by its timeout: ``Outcome`` says what happened to the device, never whether it is cured
(the ladder probes for that after the device settles).

Each mechanism reuses what already exists rather than a copy of it: usbreset through io_diagnostics.usb_reset's own method
choice, the fdcanusb by Sim to Real's fdcanusb-reset (bam_rack_twin.hardware.fdcanusb_reset), a camera's or a board's own
restart through its driver plugin's act (its dry-run token, its own holder check and lock).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from io_diagnostics import usb_reset
from io_diagnostics.recovery import device as devices
from io_diagnostics.recovery.availability import plugin_declared
from io_diagnostics.recovery.rung import AUTHORIZED, NATIVE, POWER_CYCLE, USBRESET, VIA_COMMAND, VIA_PLUGIN, Rung
from io_diagnostics.recovery.system import Seams, fill, tool_paths
from io_diagnostics.usb_reset import sysfs_root

AUTHORIZED_GAP_S = 0.5  # between the two writes: the kernel finishes the disconnect first (usb_reset.AUTHORIZED_GAP_S)
POWER_OFF_S = 2.0  # VBUS off this long: a microcontroller's supply rail decays below brown-out well within it
DONE, FAILED, TIMEOUT, REFUSED = "done", "failed", "timeout", "refused"
REFUSAL_CODES = ("ActionRefused", "ActionBlocked", "ConfirmationRequired", "ConfirmRequired")


@dataclass
class Outcome:
    state: str  # done | failed | timeout | refused
    words: str  # plain words for the ledger and the run's terminal
    command: list[str] | str = ""
    exit_code: int | None = None
    output: str = ""
    details: dict = field(default_factory=dict)


def run_rung(rung: Rung, device: devices.Device, seams: Seams, *, plugins_dir: Path | None = None,
             skeleton: dict | None = None) -> Outcome:
    if rung.kind == NATIVE and rung.via == VIA_COMMAND:
        return _command(rung, device, seams)
    if rung.kind == NATIVE and rung.via == VIA_PLUGIN:
        return _plugin(rung, device, seams, plugins_dir, skeleton)
    if rung.kind == USBRESET:
        return _usbreset(rung, device, seams)
    if rung.kind == AUTHORIZED:
        return _authorized(rung, device, seams)
    if rung.kind == POWER_CYCLE:
        return _power_cycle(rung, device, seams)
    return Outcome(FAILED, f"{rung.kind} has no mechanism")


def _command(rung: Rung, device: devices.Device, seams: Seams) -> Outcome:
    values = {**tool_paths(), "serial": device.serial, "port": device.port, "wait_s": f"{rung.settle_s:g}",
              "node": "" if device.node is None else device.node}
    argv = [fill(word, values) for word in rung.command]
    code, output = seams.run(argv, rung.timeout_s)
    meaning = dict(rung.exit_codes).get(code, "") if code is not None else ""
    last = output.strip().splitlines()[-1] if output.strip() else ""
    if code is None:
        return Outcome(TIMEOUT, f"{Path(argv[0]).name} ran over {rung.timeout_s:g} s and was stopped", argv, None, output)
    if meaning == "back" or (not rung.exit_codes and code == 0):
        return Outcome(DONE, last or f"{Path(argv[0]).name} reset it", argv, code, output)
    if meaning == "no_permission":
        return Outcome(REFUSED, last or "no permission", argv, code, output, {"needs": "the one-time installer"})
    return Outcome(FAILED, last or f"{Path(argv[0]).name} exited {code}", argv, code, output, {"meaning": meaning})


def _usbreset(rung: Rung, device: devices.Device, seams: Seams) -> Outcome:
    target = device.target()
    method = usb_reset.methods(target)[0]
    if not method.usable:
        return Outcome(REFUSED, method.why_not, method.command)
    argv = list(method.command)  # [usbreset, "BBB/DDD"]: by bus/device number, never by vendor:product
    code, output = seams.run(argv, rung.timeout_s)
    if code is None:
        return Outcome(TIMEOUT, f"usbreset ran over {rung.timeout_s:g} s and was stopped", argv, None, output)
    if code != 0:
        return Outcome(FAILED, f"usbreset exited {code}: {output.strip()[-200:]}", argv, code, output)
    return Outcome(DONE, f"usbreset {target.bus_dev} sent", argv, code, output)


def _in_thread(work, timeout_s: float) -> tuple[bool, BaseException | None]:
    """``(finished, its error)``. WHY a thread and not a child process: the second write (authorized 1, disable 0) sits in a
    ``finally``; a killed child would skip it and leave the device off. A thread that hangs in the kernel is left to finish
    and the rung reports it as pending, and no further rung runs on that device."""

    errors: list[BaseException] = []

    def body() -> None:
        try:
            work()
        except BaseException as error:  # noqa: BLE001 - reported, not swallowed
            errors.append(error)

    worker = threading.Thread(target=body, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return (not worker.is_alive()), (errors[0] if errors else None)


def _authorized(rung: Rung, device: devices.Device, seams: Seams) -> Outcome:
    path = sysfs_root() / device.port / "authorized"
    command = f"echo 0 > {path} && echo 1 > {path}"

    def cycle() -> None:
        seams.write_attr(path, "0")
        try:
            seams.sleep(AUTHORIZED_GAP_S)
        finally:
            # WHY in a finally: an error or a Stop between the two writes must never leave the device deauthorized
            seams.write_attr(path, "1")

    finished, error = _in_thread(cycle, rung.timeout_s)
    if not finished:
        return Outcome(TIMEOUT, f"the kernel did not finish the authorized switch within {rung.timeout_s:g} s (still pending)",
                       command, details={"pending": True})
    if error is not None:
        return Outcome(REFUSED if isinstance(error, PermissionError) else FAILED, f"authorized switch failed: {error}", command)
    return Outcome(DONE, "deauthorized and reauthorized", command)


def _power_cycle(rung: Rung, device: devices.Device, seams: Seams) -> Outcome:
    here = devices.port_dir(device.port)
    if here is None:
        return Outcome(FAILED, f"no port directory for {device.port}")
    switches = [here / "disable"] + ([here / "peer" / "disable"] if (here / "peer" / "disable").exists() else [])
    command = " && ".join([f"echo 1 > {path}" for path in switches] + [f"sleep {POWER_OFF_S:g}"]
                          + [f"echo 0 > {path}" for path in switches])

    def cycle() -> None:
        off = []
        try:
            for path in switches:
                seams.write_attr(path, "1")
                off.append(path)
            seams.sleep(POWER_OFF_S)
        finally:
            # WHY every switch back on in a finally, the USB 3 peer too: a port left disabled is a robot left blind
            for path in off:
                seams.write_attr(path, "0")

    finished, error = _in_thread(cycle, rung.timeout_s)
    if not finished:
        return Outcome(TIMEOUT, f"the port switch did not finish within {rung.timeout_s:g} s (still pending)", command,
                       details={"pending": True})
    if error is not None:
        return Outcome(REFUSED if isinstance(error, PermissionError) else FAILED, f"port power cycle failed: {error}", command)
    return Outcome(DONE, f"port {device.port} power off {POWER_OFF_S:g} s and back on"
                         + (" (with its USB 3 peer)" if len(switches) > 1 else ""), command)


# ----- a driver's own restart, through its plugin -----------------------------------------------------------------
def skeleton_of(device: devices.Device) -> dict:
    """What the host would pass a driver in IO_DIAGNOSTICS_DEVICE, built from sysfs (the host's own dict wins when there is one)."""

    hardware_id = f"usb:{device.vid}:{device.pid}:{device.serial}" if device.serial else f"usb:{device.vid}:{device.pid}@{device.port}"
    return {"bus": "usb", "id": f"usb:{device.port}", "label": device.product or device.name, "product": device.product or None,
            "serial": device.serial or None,
            "attrs": {"port": device.port, "vid": device.vid, "pid": device.pid, "usb_id": f"{device.vid}:{device.pid}",
                      "hardware_id": hardware_id, "by_id": devices.by_id_links(device.serial)}}


def _records(output: str) -> list[dict]:
    out = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                out.append(value)
    return out


def plugin_act(plugin: str, action: str, device: devices.Device, seams: Seams, timeout_s: float, *,
               plugins_dir: Path | None = None, skeleton: dict | None = None, confirm: str | None = None,
               dry_run: bool = False) -> tuple[int | None, list[dict], list[str], str]:
    """``(exit code, records, argv, raw output)`` of ``<plugin> act --device usb:<port> --action A [--node N] ...``."""

    spec = plugin_declared(plugin, plugins_dir)
    if spec is None:
        return 127, [], [], f"no enabled {plugin} plugin"
    from io_diagnostics.runner import act_argv, plugin_env

    dict_ = skeleton or skeleton_of(device)
    argv = act_argv(spec, dict_, action, node=device.node, confirm=confirm, dry_run=dry_run)
    env = plugin_env(spec, dict_, "", seams.now())
    env.update(seams.env)
    code, output = seams.run(argv, timeout_s, env)
    return code, _records(output), argv, output


def _token(records: list[dict]) -> str:
    for record in records:
        if record.get("record") == "action-result":
            return str(record.get("token") or record.get("confirm_token") or "")
    for record in records:
        if record.get("code") == "ConfirmationRequired":
            return str((record.get("args") or {}).get("token") or record.get("token") or "")
    return ""


def _refusal(records: list[dict]) -> dict | None:
    for record in records:
        if record.get("record") == "event" and record.get("code") in REFUSAL_CODES:
            return record
        if record.get("code") in REFUSAL_CODES:
            return record
    return None


def _plugin(rung: Rung, device: devices.Device, seams: Seams, plugins_dir: Path | None, skeleton: dict | None) -> Outcome:
    code, records, argv, output = plugin_act(rung.plugin, rung.action, device, seams, rung.timeout_s, plugins_dir=plugins_dir,
                                             skeleton=skeleton, dry_run=True)
    if code is None:
        return Outcome(TIMEOUT, f"the {rung.plugin} driver's dry run ran over {rung.timeout_s:g} s", argv, None, output)
    token = _token(records)
    refused = _refusal([r for r in records if r.get("code") != "ConfirmationRequired"])
    if refused is not None or not token:
        why = (refused or {}).get("message") or f"the {rung.plugin} driver gave no confirmation token (exit {code})"
        reason = ((refused or {}).get("args") or {}).get("reason") or (refused or {}).get("code") or "NoToken"
        return Outcome(REFUSED, f"the {rung.plugin} driver refused: {why}", argv, code, output, {"reason": reason})
    code, records, argv, output = plugin_act(rung.plugin, rung.action, device, seams, rung.timeout_s, plugins_dir=plugins_dir,
                                             skeleton=skeleton, confirm=token)
    if code is None:
        return Outcome(TIMEOUT, f"the {rung.plugin} driver's {rung.action} ran over {rung.timeout_s:g} s and was stopped", argv,
                       None, output)
    refused = _refusal(records)
    if refused is not None:
        reason = (refused.get("args") or {}).get("reason") or refused.get("code")
        return Outcome(REFUSED, f"the {rung.plugin} driver refused: {refused.get('message')}", argv, code, output,
                       {"reason": reason})
    failed = next((r for r in records if r.get("code") in ("ActionFailed", "ActionInterrupted")), None)
    if failed is not None or code not in (0,):
        return Outcome(FAILED, (failed or {}).get("message") or f"the {rung.plugin} driver exited {code}", argv, code, output)
    said = next((r.get("message") for r in records if r.get("code") == "ActionCompleted" or
                 (r.get("record") == "action-result" and not r.get("dry_run"))), "") or f"{rung.action} done"
    return Outcome(DONE, f"the {rung.plugin} driver: {said}", argv, code, output)


def plugin_check(device: devices.Device, seams: Seams, plugins_dir: Path | None, timeout_s: float,
                 skeleton: dict | None = None, plugin: str = "moteus_servo") -> tuple[bool | None, str]:
    """A CAN board's probe: the driver's Check communication (query only) on its adapter; it answers when its id is in the
    driver's ``Probed`` event's ``answering`` list."""

    adapter = devices.Device(key=f"serial:{device.serial}", port=device.port, vid=device.vid, pid=device.pid,
                             serial=device.serial, product=device.product, present=device.present)
    code, records, _argv, output = plugin_act(plugin, "check_comms", adapter, seams, timeout_s, plugins_dir=plugins_dir,
                                              skeleton=skeleton)
    if code is None:
        return False, f"Check communication ran over {timeout_s:g} s"
    refused = _refusal(records)
    if refused is not None:
        return None, f"could not ask: {refused.get('message')}"
    probed = next((r for r in records if r.get("code") == "Probed"), None)
    if probed is None:
        return None, f"the {plugin} driver said nothing about who answers (exit {code})"
    answering = [int(each) for each in ((probed.get("args") or {}).get("answering") or []) if str(each).isdigit()]
    if device.node in answering:
        return True, f"CAN {device.node} answers ({len(answering)} boards answer on this bus)"
    return False, f"CAN {device.node} does not answer ({', '.join(map(str, answering)) or 'no board'} answers)"
