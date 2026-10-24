# Composites — contract v1

*2026-10-24, lane composition. Spec: Zach's daily note 2026-10-24 ("At the base layer it's just the real sensors and actuators ...
being able to work at a higher level is so important. Like being able to calibrate an entire robot"). Code:
`io_diagnostics/composite.py` (the file, the resolution, the rollup, the batch plan), `expected.py` (the one manifest),
`host.py`, `server.py`; the page: `web/view/structure/composites.js`, `web/detail/d6.js`, `web/app.js`. Tests:
`tests/test_composition.py`, `tests/test_composition_host.py`, `tests/test_composition.js`. Generator: `tools/cad2robot_composites.py`.*

**The rule: RobotDx never interprets what a composite is.** An arm, a robot, a rack, a facility are words in a file. RobotDx knows
only which entities a composite *contains*. It rolls their levels up, it passes a plugin's records through, and it can run an action
that every member offers. Everything that needs to know what an arm is (end-effector position, IK / FK, "tracking error within
limit", "home the arm") is someone else's Python, behind the same plugin boundary as a driver.

## 1. The composition file

One manifest, not two: composites are `[[composite]]` tables in the **expected setup** (`~/.config/io-diagnostics/expected.toml`,
`docs/expected-setup.md`; `$IO_DIAGNOSTICS_CONFIG_DIR` and `serve --expected FILE` as for rigs). `version` stays `1`: a host
that does not know the table ignores it (and, rewriting the file from its panel, would drop it, so edit by hand on such a host).

```toml
[[composite]]
id = "lab/robot-1/arm"            # required, unique: words of [A-Za-z0-9._-] joined by "/" (the slashes are a convention only)
kind = "arm"                      # required: a word. RobotDx never reads it; a plugin does (matches = ["composite:arm"])
title = "Robot 1 · arm"           # optional; default: the last word of the id
children = [                      # required, at least one; each an entity, or another composite's id
  "usb:0483:5740:272813EF#can/1", # a board: its adapter's hardware_id, "#", the node path under it (as `nodes` in a rig)
  "usb:0483:5740:272813EF#can/2",
  "host/usb1/port-1-5/usb-1-5/can/3",   # or an entity path, as the tree shows it
]
plugin = "state_arm"              # optional: a plugin (plugins.d) that handles this kind: it adds statuses, state, config, actions
expects = { dof = 3, tracking_error_rad = 0.05 }    # optional: handed to the plugin verbatim, never read by RobotDx
batch = "all"                     # optional: "all" (default) or "having": see section 6
source = "cad2robot One_Dof_Test_Rack"              # optional: where this table came from (a generator writes it); shown, never judged

[[composite]]
id = "lab/robot-1"
kind = "robot"
children = ["lab/robot-1/arm", "usb:8086:0b5b:219323070276"]   # a composite is a child by its id; a device by its hardware_id
```

**Child references**, told apart by their shape, never by a lookup order: a reference containing `#` is `<hardware_id>#<node path>`
(the node below the device whose `hardware_id` it is); any other reference containing `:` is a `hardware_id` (an entity, a
device or a board that reports one: `usb:...`, `moteus:...`); a reference starting with `host` is an entity path; anything
else is a composite id and must name a `[[composite]]`. WHY: a hardware id and an entity path both change with a cable (the path
with the port, the adapter's serial with a swap), the pair "adapter + node" is how a rig already names a board, and an id of
a composite never contains `:`.

**Nesting** is the `children` lists: any depth, any number of roots (a composite no other composite contains). WHY a tree and
not a graph: a rollup and a batch must count each leaf once. The parser refuses, naming both composites:

| Problem | Message names |
|---|---|
| a duplicate `id` (or two ids that make one path) | the id |
| a missing `id`, `kind` or `children`; a wrong type | the table's number or id |
| a child that is a composite id nobody defines | the composite and the id |
| a composite that is its own descendant | the cycle, `a -> b -> a` |
| a composite contained by two composites, or a leaf reference in two composites (or twice in one) | both composites |

**Errors are a status, never a crash.** While any composite problem exists, *no* composite loads (a half-loaded hierarchy
rolls up wrongly) and the Computer carries one UNKNOWN status `composition` (code `CompositionInvalid`) listing the problems
in `what_to_check`. Rigs and the retired list still load. The panel's edits (`Expect what's here`, ...) refuse to rewrite the
file while composite problems exist, so a typo never costs the person their composites. A file that is not TOML at all keeps
today's behaviour (the `expected setup skipped` source line, edits refused).

## 2. Entities and paths

Each composite is an entity: path `host/composite-<id with "/" as "-">`, `kind = "composite"` (always: the composite's own `kind`
word is `identity.composite_kind`, with `identity.composite_id`), name = its title, `owner` = its plugin or `io_diagnostics`. A root
hangs under the Computer; a nested composite hangs under its parent by a `via` edge (WHY not `part_of`: every page structure folds
a `part_of` child into its parent row as a quiet part, and a sub-composite must be a row of its own). Its leaf children keep their
own place in the physical tree; the composite *lists* them (`members`), and the page nests their rows under it
(`web/view/structure/composites.js`). A composite plugin's own entities go below its path like a driver's go below a device.

## 3. What RobotDx rolls up by itself

Every composite has one status, `<title>: children`, built on every view and never stored, with no expected period (it cannot go
stale):

- **level** = the worst level among its children (a child composite counts at its final level), `stale` counted as `unknown`;
  a child that is not here counts as `warn` (`ChildMissing`, absent is not broken: the rig rule). A child with nothing to say
  (no records at all) is not counted. **No children report -> `unknown`.**
- **message** names only counts and names ("all 6 ok" · "CAN 13 error · 1 more not ok"). The row draws the level as an icon only.
- **`caused_by`** names the children that themselves report a problem (the record contract's rule, v0.1 addendum): the composite
  is their consequence, listed in `knock_on` and counted once at the child. A composite whose plugin reports its own failure
  ("tracking error") stays its own cause.
- `values`: `children` (one `name: level` line per child), `defined_in` (the file), `source` (when given).

A composite's row shows the worst of its own records (its plugin's and this one), as any entity does.

## 4. What a composite plugin adds

The same boundary as a driver (`describe`, `diagnose`, `probe`, `watch`, `act`, `stop`; docs/state-config-v1.md for the records),
declared in `plugins.d` like any plugin; the one new thing is what it matches:

```toml
name = "state_arm"
command = ["{python}", "{here}/arm_plugin.py"]
matches = ["composite:arm"]          # composite kinds it handles (a glob); a real device never matches a composite:... entry
```

A composite names its plugin with `plugin = "<name>"`. The host runs it per composite exactly as it runs a driver per device (the
scheduler, `exclusive`, the on-connect probe, `follow`, the page's `watch` heartbeat, the host's `<plugin>: plugin run` check),
with `--device composite:<id>`. A plugin that cannot serve the composite (not found, disabled, does not match its kind) is one WARN
`PluginNotUsable` on the composite; the rollup still works.

**What it is given**, beside the usual `IO_DIAGNOSTICS_DEVICE`, `_DEVICE_PATH` (the composite's path), `_NOW`, `_SCHEMA`,
`_RECORD_VERSION`:

- `IO_DIAGNOSTICS_COMPOSITE`: JSON, the composite as the file says it, resolved now:
  `{"id", "kind", "title", "path", "expects", "children": [{"ref", "id", "kind": "entity" | "composite", "path", "name", "present"}]}`.
  `ref` is the child as written; `id` the same text; `path` the child's entity path now, `null` when it is not here. A child composite
  lists its own `children` too (the whole subtree, depth first), so a plugin never walks the file.
- `IO_DIAGNOSTICS_COMPOSITE_VALUES`: the path of a JSON file, written atomically before every call and about every second while
  the composite is watched: `{"stamp", "children": [{"path", "name", "level", "message", "state": {"<group>": {"<key>":
  {"value", "unit", "stamp"}}}}]}`, the children's *latest* levels and State. WHY a file and not the environment: a `watch` is
  one long process and reads it again each period (the frames directory's precedent: a file renamed over, readable by any language).
  The host cascades a page's heartbeat to the children (and their children) so their own watches run while the composite is looked at.

**What it writes**: its records land on the composite entity, relative to it (`""` is the composite): statuses (a name per check),
`state`, `config`, `frame`, `event` and actions (`entity` with `actions`). Its statuses may not name a child in `caused_by` (a path
outside the composite's own is not rebased); the host's rollup does that.

## 5. Child-value links

A `Measurement` of a composite's `state` record may carry `source = {"path": "<a child's entity path>", "key": "<its State key>"}`
(`key` optional): "this number is that child's". The host passes it through unchanged (`GET /api/v1/values`, `/node`); the page
draws `↳ CAN 13` after the value and a click selects that entity. A `path` the tree does not know is drawn as plain text.

## 6. Batch actions

A composite offers **`<action> all N`** for a standard action that has no parameters (`check_comms`, `check_firmware`,
`update_firmware`, `calibrate`, `update_config`, `clear_faults`, `reboot`, `stop_all`, `identify`; not `set_id`, `open_console`
or the configuration writes), when its leaves, found recursively through the nested composites, all offer it. Which leaves count:
`batch = "all"` (default): every leaf, so a leaf that lacks the action, or is not here, withholds it (the plan refuses naming
it); `batch = "having"`: the leaves that offer it, at least two, the others listed as *not included* in the dry run (a robot with a
camera: "Calibrate all 6" skips the camera). The action is named `all:<action>` on the composite's path.

- **Dry run** (`POST /api/v1/act {path, action: "all:calibrate", dry_run: true}`): the host dry-runs every leaf's own action (so each
  plugin's own preconditions and tokens are honoured) and answers one result: `batch.runs`, N entries `{order, path, label, plugin,
  group, argv, starts}`, where `starts` says `now` or `after <k> earlier run(s) of <group>`; `batch.skipped`; one `confirm` text; and
  one page token for the whole batch. Any leaf's refusal (`Busy`, `ConfirmRequired`, an unregistered action) refuses the batch.
- **Confirm** (`... {action: "all:calibrate", token, detach: true}`): starts N runs, all created at once (they appear as tabs in the
  runs view), answers **202** `{"runs": [<run>, ...]}`. Runs of one `exclusive` group go one at a time in the plan's order (the
  others `queued`); runs of different groups, or of none, go together. A run that fails does not stop the others.
- The batch inherits no authority: every run is an ordinary run (docs/runs-v1.md) with its own plugin token, `danger` and
  terminal; the batch's danger is the worst of its runs, and the page asks for the same confirm and tick.
- A composite's *own* actions (a plugin's "Home the arm") are the plugin's; RobotDx adds nothing custom.

## 7. API

- `GET /api/v1/state` gains `composition`: `{"file", "note", "problems": [...], "composites": [{"id", "path", "kind", "title", "parent"
  (a composite's path or null), "plugin", "level", "children": [{"ref", "kind", "path", "name", "present", "level"}], "members": [paths of
  the present leaf children], "leaves": N}]}`; each composite's tree node is an ordinary node.
- `GET /api/v1/node?path=<composite>` gains `composite`: the same object; its `actions` lists the plugin's and the `all:` ones
  (`scope: "composite"`, `batch: {"action", "n"}`, `state`/`reason` from the leaves' gates).
- `POST /api/v1/act`: `all:<action>` as above. Same page-only, same-origin and token rules as every act.
- `Measurement.source` as section 5 (`contract/record.py`).

## 8. Cad2Robot

A `cad2robot/1.1` bundle's `robot.json` lists `joints` with a `can_id` (and a `bus` or board when the exporter knows it), in kinematic
order. `tools/cad2robot_composites.py <robot.json>` writes the `[[composite]]` text for it: one composite of kind `robot` named after the
design, its children the joints' boards as `<adapter hardware_id>#can/<can_id>`, the adapter taken from `--adapter`, or from the
`nodes` of a rig in the expected setup that lists `can/<can_id>` (so a bundle and the rack's own file agree for free). A joint it cannot
place is written as a comment, never guessed. It prints to stdout; `--write FILE` replaces the composites that carry the same
`source` and keeps everything else. Status: a stub (one robot per bundle; the bundle carries no arm / leg grouping, so the person
splits it by hand, or `--group name=joint,joint`); tested on the as-built One_Dof_Test_Rack bundle.

## 9. What this does not do

- It never reads a composite's `kind`, `expects` or a child's meaning; it has no IK, no kinematics, no end-effector.
- It runs no check across children beyond the level rollup; "all joints answer" is a plugin's check.
- It starts no batch on its own: a person confirms one, from the page.
- A composite is not a rig: a rig is "what this PC should see" (`devices`, `nodes`, counts); a composite is "what this thing is made of".
