"""``GET /api/v1/live`` (docs/api-v1.md "Live"): the change-only Server-Sent Events feed that replaces the page's 1 s ``/state`` poll.

Over a real server on a fixture host (F1 through the real plugin runner, a fixed clock) and a stub host for the cases a fixture
cannot make (a row going away, a clock that runs). The rules: the first event is a ``snapshot`` equal to ``/state``; then only
what changed (``upsert`` for a changed row and nothing for an unchanged one, ``remove``, ``samples``); every event has a rising
``id`` and ``Last-Event-ID`` replays only what a client missed, or a fresh snapshot when the ring no longer holds it; and a client that
leaves frees its place, with no thread left behind.
"""

from __future__ import annotations

import copy
import dataclasses
import http.client
import json
import queue
import socket
import threading
import time
from urllib.parse import urlparse

import pytest

from io_diagnostics import live, server
from io_diagnostics.host import make_host
from io_diagnostics.live import LiveFeed, rows_of
from io_diagnostics.server import make_server, serve_in_thread

MOTOR = "host/usb1/port-1-3/usb-1-3/can/1/motor"
MOTOR_CHECK = "CAN 1 motor: mapping"


class Stream:
    """One SSE connection read by its own thread: ``event()`` is the next whole event as ``{"id", "event", "data"}`` (data parsed),
    ``None`` once the server has closed the stream; comment lines (keepalives) collect in ``comments``."""

    def __init__(self, host, headers: dict | None = None, path: str = "/api/v1/live"):
        self.connection = http.client.HTTPConnection(urlparse(host.url).netloc, timeout=10)
        self.connection.request("GET", path, headers=headers or {})
        self.sock = self.connection.sock  # getresponse() lets go of it for a response with no length
        self.response = self.connection.getresponse()
        self.comments: list[str] = []
        self.queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        out: dict = {}
        try:
            while True:
                line = self.response.readline()
                if not line:
                    break
                text = line.decode().rstrip("\n")
                if text == "":
                    if out.get("event"):
                        self.queue.put(out)
                    out = {}
                elif text.startswith(":"):
                    self.comments.append(text)
                else:
                    name, _, value = text.partition(": ")
                    out[name] = json.loads(value) if name == "data" else value
        except (OSError, ValueError):
            pass
        self.queue.put(None)

    def event(self, timeout: float = 10.0) -> dict | None:
        return self.queue.get(timeout=timeout)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # a close() alone leaves the reader thread's file holding the connection open
        except OSError:
            pass
        self.connection.close()


def until_idle(stream: Stream, quiet_s: float = 0.6) -> list[dict]:
    """Every event that arrives until the stream has been quiet for ``quiet_s``."""

    out = []
    while True:
        try:
            found = stream.event(quiet_s)
        except queue.Empty:
            return out
        if found is None:
            return out
        out.append(found)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(server, "LIVE_POLL_S", 0.1)
    monkeypatch.setattr(live, "COALESCE_S", 0.02)
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    host.feed.idle_grace_s = 0.4
    http_server = make_server(host, 0)
    serve_in_thread(http_server)
    streams: list[Stream] = []

    def open_stream(headers: dict | None = None) -> Stream:
        streams.append(Stream(host, headers))
        return streams[-1]

    host.open_stream = open_stream
    yield host
    for stream in streams:
        stream.close()
    http_server.shutdown()
    http_server.server_close()
    host.stop()


def state_json(host) -> dict:
    connection = http.client.HTTPConnection(urlparse(host.url).netloc, timeout=10)
    connection.request("GET", "/api/v1/state")
    return json.loads(connection.getresponse().read())


def change_motor_message(host, message: str) -> None:
    """The plugin's cache says something else about one leaf (an OK check, so no parent's roll-up changes), then the host's tick."""

    with host.lock:
        for job in host.jobs.values():
            for key, status in job.cache.statuses.items():
                if status.name == MOTOR_CHECK:
                    job.cache.statuses[key] = dataclasses.replace(status, message=message)
    host.notify("plugin")


def add_point(host, value: float):
    """A new State point on one series, as a plugin run appends it (the cache's own numbering)."""

    with host.lock:
        for job in host.jobs.values():
            if job.cache.series:
                key = next(iter(job.cache.series))
                job.cache.serial += 1
                job.cache.series[key].append((time.time(), value, job.cache.serial))
                return key
    raise AssertionError("no series on this host")


def feed_threads(host) -> list[threading.Thread]:
    """The live threads running THIS host's feed (another test's host may have its own, still in its idle grace)."""

    return [thread for thread in threading.enumerate() if thread.is_alive() and getattr(thread, "_target", None) == host.feed._run]


# ----- the first event -------------------------------------------------------------------------------------------------
def test_the_first_event_is_a_snapshot_equal_to_state(rig):
    stream = rig.open_stream()
    assert stream.response.status == 200 and stream.response.headers["Content-Type"].startswith("text/event-stream")
    assert stream.response.headers["Cache-Control"] == "no-store"
    first = stream.event()
    assert first["event"] == "snapshot" and first["data"] == state_json(rig) and int(first["id"]) >= 1
    assert until_idle(stream) == []  # nothing changed: nothing is sent


# ----- only changes ------------------------------------------------------------------------------------------------------
def test_a_state_change_is_one_upsert_for_the_changed_row_and_nothing_for_the_rest(rig):
    stream = rig.open_stream()
    snapshot = stream.event()
    before = rows_of(snapshot["data"])
    change_motor_message(rig, "mapping read again · 10:1")
    after = rows_of(state_json(rig))
    differing = {key for key in after if after[key] != before.get(key)}
    assert differing == {("tree", MOTOR)}  # an independent expectation: /state itself says this one row differs
    got = until_idle(stream)
    assert [(event["event"], event["data"]["table"], event["data"]["id"]) for event in got] == [("upsert", "tree", MOTOR)]
    row = got[0]["data"]["row"]
    assert row["message"] == "mapping read again · 10:1" and row["children"] == after[("tree", MOTOR)]["children"] == []
    assert row == after[("tree", MOTOR)]
    ids = [int(snapshot["id"]), int(got[0]["id"])]
    assert ids[1] > ids[0]
    # a mirror built from the snapshot and the events is /state again
    mirror = {**before, ("tree", MOTOR): row}
    assert mirror == after


def test_the_same_change_again_sends_nothing(rig):
    stream = rig.open_stream()
    stream.event()
    change_motor_message(rig, "mapping read again · 10:1")
    assert len(until_idle(stream)) == 1
    change_motor_message(rig, "mapping read again · 10:1")  # a tick with no difference
    rig.notify("refresh")
    assert until_idle(stream) == []


def test_a_row_that_leaves_is_a_remove_and_a_clock_that_runs_is_not_a_change():
    class Stub:
        def __init__(self):
            self.changes, self.revision, self._stop = threading.Condition(), 0, threading.Event()
            self.now = 100.0
            self.state_now = self.make_state(["a", "b"])

        def make_state(self, children):
            def node(path, kids=()):
                return {"path": path, "name": path, "level": "ok", "age_s": self.now - 90.0, "presence": {"state": "heard", "age_s": self.now - 90.0, "text": ""},
                        "last_received": 90.0, "children": list(kids)}
            return {"api": "io-diagnostics/1", "clock": {"now": self.now, "iso": f"t{self.now}", "time": f"{self.now}", "mode": "live"},
                    "summary": {"level": "ok", "n": len(children)}, "tree": node("host", [node(f"host/{name}") for name in children])}

        def state(self):
            return copy.deepcopy(self.state_now)

        def series_paths(self):
            return []

        def values_json(self, path, since=0):
            return None

    host = Stub()
    feed = LiveFeed(host)
    feed.state = host.state()
    feed.signatures = {key: live._masked(*key, row) for key, row in rows_of(feed.state).items()}
    feed.counter = feed.floor = 1

    def tick(children, now):
        host.now = now
        host.state_now = host.make_state(children)
        before = feed.counter
        feed._tick()
        return [(name, json.loads(data.split(b"data: ", 1)[1])) for number, _t, data in feed.ring
                for name in [data.split(b"event: ", 1)[1].split(b"\n", 1)[0].decode()] if number > before]

    assert tick(["a", "b"], 105.0) == []  # five seconds on: every age_s and the clock moved, nothing a page draws did
    got = tick(["a"], 106.0)
    assert [(name, data["table"], data["id"]) for name, data in got] == [("remove", "tree", "host/b"), ("upsert", "tree", "host"), ("upsert", "state", "summary")]
    assert got[0][1] == {"table": "tree", "id": "host/b"} and got[1][1]["row"]["children"] == ["host/a"]
    assert got[1][1]["row"]["age_s"] == 16.0  # a row that is sent carries the age it has now
    # a change to anything else in the clock section is a change
    host.state_now = host.make_state(["a"])
    host.state_now["clock"]["mode"] = "fixture"
    before = feed.counter
    feed._tick()
    assert [data.split(b"event: ", 1)[1].split(b"\n", 1)[0] for number, _t, data in feed.ring if number > before] == [b"upsert"]


def test_samples_arrive_with_the_values_shape(rig):
    stream = rig.open_stream()
    stream.event()
    key = add_point(rig, 41.5)
    rig.notify("plugin")
    got = until_idle(stream)
    assert [event["event"] for event in got] == ["samples"]
    path = f"host/usb1/port-1-3/usb-1-3/{key[0]}" if key[0] else "host/usb1/port-1-3/usb-1-3"
    data = got[0]["data"]
    assert data["path"] == path and set(data) == set(rig.values_json(path)) and data["cursor"] >= 1
    sent = [(group["name"], row["key"], row["points"]) for group in data["groups"] for row in group["rows"]]
    assert sent and all(len(points) == 1 and points[0][1] == 41.5 for _g, _k, points in sent)  # only what is new, nothing already held
    assert sent[0][:2] == (key[1], key[2])
    assert until_idle(stream) == []  # and once


# ----- ids, resume ------------------------------------------------------------------------------------------------------
def test_ids_rise_and_last_event_id_replays_only_the_missed(rig):
    stream = rig.open_stream()
    snapshot = stream.event()
    change_motor_message(rig, "one")
    first = until_idle(stream)
    change_motor_message(rig, "two")
    second = until_idle(stream)
    change_motor_message(rig, "three")
    third = until_idle(stream)
    ids = [int(snapshot["id"])] + [int(event["id"]) for event in first + second + third]
    assert ids == sorted(set(ids)) and len(ids) == 4
    stream.close()
    again = rig.open_stream({"Last-Event-ID": str(first[0]["id"])})  # the client has everything up to "one"
    got = until_idle(again)
    assert [event["event"] for event in got] == ["upsert", "upsert"]
    assert [event["data"]["row"]["message"] for event in got] == ["two", "three"]
    assert [event["id"] for event in got] == [second[0]["id"], third[0]["id"]]
    current = rig.open_stream({"Last-Event-ID": str(third[0]["id"])})
    assert until_idle(current) == []  # up to date: no snapshot, nothing replayed


def test_an_id_older_than_the_ring_gets_a_fresh_snapshot(rig):
    rig.feed.ring_events = 2
    stream = rig.open_stream()
    snapshot = stream.event()
    seen = []
    for number in range(4):
        change_motor_message(rig, f"m{number}")
        seen += until_idle(stream)
    assert len(seen) == 4 and len(rig.feed.ring) == 2
    stale = rig.open_stream({"Last-Event-ID": str(snapshot["id"])})  # the first event it knows fell out of the ring
    first = stale.event()
    assert first["event"] == "snapshot" and first["data"]["tree"]["path"] == "host" and int(first["id"]) == int(seen[-1]["id"])
    assert rows_of(first["data"])[("tree", MOTOR)]["message"] == "m3"
    ok = rig.open_stream({"Last-Event-ID": str(seen[1]["id"])})  # two events back is still in the ring
    assert [event["data"]["row"]["message"] for event in until_idle(ok)] == ["m2", "m3"]


@pytest.mark.parametrize("sent", ["0", "-5", "abc", "", "99999999", "1.5"])
def test_an_id_the_feed_never_gave_gets_a_snapshot(rig, sent):
    stream = rig.open_stream({"Last-Event-ID": sent})
    assert stream.event()["event"] == "snapshot"


def test_an_id_from_before_the_feed_last_started_gets_a_snapshot(rig):
    stream = rig.open_stream()
    old = stream.event()["id"]
    stream.close()
    deadline = time.monotonic() + 5
    while feed_threads(rig) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not feed_threads(rig)  # nobody listened for longer than the grace: the feed stopped
    change_motor_message(rig, "while nobody listened")  # a change no ring holds
    back = rig.open_stream({"Last-Event-ID": old})
    first = back.event()
    assert first["event"] == "snapshot" and int(first["id"]) > int(old)
    assert rows_of(first["data"])[("tree", MOTOR)]["message"] == "while nobody listened"


# ----- the one thread, clients leaving ----------------------------------------------------------------------------------
def test_clients_share_one_thread_and_a_disconnect_frees_its_place(rig):
    one, two, three = rig.open_stream(), rig.open_stream(), rig.open_stream()
    for stream in (one, two, three):
        assert stream.event()["event"] == "snapshot"
    assert len(rig.feed.subscribers) == 3 and len(feed_threads(rig)) == 1
    change_motor_message(rig, "all of them")
    assert [len(until_idle(stream)) for stream in (one, two, three)] == [1, 1, 1]
    two.close()
    deadline = time.monotonic() + 5
    while len(rig.feed.subscribers) != 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(rig.feed.subscribers) == 2 and feed_threads(rig)  # a quiet stream's leaver is noticed with no write
    one.close()
    three.close()
    deadline = time.monotonic() + 5
    while (rig.feed.subscribers or feed_threads(rig)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not rig.feed.subscribers and not feed_threads(rig)


def test_a_returning_client_within_the_grace_keeps_the_thread(rig):
    rig.feed.idle_grace_s = 30.0
    first = rig.open_stream()
    snapshot = first.event()
    first.close()
    time.sleep(0.4)
    change_motor_message(rig, "while away")
    back = rig.open_stream({"Last-Event-ID": snapshot["id"]})
    got = until_idle(back)
    assert [event["event"] for event in got] == ["upsert"] and got[0]["data"]["row"]["message"] == "while away"  # resumed, not a snapshot
    assert len(feed_threads(rig)) == 1


def test_a_quiet_stream_sends_a_heartbeat_comment(rig, monkeypatch):
    monkeypatch.setattr(server, "LIVE_KEEPALIVE_S", 0.3)
    stream = rig.open_stream()
    stream.event()
    deadline = time.monotonic() + 3
    while not stream.comments and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stream.comments[:1] == [": keepalive"]


def test_the_stream_ends_when_the_host_stops(rig):
    stream = rig.open_stream()
    stream.event()
    rig.stop()
    assert stream.event(5) is None
    deadline = time.monotonic() + 5
    while feed_threads(rig) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not feed_threads(rig) and not rig.feed.subscribers
    late = http.client.HTTPConnection(urlparse(rig.url).netloc, timeout=5)
    late.request("GET", "/api/v1/live")
    assert late.getresponse().status == 503  # a stopped host takes no new client
