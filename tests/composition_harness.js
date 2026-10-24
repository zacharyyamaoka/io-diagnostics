// Shared by tests/test_composition.js and docs/record_composition.js: a fixture host (F1) with the state fixture plugins, the arm
// composite plugin and a composition file over the fixture's two fdcanusb adapters and two D405s (docs/composition-v1.md).
//
//   rack (rack, batch "having")
//     robot-1 (robot) = arm-1 (arm, plugin state_arm: 272813EF can/1..3) + the D405 219323070276
//     robot-2 (robot) = arm-2 (arm, plugin state_arm: 28B3A25D  can/1..3) + the D405 130523071221
//
// Six joints in all (STATE_NODES=3 per adapter), all on one bus group ("rack-bus"), so "Calibrate all 6" queues; a real run lasts
// STATE_ACT_S seconds so the queue can be watched. Nothing touches hardware: the plugins are stdlib python that only dry-run.
'use strict';

const H = require('./entity_harness');
const { fs, path, os } = H;

const SOURCE = path.join(H.ROOT, 'tests', 'fixtures', 'plugins-state');
const ADAPTER_1 = 'usb:0483:5740:272813EF';
const ADAPTER_2 = 'usb:0483:5740:28B3A25D';
const CAMERA_1 = 'usb:8086:0b5b:219323070276';
const CAMERA_2 = 'usb:8086:0b5b:130523071221';
const PATHS = {
  rack: 'host/composite-rack', robot1: 'host/composite-rack-robot-1', robot2: 'host/composite-rack-robot-2',
  arm1: 'host/composite-rack-robot-1-arm', arm2: 'host/composite-rack-robot-2-arm',
  adapter1: 'host/usb1/port-1-3/usb-1-3', adapter2: 'host/usb1/port-1-5/usb-1-5',
  joint: (adapter, number) => `${adapter}/can/${number}`,
};

function compositionText({ rackBatch = 'having', extra = '', arm1Extra = '' } = {}) {
  const joints = (id) => [1, 2, 3].map((n) => `  "${id}#can/${n}",`).join('\n');
  return `version = 1

[[composite]]
id = "rack"
kind = "rack"
title = "Test rack"
batch = "${rackBatch}"
children = ["rack/robot-1", "rack/robot-2"]

[[composite]]
id = "rack/robot-1"
kind = "robot"
title = "Robot 1"
children = ["rack/robot-1/arm", "${CAMERA_1}"]

[[composite]]
id = "rack/robot-2"
kind = "robot"
title = "Robot 2"
children = ["rack/robot-2/arm", "${CAMERA_2}"]

[[composite]]
id = "rack/robot-1/arm"
kind = "arm"
title = "Arm 1"
plugin = "state_arm"
${arm1Extra}expects = { tracking_error_rev = 0.5, links_m = [0.3, 0.25, 0.1] }
children = [
${joints(ADAPTER_1)}
]

[[composite]]
id = "rack/robot-2/arm"
kind = "arm"
title = "Arm 2"
plugin = "state_arm"
children = [
${joints(ADAPTER_2)}
]
${extra}`;
}

function pluginDir(root, { periodS = 2, actS = 0.6 } = {}) {
  const dir = path.join(root, 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  for (const name of ['state_joint.toml', 'state_camera.toml', 'state_arm.toml']) {
    let text = fs.readFileSync(path.join(SOURCE, name), 'utf8').replace('{here}', SOURCE).replace(/period_s = \d+/, `period_s = ${periodS}`);
    if (name === 'state_joint.toml') text += `exclusive = "rack-bus"\nenv = { STATE_NODES = "3", STATE_ACT_S = "${actS}" }\n`;
    fs.writeFileSync(path.join(dir, name), text);
  }
  return dir;
}

// the fixture host with the composition file; returns { child, base, board, root, expected }
async function compositionHost({ fixture = 'F1', text, actS = 0.6, env = {} } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'composition-'));
  const expected = path.join(root, 'expected.toml');
  fs.writeFileSync(expected, text || compositionText());
  const server = await H.startServer(root, fixture, env, { plugins: pluginDir(root, { actS }), args: ['--expected', expected] });
  server.expected = expected;
  const names = (await (await fetch(`${server.base}/api/v1/plugins`)).json()).plugins.map((plugin) => plugin.name).sort().join();
  if (names !== 'state_arm,state_camera,state_joint') { H.stopServer(server.child); throw new Error(`refusing to go on: loaded ${names}`); }
  const ready = await H.waitFor(async () => {
    const node = await fetch(`${server.base}/api/v1/node?path=${encodeURIComponent(PATHS.arm1)}`).then((r) => r.json()).catch(() => ({}));
    const checks = (node.checks || []).map((check) => check.name);
    return checks.includes('Arm 1: all joints answer') && (node.actions || []).some((action) => action.name === 'all:calibrate');
  }, 45000, 400);
  if (!ready) { H.stopServer(server.child); throw new Error('the arm never reported'); }
  return server;
}

module.exports = { ...H, SOURCE, PATHS, ADAPTER_1, ADAPTER_2, CAMERA_1, CAMERA_2, compositionText, compositionHost, pluginDir };
