"""Dashboards as files, ``GET /api/v1/dashboards`` and ``GET | PUT | DELETE /api/v1/dashboards/<name>.base``
(docs/api-v1.md "Dashboards as files"), over a real server on a fixture host and a temp config directory.

The rules: a ``.base`` is opaque text kept under ``<config dir>/dashboards/``; the name cannot leave that folder; a body is at
most 1 MB of UTF-8; ``If-Match`` stops one tab overwriting another; a write is atomic; a delete moves the file to ``.trash/``;
and a write is the page's own request (same origin, the page's header), like every other write of this server.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
from urllib.parse import urlparse

import pytest

from io_diagnostics import dashboards
from io_diagnostics.host import make_host
from io_diagnostics.server import NO_ACTIONS, make_server, serve_in_thread

PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "text/plain; charset=utf-8"}
BASE = "views:\n  - type: table\n    name: Servos\n"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(config))
    host = make_host(fixture="F1", once=True)
    http = make_server(host, 0)
    serve_in_thread(http)
    yield {"host_object": host, "url": host.url, "host": urlparse(host.url).netloc, "folder": config / "dashboards", "config": config}
    http.shutdown()
    http.server_close()


def call(rig, method: str, path: str, body: bytes | str | None = None, headers: dict | None = None):
    """``(status, headers, body bytes)``; the path goes on the wire exactly as written (no client-side normalising)."""

    connection = http.client.HTTPConnection(rig["host"], timeout=10)
    try:
        data = body.encode() if isinstance(body, str) else body
        connection.request(method, path, body=data, headers=PAGE if headers is None else headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def put(rig, name: str, body: bytes | str = BASE, **headers):
    return call(rig, "PUT", f"/api/v1/dashboards/{name}", body, {**PAGE, **headers})


def as_json(raw: bytes) -> dict:
    return json.loads(raw)


def test_round_trip_list_and_the_raw_text_back(rig):
    status, headers, raw = call(rig, "GET", "/api/v1/dashboards")
    assert status == 200 and as_json(raw) == {"files": []}  # a read creates nothing
    assert not rig["folder"].exists()
    status, headers, raw = put(rig, "Servos.base")
    created = as_json(raw)
    etag = hashlib.sha256(BASE.encode()).hexdigest()
    assert status == 201 and created["ok"] is True and created["etag"] == etag and headers["ETag"] == f'"{etag}"'
    assert (rig["folder"] / "Servos.base").read_text() == BASE
    status, headers, raw = call(rig, "GET", "/api/v1/dashboards/Servos.base", headers={})
    assert status == 200 and raw.decode() == BASE and headers["ETag"] == f'"{etag}"'
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
    listed = as_json(call(rig, "GET", "/api/v1/dashboards")[2])
    assert list(listed) == ["files"] and len(listed["files"]) == 1
    entry = listed["files"][0]
    assert set(entry) == {"name", "file", "size", "mtime", "etag"}
    assert (entry["name"], entry["file"], entry["size"], entry["etag"]) == ("Servos", "Servos.base", len(BASE.encode()), etag)
    assert entry["mtime"] == (rig["folder"] / "Servos.base").stat().st_mtime
    # a replace is 200 and moves the etag; the text is kept byte for byte (no newline or BOM handling)
    changed = "a\r\nbé ☃\n\n"
    status, headers, raw = put(rig, "Servos.base", changed)
    assert status == 200 and as_json(raw)["etag"] == hashlib.sha256(changed.encode()).hexdigest() != etag
    assert (rig["folder"] / "Servos.base").read_bytes() == changed.encode()
    assert as_json(call(rig, "GET", "/api/v1/dashboards")[2])["files"][0]["size"] == len(changed.encode())


def test_a_write_is_atomic_and_leaves_no_temp_file(rig, monkeypatch):
    put(rig, "A.base")
    seen = []
    real = dashboards.os.replace

    def spy(source, target):
        seen.append((source, (rig["folder"] / "A.base").read_text()))  # at the replace, the old text is still whole
        return real(source, target)

    monkeypatch.setattr(dashboards.os, "replace", spy)
    assert put(rig, "A.base", "new\n")[0] == 200
    assert len(seen) == 1 and seen[0][1] == BASE and seen[0][0] != str(rig["folder"] / "A.base")
    assert sorted(path.name for path in rig["folder"].iterdir()) == ["A.base"]


def test_a_failed_write_keeps_the_old_file_and_cleans_up(rig, monkeypatch):
    put(rig, "A.base")

    def boom(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(dashboards.os, "replace", boom)
    status, _, _ = put(rig, "A.base", "new\n")
    assert status == 500
    assert (rig["folder"] / "A.base").read_text() == BASE
    assert sorted(path.name for path in rig["folder"].iterdir()) == ["A.base"]


def test_if_match_stops_a_stale_tab_from_overwriting(rig):
    first = as_json(put(rig, "A.base", "one")[2])["etag"]
    second = as_json(put(rig, "A.base", "two", **{"If-Match": f'"{first}"'})[2])  # right etag: written
    assert (rig["folder"] / "A.base").read_text() == "two"
    status, _, raw = put(rig, "A.base", "three", **{"If-Match": f'"{first}"'})  # the other tab still holds the first one
    refused = as_json(raw)
    assert status == 412 and refused["refused"] == "EtagMismatch" and refused["etag"] == second["etag"]
    assert (rig["folder"] / "A.base").read_text() == "two"
    assert put(rig, "A.base", "four", **{"If-Match": second["etag"]})[0] == 200  # a bare etag works as well
    assert put(rig, "A.base", "five", **{"If-Match": "*"})[0] == 200  # * is any existing file
    # nothing to match: an If-Match on a file that is not there is refused too (RFC 9110), not silently created
    assert put(rig, "New.base", "x", **{"If-Match": f'"{first}"'})[0] == 412 and not (rig["folder"] / "New.base").exists()
    assert put(rig, "New.base", "x", **{"If-Match": "*"})[0] == 412
    assert put(rig, "New.base", "x")[0] == 201  # no If-Match: last write wins, as documented


def test_if_none_match_creates_a_file_and_never_overwrites_one(rig):
    # Add to dashboard's "New dashboard…" (PEP 0001 §17) creates only: a name another tab or person took a moment ago is refused,
    # never written over (RFC 9110 §13.1.2: a failed If-None-Match on a PUT is 412)
    first = hashlib.sha256(b"first").hexdigest()
    status, headers, raw = put(rig, "Fresh.base", "first", **{"If-None-Match": "*"})
    assert status == 201 and as_json(raw)["etag"] == first and headers["ETag"] == f'"{first}"'
    status, _, raw = put(rig, "Fresh.base", "second", **{"If-None-Match": "*"})
    refused = as_json(raw)
    assert status == 412 and refused["refused"] == "Exists" and refused["etag"] == first
    assert (rig["folder"] / "Fresh.base").read_text() == "first"
    assert sorted(path.name for path in rig["folder"].iterdir()) == ["Fresh.base"]  # no temp file left behind
    # an etag list refuses the write only when it names the file as it is now
    assert put(rig, "Fresh.base", "third", **{"If-None-Match": f'"{first}", "other"'})[0] == 412
    assert put(rig, "Fresh.base", "third", **{"If-None-Match": '"other"'})[0] == 200
    assert (rig["folder"] / "Fresh.base").read_text() == "third"


def test_delete_moves_the_file_to_trash(rig):
    put(rig, "A.base", "keep me")
    stale = as_json(call(rig, "GET", "/api/v1/dashboards")[2])["files"][0]["etag"]
    put(rig, "A.base", "keep me v2")
    status, _, raw = call(rig, "DELETE", "/api/v1/dashboards/A.base", None, {**PAGE, "If-Match": f'"{stale}"'})
    assert status == 412 and (rig["folder"] / "A.base").exists()  # DELETE honours If-Match too
    status, _, raw = call(rig, "DELETE", "/api/v1/dashboards/A.base")
    done = as_json(raw)
    assert status == 200 and done["ok"] is True and done["trashed"].startswith(".trash/A.") and done["trashed"].endswith(".base")
    assert not (rig["folder"] / "A.base").exists()
    assert (rig["folder"] / done["trashed"]).read_text() == "keep me v2"  # moved, not removed
    assert call(rig, "GET", "/api/v1/dashboards/A.base")[0] == 404
    assert as_json(call(rig, "GET", "/api/v1/dashboards")[2]) == {"files": []}  # the trash is not a dashboard
    assert call(rig, "DELETE", "/api/v1/dashboards/A.base")[0] == 404
    # the same name deleted twice lands in the trash twice
    put(rig, "A.base", "again")
    second = as_json(call(rig, "DELETE", "/api/v1/dashboards/A.base")[2])["trashed"]
    assert second != done["trashed"] and sorted(path.name for path in (rig["folder"] / ".trash").iterdir()).__len__() == 2


@pytest.mark.parametrize("name", [
    "notes.txt", "A", ".base", ".hidden.base", "%20x.base", "-x.base", "_x.base", "x$.base", "x:y.base", "x\\y.base", "x%00.base",
    "x%0Ay.base", "caf%C3%A9.base", "a" * 81 + ".base", "x.base.txt", "%2e.base", "..x.base"])
def test_bad_names_are_refused_by_the_rule(rig, name):
    for method in ("GET", "PUT", "DELETE"):
        status, _, raw = call(rig, method, f"/api/v1/dashboards/{name}", BASE if method == "PUT" else None)
        assert status == 400 and as_json(raw)["refused"] == "BadName", (method, name)
    assert not rig["folder"].exists() or list(rig["folder"].iterdir()) == []


def test_the_name_rule_accepts_its_whole_alphabet(rig):
    for name in ["a", "A1", "My dashboard 2", "x_y-z.v2", "9", "a" * 80, "a.b.c"]:
        quoted = name.replace(" ", "%20")
        assert put(rig, quoted + ".base")[0] == 201, name
        assert call(rig, "GET", f"/api/v1/dashboards/{quoted}.base")[2].decode() == BASE
    assert {entry["name"] for entry in as_json(call(rig, "GET", "/api/v1/dashboards")[2])["files"]} \
        == {"a", "A1", "My dashboard 2", "x_y-z.v2", "9", "a" * 80, "a.b.c"}


@pytest.mark.parametrize("path", [
    "/api/v1/dashboards/../x.base", "/api/v1/dashboards/..%2fx.base", "/api/v1/dashboards/..%2Fx.base",
    "/api/v1/dashboards/%2e%2e/x.base", "/api/v1/dashboards/%2e%2e%2fx.base", "/api/v1/dashboards/%2E%2E%2Fx.base",
    "/api/v1/dashboards/sub/x.base", "/api/v1/dashboards/%2fetc%2fpasswd.base", "/api/v1/dashboards/..%5cx.base",
    "/api/v1/dashboards/%2e%2e", "/api/v1/dashboards/.."])
def test_path_traversal_is_refused_and_writes_nothing_outside(rig, path):
    outside = {path.name for path in rig["config"].parent.iterdir()}
    for method in ("GET", "PUT", "DELETE"):
        status, _, raw = call(rig, method, path, BASE if method == "PUT" else None)
        assert status == 400 and as_json(raw)["refused"] == "BadName", (method, path, status)
    assert {path.name for path in rig["config"].parent.iterdir()} == outside
    assert [path.name for path in rig["config"].iterdir()] in ([], ["dashboards"])
    assert not (rig["config"] / "x.base").exists() and not (rig["config"].parent / "x.base").exists()
    assert not rig["folder"].exists() or list(rig["folder"].iterdir()) == []


def test_a_link_in_the_folder_is_not_followed(rig, tmp_path):
    rig["folder"].mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (rig["folder"] / "Link.base").symlink_to(secret)
    assert call(rig, "GET", "/api/v1/dashboards/Link.base")[0] == 404
    assert as_json(call(rig, "GET", "/api/v1/dashboards")[2]) == {"files": []}
    assert call(rig, "DELETE", "/api/v1/dashboards/Link.base")[0] == 404
    assert secret.read_text() == "secret" and (rig["folder"] / "Link.base").is_symlink()


def test_the_size_cap_is_one_megabyte(rig):
    exactly = "x" * dashboards.MAX_BYTES
    assert put(rig, "Big.base", exactly)[0] == 201 and (rig["folder"] / "Big.base").stat().st_size == dashboards.MAX_BYTES
    status, _, raw = put(rig, "Big.base", exactly + "x")
    assert status == 413 and as_json(raw)["refused"] == "TooLarge"
    assert (rig["folder"] / "Big.base").stat().st_size == dashboards.MAX_BYTES  # untouched
    assert sorted(path.name for path in rig["folder"].iterdir()) == ["Big.base"]
    # a body that lies about its length is refused before it is read
    assert call(rig, "PUT", "/api/v1/dashboards/Huge.base", None, {**PAGE, "Content-Length": str(50 * 1024 * 1024)})[0] == 413
    assert not (rig["folder"] / "Huge.base").exists()


def test_a_body_must_be_utf8_text(rig):
    put(rig, "A.base", "good")
    for bad in (b"\xff\xfe\x00bad", b"caf\xe9", b"\xc3\x28", "ok".encode("utf-16")):
        status, _, raw = put(rig, "A.base", bad)
        assert status == 400 and as_json(raw)["refused"] == "NotUtf8", bad
    assert (rig["folder"] / "A.base").read_text() == "good"
    assert put(rig, "Empty.base", b"")[0] == 201 and (rig["folder"] / "Empty.base").read_bytes() == b""  # opaque: empty is fine
    # a file somebody put there by hand that is not UTF-8 is a 422 on read, never garbled text
    (rig["folder"] / "Hand.base").write_bytes(b"\xff\xfe")
    assert call(rig, "GET", "/api/v1/dashboards/Hand.base")[0] == 422


def test_a_put_without_a_length_is_refused(rig):
    with socket.create_connection((urlparse(rig["url"]).hostname, urlparse(rig["url"]).port), timeout=10) as sock:
        request = (f"PUT /api/v1/dashboards/A.base HTTP/1.1\r\nHost: {rig['host']}\r\nX-IO-Diagnostics: ui\r\n"
                   "Sec-Fetch-Site: same-origin\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nabcd\r\n0\r\n\r\n")
        sock.sendall(request.encode())
        assert sock.recv(4096).startswith(b"HTTP/1.0 411")
    assert not (rig["folder"] / "A.base").exists()


def test_only_the_page_may_write(rig):
    put(rig, "A.base")
    cases = {
        "no page header": {"Sec-Fetch-Site": "same-origin"},
        "another site": {"Sec-Fetch-Site": "cross-site"},
        "same site, other origin": {"Sec-Fetch-Site": "same-site"},
        "a command line (no fetch metadata)": {"Sec-Fetch-Site": ""},
        "another origin": {"Origin": "http://evil.example"},
        "the page's origin under another port": {"Origin": "http://127.0.0.1:9"},
    }
    for label, headers in cases.items():
        if label == "no page header":
            sent = {"Sec-Fetch-Site": "same-origin"}
        else:
            sent = {**PAGE, **headers}
        for method, body in (("PUT", "hijacked"), ("DELETE", None)):
            status, _, raw = call(rig, method, "/api/v1/dashboards/A.base", body, sent)
            assert status == 403 and as_json(raw)["refused"] in ("NotFromPage", "NotSameOrigin"), (label, method, status)
            assert (rig["folder"] / "A.base").read_text() == BASE, (label, method)
        assert as_json(call(rig, "PUT", "/api/v1/dashboards/B.base", "x", sent)[2])["refused"] in ("NotFromPage", "NotSameOrigin")
    assert not (rig["folder"] / "B.base").exists() and not (rig["folder"] / ".trash").exists()
    # the page's own request (same origin, with its Origin header) is accepted; reads need no header at all
    assert put(rig, "A.base", "mine", Origin=f"http://{rig['host']}")[0] == 200
    assert call(rig, "GET", "/api/v1/dashboards/A.base", None, {})[2] == b"mine"


def test_every_other_write_is_still_refused(rig):
    for method, path in [("PUT", "/api/v1/dashboards"), ("PUT", "/api/v1/dashboards/"), ("DELETE", "/api/v1/dashboards"),
                         ("POST", "/api/v1/dashboards"), ("POST", "/api/v1/dashboards/A.base"), ("PATCH", "/api/v1/dashboards/A.base"),
                         ("PUT", "/api/v1/state"), ("DELETE", "/api/v1/settings"), ("PUT", "/api/v1/act")]:
        status, _, raw = call(rig, method, path, "{}")
        assert status == 405 and as_json(raw)["error"] == NO_ACTIONS, (method, path)
    assert not rig["folder"].exists()


def test_a_host_without_a_config_dir_keeps_no_files(rig):
    rig["host_object"].dashboards = None  # a Host built by hand has no settings file, so no folder
    for method in ("GET", "PUT", "DELETE"):
        status, _, raw = call(rig, method, "/api/v1/dashboards/A.base", BASE if method == "PUT" else None)
        assert status == 503 and "no config directory" in as_json(raw)["error"]
    assert call(rig, "GET", "/api/v1/dashboards")[0] == 503
