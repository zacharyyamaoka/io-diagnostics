"""The report of the 3D view's rack work: one self-contained HTML page (every image and the video inline), built from the files the recording
and the evidence tools wrote into its folder.

    python3 docs/build_rack_3d_report.py /home/bam/io-diagnostics/reports/media/rack-3d-2026-11-04

Reads hero.mp4, hero.gif, shot-*.png, preview-*.png, preview-facts.json, evidence/ (tools/rack_map/) and tests.json; writes report.html.
"""

import base64
import html
import json
import os
import sys

import cv2

FOLDER = sys.argv[1]
EVIDENCE = os.path.join(FOLDER, 'evidence')


def read_json(*path, default=None):
    file = os.path.join(*path)
    if not os.path.exists(file):
        return default
    with open(file, encoding='utf-8') as handle:
        return json.load(handle)


def image(path: str, width: int = 1100, quality: int = 74, alt: str = '', cls: str = '') -> str:
    """An image as an inline JPEG: a page of stills is the one thing that keeps it small."""

    picture = cv2.imread(path)
    if picture is None:
        raise SystemExit(f'no image {path}')
    if picture.shape[1] > width:
        picture = cv2.resize(picture, (width, round(picture.shape[0] * width / picture.shape[1])), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise SystemExit(f'could not encode {path}')
    return f'<img class="{cls}" alt="{html.escape(alt)}" src="data:image/jpeg;base64,{base64.b64encode(data.tobytes()).decode()}">'


def data_uri(path: str, kind: str) -> str:
    with open(path, 'rb') as handle:
        return f'data:{kind};base64,{base64.b64encode(handle.read()).decode()}'


moved = read_json(EVIDENCE, 'who_moved.json', default={})
cameras = read_json(EVIDENCE, 'cameras.json', default={'cameras': {}, 'plates': {}, 'tags_seen': {}})
facts = read_json(FOLDER, 'preview-facts.json', default={})
tests = read_json(FOLDER, 'tests.json', default={})
landed = tests.get('landed', '')


def turn(can: int, session: str, tag: int) -> str:
    for entry in moved.get(str(can), []):
        if entry['session'].endswith(session) and str(tag) in entry['moved']:
            row = entry['moved'][str(tag)]
            return f"tag {tag} turned {row['turn_deg']:.1f}°; every other tag still (headers under {entry['header_noise_deg']:.1f}°)"
    return ''


ROWS = [
    ('CAN 1', '272813EF', 'GIM4305-10SHS:3, its hardstop and holder', 'plate A, upper left', turn(1, 'rack_2026-10-23/session_16', 0) or 'tag 0', 'rack_2026-10-23/session_16', 'can1.jpg'),
    ('CAN 14', '272813EF', 'GIM4305-10SHS:1, the tag holder', 'plate A, lower left', turn(14, 'rack_2026-10-21/session_10', 1) or 'tag 1', 'rack_2026-10-21/session_10', 'can14.jpg'),
    ('CAN 15', '272813EF', 'GIM4305-10SHS:2, gim43_arm', 'plate A, lower right', turn(15, 'rack_2026-10-24/session_14', 2) or 'tag 2', 'rack_2026-10-24/session_14', 'can15.jpg'),
    ('CAN 4', '0607623A', 'JMC 60ASM200:1, its aruco holder', 'plate B, left', turn(4, 'rack_2026-10-23/session_10', 4) or 'tag 4', 'rack_2026-10-23/session_10', 'can4.jpg'),
    ('CAN 21', '0607623A', 'JMC 60ASM200:2, pole_mount', 'plate B, right', turn(21, 'rack_2026-10-24/session_26', 5) or 'tag 5', 'rack_2026-10-24/session_26', 'can21.jpg'),
    ('CAN 13', '0607623A', 'AD60-G10-D14:1, jmc60_hardstop_plate', 'plate C, upper', turn(13, 'rack_2026-10-23/session_11', 6) or 'tag 6', 'rack_2026-10-23/session_11', 'can13.jpg'),
    ('CAN 12', '0607623A', 'AD60-G10-D14:2, JMC 60ASM200:3, jmc60_gearbox_aruco_mount', 'plate C, lower', 'tag 7 turned +28.6°, nothing else (a still, rest vs held; no recording sees tag 7)', 'identify-can12, 2026-10-23', 'identify-can12.png'),
    ('CAN 11', '0607623A', 'the rig\'s JMC Servo:1, one_dof_arm', 'plate D, the long link', turn(11, 'rack_2026-10-23/session_12', 8) or 'tag 8', 'rack_2026-10-23/session_12', 'can11.jpg'),
]
rows = ''.join(f'<tr><td class="b">{name}</td><td class="m">{adapter}</td><td>{part}</td><td>{where}</td><td>{proof}<div class="s">{session}</div></td></tr>' for name, adapter, part, where, proof, session, _ in ROWS)
gallery = ''.join(f'<figure>{image(os.path.join(EVIDENCE, file), 760, 70, name)}<figcaption><b>{name}</b> · {session}</figcaption></figure>' for name, _, _, _, _, session, file in ROWS)
plates = ''.join(f"<tr><td>{plate}</td><td>{' / '.join(str(v) for v in data['spacing_rack_mm'])} mm</td><td>{' / '.join(str(v) for v in data['spacing_cad_mm'])} mm</td></tr>" for plate, data in cameras['plates'].items())
solved = ''.join(f"<tr><td class=\"m\">{serial}</td><td>{item['rms_px']} px RMS on {len(item['header_tags'])} header tags</td><td>rack ({', '.join(str(v) for v in item['rack_mm'])}) mm</td><td>was ({', '.join(str(v) for v in item['pose_2026_10_21_mm'])}) mm on 10-21</td></tr>" for serial, item in cameras['cameras'].items())
live = ''.join(f"<tr><td>{html.escape(part['name'])}</td><td class=\"m\">{html.escape(part['path'] or 'not detected')}</td><td>{'not detected' if part['ghost'] else part['level']}</td></tr>" for part in facts.get('parts', []))
pick = next((name for name in sorted(os.listdir(EVIDENCE)) if name.endswith('218622274411-tags.jpg')), None)
pick2 = next((name for name in sorted(os.listdir(EVIDENCE)) if name.endswith('218622276036-tags.jpg')), None)
pick3 = next((name for name in sorted(os.listdir(EVIDENCE)) if name.endswith('044322072026.png')), None)

PAGE = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rack in RobotDx 3D</title><style>
:root {{ --ink: #1b1d21; --muted: #5b6069; --line: #e3e4e8; --soft: #f5f5f7; --blue: #2f7cf6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink: #e8e9ec; --muted: #a3a8b1; --line: #33363c; --soft: #212327; }} body {{ background: #17181b; }} }}
body {{ margin: 0; font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--ink); }}
main {{ max-width: 1120px; margin: 0 auto; padding: 28px 18px 64px; }}
h1 {{ font-size: 25px; margin: 0 0 8px; line-height: 1.25; }} h2 {{ font-size: 18px; margin: 34px 0 8px; }}
p {{ margin: 8px 0; }} .lead {{ font-size: 16px; }} .muted, .s {{ color: var(--muted); }} .s {{ font-size: 12px; }}
video, img {{ max-width: 100%; display: block; border: 1px solid var(--line); border-radius: 6px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; margin: 8px 0; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }} th {{ font-weight: 600; color: var(--muted); }}
.m {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }} .b {{ font-weight: 600; white-space: nowrap; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; }} figure {{ margin: 0; }} figcaption {{ font-size: 12.5px; color: var(--muted); margin-top: 4px; }}
.two {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 12px; }}
.call {{ border-left: 3px solid var(--blue); background: var(--soft); padding: 8px 12px; margin: 12px 0; }}
ul {{ margin: 6px 0; padding-left: 20px; }} li {{ margin: 3px 0; }} code {{ font-family: ui-monospace, Menlo, monospace; font-size: 13px; }}
@media (max-width: 640px) {{ .grid, .two {{ grid-template-columns: 1fr; }} table {{ font-size: 12.5px; }} }}
</style></head><body><main>
<h1>RobotDx's 3D view shows the rack live, with every motor where it really is</h1>
<p class="lead">All eight boards and both D405s are placed on the model, each on proof from the cameras, and no motor moved to get there. A person can also drop in any URDF and map a device with one click. {landed}</p>

<video controls muted loop autoplay playsinline poster="{data_uri(os.path.join(FOLDER, 'hero.gif'), 'image/gif')}" src="{data_uri(os.path.join(FOLDER, 'hero.mp4'), 'video/mp4')}"></video>
<p class="s">From the running page: the real URDF and the real description on a fixture host, so boards can go bad on cue. Sped up about five times: software WebGL draws 1.4 M triangles slowly, a GPU does not. The upload and the click are real; the status colours here are made up. Preview's real statuses are below.</p>

<h2>Which device is on which part, and why that is true</h2>
<table><tr><th>Device</th><th>Adapter</th><th>On the model</th><th>On the rack</th><th>Proof: only this board moved</th></tr>{rows}
<tr><td class="b">D405 218622274411</td><td></td><td>a camera glyph at its solved pose</td><td>in front of plate A</td><td>solvePnP on today's frame, see below</td></tr>
<tr><td class="b">D405 218622276036</td><td></td><td>a camera glyph at its solved pose</td><td>in front of plates B and C, aimed above them</td><td>solvePnP on today's frame, see below</td></tr></table>
<div class="call"><b>The old mapping was wrong.</b> The description had CAN 12 on <code>one_dof_arm</code>, from <code>robot.json</code>'s <code>can_id: 12</code>. The cameras say CAN 11 turns that arm, and CAN 12 turns tag 7 on plate C. The drive file behind <code>robot.json</code> is stale. Any bundle that carries it still says 12: uploading the a7b1095 bundle as it stands would bring that mapping back.</div>
<p><b>Not placed:</b> the D435 044322072026. The CAD has one D435 on a front mount, but this camera's frame shows the ceiling and no tag, so nothing ties its serial to that link. It stays in the "Not in the model" list with that reason. So does the desk webcam, which was never a rack device. The third adapter, FDA81D29, has a chip and no boards.</p>

<h2>How each mapping is proved</h2>
<p><b>1. Which tag the board turns.</b> Seven boards have a recording in which each alone moved. I tracked the DICT_4X4_50 tags over those videos: in each, one tag or strip moves and the rest of the rack stays still. These are recordings already on disk, so nothing moved today, and <code>tools/rack_map/who_moved.py</code> reruns it. CAN 12 has only the rest-vs-held still of 2026-10-23, because no later recording's camera sees tag 7. CAN 15 was "inferred by elimination" in the deployment record; session_14 is direct proof.</p>
<div class="grid">{gallery}</div>
<p><b>2. Which CAD body the tag is on.</b> Each plate's tags, from the camera-measured as-built map, against the CAD's motor axes:</p>
<table><tr><th>Plate</th><th>Tags, measured</th><th>CAD axes</th></tr>{plates}<tr><td>D (the arm)</td><td colspan="2">tags 8-15 on the one long weighted link; the CAD has one such link, <code>one_dof_arm</code></td></tr></table>
<p>The patterns match within 3 mm, the heights across all plates agree to 5 mm, and the left-to-right order is the CAD's +y, so the model is not mirrored. The plates' places along the rail do not match: real spacing 342 / 201 / 237 mm, CAD 425 / 225 / 190 mm. They slide on the extrusion, so the fit is per plate and nothing here hangs on a position along the rail.</p>

<h2>Today's frames</h2>
<p>One read-only colour frame per camera, under the rack bus lock taken shared, each camera held under 3 seconds. No option written.</p>
<div class="two"><figure>{image(os.path.join(EVIDENCE, pick), 800, 72, 'D405 218622274411')}<figcaption>D405 218622274411: tags {', '.join(str(t) for t in cameras['tags_seen'].get('218622274411', []))}</figcaption></figure>
<figure>{image(os.path.join(EVIDENCE, pick2), 800, 72, 'D405 218622276036')}<figcaption>D405 218622276036: tags {', '.join(str(t) for t in cameras['tags_seen'].get('218622276036', []))}. Aimed above the plates, so tag 7 and most of the strip are out of its view</figcaption></figure>
<figure>{image(os.path.join(EVIDENCE, pick3), 800, 72, 'D435')}<figcaption>D435 044322072026: the ceiling and a light, no tag</figcaption></figure></div>
<table><tr><th>Camera</th><th>solvePnP</th><th>Today</th><th>Before</th></tr>{solved}</table>
<p class="muted">The glyphs are placed through the plates' header strips: where each camera stands relative to the plates, not a CAD position.</p>

<h2>Preview, the real rack, right now</h2>
{image(os.path.join(FOLDER, 'preview-home.png'), 1100, 76, 'Preview home')}
<p class="s">Headless Chrome on Preview (:8821), read-only. {facts.get('timings', {}).get('meshes', '?')} meshes, {len(facts.get('parts', []))} parts, the live feed open. Statuses are the boards' own at that moment.</p>
{image(os.path.join(FOLDER, 'preview-chip.png'), 1100, 76, 'Preview chip') if os.path.exists(os.path.join(FOLDER, 'preview-chip.png')) else ''}
<p class="s">Hovering fdcanusb 0607623A: {facts.get('leaders', '?')} leader lines, one to each of its boards' parts.</p>
<table><tr><th>Part</th><th>Resolved on this PC</th><th>Status</th></tr>{live}</table>

<h2>What's new on the page</h2>
<div class="two"><figure>{image(os.path.join(FOLDER, 'shot-rack-status.png'), 800, 72, 'status')}<figcaption>A bad board colours its own motor, not the base_link all the JMC motors are exported into</figcaption></figure>
<figure>{image(os.path.join(FOLDER, 'shot-rack-chip.png'), 800, 72, 'chip')}<figcaption>An adapter is a chip: hover or select it, its boards light up with leader lines</figcaption></figure>
<figure>{image(os.path.join(FOLDER, 'shot-map-hover.png'), 800, 72, 'map')}<figcaption>Map devices: the tip names the piece before the click. A second click undoes</figcaption></figure>
<figure>{image(os.path.join(FOLDER, 'shot-robot-menu.png'), 800, 72, 'menu')}<figcaption>Robot: bring one in, or go back to a kept one</figcaption></figure>
<figure>{image(os.path.join(FOLDER, 'shot-uploaded.png'), 800, 72, 'uploaded')}<figcaption>A URDF zip, shown at once</figcaption></figure></div>
<p>The rack's export lumps 196 meshes into <code>base_link</code>, so a part can now name bodies inside a link (<code>meshes = ["JMC60_Plate_1___JMC_60ASM200-5-17BCZ_2__*"]</code>), and a click in Map devices takes the motor under the pointer rather than the whole rack. <code>[</code> and <code>]</code>, Shift+wheel, or Narrower and Wider change the size of the piece.</p>

<h2>Proof</h2>
<ul>{''.join(f'<li>{html.escape(line)}</li>' for line in tests.get('lines', []))}</ul>

<h2>Where this leaves you</h2>
<p><b>Decided for you</b></p><ul>{''.join(f'<li>{line}</li>' for line in tests.get('decided', []))}</ul>
<p><b>Left</b></p><ul>{''.join(f'<li>{line}</li>' for line in tests.get('left', []))}</ul>
<p><b>Needs you</b></p><ul>{''.join(f'<li>{line}</li>' for line in tests.get('needs', []))}</ul>
<p><b>Deliberately not done</b></p><ul>{''.join(f'<li>{line}</li>' for line in tests.get('skipped', []))}</ul>
</main></body></html>
'''
out = os.path.join(FOLDER, 'report.html')
with open(out, 'w', encoding='utf-8') as handle:
    handle.write(PAGE)
print(out, os.path.getsize(out))
