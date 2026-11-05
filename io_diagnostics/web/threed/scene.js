// The 3D view's scene (docs/robot-description.md): the robot description's URDF drawn with three.js, picked, coloured and framed.
// One scene for every info layer (threed/core.js draws those): the layers only read what this module reports (hover, pick, the
// camera, where a part is on screen) and hand it the parts' states. Loaded with `import()` the first time the 3D view opens, through
// index.html's importmap (vendor/three, vendor/urdf-loader; vendor/three/NOTICE.md). It never talks to the host except to fetch the
// URDF and its meshes from GET /api/v1/robot-description/files/.
//
// Look (Zach, 2026-10-29: "basically like Fusion 360"): neutral grey bodies under a soft hemisphere + key + fill light, a ground grid,
// an axis gizmo top-right (three's ViewHelper), selection as a blue tint. Mouse as Fusion: left drag orbits, middle or right drag
// pans, the wheel zooms; a click without a drag picks.
// WHY render on demand: the page is open all day beside the rack; a still scene draws nothing (a frame only on a camera move, a state
// change, a hover or an animation).
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { ViewHelper } from 'three/examples/jsm/helpers/ViewHelper.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import URDFLoader from '../vendor/urdf-loader/URDFLoader.js';

const GHOST_OPACITY = 0.15;   // a mapped device that is not detected (Zach: "a ghost (transparent)")
const SELECT_MIX = 0.3;       // how far a selected part's colour moves towards the selection blue (its status colour stays readable)
const HOVER_EMISSIVE = 0.10;
const FLY_MS = 380;
const DRAG_PX = 4;            // a press that moves more than this is a drag (orbit / pan), not a pick
const HOME_AZIMUTH = 26;      // degrees: Fusion's home stands to the front's right and a little above
const HOME_ELEVATION = 16;
const HOME_FILL = 0.92;       // of the viewport's narrower fit: the model fills it with a small margin
const LINK_IS_A_BODY = 12;    // a link of this many meshes or fewer is one body to a click; more is an assembly export's lump
const LUMP_SHARE = 0.3;       // ... and a click takes the widest component under this share of the lump

// a mesh file name against a part's pattern: * and ? only, the host's rule (robot_description.mesh_matches)
const wildcard = (pattern) => new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);

export function createScene(container, hooks = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); // WHY preserve: tests read pixels back
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = 'td-canvas';
  container.appendChild(renderer.domElement);
  renderer.autoClear = false;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.005, 200);
  camera.position.set(1.2, 0.9, 1.2);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
  controls.screenSpacePanning = true;
  controls.zoomToCursor = true;

  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.9);
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  const fill = new THREE.DirectionalLight(0xffffff, 0.55);
  scene.add(hemi, key, fill);
  // URDF is Z up, three is Y up: the robot turns -90 deg about X (URDF x stays x, URDF z is three's y, URDF y is three's -z)
  const root = new THREE.Group();
  root.rotation.x = -Math.PI / 2;
  scene.add(root);
  let grid = null;

  const gizmo = new ViewHelper(camera, renderer.domElement);
  gizmo.location = { top: 10, right: 10, bottom: 0, left: null };
  gizmo.setLabels('X', 'Z', '−Y');

  const st = {
    robot: null, links: new Map(), partOfLink: new Map(), partPatterns: [], parts: [], meshes: [], neutral: null, partMats: [],
    states: [], colors: null, centers: [], boxes: [], all: new THREE.Box3(), hover: -1, paused: false, queued: false, front: null,
    candidate: new Set(), candidateMat: null, glyphs: [],
    fly: null, timings: { started: 0, urdf: 0, firstFrame: 0, done: 0, meshes: 0, total: 0, merged: false, triangles: 0, failed: [] },
  };

  // ---------------------------------------------------------------------------------------------- drawing ----
  function size() {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = `${width}px`;
    renderer.domElement.style.height = `${height}px`;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  function draw() {
    st.queued = false;
    if (st.paused) return;
    const now = performance.now();
    if (st.fly) {
      const t = Math.min(1, (now - st.fly.start) / FLY_MS);
      const ease = t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
      camera.position.lerpVectors(st.fly.fromPos, st.fly.toPos, ease);
      controls.target.lerpVectors(st.fly.fromTarget, st.fly.toTarget, ease);
      controls.update();
      if (t >= 1) { st.fly = null; emitCamera(); } else request();
    }
    if (gizmo.animating) { gizmo.update(Math.min(0.05, (now - (st.last || now)) / 1000)); request(); if (!gizmo.animating) { controls.update(); emitCamera(); } }
    st.last = now;
    renderer.setClearColor(st.colors ? st.colors.bg : '#f4f4f5', 1);
    renderer.clear();
    renderer.render(scene, camera);
    gizmo.render(renderer);
    if (st.meshes.length && !st.timings.firstFrame) st.timings.firstFrame = now - st.timings.started;
    if (hooks.onFrame) hooks.onFrame();
  }
  function request() {
    if (st.queued || st.paused) return;
    st.queued = true;
    requestAnimationFrame(draw);
  }
  controls.addEventListener('change', request);
  controls.addEventListener('start', () => { st.touched = true; });
  controls.addEventListener('end', () => emitCamera());
  const resize = new ResizeObserver(() => { size(); request(); });
  resize.observe(container);
  size();

  // ------------------------------------------------------------------------------------------- the camera ----
  const cameraText = () => [...camera.position.toArray(), ...controls.target.toArray()].map((v) => v.toFixed(3)).join(',');
  function emitCamera() { if (hooks.onCamera) hooks.onCamera(cameraText()); }
  function setCamera(text) {
    const values = String(text || '').split(',').map(Number);
    if (values.length !== 6 || values.some((v) => !Number.isFinite(v))) return false;
    st.fly = null;
    camera.position.set(values[0], values[1], values[2]);
    controls.target.set(values[3], values[4], values[5]);
    controls.update();
    request();
    return true;
  }
  // frame a box: keep the view direction, move so it fills about 70 % of the view
  // margin: 1.25 fills the view (the whole model); a part is framed with its surroundings (WHY: a selected board's part filling the
  // screen lost where it sits on the robot), and never closer than a tenth of the model
  function frameBox(box, animate = true, margin = 1.25) {
    if (box.isEmpty()) return;
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const whole = st.all.isEmpty() ? 0 : st.all.getBoundingSphere(new THREE.Sphere()).radius;
    const radius = Math.max(sphere.radius, 0.02, whole * 0.1);
    const fov = THREE.MathUtils.degToRad(camera.fov);
    const distance = radius / Math.sin(Math.min(fov, fov * camera.aspect) / 2) * margin;
    const direction = camera.position.clone().sub(controls.target).normalize();
    if (!Number.isFinite(direction.x) || direction.lengthSq() === 0) direction.set(1, 0.75, 1).normalize();
    const toTarget = sphere.center.clone();
    const toPos = sphere.center.clone().add(direction.multiplyScalar(distance));
    camera.near = Math.max(0.001, distance / 200);
    camera.far = Math.max(50, distance * 50);
    camera.updateProjectionMatrix();
    if (!animate) { camera.position.copy(toPos); controls.target.copy(toTarget); controls.update(); request(); emitCamera(); return; }
    st.fly = { start: performance.now(), fromPos: camera.position.clone(), toPos, fromTarget: controls.target.clone(), toTarget };
    request();
  }
  function frameParts(indexes, animate = true) {
    const box = new THREE.Box3();
    for (const index of indexes) if (st.boxes[index]) box.union(st.boxes[index]);
    frameBox(box.isEmpty() ? st.all : box, animate, box.isEmpty() ? 1.25 : 2.6);
  }
  // Which way the model faces, in three's axes (URDF x is x, URDF z is y, URDF y is -z): the description's [view] front, else broadside
  // to its longest horizontal side, which is how a person walks up to a rack
  function frontAxis() {
    const axes = { '+x': [1, 0, 0], '-x': [-1, 0, 0], '+y': [0, 0, -1], '-y': [0, 0, 1] };
    if (axes[st.front]) return new THREE.Vector3(...axes[st.front]);
    const span = st.all.getSize(new THREE.Vector3());
    return span.z >= span.x ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
  }
  // Every mesh's box corners: what must be inside the view. WHY not the bounding sphere: a long rack's sphere is mostly air, so the
  // rack filled a third of the screen (Zach, 2026-11-04: "the rack fills the viewport nicely on load")
  function modelPoints() {
    const points = [];
    root.updateMatrixWorld(true);
    for (const mesh of st.meshes) {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      const { min, max } = mesh.geometry.boundingBox;
      for (const x of [min.x, max.x]) for (const y of [min.y, max.y]) for (const z of [min.z, max.z]) points.push(new THREE.Vector3(x, y, z).applyMatrix4(mesh.matrixWorld));
    }
    return points;
  }
  // The pose that shows every point from ``direction`` (unit, target to camera), centred and as large as the view allows
  function fitPose(direction) {
    const points = modelPoints();
    const target = st.all.getCenter(new THREE.Vector3());
    const forward = direction.clone().negate();
    const right = forward.clone().cross(new THREE.Vector3(0, 1, 0)).normalize();
    const up = right.clone().cross(forward).normalize();
    const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * HOME_FILL;
    const tanH = tanV * camera.aspect;
    let distance = 1;
    for (let pass = 0; pass < 6; pass += 1) {
      let lowX = Infinity; let highX = -Infinity; let lowY = Infinity; let highY = -Infinity;
      distance = 0;
      for (const point of points) {
        const offset = point.clone().sub(target);
        const depth = offset.dot(forward);
        distance = Math.max(distance, Math.abs(offset.dot(right)) / tanH - depth, Math.abs(offset.dot(up)) / tanV - depth);
      }
      for (const point of points) {
        const offset = point.clone().sub(target);
        const scale = 1 / Math.max(1e-6, distance + offset.dot(forward));
        lowX = Math.min(lowX, offset.dot(right) * scale); highX = Math.max(highX, offset.dot(right) * scale);
        lowY = Math.min(lowY, offset.dot(up) * scale); highY = Math.max(highY, offset.dot(up) * scale);
      }
      target.addScaledVector(right, (lowX + highX) / 2 * distance).addScaledVector(up, (lowY + highY) / 2 * distance);
    }
    return { target, position: target.clone().addScaledVector(direction, distance), distance };
  }
  // the first view of a model: Fusion's home, the whole model filling the viewport from the front's right, a little above
  function home(animate = false, emit = true) {
    if (st.all.isEmpty()) return;
    const front = frontAxis();
    const right = new THREE.Vector3(0, 1, 0).cross(front).normalize();
    const azimuth = THREE.MathUtils.degToRad(HOME_AZIMUTH);
    const elevation = THREE.MathUtils.degToRad(HOME_ELEVATION);
    const direction = front.clone().multiplyScalar(Math.cos(elevation) * Math.cos(azimuth)).addScaledVector(right, Math.cos(elevation) * Math.sin(azimuth))
      .add(new THREE.Vector3(0, Math.sin(elevation), 0)).normalize();
    const pose = fitPose(direction);
    camera.near = Math.max(0.001, pose.distance / 200);
    camera.far = Math.max(50, pose.distance * 50);
    camera.updateProjectionMatrix();
    if (!animate) { camera.position.copy(pose.position); controls.target.copy(pose.target); controls.update(); request(); if (emit) emitCamera(); return; }
    st.fly = { start: performance.now(), fromPos: camera.position.clone(), toPos: pose.position, fromTarget: controls.target.clone(), toTarget: pose.target };
    request();
  }

  // ---------------------------------------------------------------------------------------- the model ----
  const nearestLink = (object) => { let at = object; while (at && !at.isURDFLink) at = at.parent; return at; };
  function material(color) {
    return new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.08, side: THREE.DoubleSide });
  }

  // Which part each link and each mesh pattern belongs to. A part may be whole links, meshes by file name, or a point of its own (a
  // device the model does not have). A pattern beats a link: the rack's JMC motors are bodies inside base_link.
  function indexParts(parts) {
    st.parts = parts;
    st.partOfLink = new Map();
    parts.forEach((part, index) => (part.links || []).forEach((link) => { if (!st.partOfLink.has(link)) st.partOfLink.set(link, index); }));
    // the longest pattern first: a board's motor inside the bigger assembly another device holds is the motor's
    st.partPatterns = parts.flatMap((part, index) => (part.meshes || []).map((pattern) => ({ pattern, test: wildcard(pattern), index })))
      .sort((a, b) => b.pattern.length - a.pattern.length);
    while (st.partMats.length < parts.length) st.partMats.push(material('#c4c7cc'));
  }
  function partOfMesh(mesh) {
    if (mesh.userData.glyph) return mesh.userData.part;
    const hit = st.partPatterns.find(({ test }) => test.test(mesh.userData.file || ''));
    if (hit) return hit.index;
    return st.partOfLink.has(mesh.userData.link) ? st.partOfLink.get(mesh.userData.link) : -1;
  }
  function dress(mesh, index) {
    mesh.material = st.candidate.has(index) ? st.candidateMat : mesh.userData.part >= 0 ? st.partMats[mesh.userData.part] : st.neutral;
  }
  // a device the model does not have, placed at a point: a small camera, as much a part as any other (colour, hover, pick, leaders)
  function placeGlyphs() {
    for (const mesh of st.glyphs) { root.remove(mesh); mesh.geometry.dispose(); }
    st.meshes = st.meshes.filter((item) => !item.userData.glyph);
    st.glyphs = [];
    const span = st.all.isEmpty() ? 0.5 : st.all.getSize(new THREE.Vector3()).length();
    const size = Math.min(0.03, Math.max(0.01, span * 0.012)); // about a real camera's size on a robot, never a landmark
    st.parts.forEach((part, index) => {
      if (!part.at) return;
      const body = new THREE.BoxGeometry(size * 1.5, size * 0.8, size * 0.8);
      const lens = new THREE.ConeGeometry(size * 0.72, size * 1.1, 4, 1, true).rotateY(Math.PI / 4).rotateX(-Math.PI / 2).translate(0, 0, size * 0.95);
      const mesh = new THREE.Mesh(mergeGeometries([body, lens], false), st.partMats[index]);
      mesh.position.fromArray(part.at);
      if (part.look_at) {
        const direction = new THREE.Vector3().fromArray(part.look_at).sub(mesh.position).normalize();
        mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), direction);
      }
      mesh.userData = { part: index, link: '', file: '', glyph: true };
      root.add(mesh);
      st.glyphs.push(mesh);
      st.meshes.push(mesh);
    });
  }
  // The page assigned or removed a device: the same model, the parts dealt out again (no reload, the camera stays)
  function setParts(parts) {
    indexParts(parts);
    placeGlyphs();
    st.meshes.forEach((mesh, index) => { mesh.userData.part = partOfMesh(mesh); dress(mesh, index); if (mesh.userData.edges) mesh.userData.edges.visible = false; });
    measure();
    applyStates();
  }

  async function load(url, parts, options = {}) {
    st.timings.started = performance.now();
    st.front = options.front || null;
    st.neutral = material('#c4c7cc');
    st.candidateMat = material('#8fb8f6');
    st.candidateMat.emissive.set('#2f7cf6');
    st.candidateMat.emissiveIntensity = 0.28;
    indexParts(parts);
    const stl = new STLLoader();
    let pending = 0;
    let finished;
    const allDone = new Promise((resolve) => { finished = resolve; });
    const loader = new URDFLoader();
    loader.parseCollision = false;
    const packages = options.packages || {};
    const base = url.slice(0, url.lastIndexOf('/files/') + '/files/'.length);
    // package://<name>/... -> the folder of the upload that holds it (the host found it: robot_description.package_dirs)
    loader.packages = (name) => (`${base}${packages[name] ?? name}`).replace(/\/$/, '');
    loader.loadMeshCb = (path, manager, _material, done) => {
      pending += 1;
      st.timings.total += 1;
      const file = decodeURIComponent(path.split('?')[0].split('/').pop());
      const failed = (error) => { done(null, error); st.timings.failed.push(file); pending -= 1; if (!pending) finished(); };
      const loaded = (object) => {
        done(object);
        object.traverse((item) => {
          if (!item.isMesh) return;
          if (!item.geometry.attributes.normal) item.geometry.computeVertexNormals();
          const geometry = item.geometry;
          st.timings.triangles += geometry.index ? geometry.index.count / 3 : geometry.attributes.position.count / 3;
          afterMesh(item, file);
        });
        st.timings.meshes += 1;
        pending -= 1;
        if (!pending) finished();
      };
      if (/\.stl$/i.test(file)) stl.load(path, (geometry) => { geometry.computeVertexNormals(); loaded(new THREE.Mesh(geometry, st.neutral)); }, undefined, failed);
      else if (/\.dae$/i.test(file)) new ColladaLoader(manager).load(path, (dae) => loaded(dae.scene), undefined, failed);
      else if (/\.obj$/i.test(file)) new OBJLoader(manager).load(path, loaded, undefined, failed);
      else failed(new Error(`no loader for ${file}`));
    };
    const robot = await new Promise((resolve, reject) => loader.load(url, resolve, undefined, reject));
    st.timings.urdf = performance.now() - st.timings.started;
    st.robot = robot;
    root.add(robot);
    robot.traverse((object) => { if (object.isURDFLink) st.links.set(object.name, object); });
    // primitive geometry (box, cylinder, sphere) arrives inside the parse
    robot.traverse((object) => { if (object.isMesh && !st.meshes.includes(object)) afterMesh(object, ''); });
    home(false);
    if (!pending) finished();
    await allDone;
    st.timings.done = performance.now() - st.timings.started;
    measure();
    placeGlyphs();
    measure();
    if (!st.touched) home(false);
    request();
    return st.timings;
  }
  // a mesh arrived: it takes its link's material, the picker learns it, the bounds grow
  function afterMesh(mesh, file) {
    const link = nearestLink(mesh);
    mesh.userData.file = file;
    mesh.userData.link = link ? link.name : '';
    mesh.userData.part = partOfMesh(mesh);
    mesh.material = mesh.userData.part >= 0 ? st.partMats[mesh.userData.part] : st.neutral;
    st.meshes.push(mesh);
    // WHY home again as meshes arrive: the first pose fits whatever landed first, and a rack that takes 3 s to load sat off to one side
    // until then. A person who already took the camera keeps it
    if (!st.measuring) { st.measuring = true; requestAnimationFrame(() => { st.measuring = false; measure(); if (!st.timings.done && !st.touched) home(false, false); request(); }); }
  }
  function measure() {
    root.updateMatrixWorld(true);
    st.all.makeEmpty();
    st.boxes = st.parts.map(() => new THREE.Box3());
    const bodies = st.parts.map(() => []);
    for (const mesh of st.meshes) {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      const box = mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld);
      st.all.union(box);
      if (mesh.userData.part >= 0) { st.boxes[mesh.userData.part].union(box); bodies[mesh.userData.part].push(box.getCenter(new THREE.Vector3())); }
    }
    // WHY the body nearest the middle and not the middle: a part of two bodies (a motor and the arm it turns) has air at its middle, and
    // a leader line must land on the part
    st.centers = st.boxes.map((box, index) => {
      if (box.isEmpty()) return null;
      const middle = box.getCenter(new THREE.Vector3());
      return bodies[index].reduce((best, center) => (center.distanceTo(middle) < best.distanceTo(middle) ? center : best));
    });
    placeGrid();
  }
  function placeGrid() {
    if (st.all.isEmpty()) return;
    const span = st.all.getSize(new THREE.Vector3());
    const extent = Math.max(span.x, span.z) * 2.2;
    const step = extent > 4 ? 0.5 : extent > 1.2 ? 0.1 : 0.05;
    const divisions = Math.max(4, Math.round(extent / step));
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); }
    const colors = st.colors || { grid: '#9aa0a6', grid2: '#d0d3d8' };
    grid = new THREE.GridHelper(divisions * step, divisions, colors.grid, colors.grid2);
    grid.material.transparent = true;
    grid.material.opacity = 0.7;
    const center = st.all.getCenter(new THREE.Vector3());
    grid.position.set(center.x, st.all.min.y - 0.0005, center.z);
    scene.add(grid);
    const radius = st.all.getBoundingSphere(new THREE.Sphere()).radius;
    key.position.set(center.x + radius * 2, center.y + radius * 3, center.z + radius * 1.5);
    fill.position.set(center.x - radius * 2, center.y + radius, center.z - radius * 2);
  }

  // Merge each link's meshes that share a material into one (fewer draw calls); picking and colours are per link anyway.
  // WHY optional: docs/robot-description.md "Load time" — measured first, used only when a model is slow to draw.
  function mergeByLink() {
    const groups = new Map();
    for (const mesh of st.meshes) {
      const link = nearestLink(mesh);
      if (!link) continue;
      const key = `${link.name}\u0000${mesh.material.uuid}`;
      if (!groups.has(key)) groups.set(key, { link, mesh, items: [] });
      groups.get(key).items.push(mesh);
    }
    const kept = [];
    for (const { link, items } of groups.values()) {
      if (items.length < 2) { kept.push(...items); continue; }
      link.updateMatrixWorld(true);
      const inverse = link.matrixWorld.clone().invert();
      const geometries = items.map((mesh) => {
        const geometry = (mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone());
        for (const name of Object.keys(geometry.attributes)) if (name !== 'position' && name !== 'normal') geometry.deleteAttribute(name);
        return geometry.applyMatrix4(new THREE.Matrix4().multiplyMatrices(inverse, mesh.matrixWorld));
      });
      const merged = new THREE.Mesh(mergeGeometries(geometries, false), items[0].material);
      merged.userData = { ...items[0].userData };
      for (const mesh of items) { mesh.parent.remove(mesh); mesh.geometry.dispose(); }
      geometries.forEach((geometry) => geometry.dispose());
      link.add(merged);
      kept.push(merged);
    }
    st.meshes = kept;
    st.timings.merged = true;
    measure();
    request();
  }

  // -------------------------------------------------------------------------------------------- colours ----
  function setColors(colors) {
    st.colors = colors;
    if (st.neutral) st.neutral.color.set(colors.neutral);
    placeGrid();
    applyStates();
  }
  // states[i] = {level, ghost, selected}: the part's own colour (its device's level, never a roll-up), a ghost when not detected
  function setStates(states) { st.states = states; applyStates(); }
  function applyStates() {
    const colors = st.colors;
    if (!colors) return;
    st.partMats.forEach((mat, index) => {
      const state = st.states[index] || {};
      const base = new THREE.Color(colors.neutral);
      const tint = state.level && colors[state.level] ? new THREE.Color(colors[state.level]) : null;
      // WHY OK is a light tint, not a full green: OK carries no colour on a row (tokens.css); here it only says "mapped and well"
      const color = tint ? (state.level === 'ok' ? base.clone().lerp(tint, 0.38) : tint) : base;
      // WHY only an OK or unreported part takes the blue tint: blue over warn orange reads as the unknown token's purple; a part in a
      // problem colour keeps it and shows its selection by the blue outline
      const problem = state.level && state.level !== 'ok' && !state.ghost;
      if (state.selected && !problem) color.lerp(new THREE.Color(colors.select), SELECT_MIX);
      mat.color.copy(color);
      // a problem colour glows a little of its own hue, so shading never turns warn's orange into error's red
      mat.emissive.set(state.selected && !problem ? colors.select : problem ? tint : '#000000');
      mat.emissiveIntensity = problem ? 0.32 : state.selected ? 0.18 : 0;
      if (index === st.hover && !state.selected && !problem) { mat.emissive.set('#ffffff'); mat.emissiveIntensity = HOVER_EMISSIVE; }
      if (index === st.hover && problem) mat.emissiveIntensity = 0.45;
      // lit: its adapter or its computer is hovered or selected. A part in a problem colour keeps it and glows brighter
      if (state.lit && !state.selected) {
        if (!problem) { mat.color.lerp(new THREE.Color(colors.select), SELECT_MIX); mat.emissive.set(colors.select); }
        mat.emissiveIntensity = problem ? 0.62 : 0.34;
      }
      const ghost = Boolean(state.ghost);
      mat.transparent = ghost;
      mat.opacity = ghost ? GHOST_OPACITY : 1;
      mat.depthWrite = !ghost;
      mat.needsUpdate = true;
    });
    outline();
    request();
  }
  // the selection's outline (Fusion draws a selected body's edges in blue): edges of the selected parts' meshes, built once per mesh
  const outlineMat = new THREE.LineBasicMaterial({ color: '#2f7cf6', transparent: true, opacity: 0.95, depthTest: true });
  function outline() {
    if (st.colors) outlineMat.color.set(st.colors.select);
    for (const mesh of st.meshes) {
      const on = mesh.userData.part >= 0 && Boolean((st.states[mesh.userData.part] || {}).selected);
      if (on && !mesh.userData.edges) {
        mesh.userData.edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 28), outlineMat);
        mesh.userData.edges.raycast = () => {};
        mesh.add(mesh.userData.edges);
      }
      if (mesh.userData.edges) mesh.userData.edges.visible = on;
    }
  }

  // --------------------------------------------------------------------------------------------- picking ----
  // GPU picking: each mesh drawn once in a flat colour that encodes it (+1, background = 0) into a 1x1 target under the pointer. WHY not
  // a raycast: the rack's URDF is about a million triangles; a ray per mouse move walked every one of them.
  const pickTarget = new THREE.WebGLRenderTarget(1, 1);
  const pickPixel = new Uint8Array(4);
  const pickMats = new Map();
  const pickMat = (value) => {
    if (!pickMats.has(value)) pickMats.set(value, new THREE.MeshBasicMaterial({ color: new THREE.Color(((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255), side: THREE.DoubleSide }));
    return pickMats.get(value);
  };
  function pickAt(clientX, clientY) {
    if (!st.meshes.length) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    if (x < 0 || y < 0 || x >= rect.width || y >= rect.height) return null;
    const ratio = renderer.getPixelRatio();
    camera.setViewOffset(rect.width * ratio, rect.height * ratio, Math.floor(x * ratio), Math.floor(y * ratio), 1, 1);
    const saved = st.meshes.map((mesh) => mesh.material);
    st.meshes.forEach((mesh, index) => { mesh.material = pickMat(index + 1); });
    const hidden = [grid, hemi, key, fill, ...st.meshes.map((mesh) => mesh.userData.edges)].filter((object) => object && object.visible);
    hidden.forEach((object) => { object.visible = false; });
    renderer.setRenderTarget(pickTarget);
    renderer.setClearColor(0x000000, 1);
    renderer.clear();
    renderer.render(scene, camera);
    renderer.readRenderTargetPixels(pickTarget, 0, 0, 1, 1, pickPixel);
    renderer.setRenderTarget(null);
    hidden.forEach((object) => { object.visible = true; });
    st.meshes.forEach((mesh, index) => { mesh.material = saved[index]; });
    camera.clearViewOffset();
    const value = (pickPixel[0] << 16) | (pickPixel[1] << 8) | pickPixel[2];
    const mesh = st.meshes[value - 1];
    return mesh ? { part: mesh.userData.part, mesh: value - 1, link: mesh.userData.link, file: mesh.userData.file } : null;
  }

  // What a click on this mesh could mean, narrow to wide: its CAD occurrence path, level by level (Cad2Robot's file names are that path
  // joined by __, the last number the body), then its whole link. Each level knows the meshes it is, for the page's highlight and for
  // the part it would write. ``pick`` is the level a plain click takes: the link when the link is a body, else, in an assembly export
  // that lumps a whole rack into base_link, the widest component that is still a small share of the lump (a motor, not the rack).
  function levelsOf(index) {
    const mesh = st.meshes[index];
    if (!mesh) return { levels: [], pick: -1 };
    const { link, file, glyph } = mesh.userData;
    if (glyph) return { levels: [], pick: -1 };
    const inLink = st.meshes.map((item, at) => (item.userData.link === link ? at : -1)).filter((at) => at >= 0);
    const levels = [];
    const stem = file.replace(/\.[^.]+$/, '').replace(/__\d+$/, '');
    const segments = stem ? stem.split('__') : [];
    let last = 0;
    for (let depth = segments.length; depth >= 1; depth -= 1) {
      const prefix = segments.slice(0, depth).join('__');
      const pattern = `${prefix}__*`;
      const test = wildcard(pattern);
      const meshes = st.meshes.map((item, at) => (item.userData.file && test.test(item.userData.file) ? at : -1)).filter((at) => at >= 0);
      if (!meshes.length || meshes.length === last) continue;
      last = meshes.length;
      if (inLink.length <= meshes.length && inLink.every((at) => meshes.includes(at)) && meshes.length === inLink.length) continue;
      levels.push({ kind: 'meshes', pattern, label: segments[depth - 1].replace(/^_+/, '').replace(/_(\d+)$/, ':$1').replace(/_/g, ' '), meshes });
    }
    levels.push({ kind: 'link', link, label: link, meshes: inLink });
    levels.sort((a, b) => a.meshes.length - b.meshes.length);
    let pick = levels.length - 1;
    if (inLink.length > LINK_IS_A_BODY) {
      const allowed = levels.map((level, at) => (level.kind === 'meshes' && level.meshes.length <= inLink.length * LUMP_SHARE ? at : -1)).filter((at) => at >= 0);
      if (allowed.length) pick = allowed[allowed.length - 1];
    }
    return { levels, pick };
  }
  function setCandidate(indexes) {
    const before = st.candidate;
    st.candidate = new Set(indexes || []);
    for (const index of before) if (st.meshes[index]) dress(st.meshes[index], index);
    for (const index of st.candidate) if (st.meshes[index]) dress(st.meshes[index], index);
    request();
  }

  let press = null;
  let hoverAt = 0;
  let hoverTimer = 0;
  const onMove = (event) => {
    if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > DRAG_PX) press.drag = true;
    if (press && press.drag) return;
    clearTimeout(hoverTimer);
    const run = () => {
      hoverAt = performance.now();
      const hit = pickAt(event.clientX, event.clientY);
      const part = hit ? hit.part : -1;
      if (part !== st.hover) { st.hover = part; applyStates(); }
      if (hooks.onHover) hooks.onHover(part, event.clientX, event.clientY, hit);
    };
    if (performance.now() - hoverAt > 30) run(); else hoverTimer = setTimeout(run, 30);
  };
  const onLeave = () => { clearTimeout(hoverTimer); if (st.hover !== -1) { st.hover = -1; applyStates(); } if (hooks.onHover) hooks.onHover(-1, 0, 0); };
  const onDown = (event) => { press = { x: event.clientX, y: event.clientY, drag: false, button: event.button }; };
  const onUp = (event) => {
    const was = press;
    press = null;
    if (!was || was.drag || was.button !== 0) return;
    if (gizmo.handleClick(event)) { gizmo.center.copy(controls.target); request(); return; }
    const hit = pickAt(event.clientX, event.clientY);
    if (hooks.onPick) hooks.onPick(hit ? hit.part : -1, event, hit);
  };
  const onDouble = (event) => { const hit = pickAt(event.clientX, event.clientY); if (hooks.onDouble) hooks.onDouble(hit ? hit.part : -1, event); };
  renderer.domElement.addEventListener('pointermove', onMove);
  renderer.domElement.addEventListener('pointerleave', onLeave);
  renderer.domElement.addEventListener('pointerdown', onDown);
  renderer.domElement.addEventListener('pointerup', onUp);
  renderer.domElement.addEventListener('dblclick', onDouble);
  gizmo.center.copy(controls.target);
  controls.addEventListener('change', () => gizmo.center.copy(controls.target));

  // ----------------------------------------------------------------------------------------- screen space ----
  // where a part's centre is on screen, in client pixels (callouts, tests); null when it is behind the camera or has no meshes
  function project(index) {
    const center = st.centers[index];
    if (!center) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const at = center.clone().project(camera);
    if (at.z > 1 || at.z < -1) return null;
    return { x: rect.left + (at.x + 1) / 2 * rect.width, y: rect.top + (1 - at.y) / 2 * rect.height, inside: Math.abs(at.x) <= 1 && Math.abs(at.y) <= 1 };
  }
  function onScreen(mesh) {
    if (!mesh) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const at = mesh.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(mesh.matrixWorld).project(camera);
    return { x: rect.left + (at.x + 1) / 2 * rect.width, y: rect.top + (1 - at.y) / 2 * rect.height };
  }
  // a pixel of the last drawn frame (client coordinates), as [r, g, b]
  function pixel(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    const canvas = document.createElement('canvas');
    canvas.width = 1; canvas.height = 1;
    const ratio = renderer.getPixelRatio();
    const ctx = canvas.getContext('2d');
    ctx.drawImage(renderer.domElement, Math.floor((clientX - rect.left) * ratio), Math.floor((clientY - rect.top) * ratio), 1, 1, 0, 0, 1, 1);
    return [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)];
  }

  return {
    load, setStates, setColors, setCamera, cameraText, frameParts, home: () => home(true), mergeByLink, project, pixel, pickAt, setParts, levelsOf, setCandidate,
    request, timings: () => ({ ...st.timings }), loaded: () => Boolean(st.timings.done),
    partMaterial: (index) => st.partMats[index], linkNames: () => [...st.links.keys()], meshCount: () => st.meshes.length,
    // tests: what part a mesh file went to, the model's box in the view
    partOfFile: (file) => { const mesh = st.meshes.find((item) => item.userData.file === file); return mesh ? mesh.userData.part : null; },
    partOfGlyph: (index) => st.glyphs.some((mesh) => mesh.userData.part === index),
    meshAt: (file) => onScreen(st.meshes.find((item) => item.userData.file === file)),
    linkAt: (link) => onScreen(st.meshes.find((item) => item.userData.link === link)),
    viewFill() {
      const corners = modelPoints().map((point) => point.clone().project(camera));
      const box = { minX: Math.min(...corners.map((c) => c.x)), maxX: Math.max(...corners.map((c) => c.x)), minY: Math.min(...corners.map((c) => c.y)), maxY: Math.max(...corners.map((c) => c.y)) };
      return { ...box, width: (box.maxX - box.minX) / 2, height: (box.maxY - box.minY) / 2 };
    },
    pause(paused) { st.paused = paused; if (!paused) { size(); request(); } },
    dispose() {
      resize.disconnect();
      controls.dispose();
      gizmo.dispose();
      pickTarget.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
