"""Serving the web root's directories: a path that ends in ``/`` answers that directory's ``index.html`` (``/dx/`` for the data-ui page),
``.base`` files are plain text, and nothing outside the web root is ever served. A temporary web dir stands in for ``io_diagnostics/web``
(``web/dx`` is not on main), swapped in as ``server.WEB`` for one host over fixture F1; no hardware."""

from __future__ import annotations

import http.client
from urllib.parse import urlparse

import pytest

from io_diagnostics import server
from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread


@pytest.fixture()
def web(tmp_path, monkeypatch):
    root = tmp_path / "web"
    (root / "dx" / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "index.html").write_text("<title>root</title>")
    (root / "dx" / "index.html").write_text("<title>dx</title>")
    (root / "dx" / "sub" / "index.html").write_text("<title>sub</title>")
    (root / "dx" / "boards.base").write_text("filters:\n  and: []\n")
    (root / "dx" / "app.js").write_text("export {}\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("SECRET")
    (tmp_path / "secret.txt").write_text("SECRET")
    (tmp_path / "index.html").write_text("SECRET")  # the directory ABOVE the root has an index too: "/../" must not find it
    (root / "link").symlink_to(outside, target_is_directory=True)  # a directory inside the root that points out of it
    monkeypatch.setattr(server, "WEB", root)
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    http_server = make_server(host, 0)
    serve_in_thread(http_server)
    host.netloc = urlparse(host.url).netloc
    yield host
    http_server.shutdown()
    http_server.server_close()
    host.stop()


def fetch(host, path: str) -> tuple[int, str, bytes]:
    """A raw GET (``http.client`` sends the path as written: no dot-segment clean-up on the way)."""

    connection = http.client.HTTPConnection(host.netloc, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.headers.get("Content-Type", ""), response.read()
    finally:
        connection.close()


def test_a_path_ending_in_a_slash_serves_that_directorys_index(web):
    assert fetch(web, "/") == (200, "text/html; charset=utf-8", b"<title>root</title>")  # unchanged
    assert fetch(web, "/dx/") == (200, "text/html; charset=utf-8", b"<title>dx</title>")
    assert fetch(web, "/dx/sub/") == (200, "text/html; charset=utf-8", b"<title>sub</title>")
    assert fetch(web, "/dx/index.html")[2] == b"<title>dx</title>"  # the file by name still works
    assert fetch(web, "/dx/app.js")[1] == "text/javascript; charset=utf-8"


def test_a_directory_without_an_index_or_without_a_slash_is_not_found(web):
    assert fetch(web, "/empty/")[0] == 404
    assert fetch(web, "/nowhere/")[0] == 404
    assert fetch(web, "/dx")[0] == 404  # only a trailing slash maps to an index: no redirect, no guess
    assert fetch(web, "/dx/app.js/")[0] == 404  # a file is not a directory


def test_base_files_are_plain_text(web):
    status, kind, body = fetch(web, "/dx/boards.base")
    assert (status, kind, body) == (200, "text/plain; charset=utf-8", b"filters:\n  and: []\n")


@pytest.mark.parametrize("path", [
    "/../outside/", "/../outside/index.html", "/../secret.txt", "/dx/../../outside/", "/dx/../../secret.txt", "/dx/sub/../../../outside/",
    "/%2e%2e/outside/", "/%2e%2e/secret.txt", "/dx/%2e%2e/%2e%2e/outside/", "/dx/..%2f..%2foutside/", "/dx/%2e%2e%2f%2e%2e%2foutside/",
    "/..", "/../", "/dx/../..", "/dx/../../", "/link/", "/link/index.html", "/dx/../link/",
    "/%2flink/", "/.%2e/outside/", "/..%5coutside/", "/dx/..%5c..%5coutside/"])
def test_nothing_outside_the_web_root_is_served(web, path):
    status, _kind, body = fetch(web, path)
    assert status == 404 and b"SECRET" not in body, path


@pytest.mark.parametrize("path", ["//outside/", "//../outside/", "//outside/index.html", "//dx/", "/dx/../"])
def test_odd_spellings_never_serve_anything_outside_either(web, path):
    """``//x/`` reads as a host name to ``urlparse`` and answers what ``/`` does (as before this change); ``/dx/../`` stays inside the root."""

    status, _kind, body = fetch(web, path)
    assert b"SECRET" not in body and status in (200, 404), path
