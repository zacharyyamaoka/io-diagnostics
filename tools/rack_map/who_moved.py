"""Which ArUco tags moved in recorded sessions where exactly one board moved. The recordings are on disk: nothing here touches hardware.

The rack's bam_rack_twin sessions (/archive/datasets/bam_rig/rack_<date>/session_*/) hold each camera's video and a plan naming the board.
For each board this tracks DICT_4X4_50 tags over sampled frames of both D405s. The tag's in-plane angle is the mean over its four edges,
so a still tag's noise stays small; the plate headers (tags 16-26) never move and are the noise floor. A tag "moved" when its range clears
2.5 x that floor (and 6 deg, or 4 px).

    python3 tools/rack_map/who_moved.py <out dir> [CAN ids, comma-separated]

Writes who_moved.json and can<id>.jpg (two frames blended, the moved tags in yellow at both poses).
"""
import json
import math
import os
import sys

import cv2
import numpy as np

ROOT = '/archive/datasets/bam_rig'
# WHY a list and not every session: a plan may move two boards, and only a one-board plan proves anything. CAN 12 has none whose camera sees
# tag 7 (276036 was re-aimed); its proof is the rest-vs-held still of 2026-10-23
SESSIONS = {
    1: ['rack_2026-10-23/session_05', 'rack_2026-10-23/session_16'],
    14: ['rack_2026-10-21/session_10', 'rack_2026-10-23/session_02'],
    15: ['rack_2026-10-24/session_14', 'rack_2026-10-24/session_05'],
    4: ['rack_2026-10-23/session_10', 'rack_2026-10-24/session_25'],
    21: ['rack_2026-10-24/session_26'],
    13: ['rack_2026-10-23/session_11'],
    11: ['rack_2026-10-23/session_12', 'rack_2026-10-24/session_29'],
}
CAMERAS = ('218622274411', '218622276036')
SAMPLES = 80
FIRST_HEADER_TAG = 16


def detector():
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    parameters.adaptiveThreshWinSizeMax = 53
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), parameters)


def angle_of(quad: np.ndarray) -> float:
    """The tag's in-plane angle: its four edges, each turned back by its quarter, averaged."""

    total = 0j
    for index in range(4):
        edge = quad[(index + 1) % 4] - quad[index]
        total += complex(edge[0], edge[1]) * (-1j) ** index
    return math.atan2(total.imag, total.real)


def track(video: str, find, clahe) -> tuple[dict, dict]:
    capture = cv2.VideoCapture(video)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    tracks, frames = {}, {}
    for index in np.linspace(0, max(0, total - 2), SAMPLES).astype(int):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
        ok, image = capture.read()
        if not ok:
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        seen = {}
        for candidate in (gray, clahe.apply(gray)):                 # the rack is dark in the evening
            corners, ids, _ = find.detectMarkers(candidate)
            if ids is not None:
                for corner, tag in zip(corners, ids.ravel()):
                    if int(tag) <= 26:                              # the rack's tags; anything else is a false detection
                        seen.setdefault(int(tag), corner.reshape(4, 2))
        for tag, quad in seen.items():
            tracks.setdefault(tag, []).append((int(index), angle_of(quad), quad.mean(0), quad.copy()))
        frames[int(index)] = image
    capture.release()
    return tracks, frames


def ranges(rows: list) -> tuple[float, float]:
    angles = np.unwrap([row[1] for row in rows])
    centres = np.array([row[2] for row in rows])
    low, high = np.percentile(centres, [2, 98], axis=0)
    return float(np.degrees(np.percentile(angles, 98) - np.percentile(angles, 2))), float(np.max(high - low))


def still(can: int, session: str, serial: str, tracks: dict, frames: dict, moved: dict, path: str) -> None:
    tag = max(moved, key=lambda item: moved[item]['turn_deg'] + moved[item]['shift_px'])
    rows = tracks[tag]
    values = np.unwrap([row[1] for row in rows])
    first, last = rows[int(np.argmin(values))], rows[int(np.argmax(values))]
    image = cv2.convertScaleAbs(cv2.addWeighted(frames[first[0]], 0.5, frames[last[0]], 0.5, 0), alpha=1.5, beta=10)
    for other, other_rows in tracks.items():
        if other not in moved:
            quad = other_rows[0][3].astype(np.int32)
            cv2.polylines(image, [quad], True, (170, 170, 170), 1, cv2.LINE_AA)
            cv2.putText(image, str(other), tuple(int(v) for v in quad[0] + (0, -5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (210, 210, 210), 1, cv2.LINE_AA)
    for other in moved:
        for frame in (first[0], last[0]):
            row = next((item for item in tracks[other] if item[0] == frame), None)
            if row is not None:
                quad = row[3].astype(np.int32)
                cv2.polylines(image, [quad], True, (0, 230, 255), 2, cv2.LINE_AA)
                cv2.putText(image, str(other), tuple(int(v) for v in quad[0] + (0, -6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2, cv2.LINE_AA)
    cv2.rectangle(image, (0, 0), (image.shape[1], 30), (0, 0, 0), -1)
    title = f"CAN {can}: the only board moving ({session.replace('rack_', '')}, D405 {serial}). Yellow: tags that moved"
    cv2.putText(image, title, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 88])


def main(out: str, wanted: set[int] | None) -> None:
    os.makedirs(out, exist_ok=True)
    find, clahe = detector(), cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    summary = {}
    for can, sessions in SESSIONS.items():
        if wanted and can not in wanted:
            continue
        best, summary[can] = None, []
        for session in sessions:
            plan = json.load(open(f'{ROOT}/{session}/session.json')).get('plan') or {}
            boards = {step['can_id'] for step in plan.get('steps', []) if 'can_id' in step}
            if boards != {can}:
                raise SystemExit(f'{session} moved {sorted(boards)}, not only CAN {can}: it proves nothing')
            for serial in CAMERAS:
                tracks, frames = track(f'{ROOT}/{session}/cameras/cam_{serial}.mp4', find, clahe)
                rows = {tag: dict(zip(('turn_deg', 'shift_px'), (round(value, 1) for value in ranges(items))), seen=len(items)) for tag, items in tracks.items() if len(items) >= 8}
                headers = [row for tag, row in rows.items() if tag >= FIRST_HEADER_TAG]
                turn = max((row['turn_deg'] for row in headers), default=4.0)
                shift = max((row['shift_px'] for row in headers), default=1.5)
                moved = {tag: row for tag, row in rows.items() if tag < FIRST_HEADER_TAG and (row['turn_deg'] > max(6.0, 2.5 * turn) or row['shift_px'] > max(4.0, 3 * shift))}
                entry = {'session': session, 'camera': serial, 'tags_seen': sorted(rows), 'moved': moved, 'header_noise_deg': turn, 'header_noise_px': shift}
                summary[can].append(entry)
                print(can, session, serial[-6:], 'moved', moved, 'noise', turn, shift, flush=True)
                score = sum(row['turn_deg'] + row['shift_px'] for row in moved.values())
                if moved and (best is None or score > best[0]):
                    best = (score, session, serial, tracks, frames, moved)
        if best:
            still(can, best[1], best[2], best[3], best[4], best[5], os.path.join(out, f'can{can}.jpg'))
        with open(os.path.join(out, 'who_moved.json'), 'w') as file:
            json.dump(summary, file, indent=1)


if __name__ == '__main__':
    main(sys.argv[1], {int(value) for value in sys.argv[2].split(',')} if len(sys.argv) > 2 else None)
