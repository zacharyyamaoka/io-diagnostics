# Composites — contract v1

*2026-10-24, lane composition. Spec: Zach's daily note 2026-10-24 ("At the base layer it's just the real sensors and actuators ...
being able to work at a higher level is so important. Like being able to calibrate an entire robot"). Code:
`io_diagnostics/composite.py` (the file: parse, dump), `io_diagnostics/composition.py` (resolution, rollup, batch plan), `expected.py` (the
one manifest), `host.py`, `server.py`; the page: `web/view/composites.js`, `web/detail/d6.js`, `web/app.js`. Tests:
`tests/test_composition.py`, `tests/test_composition_host.py`, `tests/test_cad2robot_composites.py`, `tests/test_composition.js`; the
rules are mutation-checked by `tests/mutate_composition.py`. Generator: `tools/cad2robot_composites.py`.*

**The rule: RobotDx never interprets what a composite is.** An arm, a robot, a rack, a facility are words in a file. RobotDx knows
only which entities a composite *contains*. It rolls their levels up, it passes a plugin's records through, and it can run an action
that every member offers. Everything that needs to know what an arm is (end-effector position, IK / FK, "tracking error within
limit", "home the arm") is someone else's Python, behind the same plugin boundary as a driver.

## 1. The composition file

One manifest, not two: composites are `[[composite]]` tables in the **expected setup** (`~/.config/io-diagnostics/expected.toml`,
`docs/expected-setup.md`; `$IO_DIAGNOSTICS_CONFIG_DIR` and `serve --expected FILE` as for rigs). `version` stays `1`: a host
that does not know the table ignores it (and, rewriting the file from its panel, would drop it, so edit by hand on such a host). A hand
edit shows within one refresh (2 s).

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
expects = { dof = 3, tracking_error_rev = 0.05 }    # optional: handed to the plugin verbatim, never read by RobotDx
batch = "all"                     # optional: "all" (default) or "having": see section 6
source = "cad2robot One_Dof_Test_Rack"              # optional: where this table came from (a generator writes it); shown, never judged

[[composite]]
id = "lab/robot-1"
kind = "robot"
children = ["lab/robot-1/arm", "usb:8086:0b5b:219323070276"]   # a composite is a child by its id; a device by its hardware_id
```

**Child references**, told apart by their shape, never by a lookup order: a reference containing `#` is `<hardware_id>#<node path>`
(the node below the device whose `hardware_id` it is); any other reference containing `:` is a `hardware_id` (an entity, a
device or a board that reports one: `usb:...`, `moteus:...`); a reference equal to `host` or starting with `host/` is an entity path;
anything else is a composite id and must name a `[[composite]]`. WHY: a hardware id and an entity path both change with a cable (the path
with the port, the adapter's serial with a swap), the pair "adapter + node" is how a rig already names a board, and an id of
a composite never contains `:` or starts with `host/`.

**Nesting** is the `children` lists: any depth, any number of roots (a composite no other composite contains). WHY a tree and
not a graph: a rollup and a batch must count each leaf once. The parser refuses, naming both composites:

| Problem | Message names |
|---|---|
| a duplicate `id` (or two ids that make one path) | the id |
| a missing `id`, `kind` or `children`; a wrong type; a plugin that is not a name; `batch` not `all` / `having`; an `expects` that is not a table of JSON-able values | the table's id or number |
| a child that is a composite id nobody defines, or a composite's entity path (`host/composite-...`: name it by its id) | the composite and the child |
| a composite that is its own descendant | the loop, `b -> a -> b` |
| a composite contained by two composites, or a leaf reference in two composites (or twice in one) | both composites |

**Errors are a status, never a crash.** While any composite problem exists, *no* composite loads (a half-loaded hierarchy
rolls up wrongly) and the Computer carries one UNKNOWN status `composition` (code `CompositionInvalid`) listing the problems
in `what_to_check`. Rigs and the retired list still load. The panel's edits (`Expect what's here`, ...) refuse to rewrite the
file while composite problems exist, so a typo never costs the person their composites. A file that is not TOML at all keeps
today's behaviour (the `expected setup skipped` source line, edits refused). A child that is a hardware id or path nobody has *now*
is not an error: it is a child that is not here (section 3).

## 2. Entities and paths

Each composite is an entity: path `host/composite-<id with "/" as "-">`, `kind = "composite"` (always: the composite's own `kind`
word is `identity.model`, the page's id text), name = its title, `owner` = its plugin or `io_diagnostics`. A root hangs under the
Computer, before the OS groups; a nested composite hangs under its parent by a `via` edge (WHY not `part_of`: every page structure folds
a `part_of` child into its parent row as a quiet part, and a sub-composite must be a row of its own). Its leaf children keep their
own place in the physical tree; the composite *lists* them (`members`), and the page nests their rows under it
(`web/view/composites.js`, called by the Devices structure p1 and so by every structure built on it): an entity is drawn once, under its
composite. A composite plugin's own entities go below its path like a driver's go below a device.

## 3. What RobotDx rolls up by itself

Every composite has one status, `<title>: children`, built on every view and never stored, with no expected period (it cannot go
stale):

- **level** = the worst level among its children (a child composite counts at its final level), `stale` counted as `unknown`;
  a child that is not here counts as `warn` (`ChildMissing`, absent is not broken: the rig rule). A child with nothing to say
  (no records at all) is not counted. **No child reports -> `unknown`** (`ChildrenSilent`). Codes: `ChildError`, `ChildWarn`,
  `ChildUnknown`, `ChildMissing`.
- **message** names only counts and names ("all 6 ok" · "CAN 13 error · 1 more not ok"); the row draws the level as an icon only.
- **`caused_by`** names the children that themselves report a problem (the record contract's rule, v0.1 addendum): the composite
  is their consequence, listed in `knock_on` and counted once at the child, so the chip counts a joint's fault once and not once more for
  its arm, robot and rack. A composite whose plugin reports its own failure ("tracking error", no `caused_by`) stays its own cause too;
  one with a child that is not here, or whose problem no row reports, names nothing and counts itself.
- `values`: `children` (one `name: level` line per child), `defined_in` (the file), `source` (when given).
- The Computer (`host`) may be a child ("arm + computer + camera"): it counts by its own checks (its computer plugin's), never by its
  rollup, which is everything below it.

A composite's row shows the worst of its own records (its plugin's and this one), as any entity does. WHY the page still draws the icon on a
composite that is a consequence (every other knock-on row draws none): a collapsed rack over a failed joint must not look healthy; the API
keeps `knock_on_of` (the entity page says "Explained by CAN 1 above: counted once, as that cause"), and `web/view/composites.js` clears it
on the composite's tree row.

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
`PluginNotUsable` (`<title>: plugin`) on the composite and is never run for it; the rollup still works.

**What it is given**, beside the usual `IO_DIAGNOSTICS_DEVICE` (a skeleton-shaped dict, `bus` `composite`), `_DEVICE_PATH` (the composite's
path), `_NOW`, `_SCHEMA`, `_RECORD_VERSION`:

- `IO_DIAGNOSTICS_COMPOSITE`: JSON, the composite as the file says it, resolved now:
  `{"id", "kind", "title", "path", "expects", "children": [{"ref", "id", "kind": "entity" | "composite", "path", "name", "present"}]}`.
  `ref` is the child as written (`id` is the same text); `path` the child's entity path now, `null` when it is not here. A child composite
  also carries `composite_kind` and its own `children` (the whole subtree), so a plugin never walks the file.
- `IO_DIAGNOSTICS_COMPOSITE_VALUES`: the path of a JSON file, written atomically before every call and about every half second while the
  composite is watched: `{"stamp", "children": [{"ref", "id", "kind", "path", "name", "present", "level", "message", "state": {"<group>":
  {"<key>": {"value", "unit", "stamp"}}}}]}`, a flat list of every child in the subtree (entities and composites), their *latest* levels
  (the rows' own, a composite's final) and State. WHY a file and not the environment: a `watch` is one long process and reads it again
  each period (the frames directory's precedent: a file renamed over, readable by any language). The host cascades a page's heartbeat to
  the children (and theirs) so their own watches run while the composite is looked at.

**What it writes**: its records land on the composite entity, relative to it (`""` is the composite): statuses (a name per check),
`state`, `config`, `frame`, `event` and actions (an `entity` record with `actions`). Its statuses cannot name a child in `caused_by` (a
path outside the composite's own is not rebased); the host's rollup does that.

## 5. Child-value links

A `Measurement` of a composite's `state` record may carry `source = {"path": "<a child's entity path>", "key": "<its State key>"}`
(`key` optional; `SourceRef` in `contract/record.py`, validated: a path is required): "this number is that child's". The host passes it
through unchanged (`GET /api/v1/values` rows carry `source`); the page draws `↳ CAN 13` after the value, and a click selects that entity
(the page's own `data-go` selection, so Back returns). A `path` the tree does not know is drawn as plain text, never a dead link.

## 6. Batch actions

A composite offers **`<action> all N`** for a standard action that has no parameters (`check_comms`, `check_firmware`,
`update_firmware`, `calibrate`, `update_config`, `clear_faults`, `reboot`, `stop_all`, `identify`; not `set_id`, `open_console`
or the configuration writes), when its leaves, found recursively through the nested composites, all offer it, and there are at least two.
Which leaves count: `batch = "all"` (default): every leaf, so a leaf that lacks the action, or is not here, withholds it (asked
for anyway, the plan is refused naming it: `ChildLacksAction`, `ChildMissing`); `batch = "having"`: the leaves that offer it, the others
listed as *not included* (a robot with a camera: "Calibrate all 6" skips the camera). The action is named `all:<action>` on the composite's
path; its title says the count ("Calibrate all 6…"), its danger is the worst of its leaves', its state and reason come from their gates, its
preconditions are theirs (one row per check, the sentences of the first three boards).

- **Dry run** (`POST /api/v1/act {path, action: "all:calibrate", dry_run: true}`): the host dry-runs every leaf's own action (so each
  plugin's own preconditions and token are honoured) and answers one result: `batch` = `{action, title, n, danger, skipped: [{path, name,
  why}], runs: [{order, path, label, node, plugin, group, starts, after, danger, argv, confirm}]}`, where `label` names the board and its
  adapter ("CAN 1 · fdcanusb 272813EF · Run calibration": two adapters both have a CAN 1), `starts` says `now` or `after <k> earlier run(s)
  of <group>` and `after` is that k; one `confirm` text; and one page token for the whole batch (each board's own plugin token stays in
  the server beside it, never sent to the page). Any leaf's refusal (`Busy`, `ConfirmRequired`, an unregistered action) refuses the batch.
- **Confirm** (`... {action: "all:calibrate", token, detach: true}`): starts N runs, all created at once and `queued` (they appear as tabs
  in the runs view, in the plan's order), answers **202** `{"runs": [<run>, ...], "batch": ...}`. Runs of one `exclusive` group go one at a
  time in the plan's order, each with its own plugin token; runs of different groups, or of none, go together. A run that fails does not
  stop the others. Without `detach` the call waits for all of them.
- The batch inherits no authority: every run is an ordinary run (docs/runs-v1.md) with its own `danger` and terminal; the page asks for the
  same confirm and tick. Page-only, same-origin, one token per dry run, single use, bound to the composite and the action (`TokenMismatch`).
  `io-diagnostics act` cannot start one (`BatchFromPage`); its dry run works.
- A composite's *own* actions (a plugin's "Home the arm") are the plugin's; RobotDx adds nothing custom.

## 7. API

- `GET /api/v1/state` gains `composition`: `{"file", "problems": [...], "composites": [{"id", "path", "kind", "title", "parent" (a
  composite's path or null), "plugin", "batch", "source", "expects", "level", "children": [{"ref", "id", "kind", "path", "name",
  "present", "level", "message"}], "members": [paths of the present leaf children], "leaves": N}]}`, in tree order; each composite's tree
  node is an ordinary node.
- `GET /api/v1/node?path=<composite>` gains `composite`: the same object (null for any other entity); its `actions` lists the plugin's and
  the `all:` ones (`scope: "composite"`, `batch: {"action", "n", "groups", "skipped"}`).
- `POST /api/v1/act`: `all:<action>` as above.
- `Measurement.source` as section 5 (`contract/record.py`).

## 8. Cad2Robot

A `cad2robot/1.x` bundle's `robot.json` lists `joints` with a `can_id` (and a `bus` when the exporter knows it), in kinematic order.
`tools/cad2robot_composites.py <robot.json>` writes the `[[composite]]` text for it: one composite of kind `robot` named after the design,
its children the joints' boards as `<adapter hardware_id>#can/<can_id>`; the adapter from `--bus NAME=HARDWARE_ID` (the joint's own `bus`
word), else `--adapter HARDWARE_ID`, else the rig in the expected setup (`--expected FILE`) with exactly one device that lists `can/<can_id>`
among its nodes (so a bundle and the rack's own file agree for free). A joint it cannot place, or that has no `can_id`, is a `# not placed:`
note, never a guess; if none can be placed nothing is written. `--group arm=revolute_1,revolute_2` makes a sub-composite of that kind of
those joints (the bundle carries no arm / leg grouping, so the person says it). It prints to stdout; `--write FILE` replaces the composites
that carry the same `source` and keeps everything else (the file is rewritten whole, as the panel's edits do; a result that would not load
is refused). Status: a stub, one robot per bundle, tested on a trimmed fixture bundle and run (read-only) on the real as-built
One_Dof_Test_Rack bundle against the real expected setup.

## 9. What this does not do

- It never reads a composite's `kind`, `expects` or a child's meaning; it has no IK, no kinematics, no end-effector.
- It runs no check across children beyond the level rollup; "all joints answer" is a plugin's check.
- It starts no batch on its own: a person confirms one, from the page.
- A composite is not a rig: a rig is "what this PC should see" (`devices`, `nodes`, counts); a composite is "what this thing is made of".
- It does not yet map a facility: the fleet views Zach described (uptime, runtime across racks and rows) are a page over these same
  entities and rollups, not a change to them.
