"""Mutation checks for the entity page's gates (docs/state-config-v1.md sections 4 and 9): each mutation breaks ONE rule in a scratch
copy of the checkout, and the named tests must go red. A gate whose test stays green under its own mutation is not a gate.

    uv run --project /home/bam/io-diagnostics-lanes/entity-page python /home/bam/io-diagnostics-lanes/entity-page/tests/mutate_entity_page.py

Exit 0 when every mutation is caught; each line says which tests caught it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ("io_diagnostics", "contract", "io_facts", "tests", "plugins", "plugins.d", "docs", "pyproject.toml", "README.md")

# (name, file, old text, new text, the tests that must fail)
MUTATIONS = [
    ("safety: the board need not be stopped", "io_diagnostics/config_edit.py", "if measurement.value != STOPPED:", "if False:",
     ["tests/test_config_edit.py::test_a_safety_key_needs_the_second_confirm_and_a_stopped_board"]),
    ("safety: the second confirm is not asked", "io_diagnostics/config_edit.py", "if confirm_safety is not True:\n                return self._refuse(job, rel, entity, path, base, \"SafetyConfirmRequired\"",
     "if False:\n                return self._refuse(job, rel, entity, path, base, \"SafetyConfirmRequired\"",
     ["tests/test_config_edit.py::test_a_safety_key_needs_the_second_confirm_and_a_stopped_board"]),
    ("safety: an unknown mode counts as stopped", "io_diagnostics/config_edit.py", "return f\"{row.key} is a safety setting and the board reports no mode: it is written only to a board known to be stopped\"",
     "return None", ["tests/test_config_edit.py::test_a_safety_key_is_refused_when_the_boards_mode_is_unknown_or_old"]),
    ("safety: an old mode report is trusted", "io_diagnostics/config_edit.py", "if age is None or age > limit:", "if age is None:",
     ["tests/test_config_edit.py::test_a_safety_key_is_refused_when_the_boards_mode_is_unknown_or_old"]),
    ("session: any token will do", "io_diagnostics/config_edit.py", "or not secrets.compare_digest(session.token, str(token or \"\"))", "",
     ["tests/test_config_edit.py::test_editing_starts_with_one_confirm_names_the_board_and_ends_with_the_page",
      "tests/test_config_edit.py::test_the_configuration_writes_are_the_pages_alone"]),
    ("session: it never expires", "io_diagnostics/config_edit.py", "return time.monotonic() - session.beat <= self.grace_s", "return True",
     ["tests/test_config_edit.py::test_a_session_ends_by_itself_when_the_page_stops_sending_heartbeats"]),
    ("session: no confirm needed to open", "io_diagnostics/config_edit.py", "if confirm is not True:\n            return {\"ok\": False, \"refused\": \"ConfirmRequired\"", "if False:\n            return {\"ok\": False, \"refused\": \"ConfirmRequired\"",
     ["tests/test_config_edit.py::test_editing_starts_with_one_confirm_names_the_board_and_ends_with_the_page"]),
    ("gates: a person's tool may hold the port", "io_diagnostics/config_edit.py", "found = self.host._gate_for(job, gates)", "found = None",
     ["tests/test_config_edit.py::test_a_persons_tool_holding_the_port_blocks_every_write_and_nothing_runs"]),
    ("gates: a run of the bus does not stop a write", "io_diagnostics/host.py", "            run = next((run for run in self.runs.active() if run.spec.group == group), None)\n            if run is not None:\n                return {\"reason\": f\"{run.spec.label} is running\", \"holders\": []}",
     "            run = None", ["tests/test_config_edit.py::test_a_safety_key_is_refused_while_a_run_is_active_on_its_bus",
                               "tests/test_state_config.py::test_an_active_run_of_the_same_bus_pauses_the_watch"]),
    ("writes: a read-only setting may be written", "io_diagnostics/config_edit.py", "if row is None or not row.writable:", "if row is None:",
     ["tests/test_config_edit.py::test_a_value_of_the_wrong_type_or_choice_is_refused_before_anything_is_sent"]),
    ("writes: an enum takes any choice", "io_diagnostics/config_edit.py", "            return None, f\"{row.key} is one of {', '.join(map(str, row.choices))}\"", "            return value, \"\"",
     ["tests/test_config_edit.py::test_a_value_of_the_wrong_type_or_choice_is_refused_before_anything_is_sent"]),
    ("writes: a clamped value counts as accepted", "io_diagnostics/config_edit.py", "if ran[\"ok\"] and after is not None and same_value(kept, coerced):", "if ran[\"ok\"]:",
     ["tests/test_config_edit.py::test_a_value_the_board_clamps_is_rejected_with_what_it_kept"]),
    ("page-only: a script may open an edit session", "io_diagnostics/server.py", "        body, answer = self._body()\n        if body is None:\n            return self._json(*answer)\n        path, token = body.get(\"path\"), body.get(\"token\")",
     "        body, answer = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}'), ()\n        path, token = body.get(\"path\"), body.get(\"token\")",
     ["tests/test_config_edit.py::test_the_configuration_writes_are_the_pages_alone"]),
    ("page-only: a script may start a watch", "io_diagnostics/server.py", "        # WHY the same page-only rule as /act: a heartbeat makes the host run a plugin's query-only verb against hardware\n        body, answer = self._body()\n        if body is None:\n            return self._json(*answer)",
     "        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')", ["tests/test_state_config.py::test_the_watch_heartbeat_is_the_pages_alone"]),
    ("page-only: a script may write the arrangement", "io_diagnostics/server.py", "        # WHY the same page-only rule as /settings: an arrangement is the person's preference, written to their file\n        body, answer = self._body()\n        if body is None:\n            return self._json(*answer)",
     "        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')", ["tests/test_config_edit.py::test_the_arrangement_endpoint_is_the_pages_alone"]),
    ("watch: it never stops", "io_diagnostics/host.py", "if time.monotonic() - watch.last_beat > self.watch_grace_s:", "if False:",
     ["tests/test_state_config.py::test_a_heartbeat_starts_the_watch_with_the_entity_path_and_it_stops_after_the_grace_period"]),
    ("watch: a holder does not pause it", "io_diagnostics/host.py", "            if state is False:\n                return entity_state.holder_reason(check, detail)", "            if False:\n                return entity_state.holder_reason(check, detail)",
     ["tests/test_state_config.py::test_a_holder_pauses_the_watch_in_plain_words_and_clearing_it_resumes"]),
    ("watch: a plugin that is not query-only still runs", "io_diagnostics/host.py", "if declared.get(\"danger\", \"query\") != \"query\":", "if False:",
     ["tests/test_state_config.py::test_only_a_query_watch_that_a_plugin_described_runs"]),
    ("frames: a path outside the frame directory is read", "io_diagnostics/host.py", "if found.parent != folder or not found.is_file():", "if not found.is_file():",
     ["tests/test_state_config.py::test_a_frame_names_a_plain_file_inside_its_directory_or_nothing_is_read"]),
    ("state/health: a state series reaches the health bar", "io_diagnostics/history.py", "(not health or not series.state)", "True",
     ["tests/test_state_config.py::test_a_state_series_is_history_but_never_a_health_bar"]),
    ("config: a value that differs is not marked", "io_diagnostics/entity_state.py", "differs = config.expected is not None and not same_value(config.value, config.expected)", "differs = False",
     ["tests/test_state_config.py::test_configuration_marks_a_value_that_differs_from_what_is_expected"]),
    ("watch: the host's own watch is a holder to its own gates", "io_diagnostics/host.py", "if lead.isdigit() and RUNNING.is_passive(int(lead)):", "if False:",
     ["tests/test_config_edit.py::test_the_hosts_own_watch_is_not_a_holder_to_its_own_gates_and_a_persons_tool_still_is",
      "tests/test_config_edit.py::test_a_write_goes_through_at_once_while_the_watch_runs_and_the_watch_resumes_after_it"]),
    ("watch: a write does not stop the board's watch first", "io_diagnostics/host.py", "            paused = self.pause_watches(job)\n            try:\n                yield", "            paused = []\n            try:\n                yield",
     ["tests/test_config_edit.py::test_a_write_goes_through_at_once_while_the_watch_runs_and_the_watch_resumes_after_it"]),
    ("watch: it does not resume after a write", "io_diagnostics/host.py", "                watch.paused = max(0, watch.paused - 1)", "                watch.paused = watch.paused",
     ["tests/test_config_edit.py::test_a_write_goes_through_at_once_while_the_watch_runs_and_the_watch_resumes_after_it"]),
]


def run(tests: list[str], cwd: Path) -> tuple[bool, str]:
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=cwd, capture_output=True, text=True)
    return done.returncode == 0, (done.stdout + done.stderr)[-600:]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="entity-mutations-"))
    try:
        missed = []
        for name, file, old, new, tests in MUTATIONS:
            copy = work / "tree"
            if copy.exists():
                shutil.rmtree(copy)
            copy.mkdir()
            for item in COPY:
                source = ROOT / item
                if source.is_dir():
                    shutil.copytree(source, copy / item, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "reports", ".venv", "media"))
                else:
                    shutil.copy2(source, copy / item)
            target = copy / file
            text = target.read_text()
            if old not in text:
                print(f"BROKEN  {name}: the text to mutate is not in {file}")
                missed.append(name)
                continue
            target.write_text(text.replace(old, new, 1))
            clean_ok, _ = run(tests, ROOT)
            mutated_ok, tail = run(tests, copy)
            verdict = "caught" if clean_ok and not mutated_ok else "MISSED" if clean_ok else "BASELINE RED"
            print(f"{verdict:12} {name}  ->  {', '.join(test.split('::')[-1] for test in tests)}")
            if verdict != "caught":
                missed.append(name)
                print(tail)
        print(f"\n{len(MUTATIONS) - len(missed)} mutations caught, {len(missed)} not")
        return 1 if missed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
