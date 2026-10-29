"""A cold entity's slow snapshot must not block the independent watched-samples lane."""
import json
import threading

from io_diagnostics.live import LiveFeed


def answer(path, cursor):
    return {"path": path, "cursor": cursor, "groups": [{"name": "motor", "rows": [
        {"key": "position", "points": [[cursor, cursor]]}]}]}


class ColdHost:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def state(self):
        return {"tree": {"path": "host", "children": []}}

    def series_paths(self):
        return ["cold", "hot"]

    def watched_paths(self):
        return ["hot"]

    def values_json(self, path, since):
        if path == "cold":
            self.entered.set()
            assert self.release.wait(3), "test did not release the cold snapshot"
        return answer(path, 1) if since < 1 else answer(path, since) | {"groups": []}


def events(feed):
    return [json.loads(frame.split(b"data: ", 1)[1]) for _, _, frame in feed.ring]


def test_cold_snapshot_does_not_starve_watched_samples():
    host = ColdHost()
    feed = LiveFeed(host)
    tick = threading.Thread(target=feed._tick)
    sent = threading.Event()
    sender = threading.Thread(target=lambda: (feed._send_watched({}), sent.set()))
    tick.start()
    try:
        assert host.entered.wait(1)
        sender.start()
        assert sent.wait(1), "watched samples blocked behind a cold entity's snapshot"
        assert any(event.get("path") == "hot" for event in events(feed))
    finally:
        host.release.set()
        tick.join(3)
        if sender.ident is not None:
            sender.join(3)
    assert not tick.is_alive() and not sender.is_alive()
    assert [event.get("path") for event in events(feed)].count("hot") == 1


def test_late_snapshot_cannot_rewind_a_cursor_or_duplicate_points():
    host = ColdHost()
    host.series_paths = lambda: ["hot"]
    calls = 0

    def read(path, since):
        nonlocal calls
        calls += 1
        if calls == 1:
            host.entered.set()
            assert host.release.wait(3)
            return answer(path, 1)
        return answer(path, 2)

    host.values_json = read
    feed = LiveFeed(host)
    tick = threading.Thread(target=feed._tick)
    sender = threading.Thread(target=lambda: feed._send_watched({}))
    tick.start()
    try:
        assert host.entered.wait(1)
        sender.start()
        sender.join(1)
        assert not sender.is_alive(), "snapshot still owns the samples lock during the read"
    finally:
        host.release.set()
        tick.join(3)
        if sender.ident is not None:
            sender.join(3)
    assert feed.cursors["hot"] == 2
    assert [event["cursor"] for event in events(feed) if event.get("path") == "hot"] == [2]


def test_cursor_advanced_after_path_listing_survives_cleanup():
    host = ColdHost()
    host.series_paths = lambda: ["cold"]
    original_read = host.values_json
    host.values_json = lambda path, since: answer(path, 2) if path == "hot" else original_read(path, since)
    feed = LiveFeed(host)
    feed.cursors["hot"] = 1
    tick = threading.Thread(target=feed._tick)
    tick.start()
    try:
        assert host.entered.wait(1)
        feed._send_watched({})
    finally:
        host.release.set()
        tick.join(3)
    assert feed.cursors.get("hot") == 2, "cleanup discarded a cursor advanced after the path listing"
