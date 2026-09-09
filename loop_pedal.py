#!/usr/bin/env python3
"""
loop_pedal.py -- a loop pedal for your webcam.

Hold the pedal key: the live camera keeps going out to the call while the
frames are recorded. Release it: the recording plays on a loop to the call
instead of the live feed. Tap it (shorter than --min-seconds): back to live.

The output is published as the "OBS Virtual Camera" device, which Zoom, Meet,
Teams, Discord and friends see as an ordinary webcam.

    python loop_pedal.py                 # preview window + virtual camera
    python loop_pedal.py --no-vcam       # preview only, no OBS needed
    python loop_pedal.py --list-cameras  # find the index of your real webcam
    python loop_pedal.py --key f13       # use a different pedal key
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import os
import queue
import subprocess
import sys
import time

import cv2
import numpy as np

LIVE, RECORDING, LOOPING = "LIVE", "REC", "LOOP"


# --------------------------------------------------------------------------- #
# Frame storage
# --------------------------------------------------------------------------- #

class JpegCodec:
    """Frames are kept in RAM as JPEG so a 30 s clip is ~100 MB, not ~2.5 GB."""

    def __init__(self, quality: int = 90):
        self._params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]

    def encode(self, frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, self._params)
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return buf.tobytes()

    def decode(self, data: bytes) -> np.ndarray:
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


class RawCodec:
    """Lossless passthrough. Used by the tests; fine for short clips too."""

    def encode(self, frame: np.ndarray) -> np.ndarray:
        return frame.copy()

    def decode(self, data: np.ndarray) -> np.ndarray:
        return data


class Recorder:
    """Collects encoded frames while the pedal is held; keeps only the newest max_frames."""

    def __init__(self, codec, max_frames: int):
        self._codec = codec
        self._frames: collections.deque = collections.deque(maxlen=max(1, max_frames))

    def push(self, frame: np.ndarray) -> None:
        self._frames.append(self._codec.encode(frame))

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def capacity(self) -> int:
        return self._frames.maxlen

    def take(self) -> list:
        frames = list(self._frames)
        self._frames.clear()
        return frames


def build_loop(frames: list, crossfade: int, codec) -> list:
    """Turn a recording into a list of encoded frames that plays end-to-start without a jump cut.

    The last `crossfade` frames are dissolved into the first `crossfade` frames, so
    the seam is a short fade rather than a cut. The result has len(frames) - crossfade
    frames. `crossfade` is clamped to half the recording.
    """
    n = len(frames)
    k = max(0, min(int(crossfade), n // 2))
    if k == 0:
        return list(frames)
    body = list(frames[k:n - k])
    seam = []
    for i in range(k):
        alpha = (i + 1) / (k + 1)
        tail = codec.decode(frames[n - k + i])
        head = codec.decode(frames[i])
        seam.append(codec.encode(cv2.addWeighted(tail, 1.0 - alpha, head, alpha, 0.0)))
    return body + seam


class Player:
    def __init__(self, frames: list, codec, start: int = 0):
        if not frames:
            raise ValueError("empty loop")
        self._frames = frames
        self._codec = codec
        self._i = start % len(frames)

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def position(self) -> int:
        return self._i

    def next(self) -> np.ndarray:
        frame = self._codec.decode(self._frames[self._i])
        self._i = (self._i + 1) % len(self._frames)
        return frame


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #

class LoopPedal:
    """LIVE --hold--> REC --release--> LOOP (or back to LIVE if the hold was just a tap)."""

    def __init__(self, codec, fps: float, max_seconds: float, min_seconds: float,
                 crossfade_seconds: float, log=print):
        self.state = LIVE
        self.fps = fps
        self._codec = codec
        self._recorder = Recorder(codec, int(max_seconds * fps))
        self._min_frames = max(1, int(min_seconds * fps))
        self._crossfade = int(crossfade_seconds * fps)
        self._player: Player | None = None
        self._log = log

    # -- pedal events --------------------------------------------------------

    def pedal_down(self) -> None:
        if self.state == RECORDING:
            return
        self._recorder.take()
        self.state = RECORDING
        self._log("REC   recording (live feed still going out) - release to loop")

    def pedal_up(self) -> None:
        if self.state != RECORDING:
            return
        frames = self._recorder.take()
        if len(frames) < self._min_frames:
            self.go_live()
            return
        loop = build_loop(frames, self._crossfade, self._codec)
        # Start playback just before the seam: the newest frames dissolve into the
        # oldest ones, so the switch from live to loop is a fade, not a jump.
        k = min(self._crossfade, len(frames) // 2)
        start = len(loop) - k - 1 if k > 0 else 0
        self._player = Player(loop, self._codec, start=start)
        self.state = LOOPING
        self._log(f"LOOP  playing {len(loop) / self.fps:.1f}s loop - hold to re-record, tap to go live")

    def toggle_record(self) -> None:
        """For keyboards without press/release events (the preview window)."""
        if self.state == RECORDING:
            self.pedal_up()
        else:
            self.pedal_down()

    def go_live(self) -> None:
        self._recorder.take()
        self._player = None
        self.state = LIVE
        self._log("LIVE  live feed - hold the pedal key to record")

    # -- per-frame -----------------------------------------------------------

    def process(self, live_frame: np.ndarray) -> np.ndarray:
        """Given the newest camera frame, return the frame to send to the call."""
        if self.state == RECORDING:
            self._recorder.push(live_frame)
            return live_frame
        if self.state == LOOPING and self._player is not None:
            return self._player.next()
        return live_frame

    def hud_info(self) -> dict:
        """What the preview overlay needs: state, seconds elapsed/total, 0..1 progress."""
        if self.state == RECORDING:
            elapsed = len(self._recorder) / self.fps
            total = self._recorder.capacity / self.fps
            return {"state": RECORDING, "elapsed": elapsed, "total": total, "progress": min(1.0, elapsed / total)}
        if self.state == LOOPING and self._player is not None:
            p = self._player
            return {"state": LOOPING, "elapsed": p.position / self.fps, "total": len(p) / self.fps,
                    "progress": p.position / len(p)}
        return {"state": LIVE, "elapsed": None, "total": None, "progress": None}


# --------------------------------------------------------------------------- #
# Global hotkey ("the pedal")
# --------------------------------------------------------------------------- #

def parse_key(keyboard, name: str):
    name = name.strip()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name.lower())
    try:
        return keyboard.Key[name.lower()]
    except KeyError:
        names = ", ".join(k.name for k in keyboard.Key)
        raise SystemExit(f"Unknown key {name!r}. Use a single character or one of: {names}")


class Pedal:
    """Listens for one key system-wide and queues 'down' / 'up' events, ignoring key-repeat."""

    def __init__(self, key_name: str, events: queue.Queue):
        from pynput import keyboard  # imported lazily so --no-pedal works without it
        self._key = parse_key(keyboard, key_name)
        self._events = events
        self._held = False
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)

    def start(self) -> bool:
        """Start listening. Returns False if macOS has not granted Input Monitoring."""
        self._listener.start()
        self._listener.wait()
        return bool(getattr(self._listener, "IS_TRUSTED", True))

    def stop(self) -> None:
        self._listener.stop()

    def _on_press(self, key) -> None:
        if key == self._key and not self._held:
            self._held = True
            self._events.put("down")

    def _on_release(self, key) -> None:
        if key == self._key and self._held:
            self._held = False
            self._events.put("up")


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #

def quiet_opencv() -> None:
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except AttributeError:
        pass


@contextlib.contextmanager
def stderr_silenced():
    """OpenCV's AVFoundation backend prints probe failures straight to fd 2; hide them."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def list_cameras(max_index: int = 5) -> None:
    quiet_opencv()
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["system_profiler", "SPCameraDataType"], capture_output=True,
                                 text=True, timeout=20).stdout
            names = [ln.strip().rstrip(":") for ln in out.splitlines()
                     if ln.startswith("    ") and not ln.startswith("      ")]
            if names:
                print("Cameras macOS knows about:", ", ".join(names))
        except (OSError, subprocess.SubprocessError):
            pass
    print("Probing OpenCV indexes (a static image is a virtual camera's placeholder, not a webcam):")
    for i in range(max_index + 1):
        with stderr_silenced():
            cap = open_capture(i)
            frames = read_frames(cap, 6) if cap.isOpened() else []
            cap.release()
        if frames:
            h, w = frames[-1].shape[:2]
            kind = "live camera" if looks_live(frames) else "STATIC image (skip)"
            print(f"  --camera {i}: {w}x{h}  {kind}")


def read_frames(cap: cv2.VideoCapture, count: int) -> list:
    frames = []
    for _ in range(count):
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    return frames


def looks_live(frames: list) -> bool:
    """A real sensor never produces two identical frames; a placeholder image does."""
    if len(frames) < 2:
        return False
    diffs = [cv2.absdiff(a, b).mean() for a, b in zip(frames, frames[1:])]
    return max(diffs) > 0.05


def open_capture(index: int) -> cv2.VideoCapture:
    if sys.platform == "darwin":
        return cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    return cv2.VideoCapture(index)


def find_live_camera(max_index: int = 5) -> int:
    """First index whose frames change over time, i.e. not a virtual camera's placeholder."""
    for i in range(max_index + 1):
        with stderr_silenced():
            cap = open_capture(i)
            frames = read_frames(cap, 6) if cap.isOpened() else []
            cap.release()
        if frames and looks_live(frames):
            return i
    sys.exit("No live camera found. Run --list-cameras, and check that this terminal is allowed "
             "to use the camera (System Settings > Privacy & Security > Camera).")


def open_camera(index: int | None, width: int, height: int, fps: float):
    quiet_opencv()
    if index is None:
        index = find_live_camera()
        print(f"Auto-picked camera {index} (first index that is a live sensor, not a placeholder)")
    with stderr_silenced():
        cap = open_capture(index)
    if not cap.isOpened():
        sys.exit(f"Could not open camera {index}. Try --list-cameras, and check that this "
                 f"terminal is allowed to use the camera (System Settings > Privacy & Security > Camera).")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    ok, frame = cap.read()
    if not ok:
        sys.exit(f"Camera {index} opened but returned no frames.")
    h, w = frame.shape[:2]
    return cap, w, h


def open_vcam(width: int, height: int, fps: float):
    import pyvirtualcam
    try:
        cam = pyvirtualcam.Camera(width=width, height=height, fps=fps,
                                  fmt=pyvirtualcam.PixelFormat.BGR, print_fps=False)
    except RuntimeError as exc:
        sys.exit(
            f"Virtual camera unavailable: {exc}\n\n"
            "One-time setup on macOS:\n"
            "  1. Open OBS (installed in /Applications).\n"
            "  2. Click 'Start Virtual Camera' (bottom right). macOS will ask you to allow the\n"
            "     OBS camera extension in System Settings > Privacy & Security. Allow it.\n"
            "  3. Click 'Stop Virtual Camera', quit OBS. Reboot if macOS asked you to.\n"
            "Then run this again. Use --no-vcam to try the preview without it."
        )
    return cam


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

KEY_LABELS = {
    "alt": "Option", "alt_l": "left Option", "alt_r": "right Option",
    "cmd": "Command", "cmd_l": "left Command", "cmd_r": "right Command",
    "ctrl": "Control", "ctrl_l": "left Control", "ctrl_r": "right Control",
    "shift": "Shift", "shift_l": "left Shift", "shift_r": "right Shift",
}


def key_label(name: str) -> str:
    name = name.strip().lower()
    if len(name) == 1:
        return name.upper()
    return KEY_LABELS.get(name, name.upper() if name.startswith("f") and name[1:].isdigit() else name)


FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE, GREY = (255, 255, 255), (185, 185, 185)
STATE_STYLE = {   # colour (BGR), title
    LIVE: ((70, 170, 60), "LIVE"),
    RECORDING: ((40, 40, 220), "REC"),
    LOOPING: ((200, 130, 30), "LOOP"),
}


def draw_hud(img: np.ndarray, info: dict, key: str) -> None:
    """Status bar across the top of the preview: badge, timer, progress bar, hints."""
    h, w = img.shape[:2]
    scale = max(0.6, min(1.0, w / 1280))
    bar_h = int(58 * scale)
    pad = int(14 * scale)

    # translucent dark strip
    strip = img[:bar_h]
    cv2.addWeighted(strip, 0.35, np.zeros_like(strip), 0.65, 0, dst=strip)

    colour, title = STATE_STYLE[info["state"]]
    state = info["state"]

    # state badge
    fs_big = 0.85 * scale
    (tw, th), _ = cv2.getTextSize(title, FONT, fs_big, 2)
    badge_w = tw + 2 * pad + (int(22 * scale) if state == RECORDING else 0)
    x0, y0, y1 = pad, int(9 * scale), bar_h - int(9 * scale)
    cv2.rectangle(img, (x0, y0), (x0 + badge_w, y1), colour, -1)
    tx = x0 + pad
    if state == RECORDING:
        dot_on = (time.time() % 1.0) < 0.6
        cv2.circle(img, (x0 + pad + int(6 * scale), (y0 + y1) // 2), int(6 * scale),
                   WHITE if dot_on else colour, -1)
        tx += int(22 * scale)
    cv2.putText(img, title, (tx, (y0 + y1) // 2 + th // 2), FONT, fs_big, WHITE, 2, cv2.LINE_AA)

    # timer
    x = x0 + badge_w + pad
    fs = 0.7 * scale
    if state == RECORDING:
        timer = f"{info['elapsed']:.1f}s"
    elif state == LOOPING:
        timer = f"{info['elapsed']:4.1f} / {info['total']:.1f}s"
    else:
        timer = ""
    if timer:
        cv2.putText(img, timer, (x, (y0 + y1) // 2 + int(9 * scale)), FONT, fs, WHITE, 2, cv2.LINE_AA)
        x += cv2.getTextSize(timer, FONT, fs, 2)[0][0] + 2 * pad

    # hints, right-aligned
    hints = {
        LIVE: f"hold {key} to record",
        RECORDING: f"release {key} to loop",
        LOOPING: f"hold {key}: re-record   tap: go live",
    }[state]
    fs_h = 0.55 * scale
    (hw, hh), _ = cv2.getTextSize(hints, FONT, fs_h, 1)
    hx = max(x, w - pad - hw)
    cv2.putText(img, hints, (hx, (y0 + y1) // 2 + hh // 2), FONT, fs_h, GREY, 1, cv2.LINE_AA)

    # progress bar along the bottom edge of the strip
    if info["progress"] is not None:
        bar_y = bar_h - max(3, int(4 * scale))
        cv2.rectangle(img, (0, bar_y), (w, bar_h), (60, 60, 60), -1)
        cv2.rectangle(img, (0, bar_y), (int(w * info["progress"]), bar_h), colour, -1)


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1280x720")


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None,
                    help="OpenCV camera index of your real webcam (default: first index that is a live sensor)")
    ap.add_argument("--list-cameras", action="store_true", help="probe camera indexes and exit")
    ap.add_argument("--size", type=parse_size, default=(1280, 720), help="requested capture size (default 1280x720)")
    ap.add_argument("--fps", type=float, default=30.0, help="output frame rate (default 30)")
    ap.add_argument("--key", default="alt_r",
                    help="pedal key, held to record (default alt_r = right Option; try f13, or a letter)")
    ap.add_argument("--no-pedal", action="store_true", help="no global hotkey; control from the preview window only")
    ap.add_argument("--no-vcam", action="store_true", help="preview only; don't publish the virtual camera")
    ap.add_argument("--no-preview", action="store_true", help="don't open the preview window (Ctrl+C to quit)")
    ap.add_argument("--max-seconds", type=float, default=30.0, help="longest recording kept (default 30)")
    ap.add_argument("--min-seconds", type=float, default=1.0,
                    help="holds shorter than this count as a tap = go live (default 1.0)")
    ap.add_argument("--crossfade", type=float, default=0.5, help="seconds of dissolve at the loop seam (default 0.5, 0 = hard cut)")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality for frames held in RAM (default 90)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list_cameras:
        list_cameras()
        return 0

    cap, width, height = open_camera(args.camera, *args.size, args.fps)
    print(f"Camera {args.camera}: {width}x{height} @ {args.fps:g} fps")

    pedal = LoopPedal(JpegCodec(args.quality), args.fps, args.max_seconds, args.min_seconds,
                      args.crossfade, log=lambda m: print(time.strftime("%H:%M:%S"), m))

    events: queue.Queue = queue.Queue()
    pedal_label = key_label(args.key) if not args.no_pedal else "r"
    hotkey = None
    if not args.no_pedal:
        hotkey = Pedal(args.key, events)
        if hotkey.start():
            print(f"Pedal key: '{args.key}'  hold = record, release = loop, tap = live  (works from any app)")
        else:
            print("!! macOS is not letting this terminal watch the keyboard, so the global pedal key is off.\n"
                  "   System Settings > Privacy & Security > Input Monitoring: enable your terminal app, restart it.\n"
                  "   Until then use the preview window keys.")

    vcam = None
    if not args.no_vcam:
        vcam = open_vcam(width, height, args.fps)
        print(f"Virtual camera: '{vcam.device}'  <- pick this camera in Zoom / Meet / Teams")
    else:
        print("Virtual camera: off (--no-vcam)")

    if not args.no_preview:
        print("Preview keys: r = start/stop recording, l = go live, q = quit")
    print("Ctrl+C to quit.")

    window = "loop pedal"
    failures = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                failures += 1
                if failures > 30:
                    print("Camera stopped delivering frames.")
                    return 1
                time.sleep(0.05)
                continue
            failures = 0

            while True:
                try:
                    ev = events.get_nowait()
                except queue.Empty:
                    break
                if ev == "down":
                    pedal.pedal_down()
                elif ev == "up":
                    pedal.pedal_up()

            out = pedal.process(frame)

            if vcam is not None:
                vcam.send(out)
                vcam.sleep_until_next_frame()

            if not args.no_preview:
                preview = cv2.flip(out, 1)  # mirror the self-view only; the call gets it unmirrored
                draw_hud(preview, pedal.hud_info(), pedal_label)
                cv2.imshow(window, preview)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    pedal.toggle_record()
                elif key == ord("l"):
                    pedal.go_live()
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if hotkey is not None:
            hotkey.stop()
        if vcam is not None:
            vcam.close()
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
