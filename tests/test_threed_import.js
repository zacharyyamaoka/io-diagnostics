// Bringing a robot into the 3D view (web/threed/, io_diagnostics/robot_library.py, docs/robot-description.md "From the page"), measured
// in headless Chrome on a fixture host: frame F1 with tests/fixtures/robot-description/plugins (stdlib, made-up boards whose levels the
// test sets through $RD_LEVELS), a robot this test builds itself. Never hardware. Positions are getBoundingClientRect, colours are pixels
// read back from the canvas, what persists is read back after a reload and from the file.
//
//   node tests/test_threed_import.js
//
// Zach, 2026-11-04: "I can just upload the URDF, etc. and then it could show the information live"; "the lowest rung for robot dx is I plug
// it in without any urdf etc and it just tells me what is up, and we always want that"; "now we can see which motors are where"; "(to
// think about is how we represent like the FDCANUSB in all of this ...)".
'use strict';

const { spawnSync } = require('child_process');
const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const FIXTURE = path.join(H.ROOT, 'tests', 'fixtures', 'robot-description');
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const OTHER_ADAPTER = 'host/usb1/port-1-5/usb-1-5';
const CAN1 = `${ADAPTER}/can/1`;
const CAN2 = `${ADAPTER}/can/2`;
const CAMERA = 'host/usb6/port-6-1/usb-6-1';
const VIEW = { width: 1440, height: 900 };
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'];

// a cube as ASCII STL, metres (the URDF applies no scale)
function cube(x, y, z, size) {
  const h = size / 2;
  const v = (a, b, c) => `vertex ${x + a * h} ${y + b * h} ${z + c * h}`;
  const quads = [[[1, -1, -1], [1, 1, -1], [1, 1, 1], [1, -1, 1]], [[-1, 1, -1], [-1, -1, -1], [-1, -1, 1], [-1, 1, 1]], [[-1, 1, -1], [1, 1, -1], [1, -1, -1], [-1, -1, -1]],
    [[-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], [[1, 1, -1], [-1, 1, -1], [-1, 1, 1], [1, 1, 1]], [[-1, -1, -1], [1, -1, -1], [1, -1, 1], [-1, -1, 1]]];
  const lines = ['solid c'];
  for (const q of quads) for (const tri of [[0, 1, 2], [0, 2, 3]]) lines.push('facet normal 0 0 0', 'outer loop', ...tri.map((i) => v(...q[i])), 'endloop', 'endfacet');
  return `${lines.join('\n')}\nendsolid c\n`;
}
// a long rack like the real one: everything lumped into base_link, as an assembly export does: a frame and two motors of four bodies each
function buildRobot(folder) {
  fs.mkdirSync(path.join(folder, 'urdf', 'meshes'), { recursive: true });
  const visuals = [];
  const put = (name, x, y, z, size) => { fs.writeFileSync(path.join(folder, 'urdf', 'meshes', name), cube(x, y, z, size)); visuals.push(`<visual><geometry><mesh filename="meshes/${name}"/></geometry></visual>`); };
  for (let i = 0; i < 8; i += 1) put(`Rail_1__Beam_${i + 1}__0.stl`, 0, 0.1 * i - 0.35, 0.02, 0.09);
  for (let i = 0; i < 4; i += 1) put(`Plate_1___Motor_1__Body_${i + 1}__0.stl`, 0.03 * i, -0.2, 0.2, 0.08);
  for (let i = 0; i < 4; i += 1) put(`Plate_1___Motor_2__Body_${i + 1}__0.stl`, 0.03 * i, 0.2, 0.2, 0.08);
  fs.writeFileSync(path.join(folder, 'urdf', 'mini.urdf'), `<?xml version="1.0"?>
<robot name="mini_rack">
  <link name="base_link">${visuals.join('')}</link>
  <link name="arm"><visual><origin xyz="0.05 0.52 0.2"/><geometry><box size="0.04 0.04 0.2"/></geometry></visual></link>
  <joint name="arm_joint" type="revolute"><parent link="base_link"/><child link="arm"/><axis xyz="1 0 0"/><limit lower="-3" upper="3" effort="1" velocity="1"/></joint>
</robot>
`);
}
// a ROS package as a zip holds one: package:// paths, meshes beside urdf/, not next to the URDF, in the two other formats
function buildPackage(folder) {
  fs.mkdirSync(path.join(folder, 'urdf'), { recursive: true });
  fs.mkdirSync(path.join(folder, 'meshes'), { recursive: true });
  fs.writeFileSync(path.join(folder, 'package.xml'), '<package><name>demo_pkg</name></package>');
  const corners = [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]];
  const faces = [[1, 2, 3, 4], [5, 8, 7, 6], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 8, 4], [4, 8, 5, 1]];
  fs.writeFileSync(path.join(folder, 'meshes', 'body.obj'), `${corners.map(([x, y, z]) => `v ${x * 0.1} ${y * 0.1 - 0.15} ${z * 0.1}`).join('\n')}\n${faces.map((f) => `f ${f.join(' ')}`).join('\n')}\n`);
  fs.writeFileSync(path.join(folder, 'meshes', 'head.dae'), `<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
  <library_geometries><geometry id="m" name="m"><mesh>
    <source id="p"><float_array id="pa" count="12">0.1 0.05 -0.1  0.1 0.25 -0.1  0.1 0.25 0.1  0.1 0.05 0.1</float_array>
      <technique_common><accessor source="#pa" count="4" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
    <vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
    <triangles count="2"><input semantic="VERTEX" source="#v" offset="0"/><p>0 1 2 0 2 3</p></triangles>
  </mesh></geometry></library_geometries>
  <library_visual_scenes><visual_scene id="s"><node id="n"><instance_geometry url="#m"/></node></visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>
`);
  fs.writeFileSync(path.join(folder, 'urdf', 'demo.urdf'), `<?xml version="1.0"?>
<robot name="ros_demo">
  <link name="body"><visual><geometry><mesh filename="package://demo_pkg/meshes/body.obj"/></geometry></visual></link>
  <link name="head"><visual><geometry><mesh filename="package://demo_pkg/meshes/head.dae"/></geometry></visual></link>
  <joint name="neck" type="fixed"><parent link="body"/><child link="head"/></joint>
</robot>
`);
}
function zipFolder(folder, out, extra = '') {
  const script = `import os, sys, zipfile
folder, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
    for base, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(base, name)
            archive.write(path, os.path.relpath(path, os.path.dirname(folder)))
${extra}
`;
  const made = spawnSync('python3', ['-c', script, folder, out]);
  if (made.status !== 0) throw new Error(String(made.stderr));
}
function setup() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'threed-import-'));
  const config = path.join(root, 'config');
  fs.mkdirSync(config, { recursive: true });
  const levels = path.join(root, 'levels.json');
  fs.writeFileSync(levels, '{}');
  const robot = path.join(root, 'made', 'mini');
  buildRobot(robot);
  const zip = path.join(root, 'mini.zip');
  zipFolder(robot, zip);
  const evil = path.join(root, 'evil.zip');
  zipFolder(robot, evil, `    info = zipfile.ZipInfo('x')
    info.filename = '../../evil.txt'
    archive.writestr(info, b'evil')`);
  // a cad2robot/1 bundle: the same robot with a robot.json that names CAN 2 on the arm's joint
  const bundle = path.join(root, 'made', 'bundle');
  fs.cpSync(robot, bundle, { recursive: true });
  fs.writeFileSync(path.join(bundle, 'robot.json'), JSON.stringify({ schema: 'cad2robot/1', urdf: 'urdf/mini.urdf', joints: [{ name: 'arm_joint', occurrence: 'Plate:1+(Motor):2', can_id: 2 }], cameras: [] }));
  const bundleZip = path.join(root, 'bundle.zip');
  zipFolder(bundle, bundleZip);
  const rosPackage = path.join(root, 'made', 'demo_pkg');
  buildPackage(rosPackage);
  const packageZip = path.join(root, 'package.zip');
  zipFolder(rosPackage, packageZip);
  return { root, config, levels, robot, zip, evil, bundleZip, packageZip };
}
const ready3d = (page) => page.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states; }, null, { timeout: 60000 });
const pixel = (page, x, y) => page.evaluate(([px, py]) => IOThreeD.debug().scene.pixel(px, py), [x, y]);
const project = (page, index) => page.evaluate((i) => IOThreeD.debug().scene.project(i), index);
const rectOf = (page, selector) => page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom, visible: r.width > 0 && r.height > 0 }; }, selector);
const describe = (base) => H.getJson(base, '/api/v1/robot-description');
const reddish = ([r, g, b]) => r - Math.max(g, b);
const blueish = ([r, g, b]) => b - r;
async function openThreed(page, base) {
  await page.goto(`${base}/?view=threed`);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.data && window.IOThreeD && IOThreeD.debug().desc, null, { timeout: 30000 });
  await page.waitForTimeout(300);
}
async function treeRow(page, entity) {
  const key = await page.evaluate((p) => S.built.byEntity.get(p), entity);
  return `.t6-row[data-t6-path="${key}"] .t6-name`;
}
// a pixel of a body by its mesh file: the projected centre of its box, from the page's own numbers
const fileAt = (page, file) => page.evaluate((name) => {
  const scene = IOThreeD.debug().scene;
  return scene.meshAt(name);
}, file);

(async () => {
  const env = setup();
  const server = await H.startServer(env.root, 'F1', { RD_LEVELS: env.levels }, { plugins: path.join(FIXTURE, 'plugins') });
  const { base } = server;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  let failed = false;
  const fileText = () => fs.readFileSync(path.join(env.config, 'robot-description.toml'), 'utf8');
  try {
    await H.waitFor(async () => { const node = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(CAN2)}`).catch(() => ({})); return node.node && node.node.level === 'ok'; }, 30000);
    const context = await browser.newContext({ viewport: VIEW, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = H.watchErrors(page, base);

    // ------------------------------------------------------------------------- 1. the lowest rung, and a zip brought in ----
    await openThreed(page, base);
    const emptyText = await page.$eval('.td-empty', (el) => el.textContent);
    check('with no description the empty state offers the upload (a drop zone, a folder, a zip)', /Drop a URDF/.test(emptyText) && Boolean(await page.$('.td-empty [data-td-pick="folder"]')) && Boolean(await page.$('.td-empty [data-td-pick="zip"]')), emptyText.slice(0, 120));
    check('... and still says how to write the file by hand', /robot-description\.toml/.test(emptyText) && /\[\[part\]\]/.test(emptyText));
    check('... and the tree stays beside it', (await rectOf(page, '#tree-host')).visible);
    await page.setInputFiles('.td-file-zip', env.evil);
    await page.waitForFunction(() => /Not brought in/.test(document.querySelector('.td-empty-status').textContent), null, { timeout: 15000 }).catch(() => {});
    const refusal = await page.$eval('.td-empty-status', (el) => el.textContent);
    check('a zip that climbs out of its folder is refused on the page, with the reason', /Not brought in/.test(refusal) && /unsafe/i.test(refusal), refusal);
    check('... nothing was written', !fs.existsSync(path.join(env.config, 'robot-description.toml')) && !fs.existsSync(path.join(env.root, 'evil.txt')) && !fs.existsSync(path.join(env.config, 'evil.txt')));
    errors.errors.length = 0; // the 400 of the refused upload, on purpose

    const started = Date.now();
    const asked = { upload: 0, next: 0 };
    page.on('response', (response) => { if (/robot-description\/upload/.test(response.url()) && response.status() === 200) asked.upload = Date.now(); });
    page.on('request', (request) => { if (asked.upload && !asked.next && /api\/v1\/robot-description$/.test(request.url())) asked.next = Date.now(); });
    await page.setInputFiles('.td-file-zip', env.zip);
    await ready3d(page);
    const elapsed = Date.now() - started;
    check('the page asks for the new description the moment the upload answers (not on its 5 s poll)', asked.next > 0 && asked.next - asked.upload < 900, asked);
    const canvas = await rectOf(page, '.td-canvas');
    const shown = await page.evaluate(() => { const d = IOThreeD.debug(); return { robot: d.desc.robot, meshes: d.scene.timings().meshes, url: location.search }; });
    check('a zip dropped into the empty state shows at once, with no reload', shown.robot === 'mini_rack' && shown.meshes === 16 && canvas.visible, { ...shown, ms: elapsed });
    const kept = path.join(env.config, 'robot-descriptions', 'mini-rack', 'mini', 'urdf', 'mini.urdf');
    check('... unpacked under robot-descriptions/<name>/ and written as the active description', fs.existsSync(kept) && fileText().includes(kept));
    await page.waitForTimeout(300);
    const fill = await page.evaluate(() => IOThreeD.debug().scene.viewFill());
    check('the model fills the viewport on load (Fusion\'s home: the wider way at least 84 %, nothing cut)', Math.max(fill.width, fill.height) > 0.84 && Math.max(fill.width, fill.height) <= 1.0 && fill.minX > -1 && fill.maxX < 1 && fill.minY > -1 && fill.maxY < 1, fill);
    check('... and it is centred', Math.abs(fill.minX + fill.maxX) < 0.06 && Math.abs(fill.minY + fill.maxY) < 0.06, fill);

    // ---------------------------------------------------------------- 2. what no CAD has: chips and the list ----
    const chips = await page.$$eval('.td-hw-chip', (els) => els.map((el) => ({ path: el.dataset.tdHw, kind: el.dataset.kind, text: el.textContent })));
    check('the hardware row has a chip per adapter and one for the computer', chips.some((c) => c.path === ADAPTER && c.kind === 'adapter' && /272813EF/.test(c.text)) && chips.some((c) => c.path === OTHER_ADAPTER) && chips.some((c) => c.path === 'host' && c.kind === 'computer'), chips);
    const hw = await rectOf(page, '.td-hw');
    const viewport = await rectOf(page, '.td-view');
    check('... floating in the viewport, under the tools', hw.visible && hw.x >= viewport.x && hw.y > viewport.y + 30 && hw.y < viewport.y + 90 && hw.right <= viewport.right, hw);
    await page.click('.td-nim summary');
    await page.waitForTimeout(100);
    const list = await page.$$eval('.td-nim-item', (els) => els.map((el) => el.textContent));
    check('nothing placed yet: every board and camera is in the "not in the model" list, each once', list.length === 7 && new Set(list).size === 5, list); // four boards (two pairs read alike), three cameras
    check('... each with its status', await page.$$eval('.td-nim-item .td-dot', (els) => els.length) === list.length);
    await page.click(`.td-nim-item[data-td-nim="${CAN2}"]`);
    await page.waitForFunction((p) => S.sel === p, CAN2, { timeout: 5000 }).catch(() => {});
    check('clicking a device in the list selects it in the tree', (await page.evaluate(() => S.sel)) === CAN2);

    // ------------------------------------------------------------------------------- 3. Map devices ----
    await page.click('[data-td-map]');
    await page.waitForTimeout(150);
    const bar = await rectOf(page, '.td-mapbar');
    const barText = await page.$eval('.td-mapbar', (el) => el.textContent);
    check('Map devices names the device it is placing', bar.visible && /CAN 2/.test(barText), barText);
    const motor1 = await fileAt(page, 'Plate_1___Motor_1__Body_2__0.stl');
    const beforePx = await pixel(page, motor1.x, motor1.y);
    await page.mouse.move(motor1.x, motor1.y, { steps: 3 });
    await page.waitForTimeout(200);
    const hover = await page.evaluate(() => { const d = IOThreeD.debug(); const levels = d.map.levels || []; return { level: d.map.level, labels: levels.map((l) => `${l.label}:${l.meshes.length}`), tip: document.querySelector('.td-tip').textContent, tipShown: !document.querySelector('.td-tip').hidden }; });
    check('hovering a body inside a lump offers its component, not the whole lump (Motor:1, 4 of 16 meshes)', hover.tipShown && /Motor:1/.test(hover.tip) && hover.labels[hover.level] === 'Motor:1:4', hover);
    const litPx = await pixel(page, motor1.x, motor1.y);
    check('... and highlights exactly that component (blue, measured)', blueish(litPx) > blueish(beforePx) + 15, { beforePx, litPx });
    const rail = await fileAt(page, 'Rail_1__Beam_2__0.stl');
    const railPx = await pixel(page, rail.x, rail.y);
    check('... the rest of the lump stays grey', Math.abs(blueish(railPx)) < 20, railPx);
    await page.mouse.click(motor1.x, motor1.y);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 1, null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(400);
    const first = (await describe(base)).parts[0] || {};
    check('a click places the device: the assignment is saved at once', first.path === CAN2 && first.meshes && first.meshes[0] === 'Plate_1___Motor_1__*' && /Plate_1___Motor_1__\*/.test(fileText()), first);
    check('... under the keys that find it after a replug, not its entity path', first.match && first.match.hardware_id === 'usb:0483:5740:272813EF' && first.match.node === 'can/2', first.match);
    const camera = await page.evaluate(() => IOThreeD.debug().scene.cameraText());
    check('... the model did not reload (one canvas, the same 16 meshes)', (await page.$$('.td-canvas')).length === 1 && await page.evaluate(() => IOThreeD.debug().scene.timings().meshes) === 16);
    // the mouse off the model: the highlight goes, the device's own colour shows
    await page.mouse.move(viewport.x + 20, viewport.y + viewport.height - 20);
    await page.waitForTimeout(250);
    const placedPx = await pixel(page, motor1.x, motor1.y);
    await page.click('[data-td-map-done]');
    await page.waitForTimeout(200);
    // the person's camera, by a real drag: it must survive the reload, not the scene's own home pose
    await page.mouse.move(viewport.x + 300, viewport.y + 500);
    await page.mouse.down();
    await page.mouse.move(viewport.x + 380, viewport.y + 470, { steps: 4 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    const dragged = await page.evaluate(() => new URLSearchParams(location.search).get('cam'));
    await page.reload();
    await ready3d(page);
    await page.waitForTimeout(400);
    const reopened = await page.evaluate(() => ({ cam: IOThreeD.debug().scene.cameraText(), url: new URLSearchParams(location.search).get('cam') }));
    check('the camera in the URL survives a reload (the model does not go home over it)', dragged && dragged !== camera && reopened.cam === dragged && reopened.url === dragged, { dragged, reopened });
    const afterReload = await page.evaluate(() => { const d = IOThreeD.debug(); return { parts: d.desc.parts.map((p) => p.path), state: (d.states || []).map((s) => s.level) }; });
    check('the assignment persists across a reload', afterReload.parts.length === 1 && afterReload.parts[0] === CAN2, afterReload);
    const motorPart = await page.evaluate(() => IOThreeD.debug().scene.partOfFile('Plate_1___Motor_1__Body_3__0.stl'));
    const railPart = await page.evaluate(() => IOThreeD.debug().scene.partOfFile('Rail_1__Beam_2__0.stl'));
    check('... every mesh of the component is the part, the rest of the link is not', motorPart === 0 && railPart === -1, { motorPart, railPart });

    // the camera came back from the URL (S.cam), so the same pixel is the same body
    const again = await fileAt(page, 'Plate_1___Motor_1__Body_2__0.stl');
    fs.writeFileSync(env.levels, JSON.stringify({ '272813EF/can/2': 'error' }));
    await page.waitForFunction(() => (IOThreeD.debug().states[0] || {}).level === 'error', null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(400);
    const errPx = await pixel(page, again.x, again.y);
    const railNow = await fileAt(page, 'Rail_1__Beam_2__0.stl');
    const railErr = await pixel(page, railNow.x, railNow.y);
    check('a board going to error recolours its mapped part (pixel read back)', reddish(errPx) > 60, { errPx, placedPx });
    check('... and only that part: the same link\'s other bodies stay grey', reddish(railErr) < 25, railErr);
    fs.writeFileSync(env.levels, '{}');
    await page.waitForFunction(() => (IOThreeD.debug().states[0] || {}).level === 'ok', null, { timeout: 20000 }).catch(() => {});

    // undo, by clicking again
    await page.click(await treeRow(page, CAN2));
    await page.waitForTimeout(300);
    await page.evaluate(() => IOThreeD.debug().scene.home());
    await page.waitForTimeout(600);
    await page.click('[data-td-map]');
    await page.waitForTimeout(100);
    const motorAgain = await fileAt(page, 'Plate_1___Motor_1__Body_2__0.stl');
    await page.mouse.move(motorAgain.x, motorAgain.y, { steps: 3 });
    await page.waitForTimeout(200);
    await page.mouse.click(motorAgain.x, motorAgain.y);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 0, null, { timeout: 8000 }).catch(() => {});
    check('clicking the same part again undoes it', (await describe(base)).parts.length === 0 && !/\[\[part\]\]/.test(fileText()));

    // a bigger piece: a keyboard step, the button (the pointer leaves the body to reach it), Shift+wheel; kept for the next body
    await page.mouse.move(motorAgain.x + 3, motorAgain.y, { steps: 2 });
    await page.waitForTimeout(250);
    const plain = await page.evaluate(() => { const d = IOThreeD.debug(); return d.map.levels[d.map.level].meshes.length; });
    await page.keyboard.press(']');
    await page.waitForTimeout(100);
    const wider = await page.evaluate(() => { const d = IOThreeD.debug(); return d.map.levels[d.map.level].meshes.length; });
    check('] takes a bigger piece of the same body', wider > plain, { plain, wider });
    await page.click('[data-td-map-level="-1"]');
    await page.waitForTimeout(100);
    await page.mouse.move(motorAgain.x, motorAgain.y, { steps: 2 });
    await page.waitForTimeout(250);
    const narrower = await page.evaluate(() => { const d = IOThreeD.debug(); return { meshes: d.map.levels[d.map.level].meshes.length, shift: d.map.shift }; });
    check('Narrower, reached with the mouse, steps it back', narrower.meshes === plain && narrower.shift === 0, { plain, narrower });
    const zoomBefore = await page.evaluate(() => IOThreeD.debug().scene.cameraText());
    await page.keyboard.down('Shift');
    await page.mouse.wheel(0, 120);
    await page.keyboard.up('Shift');
    await page.waitForTimeout(250);
    const wheeled = await page.evaluate(() => { const d = IOThreeD.debug(); return { meshes: d.map.levels[d.map.level].meshes.length, cam: d.scene.cameraText() }; });
    check('Shift+wheel does it too, and does not zoom', wheeled.meshes > plain && wheeled.cam === zoomBefore, { plain, wheeled, zoomBefore });
    await page.keyboard.press('[');
    await page.waitForTimeout(100);

    // a device from the list: arm it there, click the arm (a link of one mesh is placed whole)
    await page.mouse.move(viewport.x + 20, viewport.y + viewport.height - 20);
    await page.waitForTimeout(200);
    await page.evaluate(() => { const d = document.querySelector('.td-nim'); if (d && !d.open) d.open = true; });
    await page.waitForTimeout(100);
    await page.click(`.td-nim-item[data-td-nim="${CAMERA}"]`);
    await page.waitForFunction((p) => S.sel === p, CAMERA, { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(250);
    const armAt = await page.evaluate(() => IOThreeD.debug().scene.linkAt('arm'));
    await page.mouse.move(armAt.x, armAt.y, { steps: 3 });
    await page.waitForTimeout(250);
    await page.mouse.click(armAt.x, armAt.y);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 1, null, { timeout: 8000 }).catch(() => {});
    const placed = (await describe(base)).parts[0] || {};
    check('a device armed from the list goes on the link clicked (the whole link, it is one body)', placed.path === CAMERA && JSON.stringify(placed.links) === '["arm"]' && placed.match.hardware_id === 'usb:8086:0b5b:219323070276', placed);
    await page.waitForTimeout(300);
    const listNow = await page.$$eval('.td-nim-item', (els) => els.map((el) => el.dataset.tdNim));
    check('... and leaves the list', !listNow.includes(CAMERA) && listNow.length === 6, listNow);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    check('Escape leaves Map devices', await page.$eval('.td-mapbar', (el) => el.hidden));

    // ---------------------------------------------------------------- 4. an adapter's leader lines, measured ----
    // two boards of one adapter on two bodies
    await page.click(await treeRow(page, CAN1));
    await page.waitForTimeout(250);
    await page.click('[data-td-map]');
    await page.waitForTimeout(100);
    const m1 = await fileAt(page, 'Plate_1___Motor_1__Body_2__0.stl');
    await page.mouse.move(m1.x, m1.y, { steps: 3 });
    await page.waitForTimeout(200);
    await page.mouse.click(m1.x, m1.y);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 2, null, { timeout: 8000 }).catch(() => {});
    await page.click(await treeRow(page, CAN2));
    await page.waitForTimeout(250);
    const m2 = await fileAt(page, 'Plate_1___Motor_2__Body_2__0.stl');
    await page.mouse.move(m2.x, m2.y, { steps: 3 });
    await page.waitForTimeout(200);
    await page.mouse.click(m2.x, m2.y);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 3, null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(300);
    await page.click('[data-td-map-done]');
    await page.waitForTimeout(100);
    await page.mouse.move(viewport.x + 20, viewport.y + viewport.height - 20);
    await page.click('.td-canvas', { position: { x: 30, y: viewport.height - 40 } }).catch(() => {});
    await page.waitForTimeout(200);
    await page.evaluate(() => { select('host/usb1/port-1-5/usb-1-5/can/1'); pushNav(); render(); });
    await page.waitForTimeout(300);
    const before = await page.$$eval('.td-leaders line', (els) => els.length);
    const parts = (await describe(base)).parts;
    const indexes = parts.map((part, index) => (part.path && part.path.startsWith(`${ADAPTER}/`) ? index : -1)).filter((index) => index >= 0);
    const motorBefore = await pixel(page, m1.x, m1.y);
    const chip = await rectOf(page, `.td-hw-chip[data-td-hw="${ADAPTER}"]`);
    await page.mouse.move(chip.x + chip.width / 2, chip.y + chip.height / 2, { steps: 3 });
    await page.waitForTimeout(350);
    const lines = await page.$$eval('.td-leaders line', (els) => els.map((el) => ({ part: Number(el.dataset.part), x1: Number(el.getAttribute('x1')), y1: Number(el.getAttribute('y1')), x2: Number(el.getAttribute('x2')), y2: Number(el.getAttribute('y2')) })));
    const anchors = await Promise.all(indexes.map((index) => project(page, index)));
    const view = await rectOf(page, '.td-view');
    const landed = indexes.every((index, i) => { const line = lines.find((l) => l.part === index); return line && Math.hypot(line.x2 + view.x - anchors[i].x, line.y2 + view.y - anchors[i].y) < 2; });
    const fromChip = lines.every((l) => Math.abs(l.x1 + view.x - (chip.x + chip.width / 2)) < 2 && Math.abs(l.y1 + view.y - chip.bottom) < 2);
    check('hovering an adapter\'s chip draws one leader line to each of its boards\' parts', before === 0 && lines.length === indexes.length && indexes.length === 2, { before, lines: lines.length, indexes });
    check('... each line starts at the chip and ends on its part (measured, 2 px)', landed && fromChip, { lines, anchors, chip });
    const lit = await page.evaluate(() => IOThreeD.debug().states.map((s) => s.lit));
    check('... and lights exactly its boards\' parts', indexes.every((index) => lit[index]) && lit.filter(Boolean).length === indexes.length, lit);
    const motorLit = await pixel(page, m1.x, m1.y);
    check('... visibly (blue, pixel read back)', blueish(motorLit) > blueish(motorBefore) + 10, { motorBefore, motorLit });
    await page.mouse.move(viewport.x + 200, viewport.y + viewport.height - 30, { steps: 3 });
    await page.waitForTimeout(300);
    check('leaving the chip takes the lines away', (await page.$$('.td-leaders line')).length === 0);
    await page.click(`.td-hw-chip[data-td-hw="${ADAPTER}"]`);
    await page.waitForFunction((p) => S.sel === p, ADAPTER, { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(300);
    await page.mouse.move(viewport.x + 200, viewport.y + viewport.height - 30, { steps: 3 });
    await page.waitForTimeout(300);
    const selRow = await page.evaluate(() => { const row = document.querySelector('#tree-host .t6-row[aria-selected="true"]'); return row ? row.textContent : null; });
    check('clicking the chip selects the adapter in the tree', /272813EF/.test(selRow || ''), selRow);
    check('... and its lines stay while it is selected', (await page.$$('.td-leaders line')).length === indexes.length);
    await page.evaluate(() => IOThreeD.debug().scene.setCamera('0.9,0.9,1.4,0,0.1,0'));
    await page.waitForTimeout(400);
    const moved = await page.$$eval('.td-leaders line', (els) => els.map((el) => Number(el.getAttribute('x2'))));
    const anchorsNow = await project(page, indexes[0]);
    check('... and follow the camera', moved.some((x) => Math.abs(x + view.x - anchorsNow.x) < 2), { moved, anchorsNow });

    // ---------------------------------------------------------------- 5. a device the model does not have ----
    fs.writeFileSync(path.join(env.config, 'robot-description.toml'), `${fileText()}\n[[part]]\nname = "Camera at a point"\nat = [0.4, 0.3, 0.3]\nlook_at = [0, 0, 0.1]\nserial = "130523071221"\nnote = "test"\n`);
    await page.waitForFunction(() => IOThreeD.debug().desc.parts.length === 4 && IOThreeD.debug().scene.partOfGlyph(3), null, { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(400);
    await page.evaluate(() => IOThreeD.debug().scene.home());
    await page.waitForTimeout(700);
    const glyphAt = await project(page, 3);
    check('a device placed at a point draws as a small camera and is on screen', Boolean(glyphAt && glyphAt.inside), glyphAt);
    await page.mouse.click(glyphAt.x, glyphAt.y);
    await page.waitForFunction(() => S.sel === 'host/usb6/port-6-2/usb-6-2', null, { timeout: 5000 }).catch(() => {});
    check('... and picks like any part (it selects its camera in the tree)', (await page.evaluate(() => S.sel)) === 'host/usb6/port-6-2/usb-6-2');

    // ---------------------------------------------------------------- 6. another robot, kept ones, switch, remove ----
    await page.click('[data-td-model]');
    await page.waitForTimeout(200);
    await page.setInputFiles('.td-file-folder', env.robot);
    await page.waitForFunction(() => { const d = IOThreeD.debug().desc; return d && d.name === 'mini-rack-2' && d.parts.length === 0; }, null, { timeout: 20000 }).catch(() => {});
    await ready3d(page);
    await page.waitForTimeout(500);
    let info = await describe(base);
    check('a folder is brought in too, as a second description', info.name === 'mini-rack-2' && (await page.evaluate(() => IOThreeD.debug().scene.timings().meshes)) === 16, info.name);
    await page.click('[data-td-model]');
    await page.waitForTimeout(600);
    const menu = await page.$$eval('.td-menu .td-kept-row', (els) => els.map((el) => el.textContent));
    check('the Robot menu lists what is kept, the shown one marked', menu.length === 2 && menu.some((t) => /mini-rack-2/.test(t) && /showing/.test(t)), menu);
    await page.click('.td-menu [data-td-use="mini-rack"]');
    await page.waitForFunction(() => { const d = IOThreeD.debug().desc; return d && d.name === 'mini-rack' && d.parts.length === 4; }, null, { timeout: 15000 }).catch(() => {});
    await ready3d(page);
    await page.waitForTimeout(500);
    info = await describe(base);
    check('switching back brings the first one with its mappings', info.name === 'mini-rack' && info.parts.length === 4, info.parts.length);
    await page.click('[data-td-model]');
    await page.waitForTimeout(500);
    await page.click('.td-menu [data-td-remove="mini-rack-2"]');
    await page.waitForTimeout(100);
    check('Remove asks once more on the button itself', (await page.$eval('.td-menu [data-td-remove="mini-rack-2"]', (el) => el.textContent)) === 'Remove it?' && fs.existsSync(path.join(env.config, 'robot-descriptions', 'mini-rack-2')));
    await page.click('.td-menu [data-td-remove="mini-rack-2"]');
    await page.waitForFunction(() => IOThreeD.debug().desc.library.length === 1, null, { timeout: 8000 }).catch(() => {});
    check('... and removes it from this PC', !fs.existsSync(path.join(env.config, 'robot-descriptions', 'mini-rack-2')) && (await describe(base)).library.length === 1);

    // dropped on the view: the same path, with the drop hint while it is carried over
    const dropped = await page.evaluate(async (base64) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], 'mini.zip', { type: 'application/zip' }));
      const root = document.querySelector('.td-root');
      root.dispatchEvent(new DragEvent('dragenter', { dataTransfer: transfer, bubbles: true, cancelable: true }));
      const hint = getComputedStyle(document.querySelector('.td-drop')).display;
      root.dispatchEvent(new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true }));
      return { hint, after: root.classList.contains('td-dropping') };
    }, fs.readFileSync(env.zip).toString('base64'));
    check('files carried over the view show the drop hint, and it goes on the drop', dropped.hint === 'flex' && dropped.after === false, dropped);
    await page.waitForFunction(() => { const d = IOThreeD.debug().desc; return d && d.name === 'mini-rack-2' && d.parts.length === 0; }, null, { timeout: 20000 }).catch(() => {});
    await ready3d(page);
    await page.waitForTimeout(300);
    check('a zip dropped on the view is brought in', (await describe(base)).name === 'mini-rack-2' && (await page.evaluate(() => IOThreeD.debug().scene.timings().meshes)) === 16);

    // a Cad2Robot bundle brings its own mapping
    await page.setInputFiles('.td-file-zip', env.bundleZip);
    await page.waitForFunction(() => { const d = IOThreeD.debug().desc; return d && d.name !== 'mini-rack' && d.parts.length === 1; }, null, { timeout: 20000 }).catch(() => {});
    await ready3d(page);
    await page.waitForTimeout(400);
    info = await describe(base);
    check('a cad2robot/1 bundle maps itself from its robot.json', info.parts.length === 1 && info.parts[0].match.can_id === 2 && info.parts[0].links[0] === 'arm' && Boolean(info.parts[0].path), info.parts);

    // any URDF: a ROS package with package:// paths, a meshes folder beside urdf/, an OBJ and a Collada mesh
    await page.setInputFiles('.td-file-zip', env.packageZip);
    await page.waitForFunction(() => { const d = IOThreeD.debug().desc; return d && d.robot === 'ros_demo'; }, null, { timeout: 20000 }).catch(() => {});
    await ready3d(page);
    await page.waitForTimeout(500);
    const ros = await page.evaluate(() => { const d = IOThreeD.debug(); return { packages: d.desc.packages, timings: d.scene.timings(), obj: d.scene.meshAt('body.obj'), dae: d.scene.meshAt('head.dae') }; });
    check('a ROS package: package:// resolves inside the upload, OBJ and Collada meshes draw', ros.packages.demo_pkg === 'demo_pkg' && ros.timings.meshes === 2 && ros.timings.failed.length === 0 && Boolean(ros.obj) && Boolean(ros.dae), { packages: ros.packages, meshes: ros.timings.meshes, failed: ros.timings.failed });
    const background = await pixel(page, viewport.x + viewport.width - 40, viewport.y + viewport.height - 30);
    const objPx = await pixel(page, ros.obj.x, ros.obj.y);
    const daePx = await pixel(page, ros.dae.x, ros.dae.y);
    check('... and both are really on screen (pixels differ from the background)', Math.abs(objPx[0] - background[0]) > 15 && Math.abs(daePx[0] - background[0]) > 15, { objPx, daePx, background });
    info = await describe(base);

    // the lowest rung again: remove everything
    for (const item of info.library) {
      await page.evaluate((name) => fetch('/api/v1/robot-description/remove', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ name }) }), item.name); // eslint-disable-line no-await-in-loop
    }
    await page.waitForFunction(() => { const e = document.querySelector('.td-empty'); return e && !e.hidden && /No robot description/.test(e.textContent); }, null, { timeout: 12000 }).catch(() => {});
    check('with every description removed RobotDx is at its lowest rung: the empty state, no canvas', !(await describe(base)).present && (await page.$$('.td-canvas')).length === 0);
    await page.click('.l1-ribbon [data-l1="tree"]');
    await page.waitForTimeout(400);
    check('... and the Tree view works as ever', (await rectOf(page, '#inspector')).visible);
    check('no page errors', errors.errors.length === 0, errors.errors.slice(0, 5));
    await context.close();
  } catch (error) {
    failed = true;
    console.error(error);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary() || (failed ? 1 : 0));
})();
