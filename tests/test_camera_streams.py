"""A camera's State tab names the streams its driver says it is not streaming (the RealSense's ``watch streams`` status), and
the pause reason names a program the way a person does. Pure functions over a cache: no host, no camera."""

from __future__ import annotations

from contract.record import DiagnosticStatus, Frame, KeyValue
from io_diagnostics import entity_state
from io_diagnostics.runner import JobCache, RunOutcome

DESCRIBE = {"streams": [{"path": "", "streams": [{"name": "color", "title": "Color"}, {"name": "depth", "title": "Depth"},
                                                  {"name": "ir", "title": "Infrared"}]}]}
WHY = "the camera on a USB 2 link (480 Mbit/s) accepts no stream set with depth or infrared (RuntimeError: Couldn't resolve requests)"


def outcome(*records) -> RunOutcome:
    return RunOutcome(plugin="p", device_id="d", device_path="host/d", started=0.0, duration_s=0.0, outcome="ok", returncode=None,
                      records=list(records), received={}, icons={}, rejected=[], problems=[], stderr_tail="", message="")


def streams_status(*, streaming="color", not_streaming="depth,ir", why=WHY, path="", name="RealSense D405 1: watch streams") -> DiagnosticStatus:
    values = [KeyValue("streaming", streaming)]
    values += [KeyValue("not_streaming", not_streaming)] if not_streaming else []
    values += [KeyValue("why_not", why)] if why else []
    return DiagnosticStatus(level="ok", name=name, message="streaming colour", path=path, values=tuple(values), stamp=1.0)


def rows(cache: JobCache, now: float = 20.0) -> dict[str, dict]:
    return {row["name"]: row for row in entity_state.stream_rows(DESCRIBE, cache, "", now, lambda rel, name: None)}


def test_a_stream_the_driver_says_it_does_not_stream_carries_the_reason():
    cache = JobCache()
    cache.apply(outcome(streams_status()), received=10.0)
    found = rows(cache)
    assert found["color"]["off"] is None
    assert found["depth"]["off"] == WHY and found["ir"]["off"] == WHY


def test_without_a_reason_the_row_says_the_plain_fallback():
    cache = JobCache()
    cache.apply(outcome(streams_status(not_streaming="ir", why="")), received=10.0)
    found = rows(cache)
    assert found["ir"]["off"] == "not streamed by this camera on this link"
    assert found["color"]["off"] is None and found["depth"]["off"] is None


def test_no_status_means_nothing_is_off():
    assert {row["off"] for row in rows(JobCache()).values()} == {None}


def test_a_frame_that_arrived_after_the_status_wins():
    cache = JobCache()
    cache.apply(outcome(streams_status()), received=10.0)
    cache.apply(outcome(Frame(path="", stream="depth", file="depth.jpg", width=8, height=6, seq=1)), received=11.0)
    found = rows(cache)
    assert found["depth"]["off"] is None, "a picture arrived: it is streaming"
    assert found["ir"]["off"] == WHY
    cache.apply(outcome(streams_status()), received=12.0)  # the watch started again without depth: the old picture is not current
    assert rows(cache)["depth"]["off"] == WHY


def test_the_newest_status_and_only_this_entitys_count():
    cache = JobCache()
    cache.apply(outcome(streams_status(not_streaming="", why="", streaming="color,depth,ir", name="X: watch streams")), received=9.0)
    cache.apply(outcome(streams_status()), received=10.0)
    cache.apply(outcome(streams_status(path="other/entity", name="Y: watch streams")), received=11.0)
    cache.apply(outcome(streams_status(not_streaming="depth", name="a check that is not it")), received=12.0)
    found = rows(cache)
    assert found["color"]["off"] is None and found["depth"]["off"] == WHY, "the newest `: watch streams` status at this path"
    cache.apply(outcome(streams_status(not_streaming="", why="", streaming="color,depth,ir", name="X: watch streams")), received=13.0)
    assert {row["off"] for row in rows(cache).values()} == {None}, "the newest says everything streams"


def test_a_holder_command_that_is_an_interpreter_and_a_path_is_named_as_a_person_does():
    def reason(command: str) -> str:
        return entity_state.holder_reason("bus_lock_free", f"blocked by pid 1630327 ({command})")["reason"]

    assert reason("/home/bam/bam_ws-worktrees/fivebar-guard/src/dev/bam_rack_twin/.venv/bin/python3 -m bam_rack_twin.hardware.fivebar_run"
                  ) == "fivebar_run has the rack bus lock (pid 1630327)"
    assert reason("python3 Settings/Skills/x/scripts/update_moteus_firmware.py --port /dev/ttyACM0"
                  ) == "update_moteus_firmware.py has the rack bus lock (pid 1630327)"
    assert reason("/home/bam/.local/bin/tview") == "tview has the rack bus lock (pid 1630327)"
    assert reason("tview") == "tview has the rack bus lock (pid 1630327)"
    assert reason("python3 -u -m pkg.mod.fivebar_run --x") == "fivebar_run has the rack bus lock (pid 1630327)"
    holder = entity_state.holder_reason("bus_lock_free", "blocked by pid 9 (python3 -m pkg.fivebar_run)")["holders"]
    assert holder == [{"pid": 9, "program": "fivebar_run"}]
