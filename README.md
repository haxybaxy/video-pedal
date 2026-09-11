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

Both keys are global hotkeys and work while any other app has focus. On Windows and
Linux the same defaults are right Alt and the right Windows / Super key (see
[Keys on Windows and Linux](#keys-on-windows-and-linux)). Change them with `--key`
and `--live-key`.

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
  device (a v4l2loopback device on Linux), which the OS exposes as a normal camera
  to every app.
- **Tests.** 31 tests cover the loop builder, ring buffer, player, state machine
  (including the dissolve to live), the overlay blend and the two-key hotkey mapping.
  They swap in a raw codec for JPEG and never touch a camera.

## Setup

macOS, Windows and Linux. The one-time part is getting a virtual camera device onto
the system; after that it's `python loop_pedal.py`. I've only run this on a Mac; the
Windows and Linux paths are what the libraries document, not something I've
exercised, so reports welcome.

### 1. Python

```
git clone https://github.com/haxybaxy/video-pedal
cd video-pedal
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.9 or newer. Everything below assumes the venv is active.

### 2. Virtual camera device (once)

`pyvirtualcam` publishes frames to whatever virtual camera the OS has; it doesn't
create one itself.

| OS | do this |
|---|---|
| macOS | install [OBS Studio](https://obsproject.com) 30 or newer (`brew install --cask obs`), then follow the five steps below: macOS only creates the device after OBS's camera extension has been allowed once |
| Windows | install [OBS Studio](https://obsproject.com). The installer registers the virtual camera driver; you never need to open OBS |
| Linux | `sudo apt install v4l2loopback-dkms` (Debian/Ubuntu; same package name on Arch; `v4l2loopback` from RPM Fusion on Fedora), then `sudo modprobe v4l2loopback video_nr=10 card_label="Video Pedal" exclusive_caps=1` |

**macOS, the five steps:**

1. Open OBS. If it shows a setup wizard, cancel it.
2. Click **Start Virtual Camera** (bottom right, under Controls).
3. macOS pops up a message about a system extension. Open
   **System Settings > Privacy & Security**, scroll down, and **Allow** the OBS
   camera extension. Reboot if it asks you to.
4. Back in OBS click **Stop Virtual Camera**, then quit OBS.
5. Done. You never need to open OBS again.

**macOS and Windows: keep OBS closed while using the script.** If OBS is open with
its own virtual camera started, OBS owns the device and pushes its (empty, black)
scene out instead of your feed.

**Linux notes.** `video_nr=10` puts the device at `/dev/video10`, safely above the
indexes camera discovery probes, so it can't be mistaken for the webcam.
`exclusive_caps=1` is what makes Chrome, Zoom and friends list it as a camera at
all. `card_label` is the name they show. The module is gone after a reboot; to make
it stick:

```
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
echo 'options v4l2loopback video_nr=10 card_label="Video Pedal" exclusive_caps=1' | sudo tee /etc/modprobe.d/v4l2loopback.conf
```

### 3. Permissions (once)

| OS | camera | global hotkeys |
|---|---|---|
| macOS | **System Settings > Privacy & Security > Camera**: macOS asks the first time you run it | **System Settings > Privacy & Security > Input Monitoring**: enable your terminal app, then restart it. Without this the script prints a warning at startup and the two keys do nothing; the preview-window keys still work |
| Windows | **Settings > Privacy & security > Camera**: "Let desktop apps access your camera" on | nothing to grant. One catch: if the app that has focus is running as administrator, a non-elevated script can't see its keys |
| Linux | your user needs to be in the `video` group (`groups` to check; usually already the case) | needs X11. Under a Wayland session `pynput` can't see keys pressed in other windows; log in to an X11/Xorg session, or run with `--no-pedal` and use the preview-window keys |

### 4. Run

```
python loop_pedal.py
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
cameras once at launch. Then pick the virtual camera in its video settings:
**OBS Virtual Camera** on macOS and Windows, **Video Pedal** on Linux (the script
prints it as `/dev/video10`).

### Keys on Windows and Linux

The key names are `pynput`'s and are the same everywhere; only the physical keys
differ. `alt_r` is right Alt and `cmd_r` is the right Windows key (right Super on
Linux). The HUD and `--help` show the local names.

Two things to watch for:

- **Tapping the Windows / Super key on its own opens the Start menu or the GNOME
  overview**, which is exactly what the live key does. Use a different one, e.g.
  `--live-key ctrl_r`.
- On keyboard layouts with **AltGr**, right Alt *is* AltGr and `pynput` reports it as
  `alt_gr`, so `--key alt_r` never fires. Use `--key alt_gr`, or move the pedal to
  `--key ctrl_r` and the live key to something else.

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
python loop_pedal.py --no-vcam        # no virtual camera needed: preview only
python loop_pedal.py --list-cameras   # which index is the real webcam?
python loop_pedal.py --camera 1       # use that index
python loop_pedal.py --key f13        # different pedal key
python loop_pedal.py --live-key f14   # different go-live key (or ctrl_r on Windows / Linux)
python loop_pedal.py --overlay 0      # no ghost: preview shows exactly what goes out
python loop_pedal.py --crossfade 0    # hard cuts at the seam and when going live
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
python -m pytest
```
