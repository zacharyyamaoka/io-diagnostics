"""The depth colour map (Zach, 2026-10-24): an app-wide setting that reaches the RealSense driver's ``watch`` through the
environment. Fakes only: the state fixture plugin logs the variables each watch started with; nothing touches a camera."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from io_diagnostics.host import Clock, Host
from io_diagnostics.server import make_server, serve_in_thread
from io_diagnostics.settings import DEPTH_COLORMAPS, SETTINGS_FILE, Settings, SettingsError
from test_host_support import FakeOs, standard_skeleton
from test_state_config import PAGE, Rig, alive, wait_for

DEFAULTS = {"depth_colormap": "Jet", "depth_equalize": True, "depth_min_m": 0.0, "depth_max_m": 6.0}


def test_the_choices_are_realsense_viewers_names_in_the_sdks_order_then_turbo():
    # rs.colorizer color_scheme 0..9 (checked against pyrealsense2 2.58.3 get_option_value_description), Turbo kept from before
    assert DEPTH_COLORMAPS == ("Jet", "Classic", "White to Black", "Black to White", "Bio", "Cold", "Warm", "Quantized",
                               "Pattern", "Hue", "Turbo")


def test_the_default_is_the_colorizers_own_and_a_choice_persists(tmp_path):
    file = tmp_path / SETTINGS_FILE
    assert Settings(file).depth() == DEFAULTS
    assert Settings(file).set_depth({"depth_colormap": "white to black", "depth_equalize": False, "depth_min_m": 0.3, "depth_max_m": 2.5})
    text = file.read_text()
    assert 'depth_colormap = "White to Black"' in text and "depth_equalize = false" in text and "depth_max_m = 2.5" in text
    again = Settings(file)
    assert again.depth() == {"depth_colormap": "White to Black", "depth_equalize": False, "depth_min_m": 0.3, "depth_max_m": 2.5}
    assert again.json()["depth_colormap"] == "White to Black" and again.json()["limits"]["depth_colormap"]["choices"][0] == "Jet"
    assert again.set_depth({"depth_colormap": "White to Black"}) is False  # the same value changes nothing


def test_a_bad_choice_is_refused_and_changes_nothing(tmp_path):
    settings = Settings(tmp_path / SETTINGS_FILE)
    for bad in ({"depth_colormap": "Rainbow"}, {"depth_equalize": "yes"}, {"depth_min_m": -1}, {"depth_max_m": 99},
                {"depth_min_m": 3, "depth_max_m": 3}, {"depth_min_m": True}, {"depth_nothing": 1}):
        with pytest.raises(SettingsError):
            settings.set_depth(bad)
        assert settings.depth() == DEFAULTS, bad
    assert not (tmp_path / SETTINGS_FILE).exists()


def test_a_bad_value_in_the_file_keeps_that_default_and_the_rest_is_read(tmp_path):
    file = tmp_path / SETTINGS_FILE
    file.write_text('depth_colormap = "Rainbow"\ndepth_equalize = false\n')
    settings = Settings(file)
    assert settings.depth() == {**DEFAULTS, "depth_equalize": False} and "Rainbow" in settings.note


def env_of(rig: Rig, number: int) -> dict:
    return rig.verbs("watch")[number]["depth"]


def test_the_choice_reaches_the_watch_environment(tmp_path):
    rig = Rig(tmp_path, "camera")
    try:
        rig.host.settings.set_depth({"depth_colormap": "Hue", "depth_equalize": False, "depth_min_m": 0.5, "depth_max_m": 3.0})
        rig.host.watch_heartbeat(rig.device)
        wait_for(lambda: rig.verbs("watch"), what="the watch")
        assert env_of(rig, 0) == {"COLORMAP": "Hue", "EQUALIZE": "0", "MIN_M": "0.5", "MAX_M": "3"}
    finally:
        rig.stop()


def test_a_change_restarts_the_running_watch_and_the_same_value_does_not(tmp_path):
    rig = Rig(tmp_path, "camera", grace_s=5.0)
    try:
        rig.host.watch_heartbeat(rig.device)
        wait_for(lambda: rig.verbs("watch"), what="the watch")
        first = rig.verbs("watch")[0]
        assert env_of(rig, 0) == {"COLORMAP": "Jet", "EQUALIZE": "1", "MIN_M": "0", "MAX_M": "6"}
        rig.host.set_settings({"depth_colormap": "Jet"})  # nothing changed: the same process keeps running
        assert alive(first["pid"]) and len(rig.verbs("watch")) == 1
        rig.host.set_settings({"depth_colormap": "Cold"})
        wait_for(lambda: len(rig.verbs("watch")) == 2, what="the watch restarted")
        assert env_of(rig, 1)["COLORMAP"] == "Cold" and not alive(first["pid"])
        assert rig.host.watch_json(*rig.host._watch_target(rig.device))["state"] in ("starting", "running")
    finally:
        rig.stop()


def test_a_change_with_no_watch_running_starts_nothing(tmp_path):
    rig = Rig(tmp_path, "camera")
    try:
        rig.host.set_settings({"depth_colormap": "Bio"})
        assert rig.host.settings.depth_colormap == "Bio" and not rig.host.watches and not rig.verbs("watch")
    finally:
        rig.stop()


def test_the_page_changes_it_through_settings_and_a_bad_choice_is_a_400(tmp_path):
    file = tmp_path / "config" / SETTINGS_FILE
    host = Host(plugins_dir=tmp_path / "none", clock=Clock(), os_source=FakeOs(standard_skeleton()), settings_file=file)
    host.refresh()
    http = make_server(host, 0)
    serve_in_thread(http)

    def post(body, headers=PAGE):
        request = urllib.request.Request(host.url.rstrip("/") + "/api/v1/settings", data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert post({"depth_colormap": "Hue"}, {"Content-Type": "application/json"})[0] == 403 and not file.exists()
        status, answer = post({"depth_colormap": "Hue", "depth_equalize": False})
        assert status == 200 and (answer["depth_colormap"], answer["depth_equalize"]) == ("Hue", False)
        assert 'depth_colormap = "Hue"' in file.read_text() and Settings(file).depth_colormap == "Hue"
        status, answer = post({"depth_colormap": "Rainbow"})
        assert (status, answer["refused"]) == (400, "BadSetting") and host.settings.depth_colormap == "Hue"
    finally:
        http.shutdown()
        http.server_close()
