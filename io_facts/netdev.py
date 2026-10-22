"""Physical network interfaces from /sys/class/net, and EtherCAT masters detected without touching the bus.

Read-only. A NIC is physical when it has a ``device`` link (lo, docker0, veth*, bridges, tun, VLANs and
bonds live under /sys/devices/virtual and have none). Each NIC reports its link (state, carrier, speed,
duplex), driver, error counters and the PCI or USB device it sits on.

EtherCAT: a NIC is marked "EtherCAT segment (not scanned)" when a master is bound to it, read passively from
- the IgH master: ``/dev/EtherCAT<n>`` exists while ``ec_master`` is loaded; its NIC is the MAC in the module's
  ``main_devices`` / ``backup_devices`` parameters or MASTERn_DEVICE in /etc/ethercat.conf, or the NIC runs an
  IgH native driver (``ec_igb``, ``ec_generic`` ...);
- SOEM / pysoem and other raw-socket masters: a packet socket for EtherType 0x88A4 in /proc/net/packet, bound to
  the NIC's ifindex (the socket's owner pid is read from /proc/<pid>/fd when permissions allow);
- a running process named like a master (ethercat, soem, pysoem, etherlab, ecmaster) whose arguments name the NIC.
Nothing sends or receives a frame: the slaves on the segment stay unknown until a driver plugin reads them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .skeleton import ROOT, link_name, natural_key, node, physical_owner, read_int, read_text, resolved

SYS_NET = Path("sys/class/net")
SEGMENT_LABEL = "EtherCAT segment (not scanned)"
ETHERCAT_ETHERTYPE = 0x88A4
ARPHRD_ETHER, ARPHRD_CAN = 1, 280
ADDRESS_ASSIGN = {0: "permanent", 1: "random", 2: "stolen", 3: "set"}
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
MASTER_NAME = re.compile(r"ethercat|soem|etherlab|ec_?master", re.IGNORECASE)  # "pysoem" contains "soem"
INTERPRETER = re.compile(r"^(python[\d.]*|pypy[\d.]*|node|ruby|perl|java)$")
STATISTICS = ("rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "rx_crc_errors", "collisions")


# ----- the NICs -----------------------------------------------------------------------------------
def _speed(entry: Path) -> int | None:
    value = read_int(entry / "speed")
    return value if value and value > 0 else None  # -1 / EINVAL when the link is down or unknown


def _carrier(entry: Path) -> bool | None:
    value = read_int(entry / "carrier")
    return None if value is None else bool(value)  # EINVAL while the interface is administratively down


def _nic(entry: Path) -> dict | None:
    """Facts for one interface; None for a virtual one."""

    device_dir = resolved(entry / "device")
    if device_dir is None or "/devices/virtual/" in str(resolved(entry)):
        return None
    arphrd = read_int(entry / "type")
    wireless = (entry / "wireless").exists() or (entry / "phy80211").exists()
    kind = "wifi" if wireless else "ethernet" if arphrd == ARPHRD_ETHER else "can" if arphrd == ARPHRD_CAN else f"arphrd {arphrd}"
    statistics = {name: read_int(entry / "statistics" / name) for name in STATISTICS}
    return {
        "ifname": entry.name,
        "ifindex": read_int(entry / "ifindex"),
        "kind": kind,
        "mac": (read_text(entry / "address") or "").lower() or None,
        "mac_assign": ADDRESS_ASSIGN.get(read_int(entry / "addr_assign_type")),
        "driver": link_name(device_dir / "driver"),
        "operstate": read_text(entry / "operstate"),
        "carrier": _carrier(entry),
        "speed_mbps": _speed(entry),
        "duplex": read_text(entry / "duplex"),
        "mtu": read_int(entry / "mtu"),
        "carrier_changes": read_int(entry / "carrier_changes"),
        "statistics": {name: value for name, value in statistics.items() if value is not None},
        "owner": physical_owner(device_dir),
        "device_path": str(device_dir),
    }


def physical_nics(root: Path = ROOT) -> list[dict]:
    base = Path(root) / SYS_NET
    if not base.is_dir():
        return []
    nics = (_nic(entry) for entry in sorted(base.iterdir(), key=lambda path: natural_key(path.name)))
    return [nic for nic in nics if nic]


# ----- EtherCAT masters, passively ----------------------------------------------------------------
def _igh_master(root: Path) -> dict | None:
    """The IgH EtherCAT master, if loaded, with the MACs / interface names its config binds it to."""

    character_devices = sorted((root / "dev").glob("EtherCAT*")) if (root / "dev").is_dir() else []
    module = root / "sys/module/ec_master"
    if not character_devices and not module.exists():
        return None
    macs, ifnames = set(), set()
    for parameter in ("main_devices", "backup_devices"):
        for value in re.split(r"[,\s]+", read_text(module / "parameters" / parameter) or ""):
            if MAC.match(value):
                macs.add(value.lower())
    for config in ("etc/ethercat.conf", "etc/sysconfig/ethercat"):
        for line in (read_text(root / config) or "").splitlines():
            match = re.match(r'^\s*MASTER\d+_(?:DEVICE|BACKUP)\s*=\s*"?([^"#\s]+)"?', line)
            if match and MAC.match(match[1]):
                macs.add(match[1].lower())
            elif match:
                ifnames.add(match[1])
    macs.discard("ff:ff:ff:ff:ff:ff")  # IgH's "first device that shows up": names no NIC
    names = ", ".join(f"/dev/{path.name}" for path in character_devices) or "ec_master module loaded"
    return {"kind": "igh", "label": "IgH EtherCAT master", "pid": None, "process": None,
            "evidence": [f"IgH master present ({names})"], "macs": sorted(macs), "ifnames": sorted(ifnames),
            "ifindexes": [], "args": []}


def _ethercat_packet_sockets(root: Path) -> list[dict]:
    """Packet sockets for EtherType 0x88A4 (/proc/net/packet: sk RefCnt Type Proto Iface R Rmem User Inode)."""

    sockets = []
    for line in (read_text(root / "proc/net/packet") or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 9:
            continue
        try:
            proto, ifindex, uid, inode = int(fields[3], 16), int(fields[4]), int(fields[7]), int(fields[8])
        except ValueError:
            continue
        if proto == ETHERCAT_ETHERTYPE:
            sockets.append({"ifindex": ifindex, "uid": uid, "inode": inode})
    return sockets


def _process(root: Path, pid: int) -> tuple[str, list[str]]:
    entry = root / "proc" / str(pid)
    comm = (read_text(entry / "comm") or "").strip()
    return comm, [part for part in (read_text(entry / "cmdline") or "").split("\0") if part]


def _candidate_processes(root: Path) -> list[tuple[int, str, list[str]]]:
    """``(pid, comm, argv)`` for processes that could be a master: comm names one, or comm is an interpreter.

    WHY comm first: reading every cmdline doubled the scan (~800 processes on the rack PC). comm is the kernel's
    task name (the executable's first 15 characters), so a master is missed only if it renames its own thread.
    """

    found = []
    proc = root / "proc"
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        comm = (read_text(entry / "comm") or "").strip()
        if MASTER_NAME.search(comm) or INTERPRETER.match(comm):
            found.append((int(entry.name), *_process(root, int(entry.name))))
    return found


def _names(comm: str, argv: list[str]) -> list[str]:
    """What a process is "named": its comm, its executable, and for an interpreter its script or ``-m`` module.

    WHY not the whole command line: ``pytest -k ethercat`` or ``vim ethercat.conf`` are not masters.
    """

    names = [comm] if comm else []
    if not argv:
        return names
    executable = Path(argv[0]).name
    names.append(executable)
    if INTERPRETER.match(executable) or INTERPRETER.match(comm):
        arguments = iter(argv[1:])
        for argument in arguments:
            if argument == "-m":
                names.append(next(arguments, ""))
                break
            if not argument.startswith("-"):
                names.append(Path(argument).name)
                break
    return [name for name in names if name]


def _socket_owners(root: Path, inodes: set[int]) -> dict[int, int]:
    """``{socket inode: pid}`` from /proc/<pid>/fd links; processes of other users are skipped silently."""

    owners: dict[int, int] = {}
    if not inodes:
        return owners
    wanted = {f"socket:[{inode}]": inode for inode in inodes}
    for entry in (root / "proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            descriptors = list((entry / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target in wanted:
                owners[wanted[target]] = int(entry.name)
    return owners


def detect_masters(root: Path = ROOT) -> list[dict]:
    """Every EtherCAT master visible from /dev, /sys/module, /etc and /proc; not yet bound to NICs."""

    root = Path(root)
    masters = []
    if igh := _igh_master(root):
        masters.append(igh)
    by_pid: dict[int, dict] = {}
    for pid, comm, argv in sorted(_candidate_processes(root)):
        named = next((name for name in _names(comm, argv) if MASTER_NAME.search(name)), None)
        if named:
            by_pid[pid] = {"kind": "process", "label": f"EtherCAT master process {named}", "pid": pid, "process": named,
                           "evidence": [f"process {pid} is named {named}"], "macs": [], "ifnames": [], "ifindexes": [],
                           "args": [token for argument in argv[1:] for token in argument.split("=")]}
    sockets = _ethercat_packet_sockets(root)
    owners = _socket_owners(root, {socket["inode"] for socket in sockets})
    for socket in sockets:
        evidence = f"packet socket for EtherType 0x88A4 on ifindex {socket['ifindex']} (inode {socket['inode']})"
        pid = owners.get(socket["inode"])
        if pid in by_pid:
            by_pid[pid]["evidence"].append(evidence)
            by_pid[pid]["ifindexes"].append(socket["ifindex"])
            continue
        named = next(iter(_names(*_process(root, pid))), None) if pid else None
        master = {"kind": "raw_socket", "label": f"EtherCAT raw-socket master{f' {named}' if named else ''}", "pid": pid,
                  "process": named, "evidence": [evidence], "macs": [], "ifnames": [], "ifindexes": [socket["ifindex"]], "args": []}
        if pid is None:
            by_pid[-socket["inode"]] = master  # owner unreadable (another user's process): still a master
        else:
            by_pid[pid] = master
    masters.extend(by_pid[key] for key in sorted(by_pid, key=lambda key: (key < 0, abs(key))))
    return masters


def _igh_native_driver(nic: dict) -> bool:
    return bool(nic["driver"] and nic["driver"].startswith("ec_"))  # ec_igb, ec_e1000e, ec_r8169, ec_generic ...


def binding(master: dict, nic: dict) -> str | None:
    """Why ``master`` drives ``nic``, or None when nothing ties them together."""

    if nic["mac"] and nic["mac"] in master["macs"]:
        return f"MAC {nic['mac']} is the master's configured device"
    if nic["ifname"] in master["ifnames"]:
        return f"{nic['ifname']} is named in the master's config"
    if nic["ifindex"] in master["ifindexes"]:
        return f"its EtherType 0x88A4 socket is bound to {nic['ifname']} (ifindex {nic['ifindex']})"
    if nic["ifname"] in master["args"]:
        return f"its command line names {nic['ifname']}"
    if master["kind"] == "igh" and _igh_native_driver(nic):
        return f"{nic['ifname']} runs the IgH device driver {nic['driver']}"
    return None


def _public(master: dict, nics: list[str], reasons: list[str]) -> dict:
    return {"kind": master["kind"], "label": master["label"], "pid": master["pid"], "process": master["process"],
            "evidence": [*master["evidence"], *reasons], "nics": nics, "scanned": False}


def ethercat_masters(root: Path = ROOT, *, detected: list[dict] | None = None) -> list[dict]:
    """Each master with the NICs it is bound to (``nics: []`` = running, but its NIC is unknown).

    ``detected``: a ``detect_masters`` result to reuse, so one skeleton pass scans /proc once.
    """

    nics = physical_nics(root)
    masters = []
    for master in detect_masters(root) if detected is None else detected:
        bound = [(nic["ifname"], why) for nic in nics if (why := binding(master, nic))]
        masters.append(_public(master, [ifname for ifname, _ in bound], [why for _, why in bound]))
    return masters


def _ethercat(nic: dict, masters: list[dict]) -> dict | None:
    bound = [_public(master, [nic["ifname"]], [why]) for master in masters if (why := binding(master, nic))]
    if _igh_native_driver(nic) and not any(master["kind"] == "igh" for master in bound):
        bound.append({"kind": "igh_driver", "label": "IgH native EtherCAT driver", "pid": None, "process": None,
                      "evidence": [f"driver {nic['driver']} is an IgH EtherCAT device driver"], "nics": [nic["ifname"]],
                      "scanned": False})
    if not bound:
        return None
    return {"segment": True, "label": SEGMENT_LABEL, "scanned": False, "masters": bound}


def devices(root: Path = ROOT, *, detected: list[dict] | None = None) -> list[dict]:
    """Physical NICs as skeleton dicts; parent = the PCI function or USB device the NIC sits on."""

    masters = detect_masters(root) if detected is None else detected
    nodes = []
    for nic in physical_nics(root):
        ethercat = _ethercat(nic, masters)
        owner = nic["owner"]
        attrs = {
            "kind": nic["kind"],
            "ifname": nic["ifname"],
            "ifindex": nic["ifindex"],
            "dev": None,  # a NIC has no /dev node
            "mac": nic["mac"],
            "mac_assign": nic["mac_assign"],
            "driver": nic["driver"],
            "operstate": nic["operstate"],
            "carrier": nic["carrier"],
            "speed_mbps": nic["speed_mbps"],
            "duplex": nic["duplex"],
            "mtu": nic["mtu"],
            "carrier_changes": nic["carrier_changes"],
            "statistics": nic["statistics"],
            "device_bus": owner.bus if owner else None,
            "device": owner.name if owner else None,
            "interface": owner.interface if owner else None,
            "ethercat": ethercat,
            "hardware_id": f"net:{nic['mac']}" if nic["mac"] and nic["mac_assign"] == "permanent" else None,
            "sysfs": nic["device_path"],
            "source": "io_facts.netdev (sysfs + /proc)",
        }
        label = f"{nic['ifname']} · {SEGMENT_LABEL}" if ethercat else nic["ifname"]
        nodes.append(node("net", f"net:{nic['ifname']}", nic["ifname"], label, parent=owner.id if owner else None, attrs=attrs))
    return nodes
