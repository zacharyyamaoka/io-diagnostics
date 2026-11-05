"""Mutation checks for the 3D view's upload, Map devices and chips (docs/robot-description.md "From the page"): each mutation breaks one
behaviour in a COPY of the tree and a named check of tests/test_threed_import.js or a test of tests/test_robot_library.py must fail; the
checkout itself is never edited.

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/tests/mutate_threed_import.py [part of a name]

Prints CAUGHT / MISSED per mutation (with the first failing check) and exits 1 if any was missed or its text is no longer in the source.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = tempfile.mkdtemp(prefix='threed-import-mutation-')
COPY = os.path.join(BASE, 'tree')
CORE = 'io_diagnostics/web/threed/core.js'
SCENE = 'io_diagnostics/web/threed/scene.js'
LIBRARY = 'io_diagnostics/robot_library.py'
PAGE = ['node', 'tests/test_threed_import.js']
PYTEST = [sys.executable, '-m', 'pytest', 'tests/test_robot_library.py', '-q', '-x', '-p', 'no:cacheprovider']
MUTATIONS = [
    # (name, file, old, new, the run that must fail)
    ('zip-slip: a member may climb out', LIBRARY, 'if ".." in path.parts or path.is_absolute():', 'if False:', PYTEST),
    ('zip: a symlink is unpacked as a file', LIBRARY, 'if _is_symlink(info):', 'if False:', PYTEST),
    ('cap: the unpacked size is not counted', LIBRARY, 'if self.bytes > MAX_UNPACKED:', 'if False:', PYTEST),
    ('assign: the same click never undoes', LIBRARY, 'held = own is not None and all(', 'held = False and all(', PYTEST),
    ('assign: an edit rewrites the part without its comment guard', LIBRARY, 'while first > 0 and lines[first - 1].lstrip().startswith("#"):', 'while False:', PYTEST),
    ('kept: the active file is lost on an upload', LIBRARY, 'previous = _keep_active(directory)\n        shutil.copyfile(folder', 'previous = None\n        shutil.copyfile(folder', PYTEST),
    ('home: the old sphere fit', SCENE, '    const pose = fitPose(direction);\n', '    const pose = { target: st.all.getCenter(new THREE.Vector3()), distance: st.all.getBoundingSphere(new THREE.Sphere()).radius / Math.sin(THREE.MathUtils.degToRad(camera.fov) / 2) * 1.6 };\n    pose.position = pose.target.clone().addScaledVector(direction, pose.distance);\n', PAGE),
    ('parts: mesh patterns are ignored', SCENE, 'const hit = st.partPatterns.find(({ test }) => test.test(mesh.userData.file || \'\'));', 'const hit = null;', PAGE),
    ('parts: a lump is taken whole', SCENE, 'if (inLink.length > LINK_IS_A_BODY) {', 'if (false) {', PAGE),
    ('chips: no lines', CORE, 'for (const index of item.parts) {\n        const at = T.scene.project(index);', 'for (const index of []) {\n        const at = T.scene.project(index);', PAGE),
    ('chips: nothing lit', CORE, 'return new Set(item ? item.parts : []);', 'return new Set();', PAGE),
    ('chips: lit without a colour', SCENE, 'if (state.lit && !state.selected) {', 'if (false) {', PAGE),
    ('map: parts are not dealt out again', CORE, 'if (T.partsKey !== partsKey && T.scene && T.scene.loaded()) { T.partsKey = partsKey; T.scene.setParts(parts()); T.lastSel = undefined; }', '', PAGE),
    ('map: the step is lost on the way to the button', CORE, 'T.map.shift = Math.max(-6, Math.min(6, T.map.shift + step));', 'T.map.shift = 0;', PAGE),
    ('home: the load replaces the camera the page was opened with', CORE, 'const cam = S().cam;\n      const loading', 'const cam = null;\n      const loading', PAGE),
    ('upload: the page does not reload the description', CORE, "      wantDescription(true);\n    } catch (error) {\n      T.upload = null;", "    } catch (error) {\n      T.upload = null;", PAGE),
]


def fresh() -> None:
    shutil.rmtree(COPY, ignore_errors=True)
    shutil.copytree(SRC, COPY, ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv', 'out', 'media', '.pytest_cache', 'prototypes', 'bench'))


def main() -> int:
    missed = 0
    env = {key: value for key, value in os.environ.items() if key != 'VIRTUAL_ENV'}
    env['PYTHONPATH'] = COPY
    wanted = sys.argv[1] if len(sys.argv) > 1 else ''
    for name, file, old, new, run in MUTATIONS:
        if wanted not in name:
            continue
        fresh()
        path = os.path.join(COPY, file)
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        if text.count(old) != 1:
            print(f'STALE   {name}: {text.count(old)} matches')
            missed += 1
            continue
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text.replace(old, new))
        result = subprocess.run(run, cwd=COPY, env=env, capture_output=True, text=True, timeout=900, check=False)
        output = result.stdout + result.stderr
        first = re.search(r'^(FAIL  .*|FAILED .*|.*Error.*)$', output, re.M)
        caught = result.returncode != 0
        print(f"{'CAUGHT' if caught else 'MISSED'}  {name}: {(first[1][:110] if first else '').strip()}")
        sys.stdout.flush()
        missed += 0 if caught else 1
    shutil.rmtree(BASE, ignore_errors=True)
    print('all caught' if not missed else f'{missed} missed')
    return 1 if missed else 0


if __name__ == '__main__':
    raise SystemExit(main())
