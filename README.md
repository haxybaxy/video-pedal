# video pedal

<img width="640" height="480" alt="video-pedal" src="https://github.com/user-attachments/assets/7d989573-c523-494c-8923-651b8b29c1d9" />

A loop pedal for your webcam, in the same sense as a guitar looper: hold a key to
record what the camera sees, let go and the recording plays on repeat as your camera
output, press a second key to dissolve back to live. The output is a virtual camera,
so anything that reads a webcam (Zoom, Meet, Teams, Discord, QuickTime, OBS itself)
sees an ordinary camera device.

About 650 lines of Python on top of OpenCV, pyvirtualcam and pynput, with the
interesting parts (loop builder, ring buffer, state machine, hotkey logic) tested
without any hardware.

## What it does

- **Hold right Option (⌥)** to record. The live feed keeps going out while you hold;
  the switch to the loop happens on release, not on press.
- **Release** and the recording plays on a loop. The seam is crossfaded, and playback
  starts just before the seam, so both the loop's own wrap-around and the cut from
  live to loop are half-second dissolves rather than jump cuts.
- **Press right Command (⌘)** once and the loop dissolves back into the live feed.
- While a loop plays, the preview window ghosts it over the live camera at half
  opacity, so you can line yourself up with the loop before ending it.

Both keys are global hotkeys and work while any other app has focus. Change them with
`--key` and `--live-key`.

## How it works

Per frame: camera → `LoopPedal.process()` → virtual camera + preview window.

- **Ring buffer of JPEGs.** While recording, each frame is JPEG-encoded and pushed
  onto a `collections.deque` capped at `--max-seconds × fps`. The newest N seconds are
  always kept, so a long hold just slides the window. About 2 MB/s at 720p;
  `--quality` trades RAM for artifacts.
- **Seamless loop.** `build_loop()` dissolves the last *k* frames of the recording
  into the first *k* with `cv2.addWeighted` and drops the overlap. The result plays
  end-to-start with a short fade where the cut would be.
- **Fade in, fade out.** On release, the player starts *k* frames before the seam, so
  the first thing that goes out is the recording's tail dissolving into its head. The
  tail *was* the live feed a moment ago, so visually that's a dissolve from live into
  the loop. On go-live the same ramp runs in the other direction: each loop frame is
  blended with the current live frame until it's all live.
- **State machine.** `LIVE → REC → LOOP → LIVE`, plus the edges: holds shorter than
  `--min-seconds` are ignored, holding the pedal while looping re-records, the live
  key mid-recording cancels it, and the live key during a dissolve is a no-op.
- **Global hotkeys.** `pynput` listens system-wide for press/release on the pedal key
  and ignores key-repeat. The live key only counts as a solo press: if any other key
  goes down while it's held (right-⌘+Tab, say), it's treated as a shortcut and
  ignored, so normal use of the focused app doesn't end the loop.
- **Ghost preview.** `blend_overlay()` alpha-blends the loop frame over the live
  frame for the preview only; the virtual camera gets the plain loop.
- **Camera discovery.** Once the OBS extension is installed, the virtual camera shows
  up as a capture index too, and reading it would loop its placeholder image back into
  itself. So discovery reads a few frames from each index and picks the first one
  whose frames actually change over time.
- **Virtual camera.** `pyvirtualcam` publishes BGR frames to OBS's virtual camera
  device, which macOS exposes as a normal camera to every app.
- **Tests.** 31 tests cover the loop builder, ring buffer, player, state machine
  (including the dissolve to live), the overlay blend and the two-key hotkey mapping.
  They swap in a raw codec for JPEG and never touch a camera.

## Setup (macOS)

### 1. Python

```
git clone https://github.com/haxybaxy/video-pedal
cd video-pedal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. OBS virtual camera (once)

The script publishes video through OBS's virtual camera device. macOS only creates
that device after OBS has been run once:

1. Install OBS: `brew install --cask obs`.
2. Open OBS. If it shows a setup wizard, cancel it.
3. Click **Start Virtual Camera** (bottom right, under Controls).
4. macOS pops up a message about a system extension. Open
   **System Settings > Privacy & Security**, scroll down, and **Allow** the OBS
   camera extension. Reboot if it asks you to.
5. Back in OBS click **Stop Virtual Camera**, then quit OBS.

You never need to open OBS again after this. **Keep OBS closed while using the
script.** If OBS is open with its own virtual camera started, OBS owns the device and
pushes its (empty, black) scene out instead of your feed.

### 3. Permissions (once)

Both are under **System Settings > Privacy & Security**:

- **Camera**: so the script can read the webcam. macOS asks the first time you run it.
- **Input Monitoring**: so the pedal and live keys are noticed while another app has
  focus. If this is missing, the script prints a warning at startup and the two keys
  do nothing, but the preview-window keys still work. Restart the terminal after
  enabling it.

### 4. Run

```
.venv/bin/python loop_pedal.py
```

You should see:

```
Camera 0: 1280x720 @ 30 fps
Pedal key: 'alt_r'  hold = record, release = loop  (works from any app)
Live key:  'cmd_r'  press once = end the loop / cancel a recording, go live
Virtual camera: 'OBS Virtual Camera'  <- pick this camera in Zoom / Meet / Teams
Preview keys: r = start/stop recording, l = go live, q = quit
```

and a preview window showing the output, with a status line at the top (LIVE, REC
with a red dot, or LOOP). While a loop plays, the preview ghosts the loop over your
live camera at half opacity; the virtual camera still gets the plain loop.

Start the script **before** opening the app you want to feed; most apps enumerate
cameras once at launch. Then pick **OBS Virtual Camera** in its video settings.

## Controls

| key | does |
|---|---|
| hold right Option | record. Live feed keeps going out while you hold. |
| release right Option | play the recording on a loop, with a short dissolve at the seam. |
| hold right Option again | replace the loop with a new recording. |
| tap right Option (under 1 s) | ignored; nothing changes. |
| press right Command | go live: the loop dissolves into the live feed. Also cancels a recording in progress. |

Both keys work from any app. Right Command only counts when pressed on its own; as
part of a shortcut (right-Cmd+Tab, say) it is ignored. The preview window also takes
`r` (start / stop recording), `l` (go live) and `q` (quit). Ctrl+C in the terminal
also quits.

## Handy variants

```
.venv/bin/python loop_pedal.py --no-vcam        # try it without OBS: preview only
.venv/bin/python loop_pedal.py --list-cameras   # which index is the real webcam?
.venv/bin/python loop_pedal.py --camera 1       # use that index
.venv/bin/python loop_pedal.py --key f13        # different pedal key
.venv/bin/python loop_pedal.py --live-key f14   # different go-live key (also if Karabiner remaps Command)
.venv/bin/python loop_pedal.py --overlay 0      # no ghost: preview shows exactly what goes out
.venv/bin/python loop_pedal.py --crossfade 0    # hard cuts at the seam and when going live
```

## All options

| flag | default | |
|---|---|---|
| `--key` | `alt_r` | pedal key. Modifier names like `alt_r`, `ctrl_r`, `shift_r`, function keys like `f13`, or a single letter (which also gets typed into whatever has focus). |
| `--live-key` | `cmd_r` | go-live key, pressed once on its own to end the loop or cancel a recording. Same key names as `--key`; must differ from it. |
| `--camera` | auto | OpenCV index of the real webcam; default is the first index that is a live sensor |
| `--size` | `1280x720` | requested capture size |
| `--fps` | `30` | output frame rate |
| `--max-seconds` | `30` | longest recording kept; older frames drop off. About 2 MB of RAM per second at 720p. |
| `--min-seconds` | `1.0` | holds shorter than this are ignored (too short to loop) |
| `--crossfade` | `0.5` | seconds of dissolve at the loop seam and when the loop dissolves into live, `0` for hard cuts |
| `--overlay` | `0.5` | preview only: opacity of the loop ghosted over the live camera while looping, `0` for no ghost |
| `--quality` | `90` | JPEG quality of frames held in RAM |
| `--no-pedal` | | skip the global hotkey |
| `--no-vcam` | | preview only, no virtual camera |
| `--no-preview` | | no window; Ctrl+C to quit |

## Tests

```
.venv/bin/python -m pytest
```
