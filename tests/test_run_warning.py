"""A run that refused or warned says so in its RunEnded line. Zach, 2026-11-13, of an Identify that wiggled nothing: "after like wiggle fails, it
seemed to like block future efforts?". The run exited 0 and the log said "done after 2 s (exit 0)" while the plugin had said "no board answered
the scan: nothing moved" (run 20261113T081816-0d8d)."""

from io_diagnostics.terminal import run_warning


def test_the_plugins_refusal_is_the_runs_sentence():
    result = {"outcome": "ok", "returncode": 0, "records": [
        {"record": "status", "level": "ok", "message": "not this"},
        {"record": "event", "code": "ActionRefused", "severity": "warn", "message": "no board answered the scan: nothing moved"}]}
    assert run_warning(result) == "no board answered the scan: nothing moved"


def test_a_clean_run_says_nothing_more():
    assert run_warning({"records": [{"record": "event", "severity": "info", "message": "wiggled CAN 14"}]}) == ""
    assert run_warning({}) == "" and run_warning(None) == ""
