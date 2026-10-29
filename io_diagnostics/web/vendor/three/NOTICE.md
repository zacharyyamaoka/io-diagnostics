# Vendored 3D libraries (the 3D view only)

The page is served offline and has no bundler, so the 3D view's libraries ship in the checkout, loaded through the importmap in
`index.html` by `threed/scene.js` (a `<script type="module">` import, only when the 3D view opens; docs/robot-description.md).

- `build/three.module.js`, `build/three.core.js`: three.js 0.186.1 (MIT, see LICENSE). `three.module.js` imports `three.core.js`.
- `examples/jsm/controls/OrbitControls.js`: the camera's orbit, pan and zoom.
- `examples/jsm/loaders/STLLoader.js`: the URDF's meshes.
- `examples/jsm/loaders/ColladaLoader.js`, `TGALoader.js`, `collada/ColladaParser.js`, `collada/ColladaComposer.js`: imported by urdf-loader
  for `.dae` meshes (a Cad2Robot bundle has none; they are here so the import resolves).
- `examples/jsm/utils/BufferGeometryUtils.js`: merges a link's meshes into one when a model has many.

All are copied unmodified from the npm package `three@0.186.1` (here from `/home/bam/aruco-studio/web/node_modules/three`).

`../urdf-loader/URDFLoader.js`, `URDFClasses.js`: urdf-loader 0.13.1 (gkjohnson/urdf-loaders, Apache-2.0, see that folder's LICENSE),
the package's `src/` ES modules unmodified (it ships no single-file ESM build; `npm pack urdf-loader` 2026-10-29, tarball sha256
19909aad681a1dfb5e4820aaf8369f6ff0837ae91dbb664f47895fe2d64f0b83). They import `three` and `three/examples/jsm/...`, which the importmap
points here. To update: `npm pack three urdf-loader` in an empty directory and copy the same files.
