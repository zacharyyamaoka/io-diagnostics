"""The Stable / Preview chip's three duties on this server (release_channels.py, docs/release-channels.md), over a real
server and a stand-in ``sps.py`` that echoes what it was asked. The real runtime end to end is the scratch-clone proof
in docs/release-channels.md; this pins the server's side of it: nothing without the runtime, all three under it."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from test_host_support import FakeOs, SetClock, standard_skeleton

from io_diagnostics.host import Host
from io_diagnostics.server import make_server, serve_in_thread

ECHO_RUNTIME = r'''
import json, os, sys
print("a log line the runtime printed first")
print(json.dumps({"status": 202, "body": {"argv": sys.argv[1:], "stdin": sys.stdin.read(),
                                          "headers": json.loads(os.environ.get("SPS_API_HEADERS", "{}"))}}))
'''
PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    for name in ("SPS_CHANNEL", "SPS_RUNTIME", "SPS_CHECKOUT", "SPS_CHIP_POSITION", "SPS_CHIP_AVOID",
                 "IO_DIAGNOSTICS_PAGE_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    script = tmp_path / "runtime" / "sps.py"
    (script.parent / "stable_preview_switcher" / "assets").mkdir(parents=True)
    script.write_text(ECHO_RUNTIME)
    (script.parent / "stable_preview_switcher" / "assets" / "chip.js").write_text("export const chip = 1\n")
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton()))
    http = make_server(host, 0)
    serve_in_thread(http)

    def under_runtime(channel: str = "preview", **env: str) -> None:
        monkeypatch.setenv("SPS_CHANNEL", channel)
        monkeypatch.setenv("SPS_RUNTIME", str(script))
        monkeypatch.setenv("SPS_CHECKOUT", str(tmp_path))
        for name, value in env.items():
            monkeypatch.setenv(name, value)

    yield {"url": host.url, "port": http.server_address[1], "checkout": tmp_path, "under_runtime": under_runtime}
    http.shutdown()


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_without_the_runtime_there_is_no_chip_and_no_sps(server):
    status, _, page = fetch(server["url"])
    assert status == 200 and b"data-sps-chip" not in page
    assert fetch(server["url"] + "__sps/status")[0] == 404
    assert fetch(server["url"] + "__sps/chip.js")[0] == 404
    assert fetch(server["url"] + "__sps/promote", data=b"{}", headers={"Content-Type": "application/json"})[0] == 404


def test_stable_is_the_runtimes_own_server_so_this_one_adds_nothing(server):
    server["under_runtime"]("stable")
    assert b"data-sps-chip" not in fetch(server["url"])[2]
    assert fetch(server["url"] + "__sps/status")[0] == 404


def test_preview_tags_the_page_once_before_its_head_ends(server):
    server["under_runtime"]("preview", SPS_CHIP_POSITION="top-left", SPS_CHIP_AVOID='[".bar[x=\'1\']"]')
    page = fetch(server["url"])[2]
    tag = (b'<script type="module" src="/__sps/chip.js" data-sps-chip data-position="top-left"'
           b' data-avoid="[&quot;.bar[x=&#39;1&#39;]&quot;]"></script>')
    assert page.count(b"data-sps-chip") == 1 and tag + b"</head>" in page
    assert b"data-sps-chip" not in fetch(server["url"] + "app.js")[2]  # only pages
    server["under_runtime"]("dev", SPS_CHIP_POSITION="nowhere", SPS_CHIP_AVOID="not json")
    assert b'data-position="bottom-right"></script></head>' in fetch(server["url"] + "index.html")[2]


def test_the_chip_bundle_is_the_runtimes_own(server):
    server["under_runtime"]()
    status, headers, body = fetch(server["url"] + "__sps/chip.js")
    assert (status, headers["Content-Type"], body) == (200, "text/javascript", b"export const chip = 1\n")


def test_sps_requests_go_to_the_runtimes_api_with_their_body_and_headers(server):
    server["under_runtime"]("dev")
    origin = f"http://127.0.0.1:{server['port']}"
    status, _, body = fetch(server["url"] + "__sps/promote?x=1", data=b'{"go": true}',
                            headers={"Content-Type": "application/json", "Origin": origin, "Cookie": "never-forwarded"})
    answer = json.loads(body)
    assert status == 202
    assert answer["argv"] == ["api", "POST", "/__sps/promote?x=1", "--channel", "dev", "--checkout", str(server["checkout"]),
                              "--header", f"Origin={origin}", "--header", "Content-Type=application/json",
                              "--header", f"Host=127.0.0.1:{server['port']}"]
    assert answer["stdin"] == '{"go": true}'
    assert answer["headers"] == {"Origin": origin, "Content-Type": "application/json", "Host": f"127.0.0.1:{server['port']}"}
    status, _, body = fetch(server["url"] + "__sps/status")
    assert status == 202 and json.loads(body)["argv"][1:3] == ["GET", "/__sps/status"]


def test_a_runtime_without_an_answer_is_a_502_with_its_log(server, tmp_path):
    server["under_runtime"]()
    (tmp_path / "runtime" / "sps.py").write_text("import sys\nprint('boom', file=sys.stderr)\nraise SystemExit(3)\n")
    status, _, body = fetch(server["url"] + "__sps/status")
    assert status == 502 and json.loads(body) == {"error": "sps api exited 3 without an answer", "log": ["boom"]}


def test_a_page_write_through_the_stable_proxy_is_from_the_page(server, monkeypatch):
    """On Stable the page is 127.0.0.1:8820's and its /api/ reaches this process with Host rewritten to this port."""

    body = json.dumps({"window": "15m"}).encode()
    proxied = {**PAGE, "Origin": "http://127.0.0.1:8820"}
    status, _, answer = fetch(server["url"] + "api/v1/settings", data=body, headers=proxied)
    assert (status, json.loads(answer)["refused"]) == (403, "NotSameOrigin")
    monkeypatch.setenv("IO_DIAGNOSTICS_PAGE_ORIGINS", "http://127.0.0.1:8820 http://localhost:8820")
    status, _, answer = fetch(server["url"] + "api/v1/settings", data=body, headers=proxied)
    assert (status, json.loads(answer)["refused"]) == (400, "BadSetting")  # past the page gate (no file is written)
    status, _, answer = fetch(server["url"] + "api/v1/settings", data=body, headers={**PAGE, "Origin": "http://evil.example"})
    assert (status, json.loads(answer)["refused"]) == (403, "NotSameOrigin")
