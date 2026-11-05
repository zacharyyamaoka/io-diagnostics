"""One read-only colour frame from each of the rack's RealSense cameras, for tools/rack_map/ (docs/robot-description.md "The rack's description").

No option is written; each pipeline is started and stopped within a few seconds, because RobotDx's own probes share these cameras. It runs
under the rack bus lock, taken SHARED and non-blocking: an experiment holding it exclusively makes this exit at once. It never waits and
never forces, and nothing here can move a motor.

    uv run --project ~/bam_ws/src/core/mdp/agent/sensors/intel_rgbd --extra real python tools/rack_map/capture_frames.py <out dir>
"""
import fcntl
import json
import os
import sys
import time

import numpy as np
import pyrealsense2 as rs
from PIL import Image

LOCK = '/home/bam/.cache/rack-commissioning/bus.lock'
CAMERAS = ('218622274411', '218622276036', '044322072026')
WARMUP_FRAMES = 25  # the auto exposure settles
MODES = ((1280, 720, 15), (1280, 720, 6), (848, 480, 15), (640, 480, 30))  # the D435 sits on USB 2


def grab(serial: str, width: int, height: int, rate: int):
    context = rs.context()
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, width, height, rs.format.rgb8, rate)
    pipeline = rs.pipeline(context)
    started = time.time()
    profile = pipeline.start(config)
    try:
        for _ in range(WARMUP_FRAMES + 1):
            frame = pipeline.wait_for_frames(5000).get_color_frame()
        pixels = np.asanyarray(frame.get_data()).copy()
        intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        return pixels, {'serial': serial, 'name': profile.get_device().get_info(rs.camera_info.name), 'width': intrinsics.width, 'height': intrinsics.height,
                        'fx': intrinsics.fx, 'fy': intrinsics.fy, 'ppx': intrinsics.ppx, 'ppy': intrinsics.ppy, 'coeffs': list(intrinsics.coeffs),
                        'held_s': round(time.time() - started, 2)}
    finally:
        pipeline.stop()


def main(out: str) -> int:
    os.makedirs(out, exist_ok=True)
    with open(LOCK, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            print('the bus lock is held exclusively: an experiment runs, no capture')
            return 3
        results = []
        try:
            for serial in CAMERAS:
                for width, height, rate in MODES:
                    try:
                        pixels, info = grab(serial, width, height, rate)
                    except RuntimeError as error:
                        print(serial, width, height, rate, 'refused:', error)
                        continue
                    info['image'] = os.path.join(out, f"{time.strftime('%Y%m%dT%H%M%S')}-{serial}.png")
                    Image.fromarray(pixels).save(info['image'])
                    results.append(info)
                    print(json.dumps(info))
                    break
                time.sleep(0.3)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    with open(os.path.join(out, 'capture.json'), 'w') as file:
        json.dump(results, file, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
