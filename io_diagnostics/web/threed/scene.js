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
import { ViewHelper } from 'three/examples/jsm/helpers/ViewHelper.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import URDFLoader from '../vendor/urdf-loader/URDFLoader.js';

const GHOST_OPACITY = 0.15;   // a mapped device that is not detected (Zach: "a ghost (transparent)")
const SELECT_MIX = 0.3;       // how far a selected part's colour moves towards the selection blue (its status colour stays readable)
const HOVER_EMISSIVE = 0.10;
const FLY_MS = 380;
const DRAG_PX = 4;            // a press that moves more than this is a drag (orbit / pan), not a pick

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
    robot: null, links: new Map(), partOfLink: new Map(), partLinks: [], meshes: [], neutral: null, partMats: [],
    states: [], colors: null, centers: [], boxes: [], all: new THREE.Box3(), hover: -1, paused: false, queued: false,
    fly: null, timings: { started: 0, urdf: 0, firstFrame: 0, done: 0, meshes: 0, total: 0, merged: false, triangles: 0 },
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
  // the first view of a model: Fusion's home, from the front-right, a little above
  function home(animate = false) {
    camera.position.copy(st.all.getCenter(new THREE.Vector3()).add(new THREE.Vector3(1, 0.8, 1.15)));
    controls.target.copy(st.all.getCenter(new THREE.Vector3()));
    frameBox(st.all, animate);
  }

  // ---------------------------------------------------------------------------------------- the model ----
  const nearestLink = (object) => { let at = object; while (at && !at.isURDFLink) at = at.parent; return at; };
  function material(color) {
    return new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.08, side: THREE.DoubleSide });
  }

  async function load(url, parts) {
    st.timings.started = performance.now();
    st.partLinks = parts.map((part) => part.links.slice());
    parts.forEach((part, index) => part.links.forEach((link) => { if (!st.partOfLink.has(link)) st.partOfLink.set(link, index); }));
    st.neutral = material('#c4c7cc');
    st.partMats = parts.map(() => material('#c4c7cc'));
    const stl = new STLLoader();
    let pending = 0;
    let finished;
    const allDone = new Promise((resolve) => { finished = resolve; });
    const loader = new URDFLoader();
    loader.parseCollision = false;
    loader.loadMeshCb = (path, manager, _material, done) => {
      pending += 1;
      st.timings.total += 1;
      stl.load(path, (geometry) => {
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(geometry, st.neutral);
        done(mesh);
        st.timings.meshes += 1;
        st.timings.triangles += geometry.index ? geometry.index.count / 3 : geometry.attributes.position.count / 3;
        afterMesh(mesh);
        pending -= 1;
        if (!pending) finished();
      }, undefined, (error) => { done(null, error); pending -= 1; if (!pending) finished(); });
    };
    const robot = await new Promise((resolve, reject) => loader.load(url, resolve, undefined, reject));
    st.timings.urdf = performance.now() - st.timings.started;
    st.robot = robot;
    root.add(robot);
    robot.traverse((object) => { if (object.isURDFLink) st.links.set(object.name, object); });
    // primitive geometry (box, cylinder, sphere) arrives inside the parse
    robot.traverse((object) => { if (object.isMesh && !st.meshes.includes(object)) afterMesh(object); });
    home(false);
    if (!pending) finished();
    await allDone;
    st.timings.done = performance.now() - st.timings.started;
    measure();
    home(false);
    request();
    return st.timings;
  }
  // a mesh arrived: it takes its link's material, the picker learns it, the bounds grow
  function afterMesh(mesh) {
    const link = nearestLink(mesh);
    const index = link && st.partOfLink.has(link.name) ? st.partOfLink.get(link.name) : -1;
    mesh.userData.part = index;
    mesh.userData.link = link ? link.name : '';
    mesh.material = index >= 0 ? st.partMats[index] : st.neutral;
    st.meshes.push(mesh);
    if (!st.measuring) { st.measuring = true; requestAnimationFrame(() => { st.measuring = false; measure(); request(); }); }
  }
  function measure() {
    root.updateMatrixWorld(true);
    st.all.makeEmpty();
    st.boxes = st.partLinks.map(() => new THREE.Box3());
    for (const mesh of st.meshes) {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      const box = mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld);
      st.all.union(box);
      if (mesh.userData.part >= 0) st.boxes[mesh.userData.part].union(box);
    }
    st.centers = st.boxes.map((box) => (box.isEmpty() ? null : box.getCenter(new THREE.Vector3())));
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
  // GPU picking: each mesh drawn once in a flat colour that encodes its part (+1; 0 = a mesh of no part, background = none) into a
  // 1x1 target under the pointer. WHY not a raycast: the rack's URDF is about a million triangles; a ray per mouse move walked
  // every one of them.
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
    st.meshes.forEach((mesh) => { mesh.material = pickMat(mesh.userData.part + 2); });
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
    if (!value) return null;
    return { part: value - 2 };
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
      if (hooks.onHover) hooks.onHover(part, event.clientX, event.clientY);
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
    if (hooks.onPick) hooks.onPick(hit ? hit.part : -1, event);
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
    load, setStates, setColors, setCamera, cameraText, frameParts, home: () => home(true), mergeByLink, project, pixel, pickAt,
    request, timings: () => ({ ...st.timings }), loaded: () => Boolean(st.timings.done),
    partMaterial: (index) => st.partMats[index], linkNames: () => [...st.links.keys()],
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
