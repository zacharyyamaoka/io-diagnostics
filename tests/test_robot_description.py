"""The robot description (io_diagnostics/robot_description.py, docs/robot-description.md): the file's format, the matching of a
part to an entity (expected.toml's rules), GET /api/v1/robot-description and its read-only file serving, which never leaves the
URDF's folder. Fixture only: frame F1 with tests/fixtures/robot-description/plugins (stdlib, made-up boards), never hardware."""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_host_support import ROOT

from io_diagnostics import robot_description as rd
from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread

FIXTURE = ROOT / "tests" / "fixtures" / "robot-description"
DOC = (ROOT / "docs" / "api-v1.md").read_text()
ADAPTER = "host/usb1/port-1-3/usb-1-3"
OTHER_ADAPTER = "host/usb1/port-1-5/usb-1-5"
CAMERA = "host/usb6/port-6-1/usb-6-1"
OTHER_CAMERA = "host/usb6/port-6-2/usb-6-2"


def write_description(directory: Path, urdf: Path) -> Path:
    """The fixture description in ``directory`` with its urdf made absolute (``urdf`` is relative to the file it is in)."""

    text = (FIXTURE / rd.DESCRIPTION_FILE).read_text()
    text = text.replace('urdf = "robot.urdf"', f'urdf = "{urdf}"')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / rd.DESCRIPTION_FILE).write_text(text)
    return directory / rd.DESCRIPTION_FILE


def serve(config: Path, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(config))
    host = make_host(fixture="F1", once=True, plugins_dir=FIXTURE / "plugins")
    host.run_all_once()
    http = make_server(host, 0)
    serve_in_thread(http)
    return host, http


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    """A copy of the fixture model with a symlink that leads out of its folder, and a secret beside the folder."""

    root = tmp_path_factory.mktemp("rd")
    folder = root / "model"
    shutil.copytree(FIXTURE, folder, ignore=shutil.ignore_patterns("plugins", "__pycache__"))
    (root / "secret.txt").write_text("outside the URDF's folder")
    os.symlink(root / "secret.txt", folder / "escape.txt")
    os.symlink(root, folder / "up")
    return folder


@pytest.fixture(scope="module")
def server(model, tmp_path_factory):
    patch = pytest.MonkeyPatch()
    config = tmp_path_factory.mktemp("cfg")
    write_description(config, model / "robot.urdf")
    host, http = serve(config, patch)
    yield host
    http.shutdown()
    http.server_close()
    patch.undo()


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    host, http = serve(tmp_path_factory.mktemp("cfg-empty"), patch)
    yield host
    http.shutdown()
    http.server_close()
    patch.undo()


def get(host, path: str):
    with urllib.request.urlopen(host.url.rstrip("/") + path, timeout=10) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def get_json(host, path: str) -> dict:
    status, kind, body = get(host, path)
    assert status == 200 and kind.startswith("application/json")
    return json.loads(body)


def refused(host, path: str) -> int:
    with pytest.raises(urllib.error.HTTPError) as error:
        get(host, path)
    return error.value.code


# ----- the file ------------------------------------------------------------------------------------------------------
def test_the_fixture_file_parses(tmp_path):
    description = rd.parse((FIXTURE / rd.DESCRIPTION_FILE).read_text(), FIXTURE / rd.DESCRIPTION_FILE)
    assert description.urdf == FIXTURE / "robot.urdf"  # relative to the file it is in
    assert [part.links for part in description.parts] == [["shoulder_link"], ["elbow_link"], ["wrist_link"], ["tool_link"], ["camera_link"]]
    assert description.parts[0].match == {"hardware_id": "usb:0483:5740:272813EF", "node": "can/1"}
    assert description.parts[2].match == {"path": "host/usb1/port-1-5/*/can/1"}
    assert [(item.name, item.reason) for item in description.unmapped] == [("RealSense D405 130523071221", "no mount for it in this model")]
    assert rd.urdf_links(FIXTURE / "robot.urdf") == ("fixture_arm", ["base_link", "column", "shoulder_link", "elbow_link", "wrist_link",
                                                                     "tool_link", "camera_mount", "camera_link"])


@pytest.mark.parametrize(("text", "words"), [
    ('[[part]]\nlink = "a"\nnode = "can/1"\n', "urdf is required"),
    ('urdf = "/x.urdf"\nversion = 2\n', "version 2"),
    ('urdf = "/x.urdf"\n[[part]]\nnode = "can/1"\n', "link (a URDF link name)"),
    ('urdf = "/x.urdf"\n[[part]]\nlink = "a"\n', "needs at least one of"),
    ('urdf = "/x.urdf"\n[[part]]\nlink = "a"\ncan_id = "12"\n', "can_id must be an integer"),
    ('urdf = "/x.urdf"\npart = 3\n', "array of tables"),
    ('urdf = [', "not TOML"),
])
def test_a_file_that_does_not_read_names_what_is_wrong(text, words):
    with pytest.raises(rd.DescriptionError, match=re.escape(words)):
        rd.parse(text, Path("/cfg") / rd.DESCRIPTION_FILE)


def test_load_reports_a_problem_and_no_file_is_none(tmp_path):
    assert rd.load(tmp_path) is None
    (tmp_path / rd.DESCRIPTION_FILE).write_text('urdf = "missing.urdf"\n')
    assert "is not a file" in rd.load(tmp_path).problem
    (tmp_path / "bad.urdf").write_text("<robot><link name='a'>")
    (tmp_path / rd.DESCRIPTION_FILE).write_text('urdf = "bad.urdf"\n')
    assert rd.load(tmp_path).problem.startswith(str(tmp_path / "bad.urdf"))


# ----- matching --------------------------------------------------------------------------------------------------
def tree():
    def row(path, hardware_id=None, identity=None, children=()):
        return {"path": path, "hardware_id": hardware_id, "identity": identity or {}, "children": list(children)}
    return row("host", children=[
        row("host/usb1/port-1-3/usb-1-3", "usb:0483:5740:272813EF", {"serial_number": "272813EF"}, [
            row("host/usb1/port-1-3/usb-1-3/can", children=[row("host/usb1/port-1-3/usb-1-3/can/12", identity={"model_id": "mjbots-moteus-r4-11"})])]),
        row("host/usb1/port-1-5/usb-1-5", "usb:0483:5740:0607623A", {}, [
            row("host/usb1/port-1-5/usb-1-5/can", children=[row("host/usb1/port-1-5/usb-1-5/can/12", identity={"model_id": "mjbots-moteus-c1"})])]),
        row("host/usb6/port-6-1/usb-6-1", "usb:8086:0b5b:219323070276", {"serial_number": "218622274411"}),
    ])


@pytest.mark.parametrize(("match", "path"), [
    ({"hardware_id": "usb:0483:5740:0607623A", "node": "can/12"}, "host/usb1/port-1-5/usb-1-5/can/12"),
    ({"hardware_id": "usb:8086:0B5B:219323070276"}, "host/usb6/port-6-1/usb-6-1"),  # vid / pid in any case
    ({"serial": "218622274411"}, "host/usb6/port-6-1/usb-6-1"),           # a driver's identity serial (the RealSense SDK's)
    ({"serial": "219323070276"}, "host/usb6/port-6-1/usb-6-1"),           # the USB serial
    ({"model_id": "mjbots-moteus-c1", "can_id": 12}, "host/usb1/port-1-5/usb-1-5/can/12"),
    ({"can_id": 12}, "host/usb1/port-1-3/usb-1-3/can/12"),                # the first in tree order
    ({"path": "host/usb1/*/can/12", "model_id": "mjbots-moteus-c1"}, "host/usb1/port-1-5/usb-1-5/can/12"),
    ({"node": "can/12"}, "host/usb1/port-1-3/usb-1-3/can/12"),
    ({"hardware_id": "usb:0483:5740:0607623A", "node": "can/13"}, None),  # not detected
    ({"hardware_id": "usb:0483:5740:DEADBEEF"}, None),
    ({}, None),
])
def test_a_part_resolves_as_expected_toml_matches(match, path):
    assert rd.resolve(match, rd._rows(tree())) == path


def test_no_description_is_present_false():
    answer = rd.describe(None, tree())
    assert answer["present"] is False and answer["parts"] == [] and answer["file_name"] == "robot-description.toml"


# ----- the endpoints ------------------------------------------------------------------------------------------------
def test_the_endpoint_resolves_every_part_on_the_fixture(server):
    answer = get_json(server, "/api/v1/robot-description")
    assert answer["present"] is True and answer["problem"] is None
    assert answer["urdf"] == "/api/v1/robot-description/files/robot.urdf"
    paths = {part["name"]: part["path"] for part in answer["parts"]}
    assert paths == {"Shoulder": f"{ADAPTER}/can/1", "Elbow": f"{ADAPTER}/can/2", "Wrist": f"{OTHER_ADAPTER}/can/1", "Tool": None,
                     "Camera": CAMERA}
    assert [(item["name"], item["path"]) for item in answer["unmapped_devices"]] == [("RealSense D405 130523071221", OTHER_CAMERA)]
    assert all(part["problem"] is None for part in answer["parts"])


def test_the_endpoint_matches_the_doc_example(server):
    sections = re.split(r"(?m)^(#{2,3} .*)$", DOC)
    at = next(index for index, part in enumerate(sections) if part.startswith("## `GET /api/v1/robot-description`"))
    documented = json.loads(re.findall(r"```json\n(.*?)\n```", sections[at + 1], re.S)[0])
    answer = get_json(server, "/api/v1/robot-description")
    assert set(answer) == set(documented)
    assert set(answer["parts"][0]) == set(documented["parts"][0])
    assert set(answer["unmapped_devices"][0]) == set(documented["unmapped_devices"][0])


def test_without_a_file_it_is_present_false(bare):
    answer = get_json(bare, "/api/v1/robot-description")
    assert answer["present"] is False and answer["problem"] is None and answer["urdf"] is None
    assert refused(bare, "/api/v1/robot-description/files/robot.urdf") == 404


def test_the_urdf_and_its_meshes_are_served(server):
    status, kind, body = get(server, "/api/v1/robot-description/files/robot.urdf")
    assert status == 200 and kind.startswith("application/xml") and body.startswith(b"<?xml")
    status, kind, body = get(server, "/api/v1/robot-description/files/meshes/camera.stl")
    assert status == 200 and kind == "model/stl" and body.startswith(b"solid camera")


@pytest.mark.parametrize("tail", ["../secret.txt", "%2e%2e/secret.txt", "meshes/..%2f..%2fsecret.txt", "escape.txt", "up/secret.txt",
                                  "%2Fetc%2Fpasswd", "/etc/passwd", "meshes", "", "nothing.stl", "meshes/camera.stl%00.png"])
def test_nothing_outside_the_urdf_folder_is_served(server, tail):
    assert refused(server, f"/api/v1/robot-description/files/{tail}") == 404


def test_the_files_are_get_only(server):
    request = urllib.request.Request(server.url.rstrip("/") + "/api/v1/robot-description/files/robot.urdf", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=10)
    assert error.value.code in (403, 404, 405)
