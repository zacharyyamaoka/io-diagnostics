"""Bringing a robot description in (io_diagnostics/robot_library.py, docs/robot-description.md "From the page"): the upload (a zip, or a
folder as multipart), zip-slip and every other refusal, the kept descriptions (switch, remove), a device assigned to a part (the
toml's comments survive), a cad2robot/1 bundle's own mappings, and the page-only rule on all of it. Fixture only: frame F1 with
tests/fixtures/robot-description/plugins (stdlib, made-up boards), never hardware."""

from __future__ import annotations

import io
import json
import os
import stat
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest
from test_host_support import ROOT

from io_diagnostics import robot_description as rd
from io_diagnostics import robot_library as library
from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread

FIXTURE = ROOT / "tests" / "fixtures" / "robot-description"
PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}
ADAPTER = "host/usb1/port-1-3/usb-1-3"
CAN1 = f"{ADAPTER}/can/1"
CAN2 = f"{ADAPTER}/can/2"
CAMERA = "host/usb6/port-6-1/usb-6-1"
URDF = """<?xml version="1.0"?>
<robot name="uploaded_arm">
  <link name="base"><visual><geometry><mesh filename="meshes/base.stl" scale="0.001 0.001 0.001"/></geometry></visual>
    <visual><geometry><mesh filename="meshes/Plate_1__Motor_2__0.stl"/></geometry></visual>
    <visual><geometry><mesh filename="meshes/Plate_1__Motor_2__1.stl"/></geometry></visual></link>
  <link name="arm"><visual><geometry><box size="0.1 0.1 0.1"/></geometry></visual></link>
  <joint name="j" type="fixed"><parent link="base"/><child link="arm"/></joint>
</robot>
"""
STL = b"solid x\nendsolid x\n"


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


ROBOT = {"robot/urdf/arm.urdf": URDF.encode(), "robot/urdf/meshes/base.stl": STL, "robot/urdf/meshes/Plate_1__Motor_2__0.stl": STL,
         "robot/urdf/meshes/Plate_1__Motor_2__1.stl": STL}


def post(server, endpoint: str, body: bytes, kind: str, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(server.url.rstrip("/") + endpoint, data=body, method="POST",
                                     headers={**PAGE, "Content-Type": kind, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def post_json(server, endpoint: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    return post(server, endpoint, json.dumps(body).encode(), "application/json", headers)


def upload(server, files: dict[str, bytes], query: str = "") -> tuple[int, dict]:
    return post(server, f"/api/v1/robot-description/upload{query}", zip_of(files), "application/zip")


def multipart(files: dict[str, bytes]) -> tuple[bytes, str]:
    boundary = "----robotdx-test-boundary"
    chunks = [(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
               "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n" for name, data in files.items()]
    return b"".join(chunks) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def get_json(server, path: str) -> dict:
    with urllib.request.urlopen(server.url.rstrip("/") + path, timeout=10) as response:
        return json.loads(response.read())


def describe(server) -> dict:
    return get_json(server, "/api/v1/robot-description")


@pytest.fixture
def config(tmp_path):
    return tmp_path / "config"


@pytest.fixture
def bare(config, monkeypatch):
    """A fixture host with no description, in a config directory of the test's own."""

    config.mkdir()
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(config))
    host = make_host(fixture="F1", once=True, plugins_dir=FIXTURE / "plugins")
    host.run_all_once()
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host
    http.shutdown()


@pytest.fixture
def described(bare, config):
    """The same host with the fixture description active (its comments are what the edits must keep)."""

    text = (FIXTURE / rd.DESCRIPTION_FILE).read_text().replace('urdf = "robot.urdf"', f'urdf = "{FIXTURE / "robot.urdf"}"')
    (config / rd.DESCRIPTION_FILE).write_text(text)
    return bare


# ----- the upload ----------------------------------------------------------------------------------------------------
def test_a_zip_becomes_the_active_description_at_once(bare, config):
    status, answer = upload(bare, ROBOT)
    assert status == 200 and answer["ok"] is True and answer["robot"] == "uploaded_arm" and answer["name"] == "uploaded-arm"
    kept = config / "robot-descriptions" / "uploaded-arm"
    assert (kept / "robot" / "urdf" / "meshes" / "base.stl").read_bytes() == STL
    now = describe(bare)
    assert now["present"] is True and now["problem"] is None and now["robot"] == "uploaded_arm"
    assert now["urdf"] == "/api/v1/robot-description/files/robot/urdf/arm.urdf"
    assert now["links"] == ["base", "arm"]
    with urllib.request.urlopen(bare.url.rstrip("/") + now["urdf"], timeout=10) as response:
        assert response.read() == URDF.encode()
    with urllib.request.urlopen(bare.url.rstrip("/") + "/api/v1/robot-description/files/robot/urdf/meshes/base.stl", timeout=10) as response:
        assert response.read() == STL
    active = tomllib.loads((config / rd.DESCRIPTION_FILE).read_text())
    assert Path(active["urdf"]) == kept / "robot" / "urdf" / "arm.urdf" and active["name"] == "uploaded-arm"


def test_a_folder_comes_as_multipart(bare):
    body, kind = multipart(ROBOT)
    status, answer = post(bare, "/api/v1/robot-description/upload", body, kind)
    assert status == 200 and answer["robot"] == "uploaded_arm"
    assert describe(bare)["urdf"] == "/api/v1/robot-description/files/robot/urdf/arm.urdf"


def test_multipart_keeps_binary_files_exact(bare, config):
    stl = bytes(range(256)) * 400 + b"\r\n--not-the-boundary\r\n"
    body, kind = multipart({"arm.urdf": URDF.encode(), "meshes/base.stl": stl, "meshes/Plate_1__Motor_2__0.stl": STL, "meshes/Plate_1__Motor_2__1.stl": STL})
    status, _ = post(bare, "/api/v1/robot-description/upload", body, kind)
    assert status == 200
    assert (config / "robot-descriptions" / "uploaded-arm" / "meshes" / "base.stl").read_bytes() == stl


def test_a_meshes_folder_beside_the_urdf_folder_is_served(bare):
    urdf = URDF.replace("meshes/", "../meshes/").encode()
    status, answer = upload(bare, {"bot/urdf/arm.urdf": urdf, "bot/meshes/base.stl": STL, "bot/meshes/Plate_1__Motor_2__0.stl": STL,
                                   "bot/meshes/Plate_1__Motor_2__1.stl": STL})
    assert status == 200 and answer["missing_meshes"] == []
    with urllib.request.urlopen(bare.url.rstrip("/") + "/api/v1/robot-description/files/bot/meshes/base.stl", timeout=10) as response:
        assert response.read() == STL


def test_package_urls_resolve_inside_the_upload(bare):
    urdf = URDF.replace('meshes/', 'package://my_robot/meshes/').encode()
    status, answer = upload(bare, {"my_robot/urdf/arm.urdf": urdf, "my_robot/meshes/base.stl": STL, "my_robot/meshes/Plate_1__Motor_2__0.stl": STL,
                                   "my_robot/meshes/Plate_1__Motor_2__1.stl": STL, "my_robot/package.xml": b"<package/>"})
    assert status == 200 and answer["missing_meshes"] == []
    assert describe(bare)["packages"] == {"my_robot": "my_robot"}


def test_a_mesh_the_upload_lacks_is_named_and_the_model_still_loads(bare):
    status, answer = upload(bare, {"arm.urdf": URDF.encode(), "meshes/base.stl": STL})
    assert status == 200 and answer["missing_meshes"] == ["meshes/Plate_1__Motor_2__0.stl", "meshes/Plate_1__Motor_2__1.stl"]
    assert describe(bare)["robot"] == "uploaded_arm"


def test_a_urdf_with_primitives_needs_no_meshes(bare):
    urdf = b'<robot name="boxes"><link name="a"><visual><geometry><box size="1 1 1"/></geometry></visual></link></robot>'
    status, answer = post(bare, "/api/v1/robot-description/upload", *multipart({"boxes.urdf": urdf}))
    assert status == 200 and answer["robot"] == "boxes" and answer["missing_meshes"] == []


def test_two_uploads_of_one_robot_are_two_descriptions(bare, config):
    assert upload(bare, ROBOT)[1]["name"] == "uploaded-arm"
    assert upload(bare, ROBOT)[1]["name"] == "uploaded-arm-2"
    assert sorted(path.name for path in (config / "robot-descriptions").iterdir()) == ["uploaded-arm", "uploaded-arm-2"]


@pytest.mark.parametrize("member", ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "..\\evil.txt", "C:\\evil.txt"])
def test_zip_slip_is_refused_and_nothing_is_written(bare, config, member):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("arm.urdf", URDF.encode())
        # writestr cleans a name it can: the raw entry keeps what an attacker's tool would write
        info = zipfile.ZipInfo("x.txt")
        info.filename = member
        archive.writestr(info, b"evil")
    status, answer = post(bare, "/api/v1/robot-description/upload", buffer.getvalue(), "application/zip")
    assert status == 400 and answer["refused"] == "UnsafePath" and "evil" in answer["error"]
    assert not (config / rd.DESCRIPTION_FILE).exists()
    assert not list((config.parent).rglob("evil.txt"))
    assert not (config / "robot-descriptions").exists() or not [path for path in (config / "robot-descriptions").iterdir()]


def test_a_zip_with_backslashes_unpacks_as_folders(bare, config):
    """PowerShell 5.1's Compress-Archive writes them; the Windows box is where a bundle comes from."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in ROBOT.items():
            info = zipfile.ZipInfo("x")
            info.filename = name.replace("/", "\\")
            archive.writestr(info, data)
    status, answer = post(bare, "/api/v1/robot-description/upload", buffer.getvalue(), "application/zip")
    assert status == 200 and answer["missing_meshes"] == []
    assert (config / "robot-descriptions" / "uploaded-arm" / "robot" / "urdf" / "meshes" / "base.stl").read_bytes() == STL


def test_a_symlink_in_the_zip_is_refused(bare, config):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("arm.urdf", URDF.encode())
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    status, answer = post(bare, "/api/v1/robot-description/upload", buffer.getvalue(), "application/zip")
    assert status == 400 and answer["refused"] == "UnsafePath"
    assert not (config / rd.DESCRIPTION_FILE).exists()


@pytest.mark.parametrize("name", ["../evil.urdf", "/abs/evil.urdf", "a/../../evil.urdf"])
def test_a_multipart_path_cannot_climb_out(bare, config, name):
    status, answer = post(bare, "/api/v1/robot-description/upload", *multipart({name: URDF.encode()}))
    assert status == 400 and answer["refused"] == "UnsafePath"
    assert not list(config.parent.rglob("evil.urdf"))


def test_the_unpacked_size_is_capped_by_what_is_read_not_what_the_zip_says(bare, config, monkeypatch):
    monkeypatch.setattr(library, "MAX_UNPACKED", 100_000)
    status, answer = upload(bare, {"arm.urdf": URDF.encode(), "meshes/base.stl": b"\0" * 2_000_000})
    assert status == 413 and answer["refused"] == "TooLarge"
    assert not (config / rd.DESCRIPTION_FILE).exists()
    assert not [path for path in (config / "robot-descriptions").iterdir()]


def test_a_body_over_the_cap_is_refused_before_it_is_read(bare, monkeypatch):
    monkeypatch.setattr(library, "MAX_UPLOAD", 1000)
    status, answer = upload(bare, {"arm.urdf": os.urandom(5000)})
    assert status == 413 and answer["refused"] == "TooLarge"


@pytest.mark.parametrize("files,word", [({"readme.txt": b"hello"}, "no .urdf"), ({"robot.xacro": b"<robot/>"}, "xacro"),
                                        ({"arm.urdf": b"not xml"}, "XML"), ({"arm.urdf": b"<sdf/>"}, "<robot>")])
def test_what_is_not_a_urdf_is_named(bare, config, files, word):
    status, answer = upload(bare, files)
    assert status == 400 and answer["refused"] == "NoRobot" and word in answer["error"]
    assert not (config / rd.DESCRIPTION_FILE).exists()


def test_a_body_that_is_not_a_zip_is_refused(bare, config):
    status, answer = post(bare, "/api/v1/robot-description/upload", b"PK not really", "application/zip")
    assert status == 400 and answer["refused"] == "BadUpload"
    assert not (config / rd.DESCRIPTION_FILE).exists()


# ----- the page-only rule -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint", ["upload", "assign", "activate", "remove"])
@pytest.mark.parametrize("headers", [{"X-IO-Diagnostics": ""}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                                     {"Origin": "http://evil.example"}])
def test_every_write_is_the_pages_own(bare, config, endpoint, headers):
    body = zip_of(ROBOT) if endpoint == "upload" else json.dumps({"name": "x", "path": CAN1, "links": ["a"]}).encode()
    kind = "application/zip" if endpoint == "upload" else "application/json"
    status, answer = post(bare, f"/api/v1/robot-description/{endpoint}", body, kind, headers)
    assert status == 403 and answer["refused"] in ("NotFromPage", "NotSameOrigin")
    assert not (config / rd.DESCRIPTION_FILE).exists() and not (config / "robot-descriptions").exists()


@pytest.mark.parametrize("endpoint", ["upload", "assign", "activate", "remove"])
def test_the_writes_are_post_only(bare, endpoint):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(bare.url.rstrip("/") + f"/api/v1/robot-description/{endpoint}", timeout=10)
    assert error.value.code == 405


def test_the_json_writes_want_json(bare):
    status, answer = post(bare, "/api/v1/robot-description/assign", b"{}", "text/plain")
    assert status == 403 and answer["refused"] == "NotJson"


# ----- kept descriptions: switch and remove ---------------------------------------------------------------------------
def test_the_previous_description_is_kept_and_can_come_back(described, config):
    before = (config / rd.DESCRIPTION_FILE).read_text()
    status, answer = upload(described, ROBOT)
    assert status == 200 and answer["previous"] == "robot"
    now = describe(described)
    assert now["robot"] == "uploaded_arm"
    assert [(item["name"], item["robot"], item["active"]) for item in now["library"]] == [("robot", "fixture_arm", False), ("uploaded-arm", "uploaded_arm", True)]
    status, answer = post_json(described, "/api/v1/robot-description/activate", {"name": "robot"})
    assert status == 200 and answer["ok"] is True
    assert (config / rd.DESCRIPTION_FILE).read_text() == before            # comments and all
    assert describe(described)["robot"] == "fixture_arm"
    assert [item["active"] for item in describe(described)["library"]] == [True, False]


def test_a_part_assigned_after_a_switch_survives_the_round_trip(described, config):
    upload(described, ROBOT)
    post_json(described, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    post_json(described, "/api/v1/robot-description/activate", {"name": "robot"})
    assert describe(described)["robot"] == "fixture_arm"
    post_json(described, "/api/v1/robot-description/activate", {"name": "uploaded-arm"})
    assert [part["links"] for part in describe(described)["parts"]] == [["arm"]]


def test_removing_the_active_description_goes_back_to_the_lowest_rung(bare, config):
    upload(bare, ROBOT)
    status, answer = post_json(bare, "/api/v1/robot-description/remove", {"name": "uploaded-arm"})
    assert status == 200 and answer["ok"] is True
    assert not (config / rd.DESCRIPTION_FILE).exists() and not (config / "robot-descriptions" / "uploaded-arm").exists()
    now = describe(bare)
    assert now["present"] is False and now["library"] == []


def test_removing_a_kept_one_leaves_the_active_one(described, config):
    upload(described, ROBOT)
    status, _ = post_json(described, "/api/v1/robot-description/remove", {"name": "robot"})
    assert status == 200
    assert describe(described)["robot"] == "uploaded_arm"
    assert [item["name"] for item in describe(described)["library"]] == ["uploaded-arm"]
    assert (FIXTURE / "robot.urdf").is_file()                              # a file outside the kept folder is never touched


@pytest.mark.parametrize("name", ["../..", "..", "a/b", "", "nobody", "/etc", ".", "a\x00b"])
def test_a_name_that_is_not_a_kept_description_is_refused(bare, config, name):
    for endpoint in ("activate", "remove"):
        status, answer = post_json(bare, f"/api/v1/robot-description/{endpoint}", {"name": name})
        assert status in (400, 404) and answer["refused"] == "NoSuchDescription"
    assert config.is_dir()


# ----- a device assigned to a part ------------------------------------------------------------------------------------
def test_assign_writes_a_part_found_again_by_its_adapter_and_node(bare, config):
    upload(bare, ROBOT)
    status, answer = post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    assert status == 200 and answer["assigned"] is True
    part = describe(bare)["parts"][0]
    assert part["links"] == ["arm"] and part["path"] == CAN1 and part["name"] == "CAN 1"
    assert part["match"] == {"hardware_id": "usb:0483:5740:272813EF", "node": "can/1"}


def test_assign_a_camera_by_its_hardware_id(bare):
    upload(bare, ROBOT)
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAMERA, "links": ["arm"]})
    part = describe(bare)["parts"][0]
    assert part["path"] == CAMERA and part["match"] == {"hardware_id": "usb:8086:0b5b:219323070276"}


def test_assigning_meshes_keeps_the_rest_of_the_link_grey(bare):
    upload(bare, ROBOT)
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "meshes": ["Plate_1__Motor_2__*"]})
    part = describe(bare)["parts"][0]
    assert part["meshes"] == ["Plate_1__Motor_2__*"] and part["links"] == [] and part["problem"] is None


def test_clicking_the_same_part_again_undoes_it(bare, config):
    upload(bare, ROBOT)
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    status, answer = post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    assert status == 200 and answer["assigned"] is False
    assert describe(bare)["parts"] == []


def test_a_second_body_joins_the_same_device(bare):
    upload(bare, ROBOT)
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "meshes": ["Plate_1__Motor_2__*"]})
    parts = describe(bare)["parts"]
    assert len(parts) == 1 and parts[0]["links"] == ["arm"] and parts[0]["meshes"] == ["Plate_1__Motor_2__*"]
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    parts = describe(bare)["parts"]
    assert len(parts) == 1 and parts[0]["links"] == [] and parts[0]["meshes"] == ["Plate_1__Motor_2__*"]


def test_a_body_moves_to_the_device_that_claims_it(bare):
    upload(bare, ROBOT)
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAN2, "links": ["arm"]})
    parts = describe(bare)["parts"]
    assert [part["path"] for part in parts] == [CAN2]


def test_a_device_placed_at_a_point_keeps_it_when_its_last_body_comes_off(bare, config):
    upload(bare, ROBOT)
    file = config / rd.DESCRIPTION_FILE
    file.write_text(file.read_text() + f'\n[[part]]\nname = "Cam"\nat = [0.1, 0.2, 0.3]\nhardware_id = "usb:8086:0b5b:219323070276"\n')
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAMERA, "links": ["arm"]})
    assert describe(bare)["parts"][0]["links"] == ["arm"]
    post_json(bare, "/api/v1/robot-description/assign", {"path": CAMERA, "links": ["arm"]})
    part = describe(bare)["parts"][0]
    assert part["links"] == [] and part["at"] == [0.1, 0.2, 0.3] and part["path"] == CAMERA


def test_an_edit_keeps_every_comment_of_the_file(described, config):
    file = config / rd.DESCRIPTION_FILE
    before = file.read_text()
    post_json(described, "/api/v1/robot-description/assign", {"path": f"{ADAPTER}/can/2", "links": ["camera_mount"]})
    text = file.read_text()
    for line in before.splitlines():
        if line.startswith("#"):
            assert line in text
    parts = {part["name"]: part for part in describe(described)["parts"]}
    assert parts["Elbow"]["links"] == ["elbow_link", "camera_mount"]       # the part already there, not a second one
    assert (config / (rd.DESCRIPTION_FILE + ".bak")).read_text() == before


def test_removing_a_written_part_takes_its_own_comment_and_no_other(described, config):
    file = config / rd.DESCRIPTION_FILE
    post_json(described, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["shoulder_link"]})   # the Shoulder part's only link
    text = file.read_text()
    assert 'name = "Shoulder"' not in text and 'name = "Elbow"' in text
    assert "# by the adapter's hardware_id" not in text                     # its own comment went with it
    assert "# by an entity path pattern (fnmatch)" in text
    assert [part["name"] for part in describe(described)["parts"]] == ["Elbow", "Wrist", "Tool", "Camera"]


def test_a_device_listed_as_not_placed_leaves_the_list_once_it_is_placed(described):
    assert [item["name"] for item in describe(described)["unmapped_devices"]] == ["RealSense D405 130523071221"]
    other = "host/usb6/port-6-2/usb-6-2"
    post_json(described, "/api/v1/robot-description/assign", {"path": other, "links": ["camera_mount"]})
    answer = describe(described)
    assert answer["unmapped_devices"][0]["path"] == other                   # the file's table stays, it is the author's
    assert other not in [item["path"] for item in answer["not_in_model"]]


@pytest.mark.parametrize("body,code,refused", [({"path": CAN1}, 400, "BadRequest"), ({"path": CAN1, "links": "arm"}, 400, "BadRequest"),
                                               ({"path": "host/nobody", "links": ["arm"]}, 404, "NoSuchEntity"),
                                               ({"path": CAN1, "links": ["no_such_link"]}, 400, "NoSuchLink"),
                                               ({"path": CAN1, "meshes": ["nothing*"]}, 400, "NoSuchLink")])
def test_a_bad_assign_writes_nothing(bare, config, body, code, refused):
    upload(bare, ROBOT)
    before = (config / rd.DESCRIPTION_FILE).read_text()
    status, answer = post_json(bare, "/api/v1/robot-description/assign", body)
    assert status == code and answer["refused"] == refused
    assert (config / rd.DESCRIPTION_FILE).read_text() == before


def test_assign_without_a_description_is_refused(bare):
    status, answer = post_json(bare, "/api/v1/robot-description/assign", {"path": CAN1, "links": ["arm"]})
    assert status == 409 and answer["refused"] == "NoDescription"


# ----- the part's new keys, read side ----------------------------------------------------------------------------------
def test_meshes_and_at_are_parts_without_a_link(config):
    text = f"""version = 1
urdf = "{FIXTURE / 'robot.urdf'}"
[view]
front = "+y"
[[part]]
name = "by mesh"
meshes = ["camera*"]
node = "can/1"
[[part]]
name = "a camera the model does not have"
at = [0.5, 0.1, 0.2]
look_at = [0, 0, 0.1]
serial = "218622274411"
"""
    description = rd.parse(text, config / rd.DESCRIPTION_FILE)
    assert description.parts[0].links == [] and description.parts[0].meshes == ["camera*"]
    assert description.parts[1].at == (0.5, 0.1, 0.2) and description.parts[1].look_at == (0.0, 0.0, 0.1)
    assert description.view == {"front": "+y"}


@pytest.mark.parametrize("table,word", [('name = "x"\nnode = "can/1"', "link"), ('at = [1, 2]\nnode = "can/1"', "at"),
                                        ('at = ["a", 2, 3]\nnode = "can/1"', "at"), ('meshes = "x"\nnode = "can/1"', "meshes"),
                                        ('link = "a"\nnode = "can/1"\nlook_at = [1]', "look_at")])
def test_a_wrong_new_key_is_named(config, table, word):
    with pytest.raises(rd.DescriptionError, match=word):
        rd.parse(f'urdf = "robot.urdf"\n[[part]]\n{table}\n', config / rd.DESCRIPTION_FILE)


@pytest.mark.parametrize("front", ["up", "+q", 3])
def test_a_wrong_front_is_named(config, front):
    with pytest.raises(rd.DescriptionError, match="front"):
        rd.parse(f'urdf = "robot.urdf"\n[view]\nfront = {json.dumps(front)}\n', config / rd.DESCRIPTION_FILE)


def test_a_mesh_pattern_nothing_matches_is_that_parts_problem(bare, config):
    upload(bare, ROBOT)
    file = config / rd.DESCRIPTION_FILE
    file.write_text(file.read_text() + '\n[[part]]\nmeshes = ["nothing*"]\nnode = "can/1"\n')
    assert "nothing*" in describe(bare)["parts"][0]["problem"]


def test_mesh_patterns_are_plain_wildcards():
    assert rd.mesh_matches("Plate_1__Motor_2__*", "Plate_1__Motor_2__0.stl")
    assert rd.mesh_matches("a?c.stl", "abc.stl")
    assert not rd.mesh_matches("Plate_1__Motor_2__*", "Plate_1__Motor_22__0.stl")
    assert rd.mesh_matches("x[1].stl", "x[1].stl")                         # a [ is a [, as in the page's own matcher


# ----- what the page's hardware row and list read ----------------------------------------------------------------------
def test_the_answer_lists_adapters_and_the_computer_with_their_parts(described):
    answer = describe(described)
    hardware = {item["path"]: item for item in answer["hardware"]}
    assert hardware["host"]["kind"] == "computer"
    assert hardware[ADAPTER]["kind"] == "adapter" and hardware[ADAPTER]["name"].startswith("fdcanusb")
    names = [answer["parts"][index]["name"] for index in hardware[ADAPTER]["parts"]]
    assert names == ["Shoulder", "Elbow"]
    assert sorted(answer["parts"][index]["name"] for index in hardware["host"]["parts"]) == ["Camera", "Elbow", "Shoulder", "Wrist"]


def test_not_in_the_model_lists_each_device_once_with_its_reason(described):
    answer = describe(described)
    items = {item["path"]: item for item in answer["not_in_model"]}
    assert items["host/usb6/port-6-2/usb-6-2"]["reason"] == "no mount for it in this model"
    assert items["host/usb1/port-1-5/usb-1-5/can/2"]["reason"] == ""       # a board nobody placed
    assert ADAPTER not in items and "host" not in items                     # adapters and computers are chips
    assert CAN1 not in items and CAMERA not in items                        # placed


# ----- a cad2robot/1 bundle --------------------------------------------------------------------------------------------
def bundle(joint_can: int | None = 12, camera_serial: str | None = "219323070276") -> dict[str, bytes]:
    robot = {"schema": "cad2robot/1", "design": "Demo", "urdf": "urdf/demo.cad2robot.urdf",
             "joints": [{"name": "j1", "station": "Arm:1", "occurrence": "Arm:1+Motor:2", "can_id": joint_can, "bus": None},
                        {"name": "j2", "station": "Arm:1", "occurrence": "Arm:1+Motor:3", "can_id": None, "bus": None}],
             "cameras": [{"name": "cam", "link": "cam_link", "serial": camera_serial}]}
    urdf = """<robot name="demo">
  <link name="base"><visual><geometry><mesh filename="meshes/Arm_1__Motor_2__0.stl"/></geometry></visual>
    <visual><geometry><mesh filename="meshes/Arm_1__Motor_3__0.stl"/></geometry></visual></link>
  <link name="arm"><visual><geometry><box size="1 1 1"/></geometry></visual></link>
  <link name="arm2"><visual><geometry><box size="1 1 1"/></geometry></visual></link>
  <link name="cam_link"><visual><geometry><box size="1 1 1"/></geometry></visual></link>
  <joint name="j1" type="revolute"><parent link="base"/><child link="arm"/></joint>
  <joint name="j2" type="revolute"><parent link="base"/><child link="arm2"/></joint>
  <joint name="c" type="fixed"><parent link="base"/><child link="cam_link"/></joint>
</robot>"""
    return {"demo/robot.json": json.dumps(robot).encode(), "demo/urdf/demo.cad2robot.urdf": urdf.encode(), "demo/urdf/demo.urdf": urdf.encode(),
            "demo/urdf/meshes/Arm_1__Motor_2__0.stl": STL, "demo/urdf/meshes/Arm_1__Motor_3__0.stl": STL}


def test_a_cad2robot_bundle_brings_its_own_mappings(bare):
    status, answer = upload(bare, bundle())
    assert status == 200 and answer["mapped"] == 2 and answer["schema"] == "cad2robot/1"
    now = describe(bare)
    assert now["urdf"].endswith("demo/urdf/demo.cad2robot.urdf")           # the one robot.json names
    parts = {part["name"]: part for part in now["parts"]}
    joint = parts["CAN 12 · j1"]
    assert joint["links"] == ["arm"] and joint["match"] == {"can_id": 12} and joint["meshes"] == ["Arm_1__Motor_2__*"]
    assert "robot.json" in joint["note"]
    camera = parts["cam 219323070276"]
    assert camera["links"] == ["cam_link"] and camera["match"] == {"serial": "219323070276"} and camera["path"] == "host/usb6/port-6-1/usb-6-1"


def test_a_bundle_says_which_joints_it_could_not_map(bare):
    status, answer = upload(bare, bundle(joint_can=None, camera_serial=None))
    assert status == 200 and answer["mapped"] == 0
    assert any("j1" in line and "can_id" in line for line in answer["warnings"])


def test_a_folder_bundle_is_read_the_same(bare):
    status, answer = post(bare, "/api/v1/robot-description/upload", *multipart(bundle()))
    assert status == 200 and answer["mapped"] == 2


# ----- the rack's own description ---------------------------------------------------------------------------------------
RACK_URDF = Path("/home/bam/.cache/cad2robot-build/bundles/a7b1095/fixtures/cad2robot/one_dof_test_rack/urdf/one_dof_test_rack.urdf")


@pytest.mark.skipif(not RACK_URDF.is_file(), reason="the rack's bundle is not on this machine")
def test_the_example_rack_description_loads_with_no_problem_and_no_guess():
    file = ROOT / "examples" / "one-dof-test-rack" / rd.DESCRIPTION_FILE
    description = rd.load_file(file)
    assert description.problem is None
    answer = rd.describe(description, None)
    assert [part["problem"] for part in answer["parts"] if part["problem"]] == []
    boards = {part.match.get("node"): part for part in description.parts if part.match.get("node")}
    assert set(boards) == {"can/1", "can/4", "can/11", "can/12", "can/13", "can/14", "can/15", "can/21"}
    assert "one_dof_arm" in boards["can/11"].links and "one_dof_arm" not in boards["can/12"].links    # robot.json's can_id 12 is stale
    assert all(part.note for part in description.parts)                     # every mapping names its proof


def test_docs_name_every_new_endpoint():
    doc = (ROOT / "docs" / "api-v1.md").read_text()
    for endpoint in ("upload", "assign", "activate", "remove"):
        assert f"/api/v1/robot-description/{endpoint}" in doc

