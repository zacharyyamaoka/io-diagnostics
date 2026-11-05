"""Tags in today's frames, the D405s' poses from them, and how the rack's plates sit against the CAD. Reads only files.

From tools/rack_map/capture_frames.py's frames: detects DICT_4X4_50, writes each frame with its tags boxed, and solves each D405's pose by
cv2.solvePnP against the static header tags' camera-measured positions (the as-built bundle, rack frame: origin tag 17, x left to right
from the front, y up, z towards the cameras). Then it puts the poses in the URDF's base frame through the plates' header strips, and
prints each plate's tags against the CAD's motor axes (docs/robot-description.md "The rack's description").

    python3 tools/rack_map/solve_cameras.py <frames dir>

Writes <name>-tags.jpg, detections.json and cameras.json into the frames dir.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

import cv2
import numpy as np

ASBUILT = '/archive/datasets/bam_rig/rack_2026-10-21/asbuilt/bundle'
FIRST_HEADER_TAG = 16
# rack x -> CAD y at each plate's header strip: tag 17, tag 20, tag 23, tags 25 + 26; the strips' CAD centres from fusion360_data.json
# WHY by plate: the plates slide on the rail and sit up to 104 mm from their CAD places; a camera stays where it is relative to the plates
PLATES = [(0.0, 0.06), (0.3418, 0.485), (0.5431, 0.71), (0.7806, 0.90)]
PLATE_FACE_CAD_X = 0.154
HEADER_CAD_Z = 0.26
# the CAD's motor axes (y, z), from the meshes' boxes: each plate's tags, as the pattern they must match
CAD_AXES = {0: (0.022, 0.13), 1: (0.022, 0.055), 2: (0.098, 0.055), 4: (0.445, 0.06), 5: (0.525, 0.06), 6: (0.71, 0.185), 7: (0.71, 0.075)}


def quat_matrix(q) -> np.ndarray:
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def as_built() -> tuple[dict, dict]:
    """Every tag's four corners in the rack frame; the powered ones through their link's pose."""

    robot = json.load(open(f'{ASBUILT}/robot.json'))
    bodies = {body.get('name'): (np.array([float(v) for v in body.get('pos').split()]), quat_matrix([float(v) for v in body.get('quat').split()]))
              for body in ET.parse(f'{ASBUILT}/mjcf/scene.xml').getroot().iter('body')}
    tags = {}
    for entry in robot['fiducials']:
        corners = np.array(entry['corners_link_m'])
        host = entry.get('host_link') or 'world'
        if host in bodies:
            corners = corners @ bodies[host][1].T + bodies[host][0]
        tags[entry['id']] = corners
    return tags, robot


def rack_to_cad(point) -> list[float]:
    x, y, z = (float(value) for value in point)
    xs, ys = zip(*PLATES)
    cad_y = ys[0] + (x - xs[0]) if x <= xs[0] else ys[-1] + (x - xs[-1]) if x >= xs[-1] else float(np.interp(x, xs, ys))
    return [round(PLATE_FACE_CAD_X + z, 3), round(cad_y, 3), round(HEADER_CAD_Z + y, 3)]


def detect(path: str, find, clahe) -> dict:
    image = cv2.imread(path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found = {}
    for candidate in (gray, clahe.apply(gray)):
        corners, ids, _ = find.detectMarkers(candidate)
        if ids is not None:
            for corner, tag in zip(corners, ids.ravel()):
                found.setdefault(int(tag), corner.reshape(4, 2))
    annotated = cv2.convertScaleAbs(image, alpha=1.8, beta=12)
    for tag, quad in found.items():
        points = quad.astype(np.int32)
        cv2.polylines(annotated, [points], True, (0, 230, 255), 2, cv2.LINE_AA)
        cv2.putText(annotated, str(tag), tuple(int(v) for v in points[0] + (0, -7)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 230, 255), 2, cv2.LINE_AA)
    cv2.imwrite(path[:-4] + '-tags.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return found


def main(folder: str) -> None:
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    parameters.adaptiveThreshWinSizeMax = 53
    parameters.minMarkerPerimeterRate = 0.015
    find = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), parameters)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    tags, robot = as_built()
    capture = {item['serial']: item for item in json.load(open(os.path.join(folder, 'capture.json')))}
    out, seen = {}, {}
    for serial, info in capture.items():
        path = os.path.join(folder, os.path.basename(info['image']))
        found = detect(path, find, clahe)
        seen[serial] = sorted(found)
        print(serial, 'tags', sorted(found))
        header = [tag for tag in found if tag >= FIRST_HEADER_TAG and tag in tags]
        if len(header) < 4:
            continue
        K = np.array([[info['fx'], 0, info['ppx']], [0, info['fy'], info['ppy']], [0, 0, 1]])
        dist = np.array(info['coeffs'])
        world = np.concatenate([tags[tag] for tag in header])
        image = np.concatenate([found[tag] for tag in header])
        _, rvec, tvec = cv2.solvePnP(world, image, K, dist, flags=cv2.SOLVEPNP_SQPNP)
        rvec, tvec = cv2.solvePnPRefineLM(world, image, K, dist, rvec, tvec)
        projected, _ = cv2.projectPoints(world, rvec, tvec, K, dist)
        residual = np.linalg.norm(projected.reshape(-1, 2) - image, axis=1)
        rotation, _ = cv2.Rodrigues(rvec)
        centre = (-rotation.T @ tvec).ravel()
        forward = rotation.T @ np.array([0, 0, 1.0])
        looks_at = centre + forward * (-centre[2] / forward[2])     # where its optical axis meets the rack's plane
        before = next((camera['xyz_m'] for camera in robot['cameras'] if camera['serial'] == serial), None)
        out[serial] = {'header_tags': sorted(header), 'rms_px': round(float(np.sqrt((residual ** 2).mean())), 2), 'max_px': round(float(residual.max()), 2),
                       'rack_mm': (centre * 1000).round(1).tolist(), 'pose_2026_10_21_mm': (np.array(before) * 1000).round(1).tolist() if before else None,
                       'at': rack_to_cad(centre), 'look_at': rack_to_cad(looks_at)}
        print(' ', json.dumps(out[serial]))
    centres = {tag: corners.mean(0) for tag, corners in tags.items()}
    pattern = {}
    for plate, group in (('A (GIM43)', (0, 1, 2)), ('B (JMC60)', (4, 5)), ('C (gearbox)', (6, 7))):
        rack = np.array([centres[tag][:2] for tag in group])
        cad = np.array([CAD_AXES[tag] for tag in group])
        pattern[plate] = {'spacing_rack_mm': (np.linalg.norm(np.diff(rack, axis=0), axis=1) * 1000).round(1).tolist(), 'spacing_cad_mm': (np.linalg.norm(np.diff(cad, axis=0), axis=1) * 1000).round(1).tolist(),
                          'offset_cad_minus_rack_mm': ((cad - rack) * 1000).round(1).tolist()}
        print(plate, pattern[plate])
    with open(os.path.join(folder, 'cameras.json'), 'w') as file:
        json.dump({'cameras': out, 'tags_seen': seen, 'plates': pattern}, file, indent=1)


if __name__ == '__main__':
    main(sys.argv[1])
