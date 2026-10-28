"""``GET /api/v1/history/tree`` (the health bar of every tree row in one answer) and ``GET /api/v1/checks`` (every entity's latest checks in
one answer), against fixture F1 through the real server. The rule for both: a row's answer IS what ``/history/bar`` and ``/node`` give for
that entity, so the one-request form can never drift from the per-row form. The clock is hand-moved (``once`` holds one instant), so the
two requests judge at the same ``now`` and the window has a past to show: no data, then held levels, then stale."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from test_host_api import example, same_keys

from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread

BINS, WINDOW_S = 12, 60


@pytest.fixture()
def f1():
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    host.clock.at += 40  # the replay's records were received 40 s ago: held, then stale, inside the window
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host
    http.shutdown()
    http.server_close()
    host.stop()


def get(host, path: str):
    try:
        with urllib.request.urlopen(host.url.rstrip("/") + path, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def tree_rows(state: dict) -> list[tuple[str, int]]:
    """``/state``'s tree, parent first then its children in order: ``(path, depth)``."""

    out, stack = [], [state["tree"]]
    while stack:
        node = stack.pop()
        out.append((node["path"], node["depth"]))
        stack.extend(reversed(node["children"]))
    return out


# ----- /history/tree ----------------------------------------------------------------------------------------------------
def test_every_tree_row_has_the_bar_history_bar_gives_it(f1):
    status, answer = get(f1, f"/api/v1/history/tree?bins={BINS}&window={WINDOW_S}")
    assert status == 200
    expected_rows = tree_rows(get(f1, "/api/v1/state")[1])
    assert len(expected_rows) > 20  # F1's whole rack, not a stub
    assert [(row["path"], row["depth"]) for row in answer["rows"]] == expected_rows  # every row, in tree order, at the tree's depth
    assert (answer["path"], answer["bins"], answer["window_s"], answer["bin_s"]) == ("host", BINS, float(WINDOW_S), WINDOW_S / BINS)
    by_path = {row["path"]: row for row in answer["rows"]}
    for path, _depth in expected_rows:
        bar_status, bar = get(f1, f"/api/v1/history/bar?path={path}&bins={BINS}&window={WINDOW_S}")
        assert bar_status == 200
        assert (answer["from"], answer["to"]) == (bar["from"], bar["to"])
        own, children = bar["rows"][0], bar["rows"][1:]
        assert {**by_path[path], "depth": 0} == own, path  # the entity's own row, depth aside
        for child in children:  # and each child row /history/bar gives it
            assert {**by_path[child["path"]], "depth": 1} == child, child["path"]


def test_the_window_shows_no_data_held_levels_and_stale(f1):
    rows = get(f1, f"/api/v1/history/tree?bins={BINS}&window={WINDOW_S}")[1]["rows"]
    seen = {level for row in rows for level in row["bar"]}
    assert {None, "ok", "stale"} <= seen, seen
    assert all(len(row["bar"]) == BINS for row in rows)
    assert {row["basis"] for row in rows} <= {"own", "below", "none"} and {"own", "below"} <= {row["basis"] for row in rows}
    host_row = rows[0]
    assert host_row["path"] == "host" and host_row["depth"] == 0 and host_row["name"]


def test_the_tree_answer_takes_the_same_queries_as_the_bar(f1):
    status, answer = get(f1, "/api/v1/history/tree")
    assert status == 200 and answer["bins"] == 60 and answer["window_s"] == 900.0  # the same defaults as /history/bar
    assert get(f1, "/api/v1/history/tree?bins=many")[0] == 400
    assert get(f1, "/api/v1/history/tree?window=30s")[0] == 400  # under a minute
    assert get(f1, "/api/v1/history/tree?window=2h")[0] == 400  # beyond what the history keeps
    assert get(f1, "/api/v1/history/tree?bins=5000")[1]["bins"] == 1000  # clamped as /history/bar clamps
    assert get(f1, "/api/v1/history/tree?path=host/nowhere&bins=4")[0] == 200  # a path is not a parameter of this one


def test_the_tree_answer_is_one_cheap_pass(f1):
    started = time.perf_counter()
    status, answer = get(f1, f"/api/v1/history/tree?bins=60&window={WINDOW_S}")
    elapsed = time.perf_counter() - started
    print(f"measured: /history/tree, {len(answer['rows'])} rows x 60 bins in {elapsed * 1000:.0f} ms")
    assert status == 200 and elapsed < 5.0


def test_the_tree_answer_matches_the_doc_example(f1):
    answer, documented = get(f1, f"/api/v1/history/tree?bins={BINS}&window={WINDOW_S}")[1], example("`GET /api/v1/history/tree")
    same_keys(answer, documented, "history/tree")
    same_keys(answer["rows"][0], documented["rows"][0], "history/tree.rows[]")


# ----- /checks ----------------------------------------------------------------------------------------------------------
def test_every_entitys_checks_are_what_node_gives_it(f1):
    status, answer = get(f1, "/api/v1/checks")
    assert status == 200
    paths = [path for path, _depth in tree_rows(get(f1, "/api/v1/state")[1])]
    assert set(answer["checks"]) <= set(paths) and len(answer["checks"]) > 5  # keyed by entity path, only entities with checks
    total = 0
    for path in paths:
        node = get(f1, f"/api/v1/node?path={path}")[1]
        assert answer["checks"].get(path, []) == node["checks"], path  # an entity with none is absent, not an empty list
        total += len(node["checks"])
    assert total == sum(len(checks) for checks in answer["checks"].values()) > 10
    assert all(checks for checks in answer["checks"].values())
    assert answer["clock"]["now"] == get(f1, "/api/v1/state")[1]["clock"]["now"]


def test_the_checks_answer_matches_the_doc_example(f1):
    answer, documented = get(f1, "/api/v1/checks")[1], example("`GET /api/v1/checks")
    same_keys(answer, documented, "checks")
    same_keys(next(iter(answer["checks"].values()))[0], next(iter(documented["checks"].values()))[0], "checks[path][]")
    assert get(f1, "/api/v1/checks?path=host")[1] == answer  # no parameters: a query string changes nothing
