"""``GET /api/v1/live``: the page's 1 s ``/state`` poll as a change-only Server-Sent Events feed (docs/api-v1.md "Live").

One ``LiveFeed`` per host serves every client. Its single thread wakes on the host's own change tick (``Host.changes``, the
one ``/api/v1/events`` waits on), builds ``Host.state()`` ONCE, and diffs it against the last one by row:

* ``upsert``  a row whose JSON changed (a tree node by its ``path``, or one of ``/state``'s other top-level sections by its name),
* ``remove``  a row that is gone,
* ``samples`` State points that arrived since the last tick (what ``GET /api/v1/values?since=`` answers).

An entity a page is WATCHING (a plugin ``watch`` running, ~10 Hz) does not wait for that tick: a second thread, the samples lane, wakes on
each State point the host takes in and sends that entity's new points at once, coalesced to one ``samples`` event per entity per
``SAMPLE_COALESCE_S``. Both lanes advance the same per-entity cursor under one lock, so a point is sent once, by whichever got to it first.

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

SAMPLE_COALESCE_S = 0.1  # a watched entity's new samples are one event per this (10 Hz at most): the page's State moves as fast as the watch
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


def emitted_ms() -> float:
    """The host's wall clock now, unix milliseconds (``time.time()``). WHY not ``clock.now``: on a fixture that is the replay clock, so a page
    could not time a sample from the host's send to its own draw."""

    return round(time.time() * 1000, 3)


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
        self.sample_lock = threading.Lock()  # whoever reads values since a cursor and publishes the event holds it, so no point is sent twice
        self.epoch_over = threading.Event()  # set when this epoch's thread ends: the samples lane ends with it
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
            return [frame(self.counter, "snapshot", _dump({**self.state, "emitted": emitted_ms()}))]
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
        self.epoch_over = threading.Event()
        self.thread = threading.Thread(target=self._run, name="io-live", daemon=True)
        self.thread.start()
        threading.Thread(target=self._run_samples, args=(self.epoch_over,), name="io-live-samples", daemon=True).start()

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
            self.epoch_over.set()
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
        with self.cond:
            self.state, self.signatures = state, signatures
            self._publish(events)
        paths = self.host.series_paths()
        with self.sample_lock:
            old_cursors = dict(self.cursors)
        for path in paths:
            with self.sample_lock:
                since = self.cursors.get(path, 0)
            # WHY: a rack's cold snapshots must not hold up the watched lane. Reads can be slow;
            # validate the cursor after the read so a racing watched event wins, once, without rewinding.
            answer = self._values(path, since)
            if answer is None:
                continue
            with self.sample_lock:
                if self.cursors.get(path, 0) != since:
                    continue
                self.cursors[path] = answer["cursor"]
                event = self._fresh_samples(answer)
                if event is not None:
                    self._publish([("samples", event)])
        with self.sample_lock:
            for path in old_cursors.keys() - set(paths):
                # WHY: a watch may have revived this path after the listing; keep its newer cursor.
                if self.cursors.get(path) == old_cursors[path]:
                    self.cursors.pop(path, None)

    @staticmethod
    def _fresh_samples(answer: dict) -> dict | None:
        """``answer`` cut to the groups and rows that have new points, or None when nothing is new."""

        groups = []
        for group in answer["groups"]:
            fresh = [row for row in group["rows"] if row["points"]]
            if fresh:
                groups.append({**group, "rows": fresh})
        return {**answer, "groups": groups} if groups else None

    def _publish(self, events: list[tuple[str, dict]]) -> None:
        """Number each event, stamp it with the wall clock it leaves at (``emitted``), put it in the ring and wake the clients.

        A client that resumes with ``Last-Event-ID`` is sent the ring's bytes again, so a replayed event keeps the stamp it first left with."""

        with self.cond:
            now = time.monotonic()
            for name, data in events:
                self.counter += 1
                self.ring.append((self.counter, now, frame(self.counter, name, _dump({**data, "emitted": emitted_ms()}))))
            while self.ring and (len(self.ring) > self.ring_events or now - self.ring[0][1] > self.ring_seconds):
                self.floor = self.ring.popleft()[0]
            if events:
                self.cond.notify_all()

    # --- the samples lane: a watched entity at the watch's rate -------------------------------------------------------------
    def _run_samples(self, epoch_over: threading.Event) -> None:
        """While a watch runs, send its entity's new State points as they arrive, at most one event per entity per ``SAMPLE_COALESCE_S``.

        Woken by ``Host.sample_changes`` (each State point taken in); an entity that sent less than a coalescing period ago waits out the rest of
        it, so a burst is one event. Entities nobody watches are left to the 1 Hz tick."""

        host = self.host
        revision = host.sample_revision
        last_sent: dict[str, float] = {}
        due: float | None = None  # when an entity that has to wait may send
        while not epoch_over.is_set() and not host._stop.is_set():
            timeout = 0.25 if due is None else max(0.0, due - time.monotonic())
            with host.sample_changes:
                host.sample_changes.wait_for(lambda: host.sample_revision != revision or epoch_over.is_set() or host._stop.is_set(), timeout=timeout)
                revision = host.sample_revision
            if epoch_over.is_set() or host._stop.is_set():
                return
            try:
                due = self._send_watched(last_sent)
            except Exception:  # noqa: BLE001 - a bad read must not end the lane; the next point tries again
                due = None

    def _send_watched(self, last_sent: dict[str, float]) -> float | None:
        """One pass over the watched entities: those whose coalescing period is over send what is new. Returns the time the soonest of the
        others may send (or None)."""

        watched = self.host.watched_paths()
        for path in list(last_sent):
            if path not in watched:
                del last_sent[path]
        due: float | None = None
        for path in watched:
            ready_at = last_sent.get(path, 0.0) + SAMPLE_COALESCE_S
            if time.monotonic() < ready_at:
                due = ready_at if due is None else min(due, ready_at)
                continue
            with self.sample_lock:
                answer = self._values(path, self.cursors.get(path, 0))
                if answer is None:
                    continue
                self.cursors[path] = answer["cursor"]
                event = self._fresh_samples(answer)
                if event is not None:
                    self._publish([("samples", event)])
                    last_sent[path] = time.monotonic()
        return due
