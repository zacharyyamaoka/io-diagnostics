"""``GET /api/v1/live``: the page's 1 s ``/state`` poll as a change-only Server-Sent Events feed (docs/api-v1.md "Live").

One ``LiveFeed`` per host serves every client. Its single thread wakes on the host's own change tick (``Host.changes``, the
one ``/api/v1/events`` waits on), builds ``Host.state()`` ONCE, and diffs it against the last one by row:

* ``upsert``  a row whose JSON changed (a tree node by its ``path``, or one of ``/state``'s other top-level sections by its name),
* ``remove``  a row that is gone,
* ``samples`` State points that arrived since the last tick (what ``GET /api/v1/values?since=`` answers).

Every event gets the next number and sits, already serialized, in a ring (the last ``RING_EVENTS`` events and ``RING_SECONDS`` seconds),
so a client that reconnects with ``Last-Event-ID`` is sent only what it missed. A number older than the ring (or from before the
feed last started) gets a fresh ``snapshot`` instead.

WHY no thread per client and none while nobody listens: the diff is the cost, and it is the same for ten tabs as for one. The thread
starts with the first client and ends ``IDLE_GRACE_S`` after the last one has left (a quick reconnect finds the ring still whole).
"""

from __future__ import annotations

import collections
import json
import threading
import time

COALESCE_S = 0.1  # a burst of changes (a plug's ~30 udev blocks) is one diff, as on /api/v1/events
RING_EVENTS = 2000
RING_SECONDS = 60.0
IDLE_GRACE_S = 60.0
# WHY these fields are not compared: they are pure functions of the host clock and a row's own ``last_received``, so on a running
# clock EVERY row with records would change every tick (measured on F1: 52 of 61 rows in 1.2 s) and a change-only feed would send the
# whole tree each time. A row that is sent for any other reason carries the age it has at that moment; a page that needs the age NOW
# computes it from ``last_received`` and the clock (``clock.now`` as of the snapshot, plus the time since).
VOLATILE_ROW = ("age_s",)
VOLATILE_PRESENCE = ("age_s",)
VOLATILE_CLOCK = ("now", "iso", "time")


class FeedClosed(Exception):
    """The host stopped: the stream ends."""


def _dump(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _masked(table: str, ident: str, row) -> str:
    """What is compared of a row (``VOLATILE_*`` left out), as text."""

    if table == "tree":
        row = {key: value for key, value in row.items() if key not in VOLATILE_ROW}
        if isinstance(row.get("presence"), dict):
            row["presence"] = {key: value for key, value in row["presence"].items() if key not in VOLATILE_PRESENCE}
    elif ident == "clock" and isinstance(row, dict):
        row = {key: value for key, value in row.items() if key not in VOLATILE_CLOCK}
    return _dump(row)


def rows_of(state: dict) -> dict[tuple[str, str], object]:
    """``/state`` as keyed rows: ``("tree", path)`` for each node (its ``children`` a list of child paths), ``("state", name)`` for every
    other top-level section."""

    rows: dict[tuple[str, str], object] = {}
    stack = [state["tree"]]
    while stack:
        node = stack.pop()
        rows[("tree", node["path"])] = {**{key: value for key, value in node.items() if key != "children"},
                                        "children": [child["path"] for child in node["children"]]}
        stack.extend(reversed(node["children"]))  # parent first, then its children in order
    for name, value in state.items():
        if name != "tree":
            rows[("state", name)] = value
    return rows


def frame(event_id: int, name: str, data: str) -> bytes:
    return f"id: {event_id}\nevent: {name}\ndata: {data}\n\n".encode()


class Subscriber:
    def __init__(self, cursor: int):
        self.cursor = cursor  # the newest event number this client has been given


class LiveFeed:
    def __init__(self, host, *, idle_grace_s: float | None = None, ring_events: int = RING_EVENTS, ring_seconds: float = RING_SECONDS):
        self.host = host
        self.idle_grace_s = IDLE_GRACE_S if idle_grace_s is None else idle_grace_s
        self.ring_events, self.ring_seconds = ring_events, ring_seconds
        self.cond = threading.Condition()
        self.thread: threading.Thread | None = None
        self.subscribers: set[Subscriber] = set()
        self.counter = 0  # the number of the newest event
        self.floor = 0  # the oldest number a client may resume from: it has every event after it
        self.ring: collections.deque[tuple[int, float, bytes]] = collections.deque()
        self.state: dict | None = None  # /state as of the newest event number
        self.signatures: dict[tuple[str, str], str] = {}
        self.cursors: dict[str, int] = {}  # entity path -> the values cursor its last samples event ended at
        self.idle_since: float | None = None
        self.closed = False

    # --- clients ------------------------------------------------------------------------------------------------------
    def subscribe(self, last_event_id: str | None) -> tuple[Subscriber, list[bytes]]:
        """A client's place in the feed and its first frames: the events after ``last_event_id`` when the ring still holds them all, else
        a ``snapshot``."""

        with self.cond:
            if self.closed or self.host._stop.is_set():
                raise FeedClosed
            if self.thread is None:
                self._start()
            sub = Subscriber(self.counter)
            self.subscribers.add(sub)
            self.idle_since = None
            return sub, self._catch_up(sub, last_event_id)

    def unsubscribe(self, sub: Subscriber) -> None:
        with self.cond:
            self.subscribers.discard(sub)
            if not self.subscribers:
                self.idle_since = time.monotonic()

    def wait(self, sub: Subscriber, timeout: float) -> list[bytes]:
        """Frames newer than the client's cursor, waiting up to ``timeout``; ``[]`` on timeout (the caller sends a keepalive)."""

        with self.cond:
            self.cond.wait_for(lambda: self.counter > sub.cursor or self.closed, timeout=timeout)
            if self.closed:
                raise FeedClosed
            return self._catch_up(sub, str(sub.cursor)) if self.counter > sub.cursor else []

    def _catch_up(self, sub: Subscriber, last_event_id: str | None) -> list[bytes]:
        try:
            resume = int(last_event_id) if last_event_id is not None and last_event_id.strip() != "" else None
        except ValueError:
            resume = None
        if resume is None or not self.floor <= resume <= self.counter:
            sub.cursor = self.counter
            return [frame(self.counter, "snapshot", _dump(self.state))]
        sub.cursor = self.counter
        return [data for number, _stamp, data in self.ring if number > resume]

    # --- the one thread -----------------------------------------------------------------------------------------------
    def _start(self) -> None:
        """Called with ``cond`` held. A new baseline and a new epoch: whatever changed while nobody listened is not in the ring, so
        every number handed out before this point is no longer a place a client can resume from."""

        self.state = self.host.state()
        self.signatures = {key: _masked(*key, row) for key, row in rows_of(self.state).items()}
        answers = {path: self._values(path, 0) for path in self.host.series_paths()}
        self.cursors = {path: answer["cursor"] for path, answer in answers.items() if answer is not None}
        self.ring.clear()
        self.counter += 1
        self.floor = self.counter
        self.idle_since = None
        self.thread = threading.Thread(target=self._run, name="io-live", daemon=True)
        self.thread.start()

    def _values(self, path: str, since: int) -> dict | None:
        return self.host.values_json(path, since)

    def _run(self) -> None:
        host = self.host
        revision = host.revision
        poll = max(0.05, min(1.0, self.idle_grace_s / 2))
        try:
            while True:
                with host.changes:
                    host.changes.wait_for(lambda: host.revision != revision or host._stop.is_set(), timeout=poll)
                if host._stop.is_set():
                    return
                with self.cond:
                    if not self.subscribers and self.idle_since is not None and time.monotonic() - self.idle_since >= self.idle_grace_s:
                        self.thread = None
                        return
                if host.revision == revision:
                    continue
                time.sleep(COALESCE_S)
                revision = host.revision
                try:
                    self._tick()
                except Exception:  # noqa: BLE001 - a bad build must not end the feed; the next tick tries again
                    continue
        finally:
            with self.cond:
                if host._stop.is_set():
                    self.closed = True
                self.thread = None if self.thread is threading.current_thread() else self.thread
                self.cond.notify_all()

    def _tick(self) -> None:
        state = self.host.state()
        rows = rows_of(state)
        signatures = {key: _masked(*key, row) for key, row in rows.items()}
        events: list[tuple[str, dict]] = []
        for key in self.signatures:
            if key not in rows:
                events.append(("remove", {"table": key[0], "id": key[1]}))
        for key, row in rows.items():
            if self.signatures.get(key) != signatures[key]:
                events.append(("upsert", {"table": key[0], "id": key[1], "row": row}))
        cursors: dict[str, int] = {}
        for path in self.host.series_paths():
            answer = self._values(path, self.cursors.get(path, 0))
            if answer is None:
                continue
            cursors[path] = answer["cursor"]
            groups = []
            for group in answer["groups"]:
                fresh = [row for row in group["rows"] if row["points"]]
                if fresh:
                    groups.append({**group, "rows": fresh})
            if groups:
                events.append(("samples", {**answer, "groups": groups}))
        with self.cond:
            self.state, self.signatures, self.cursors = state, signatures, cursors
            now = time.monotonic()
            for name, data in events:
                self.counter += 1
                self.ring.append((self.counter, now, frame(self.counter, name, _dump(data))))
            while self.ring and (len(self.ring) > self.ring_events or now - self.ring[0][1] > self.ring_seconds):
                self.floor = self.ring.popleft()[0]
            if events:
                self.cond.notify_all()
