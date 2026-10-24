"""Mutation checks for the composition rules (docs/composition-v1.md): each mutation breaks ONE rule in a scratch copy of the checkout, and
the named tests must go red. A gate whose test stays green under its own mutation is not a gate.

    python3 /home/bam/io-diagnostics-lanes/composition/tests/mutate_composition.py [substring of a mutation's name]

A test is a pytest node id, or ``node:tests/<suite>.js`` (the page suite, which starts its own fixture host from the scratch copy).
Exit 0 when every mutation is caught; each line says which tests caught it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ("io_diagnostics", "contract", "io_facts", "tests", "plugins", "plugins.d", "docs", "tools", "pyproject.toml", "README.md")
PY = "tests/test_composition.py::"
HOST = "tests/test_composition_host.py::"
PAGE = "node:tests/test_composition.js"

# (name, file, old text, new text, the tests that must fail)
MUTATIONS = [
    ("rollup: the BEST child's level wins", "io_diagnostics/composition.py", "return first if RANK[first] >= RANK[second] else second",
     "return first if RANK[first] <= RANK[second] else second",
     [PY + "test_rollup_through_three_levels_is_the_worst_child_and_counts_the_cause_once", PY + "test_the_rollup_is_the_worst_not_the_best_and_all_ok_is_ok"]),
    ("rollup: a child that is not here is fine", "io_diagnostics/composition.py", "    if absent:\n        worst = _worse(worst, WARN)", "    if False:\n        worst = _worse(worst, WARN)",
     [PY + "test_a_child_that_is_not_here_is_a_warn_and_has_no_row_to_blame", HOST + "test_a_child_that_is_not_here_is_one_warn_naming_it"]),
    ("rollup: caused_by is not written (every composite is a cause of its own)", "io_diagnostics/composition.py",
     "    caused = tuple(dict.fromkeys(members)) if members and not absent and len(members) == len(bad) else ()", "    caused = ()",
     [PY + "test_rollup_through_three_levels_is_the_worst_child_and_counts_the_cause_once",
      HOST + "test_three_levels_roll_the_worst_child_up_and_the_chip_counts_each_cause_once"]),
    ("rollup: a stale child stays stale (an authored level that is not one)", "io_diagnostics/composition.py",
     "    return UNKNOWN if level == STALE else level", "    return level",
     [PY + "test_a_stale_child_is_unknown_not_stale_and_a_silent_one_is_not_counted", PY + "test_the_rollup_records_are_valid_records_and_never_stale"]),
    ("rollup: the Computer counts by everything below it", "io_diagnostics/composition.py", "    return summary.own if path == tree.HOST_PATH else summary.level", "    return summary.level",
     [PY + "test_a_robot_may_list_its_computer_and_it_counts_by_its_own_checks_not_everything_below_it"]),
    ("file: a cycle is not noticed", "io_diagnostics/composite.py", "if current in trail and current not in reported:", "if False:",
     [PY + "test_a_three_composite_loop_is_named_by_its_members", PY + "test_composites_that_disagree_with_each_other_are_named"]),
    ("file: a composite may have two parents", "io_diagnostics/composite.py", "                elif ref in parent_of:", "                elif False:",
     [PY + "test_composites_that_disagree_with_each_other_are_named"]),
    ("file: a leaf may belong to two composites", "io_diagnostics/composite.py", "            elif ref in leaf_of:", "            elif False:",
     [PY + "test_composites_that_disagree_with_each_other_are_named"]),
    ("file: an unknown composite child is accepted", "io_diagnostics/composite.py", "                elif ref not in by_id:", "                elif False:",
     [PY + "test_composites_that_disagree_with_each_other_are_named"]),
    ("file: a composite with problems still loads (half a hierarchy)", "io_diagnostics/expected.py",
     "return [] if self.composite_problems else list(self.composites)", "return list(self.composites)",
     [PY + "test_problems_in_the_composites_never_take_the_rigs_away_and_stop_a_rewrite", HOST + "test_a_broken_file_is_one_unknown_on_the_computer_and_no_composite_loads_but_the_rigs_do"]),
    ("file: a rewrite is allowed while composites have problems", "io_diagnostics/expected.py", "        if self.manifest.composite_problems:\n            # WHY",
     "        if False:\n            # WHY",
     [PY + "test_problems_in_the_composites_never_take_the_rigs_away_and_stop_a_rewrite", HOST + "test_a_broken_file_is_one_unknown_on_the_computer_and_no_composite_loads_but_the_rigs_do"]),
    ("file: the panel's rewrite drops the composites", "io_diagnostics/expected.py", "    lines += composite_module.dump(manifest.composites)\n", "",
     [PY + "test_several_roots_and_the_manifest_round_trips_with_the_rigs_kept", HOST + "test_a_panel_edit_rewrites_the_file_with_the_composites_kept"]),
    ("file: the problems are not a status on the Computer", "io_diagnostics/host.py", "        if manifest.composite_problems:\n            problems = manifest.composite_problems",
     "        if False:\n            problems = manifest.composite_problems",
     [HOST + "test_a_broken_file_is_one_unknown_on_the_computer_and_no_composite_loads_but_the_rigs_do", PAGE]),
    ("plugins: a composite match ignores the composite's kind", "io_diagnostics/plugins.py", "bus == \"composite\" and fnmatch.fnmatchcase(str(attrs.get(\"kind\") or \"\"), self.pattern)",
     "bus == \"composite\" and True",
     [PY + "test_a_composite_match_sees_only_a_composite_of_that_kind_and_no_device_glob_sees_one"]),
    ("plugins: a plugin that does not handle the kind is run for it", "io_diagnostics/host.py", "            if spec is not None and spec.accepts(device):\n                pairs.append((spec, device))",
     "            if spec is not None:\n                pairs.append((spec, device))",
     [HOST + "test_a_plugin_that_cannot_serve_a_composite_is_a_warn_and_the_rollup_still_works"]),
    ("plugin: it is not told its children", "io_diagnostics/host.py", "\"expects\": composite.expects, \"children\": self._composite_tree(composite.id, cv)}",
     "\"expects\": composite.expects, \"children\": []}",
     [HOST + "test_the_plugin_is_run_with_the_composites_id_and_its_childrens_ids_and_paths", HOST + "test_the_plugins_records_land_on_the_composite"]),
    ("plugin: it is not given its children's State", "io_diagnostics/host.py", "                state = self._state_of(child.path, now) if child.kind == \"entity\" and child.present else {}",
     "                state = {}",
     [HOST + "test_the_plugin_is_run_with_the_composites_id_and_its_childrens_ids_and_paths", HOST + "test_the_plugins_records_land_on_the_composite"]),
    ("plugin: a watch of the composite does not keep its children's watches alive", "io_diagnostics/host.py",
     "        if composite is not None and composite.plugin:\n            self._cascade_heartbeat(composite)", "        if False:\n            self._cascade_heartbeat(composite)",
     [HOST + "test_a_watch_of_a_composite_keeps_its_childrens_watches_alive_and_the_values_file_moves"]),
    ("links: a value's source is not passed through", "io_diagnostics/entity_state.py",
     "\"source\": {\"path\": measurement.source.path, \"key\": measurement.source.key} if measurement.source is not None else None}", "\"source\": None}",
     [HOST + "test_a_state_value_may_say_it_is_a_childs_and_the_host_passes_it_through", PAGE]),
    ("links: a source does not survive the wire", "contract/record.py", "        elif name == \"source\" and cls is Measurement and isinstance(value, Mapping):", "        elif False:",
     [PY + "test_a_measurement_may_say_which_entity_and_key_it_comes_from_and_it_survives_the_wire"]),
    ("batch: a leaf without the action does not refuse the batch", "io_diagnostics/composition.py",
     "        if action is None:\n            if composite.batch == \"all\":", "        if action is None:\n            if False:",
     [PY + "test_a_batch_is_refused_when_one_leaf_lacks_the_action_and_the_refusal_names_it",
      HOST + "test_a_batch_is_refused_while_one_board_lacks_the_action_and_the_plan_says_which"]),
    ("batch: every boards starts at once (no queue by group)", "io_diagnostics/composition.py",
     "        if item.group:\n            before = earlier.get(item.group, 0)", "        if False:\n            before = earlier.get(item.group, 0)",
     [PY + "test_a_batch_covers_every_leaf_recursively_in_file_order_and_queues_by_group", HOST + "test_one_dry_run_lists_the_n_runs_queued_by_their_group_and_holds_each_boards_own_token"]),
    ("batch: the runs are started together, not one at a time per group", "io_diagnostics/host.py", "            if group:\n                threading.Thread(target=one_at_a_time",
     "            if False:\n                threading.Thread(target=one_at_a_time",
     [HOST + "test_a_batch_starts_a_groups_runs_one_at_a_time_in_the_plans_order"]),
    ("batch: every run carries the first board's token", "io_diagnostics/host.py", "confirm=plugin_tokens.get(item.child.path),",
     "confirm=next(iter(plugin_tokens.values()), None),",
     [HOST + "test_one_confirm_starts_n_runs_one_at_a_time_per_group_and_each_run_is_an_ordinary_run"]),
    ("batch: the confirm starts nothing", "io_diagnostics/server.py", "            return self._batch(path, action, entry, body.get(\"detach\") is True)",
     "            return self._json({\"dry_run\": False, \"result\": {\"refused\": \"x\"}})",
     [HOST + "test_one_confirm_starts_n_runs_one_at_a_time_per_group_and_each_run_is_an_ordinary_run", PAGE]),
    ("batch: a token for one batch runs another", "io_diagnostics/server.py", "            if (entry[\"path\"], entry[\"action\"], entry[\"node\"], entry[\"new_id\"]) != (path, action, node, new_id):",
     "            if False:", [HOST + "test_the_batch_is_the_pages_alone_and_its_token_is_for_that_batch_only"]),
    ("batch: the CLI may start a real batch", "io_diagnostics/host.py", "            return {**self._batch_out(path, action, dry_run=False), \"refused\": \"BatchFromPage\",",
     "            return {**self._batch_out(path, action, dry_run=False), \"refused\": None,",
     [HOST + "test_the_batch_is_the_pages_alone_and_its_token_is_for_that_batch_only"]),
    ("batch: a batch lists no board's own preconditions", "io_diagnostics/host.py", "                for pre in (found or {}).get(\"preconditions\", []):", "                for pre in []:",
     [HOST + "test_a_composite_offers_an_action_all_its_leaves_offer_with_the_count"]),
    ("page: a composite that is a knock-on draws no mark (a collapsed rack looks healthy)", "io_diagnostics/web/view/composites.js", "      target.knock_on_of = null;\n", "",
     [PAGE]),
    ("page: members stay under their adapters", "io_diagnostics/web/view/structure/p1.js", "return root.IOComposites ? root.IOComposites.nest(rows, state) : rows;", "return rows;",
     [PAGE]),
    ("page: a child's value is plain text", "io_diagnostics/web/detail/d6.js", "    if (!row.source || !row.source.path) return '';", "    return '';",
     [PAGE]),
    ("page: a queued run's terminal is sized (a 409 in the console)", "io_diagnostics/web/runs/core.js",
     "const sizeable = () => { const run = get(id); return Boolean(writable() && run && (run.state === 'running' || run.state === 'waiting')); };",
     "const sizeable = () => writable();", [PAGE]),
]
def run(tests: list[str], cwd: Path) -> tuple[bool, str]:
    node = [test.removeprefix("node:") for test in tests if test.startswith("node:")]
    py = [test for test in tests if not test.startswith("node:")]
    tail = ""
    if py:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *py], cwd=cwd, capture_output=True, text=True)
        tail += (done.stdout + done.stderr)[-500:]
        if done.returncode != 0:
            return False, tail
    for suite in node:
        done = subprocess.run(["node", suite], cwd=cwd, capture_output=True, text=True, timeout=600)
        tail += (done.stdout + done.stderr)[-500:]
        if done.returncode != 0:
            return False, tail
    return True, tail


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    mutations = [item for item in MUTATIONS if only in item[0]]
    work = Path(tempfile.mkdtemp(prefix="composition-mutations-"))
    try:
        everything = sorted({test for item in mutations for test in item[4]}, key=lambda test: (test.startswith("node:"), test))
        green, tail = run(everything, ROOT)
        print(f"{'baseline green' if green else 'BASELINE RED'}: {len(everything)} test(s) before any mutation", flush=True)
        if not green:
            print(tail)
            return 1
        missed = []
        for name, file, old, new, tests in mutations:
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
            mutated_ok, tail = run(tests, copy)
            verdict = "caught" if not mutated_ok else "MISSED"
            print(f"{verdict:8} {name}  ->  {', '.join(test.split('::')[-1] for test in tests)}", flush=True)
            if verdict != "caught":
                missed.append(name)
                print(tail)
        print(f"\n{len(mutations) - len(missed)} mutations caught, {len(missed)} not")
        return 1 if missed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
