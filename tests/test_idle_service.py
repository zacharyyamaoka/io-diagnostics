"""A service nobody is looking at idles: the tray alone costs next to nothing, and a page that opens finds everything fresh.

What was measured on Stable's tray on 2026-11-13 (pid 4397, 37 h up, no window open): ~20 % of a core, ~1 core under the agents' load,
in two threads. ``io-view`` rebuilt the whole view ~3 times a second at its rate cap: the host's revision rose 14.9 a second (the
Computer follower's ~14.5 lines a second, one ``notify`` each), and the tray's own 2 s chip read and the stock diagnostics' 5 s beat each
counted as someone looking, so the host never went quiet. ``io-refresh`` re-read the whole OS skeleton every 2 s, 43 % of it realpath
calls for USB video nodes the skeleton then threw away; and the fault events the host kept in RAM, re-read by every build, only grew.

Pinned here: the tray's read and the beat keep no one looking; a quiet host refreshes on a slow timer, yet at once on a change or on the
first look; the fault events in memory are bounded and the log still has the old ones; the skeleton reads no USB node it discards.
Fixture hosts and fakes only: no real hardware, no port of Stable or Preview.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from test_host_support import ROOT
from test_stock_diagnostics import Rig

from contract.record import DiagnosticStatus
from io_diagnostics import cli
from io_diagnostics import faults as faults_module
from io_diagnostics import host as host_module
from io_diagnostics.host import Host, make_host
from io_diagnostics.server import make_server, serve_in_thread
from io_facts import skeleton, usb

FIXTURE_MACHINE = ROOT / "tests" / "data" / "sysfs-rack-fixture.json"
FOLLOWER_HZ = 15  # the Computer follower's lines a second on Stable (1,948,720 in 134,548 s): one notify each
FAULT_EVENTS_KEPT = getattr(faults_module, "MAX_EVENTS", 500)  # WHY getattr: on the host before the bound this test fails on the count, not on a name


def follower_notifies(host: Host, stop: threading.Event) -> threading.Thread:
    """What the Computer follower does to the host: a change every line, ~15 a second."""

    def run():
        while not stop.wait(1 / FOLLOWER_HZ):
            host.notify("plugin")

    thread = threading.Thread(target=run, name="test-follower", daemon=True)
    thread.start()
    return thread


def count_calls(monkeypatch, name: str) -> list[tuple[str, float]]:
    """Every ``Host.<name>`` call as (thread name, monotonic time)."""

    calls: list[tuple[str, float]] = []
    real = getattr(Host, name)

    def counted(self, *args, **kwargs):
        calls.append((threading.current_thread().name, time.monotonic()))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Host, name, counted)
    return calls


def quiet(host: Host) -> bool:
    return time.monotonic() - host._read_at > host_module.VIEW_QUIET_S


# ----- the tray and the beat are timers, not people --------------------------------------------------------------------------------
def test_the_trays_chip_read_keeps_no_one_looking(tmp_path, monkeypatch):
    """``cli.py tray`` as Stable runs it (a fixture host here), with the GTK loop replaced by a reader of the same model: the follower's
    changes come 15 a second, the chip is read again and again, and the view is still built only on the quiet timer."""

    monkeypatch.setenv("IO_DIAGNOSTICS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(os, "_exit", lambda code: None)  # WHY: _serve arms an os._exit watchdog for a stuck stop; this test process must outlive it
    made: dict = {}
    real_make_host = host_module.make_host
    monkeypatch.setattr(host_module, "make_host", lambda **options: made.setdefault("host", real_make_host(**options)))
    builds = count_calls(monkeypatch, "view")
    seen: dict = {}

    def fake_tray(model_source, **_options):
        host = made["host"]
        stop = threading.Event()
        follower_notifies(host, stop)
        time.sleep(1.0)  # the start's own builds are behind us
        began, chips = time.monotonic(), []
        while time.monotonic() - began < 5.0:
            chips.append(model_source())
            time.sleep(0.2)  # the tray reads every 2 s; ten times as often here, which can only look more like someone looking
        stop.set()
        seen.update(chips=chips, began=began, ended=time.monotonic(), quiet=quiet(host))

    import io_diagnostics.tray as tray_module

    monkeypatch.setattr(tray_module, "run_tray", fake_tray)
    args = cli._parser().parse_args(["tray", "--fixture", "F1", "--port", "0", "--no-udev"])
    assert cli._serve(args, tray=True) == 0
    assert seen["chips"] and all("menu" in chip and "status" in chip for chip in seen["chips"]), "the tray got its model every time"
    assert seen["quiet"], "the tray's chip read counted as someone looking: the host never goes quiet"
    window = [at for thread, at in builds if thread == "io-view" and seen["began"] <= at <= seen["ended"]]
    # quiet: one build per VIEW_MAX_AGE_S (2 s) whatever changes, so 2-3 in 5 s; looking, the follower's changes set off one every ~0.3 s
    assert len(window) <= 4, f"{len(window)} builds in 5 s with only the tray reading: it rebuilt on the follower's changes"


def test_the_stock_diagnostics_beat_keeps_no_one_looking(tmp_path):
    """The beat reads the view every ``beat_s`` (5 s on Stable, under ``VIEW_QUIET_S``), so it alone kept every service, Preview and the
    agents' lanes included, looking forever."""

    rig = Rig(tmp_path, beat_s=0.2)
    beats = []
    real = rig.host.run_stock_diagnostics
    rig.host.run_stock_diagnostics = lambda: beats.append(time.monotonic()) or real()
    rig.host.prepare_service()
    rig.host.start(udev=False)
    try:
        deadline = time.monotonic() + 15.0
        while len(beats) < 5 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(beats) >= 5, "the beat ran (else this test proves nothing)"
        assert rig.host.stock_results or rig.host.stock_quiet, "the beat read the view and judged the board"
        assert quiet(rig.host), "the stock diagnostics' beat counted as someone looking"
    finally:
        rig.host.stop()


# ----- a quiet host refreshes on a slow timer, and at once on a change or a look ------------------------------------------------------
@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path / "config"))
    host = make_host(fixture="F1")
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host
    http.shutdown()
    http.server_close()
    host.stop()


def test_a_quiet_host_refreshes_on_its_slow_timer_and_at_once_on_a_change_or_a_look(service, monkeypatch):
    monkeypatch.setattr(host_module, "REFRESH_S", 0.2)
    monkeypatch.setattr(host_module, "REFRESH_QUIET_S", 30.0, raising=False)  # raising=False: the host before this fix had no such name
    refreshes = count_calls(monkeypatch, "refresh")
    service.prepare_service()
    service.start(udev=False)

    def timer_refreshes(began: float, ended: float) -> int:
        return sum(1 for thread, at in refreshes if thread == "io-refresh" and began <= at <= ended)

    def refreshed_after(at: float, within_s: float) -> bool:
        deadline = time.monotonic() + within_s
        while time.monotonic() < deadline:
            if timer_refreshes(at, time.monotonic()):
                return True
            time.sleep(0.02)
        return False

    time.sleep(1.0)  # the start's refreshes are behind us
    began = time.monotonic()
    time.sleep(2.0)
    assert quiet(service)
    assert timer_refreshes(began, time.monotonic()) <= 1, "nobody is looking, yet the skeleton was read on the looking cadence"

    woken = time.monotonic()
    service.wake()  # what a udev event or a kernel USB line does
    assert refreshed_after(woken, 1.0), "a change refreshes at once even when nobody is looking"

    looked = time.monotonic()
    with urllib.request.urlopen(service.url.rstrip("/") + "/api/v1/state", timeout=30) as response:
        json.load(response)
    assert not quiet(service), "a page's read is someone looking"
    assert refreshed_after(looked, 1.0), "the first look after a quiet spell refreshes at once: the page finds the skeleton fresh"
    resumed = time.monotonic()
    time.sleep(1.2)
    assert timer_refreshes(resumed, time.monotonic()) >= 3, "while someone looks, the looking cadence (REFRESH_S) is back"


# ----- what a build re-reads stays bounded ------------------------------------------------------------------------------------------
def failing(number: int, at: float) -> DiagnosticStatus:
    return DiagnosticStatus("error", f"check {number}", "bad", path="host", code=f"Bad{number}", stamp=at, received=at)


def test_the_fault_events_kept_in_memory_are_bounded():
    """Every build re-reads them (``_build_view`` adds them to the events it logs and overlays); Stable's had 912 after 37 h, and only grew."""

    faults = faults_module.Faults()
    limit = FAULT_EVENTS_KEPT
    for number in range(limit + 50):
        faults.observe([failing(number, 1000.0 + number)], 1000.0 + number)
    assert len(faults.events) == limit
    assert faults.events[-1].args["fault"] == f"Bad{limit + 49}" and faults.events[0].args["fault"] == "Bad50", "the newest are kept"


def test_a_fault_event_that_left_memory_is_still_in_the_log(service):
    """The bound loses nothing: a view logs each fault event as it comes, and the log gives the old ones back."""

    service.refresh()
    now = service.clock.now()
    service.faults.observe([failing(0, now)], now)
    first = service.faults.events[0]
    service.view()  # logged here
    for number in range(1, FAULT_EVENTS_KEPT + 10):
        service.faults.observe([failing(number, now + number * 0.001)], now + number * 0.001)
    assert first not in service.faults.events
    ids = {event.args.get("id") for event in service.view().events}
    assert first.args["id"] in ids, "a fault event that left the host's memory is gone from the activity log"


# ----- the skeleton reads only what it keeps ----------------------------------------------------------------------------------------
def test_the_skeleton_reads_no_usb_node_it_throws_away(tmp_path, monkeypatch):
    """``skeleton.usb_devices`` never used ``io_facts.usb``'s ttys / by-id / video nodes (the tty and v4l sources own those), yet every
    refresh paid for them: two realpath calls per USB device per video node, 43 % of Stable's refresh."""

    machine = skeleton.fake_root(FIXTURE_MACHINE, tmp_path / "machine")
    before = skeleton.skeleton(machine)

    def thrown_away(*_args, **_kwargs):
        raise AssertionError("the skeleton looked up USB nodes it does not keep")

    monkeypatch.setattr(usb, "_video_nodes_under", thrown_away)
    monkeypatch.setattr(usb, "_by_id_links", thrown_away)
    assert skeleton.skeleton(machine) == before, "the skeleton is the same without them"


def test_the_usb_listing_still_finds_nodes_for_the_callers_that_use_them(tmp_path, monkeypatch):
    """snapshot.py, demo.py and the no-skeleton fallback read ``ttys`` / ``by_id`` / ``video_nodes``: the default still looks them up."""

    machine = skeleton.fake_root(FIXTURE_MACHINE, tmp_path / "machine")
    looked: list[Path] = []
    monkeypatch.setattr(usb, "_video_nodes_under", lambda device_dir, *rest: looked.append(device_dir) or [])
    devices = usb.list_usb_devices(machine / "sys/bus/usb/devices")
    assert devices and {device.port for device in devices} == {path.name for path in looked}
