// The rack's real URDF and real description on a fixture host: what docs/record_rack_3d.js films. Fixture frame F1 has two fdcanusbs and two
// D405-like cameras; tests/fixtures/robot-description/plugins gives them the rack's own CAN ids, and a scratch copy of the description
// names the fixture's second adapter and cameras in place of the real ones. Nothing here opens a port or a camera: the boards and their
// levels are made up (a test file sets them), the model and the mapping are the real ones.
'use strict';

const H = require('../tests/entity_harness');
const { fs, os, path } = H;

const EXAMPLE = path.join(H.ROOT, 'examples', 'one-dof-test-rack', 'robot-description.toml');
const PLUGINS = path.join(H.ROOT, 'tests', 'fixtures', 'robot-description', 'plugins');

function rackConfig() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rack-3d-'));
  const config = path.join(root, 'config');
  fs.mkdirSync(config, { recursive: true });
  const text = fs.readFileSync(EXAMPLE, 'utf8').replaceAll('0607623A', '28B3A25D').replaceAll('218622274411', '219323070276').replaceAll('218622276036', '130523071221');
  fs.writeFileSync(path.join(config, 'robot-description.toml'), text);
  const levels = path.join(root, 'levels.json');
  fs.writeFileSync(levels, '{}');
  return { root, config, levels };
}

async function rackHost() {
  const env = rackConfig();
  const server = await H.startServer(env.root, 'F1', { RD_LEVELS: env.levels, RD_BOARDS: JSON.stringify({ '272813EF': [1, 14, 15], '28B3A25D': [4, 11, 12, 13, 21] }) }, { plugins: PLUGINS });
  await H.waitFor(async () => { const node = await H.getJson(server.base, `/api/v1/node?path=${encodeURIComponent('host/usb1/port-1-5/usb-1-5/can/21')}`).catch(() => ({})); return node.node && node.node.level === 'ok'; }, 40000);
  return { ...env, server, base: server.base };
}

module.exports = { rackConfig, rackHost };
