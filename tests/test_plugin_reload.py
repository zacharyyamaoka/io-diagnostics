"""A driver updated in place is asked ``describe`` again (Zach, 2026-10-24: Preview still showed the old moteus and RealSense describe
after the new drivers landed). Fakes only: a stdlib script plugin whose declaration is written into its own source, a fake OS, a
log of every verb it was run with."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from io_diagnostics.host import Clock, Host
from io_diagnostics.plugins import load_plugin, source_stamp
from test_host_support import FakeOs, standard_skeleton, usb_device
from test_state_config import alive, wait_for

SCRIPT = '''\
import json, os, sys, time
verb = sys.argv[1]
with open(os.environ["RELOAD_LOG"], "a") as handle:
    handle.write(json.dumps({{"verb": verb, "pid": os.getpid()}}) + "\\n")
DECLARATION = {declaration!r}
if verb == "describe":
    if DECLARATION is None:
        sys.exit(3)
    print(json.dumps(DECLARATION))
elif verb == "diagnose":
    pass
elif verb == "watch":
    while True:
        print(flush=True)
        time.sleep(0.1)
'''


def declaration(*actions: str, period_s: float = 0.5, matches=("usb:0483:5740",), probe=None) -> dict:
    answer = {"schema": "diagnostics-record/0", "name": "reloadable", "record_version": "0.2", "matches": list(matches),
              "actions": [{"name": name, "title": name.title(), "description": name, "danger": "query", "scope": "device", "confirm": "",
                           "kind": "touches"} for name in actions],
              "watch": {"period_s": period_s, "gates": [], "danger": "query"}}
    if probe is not None:
        answer["probe"] = probe
    return answer


class Reloadable:
    """One host over a fake OS with the script plugin; ``rewrite`` is an update in place (new source, new modification time)."""

    def __init__(self, tmp_path: Path, first: dict | None, *, with_source: bool = True, **host_options):
        self.directory = tmp_path / "plugins.d"
        self.directory.mkdir()
        self.script = tmp_path / "reload_plugin.py"
        self.log = tmp_path / "verbs.log"
        self.generation = 0
        self.rewrite(first)
        (self.directory / "reloadable.toml").write_text(
            f'name = "reloadable"\ncommand = ["{{python}}", "{self.script if with_source else "-c"}"'
            + ('' if with_source else ', "pass"') + f']\ntimeout_s = 10\nperiod_s = 300\ncwd = "{tmp_path / "nowhere" if not with_source else tmp_path}"\n'
            f'env = {{ RELOAD_LOG = {json.dumps(str(self.log))} }}\n')
        self.os = FakeOs(standard_skeleton(usb_device("9-1", "0483", "5740", "SER1", label="thing")), {})
        self.host = Host(plugins_dir=self.directory, clock=Clock(), os_source=self.os, watch_grace_s=30.0, watch_tick_s=0.1,
                         follow_backoff_s=(0.2, 0.4), describe_check_s=0.2, frames_dir=tmp_path / "frames", **host_options)
        self.host.refresh()
        self.device = self.host.layer.device_paths["usb:9-1"]

    def rewrite(self, declared: dict | None) -> None:
        """An update in place: the script's source changes and so does its modification time (a fast edit within one tick of the
        file system's clock would otherwise be invisible to any mtime signal)."""

        self.generation += 1
        self.script.write_text(SCRIPT.format(declaration=declared))
        stamp = time.time_ns() + self.generation * 10_000_000
        os.utime(self.script, ns=(stamp, stamp))

    def verbs(self, verb: str) -> list[dict]:
        if not self.log.exists():
            return []
        return [row for row in map(json.loads, self.log.read_text().splitlines()) if row["verb"] == verb]

    def action_names(self) -> list[str]:
        """The actions the host would run for this device from the plugin's describe (``Host._registered``)."""

        job = next(iter(self.host.jobs.values()))
        entity = self.host.view().merged.entities[self.device]
        return [name for name in ("first", "second", "later", "fixed") if self.host._registered(job, entity, name)[0] is not None]

    def stop(self) -> None:
        self.host.stop()


@pytest.fixture
def rig(tmp_path):
    made = Reloadable(tmp_path, declaration("first"))
    yield made
    made.stop()


def test_a_changed_describe_is_applied_and_logged_as_plugin_reloaded(rig):
    assert rig.action_names() == ["first"] and len(rig.verbs("describe")) == 1
    rig.rewrite(declaration("first", "second"))
    assert rig.host.check_plugin_sources() == ["reloadable"]
    assert rig.action_names() == ["first", "second"]
    reloaded = [event for event in rig.host.view().events if event.code == "PluginReloaded"]
    assert len(reloaded) == 1 and reloaded[0].source == "reloadable" and "actions" in reloaded[0].message
    assert reloaded[0].args["changed"] == ["actions"]
    assert rig.host.describe_failed == {}


def test_an_unchanged_plugin_is_not_asked_again(rig):
    for _ in range(5):
        assert rig.host.check_plugin_sources() == []
    assert len(rig.verbs("describe")) == 1
    rig.rewrite(declaration("first"))  # an edit that does not change what it declares: asked once, applied, nothing restarts
    assert rig.host.check_plugin_sources() == ["reloadable"]
    assert rig.host.check_plugin_sources() == [] and len(rig.verbs("describe")) == 2
    [event] = [event for event in rig.host.view().events if event.code == "PluginReloaded"]
    assert event.args["changed"] == [] and "nothing it declares changed" in event.message


def test_the_loop_picks_an_update_up_within_the_interval(tmp_path):
    made = Reloadable(tmp_path, declaration("first"))
    try:
        made.host.start(udev=False)
        made.rewrite(declaration("later"))
        wait_for(lambda: made.action_names() == ["later"], timeout=5.0, what="the reloaded declaration")
        assert len(made.verbs("describe")) == 2
    finally:
        made.stop()


def test_a_failing_describe_keeps_the_old_one_running_and_says_unknown_until_a_good_one(rig):
    rig.rewrite(None)  # its describe now exits 3
    assert rig.host.check_plugin_sources() == ["reloadable"]
    assert rig.action_names() == ["first"], "the old declaration keeps running"
    view = rig.host.view()
    [note] = [status for status in view.merged.statuses if status.code == "DescribeFailed"]
    assert note.level == "unknown" and note.name == "reloadable: describe" and note.path == "host" and "previous declaration" in note.message
    assert not [event for event in view.events if event.code == "PluginReloaded"]
    assert rig.host.check_plugin_sources() == [], "asked again only after the next edit"
    rig.rewrite(declaration("fixed"))
    assert rig.host.check_plugin_sources() == ["reloadable"] and rig.action_names() == ["fixed"]
    assert not [status for status in rig.host.view().merged.statuses if status.code == "DescribeFailed"]


def test_a_running_watch_restarts_only_when_its_declaration_changed(rig):
    rig.host.watch_heartbeat(rig.device)
    running = wait_for(lambda: (state := rig.host.watch_json(*rig.host._watch_target(rig.device)))["state"] == "running" and state,
                       what="the watch")
    first_pid = running["pid"]
    # WHY wait: "running" is set when the host spawns the watch, a moment before the process writes its verb line
    wait_for(lambda: rig.verbs("watch"), what="the watch process starting")
    rig.rewrite(declaration("first", "second", period_s=0.5))  # other things changed, the watch's declaration did not
    rig.host.check_plugin_sources()
    rig.host.watch_heartbeat(rig.device)
    assert alive(first_pid) and len(rig.verbs("watch")) == 1
    rig.rewrite(declaration("first", "second", period_s=0.25))
    rig.host.check_plugin_sources()
    wait_for(lambda: len(rig.verbs("watch")) == 2, what="the watch restarted for its new period")
    assert not alive(first_pid)
    assert rig.host.watch_json(*rig.host._watch_target(rig.device))["period_s"] == 0.25
    rig.rewrite({k: v for k, v in declaration("first").items() if k != "watch"})  # it no longer streams: the watch ends
    rig.host.check_plugin_sources()
    assert not rig.host.watches


def test_matches_that_came_from_describe_follow_the_update(rig):
    [before] = rig.host.specs
    assert [match.text() for match in before.matches] == ["usb:0483:5740"]
    rig.rewrite(declaration("first", matches=("usb:0483:5740", "usb:1234:*")))
    rig.host.check_plugin_sources()
    [after] = rig.host.specs
    assert [match.text() for match in after.matches] == ["usb:0483:5740", "usb:1234:*"]


def test_a_plugin_with_no_source_the_host_can_see_is_never_asked_again(tmp_path):
    made = Reloadable(tmp_path, declaration("first"), with_source=False)
    try:
        assert source_stamp(made.host.specs[0]) is None
        assert made.host.check_plugin_sources() == []
    finally:
        made.stop()


def test_the_source_stamp_sees_an_edit_an_addition_and_a_removal_and_skips_tests_and_hidden_folders(tmp_path):
    project = tmp_path / "project"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / ".hidden").mkdir()
    (project / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (project / "tests" / "test_mod.py").write_text("x = 1\n")
    (project / "src" / ".hidden" / "mod.py").write_text("x = 1\n")
    (tmp_path / "plugin.toml").write_text(f'name = "p"\ncommand = ["x"]\ncwd = "{project}"\n')
    spec, _notes = load_plugin(tmp_path / "plugin.toml")
    base = source_stamp(spec)
    assert base is not None and base[1] == 1, "only src/pkg/mod.py counts"
    (project / "tests" / "test_mod.py").write_text("x = 2\n")
    (project / "src" / ".hidden" / "mod.py").write_text("x = 2\n")
    assert source_stamp(spec) == base
    (project / "src" / "pkg" / "other.py").write_text("y = 1\n")
    added = source_stamp(spec)
    assert added[1] == 2
    (project / "src" / "pkg" / "other.py").unlink()
    assert source_stamp(spec)[1] == 1
    os.utime(project / "src" / "pkg" / "mod.py", ns=(base[0] + 5_000_000_000, base[0] + 5_000_000_000))
    assert source_stamp(spec)[0] == base[0] + 5_000_000_000
