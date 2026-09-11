# loop pedal

A loop pedal for your webcam. Hold a key while you look at the camera; let go and
the call sees that clip on repeat while you do something else. Press a second key
once to go live again.

**The pedal key is the right Option key (⌥, the one to the right of the space bar).
The live key is the right Command key (⌘, next to it).** Hold Option to record,
release it to loop, press Command once to go live. While a loop plays, the preview
shows it as a translucent ghost over your live camera, so you can step back into the
same pose before ending it. Change the keys with `--key` and `--live-key`.

## First run, step by step

### 1. Install the Python side (once)

```
cd ~/projects/loop-pedal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

(Already done on this machine; the `.venv` folder is there.)

### 2. Set up the OBS virtual camera (once)

The script publishes video through OBS's virtual camera device. macOS only creates
that device after OBS has been run once:

1. Install OBS: `brew install --cask obs` (already done, it's in /Applications).
2. Open OBS. If it shows a setup wizard, cancel it.
3. Click **Start Virtual Camera** (bottom right, under Controls).
4. macOS pops up a message about a system extension. Open
   **System Settings > Privacy & Security**, scroll down, and **Allow** the OBS
   camera extension. Reboot if it asks you to.
5. Back in OBS click **Stop Virtual Camera**, then quit OBS.

You never need to open OBS again after this. **Keep OBS closed while using the
script.** If OBS is open with its own virtual camera started, OBS owns the device and
pushes its (empty, black) scene into the call instead of your feed.

### 3. Grant your terminal two permissions (once)

Both are under **System Settings > Privacy & Security**:

- **Camera**: so the script can read the webcam. macOS asks the first time you run it.
- **Input Monitoring**: so the pedal and live keys are noticed while Zoom has focus.
  If this is missing, the script prints a warning at startup and the two keys do
  nothing, but the preview-window keys still work. Restart the terminal after
  enabling it.

### 4. Run it

```
cd ~/projects/loop-pedal
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

and a preview window showing what the call will see, with a status line at the top
(LIVE, REC with a red dot, or LOOP). While a loop plays, the preview ghosts the loop
over your live camera at half opacity so you can line up; the call still gets the
plain loop.

### 5. Use it in a meeting

1. Start `loop_pedal.py` **before** opening Zoom / Meet / Teams. Most apps scan for
   cameras once at launch.
2. In the meeting app's video settings pick **OBS Virtual Camera**.
3. **Mute your mic.** The loop only covers video.
4. Look at the camera and **hold right Option** for 10 to 30 seconds. Do meeting-face:
   small movements, an occasional nod, no talking. Your live feed is still going out
   while you hold, so nobody sees anything change.
5. **Release.** The recording now plays on a loop. Walk away.
6. Come back, use the ghost in the preview to line yourself up with the loop, then
   **press right Command** once (on its own, not as part of a shortcut). The loop
   dissolves into your live feed over half a second. Or hold right Option again to
   record a fresh loop.

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
.venv/bin/python loop_pedal.py --overlay 0      # no ghost: preview shows exactly what the call sees
.venv/bin/python loop_pedal.py --crossfade 0    # hard cuts at the seam and when going live
```

The script picks the first camera index that is a live sensor. Once the OBS extension
is installed, OBS Virtual Camera also shows up as an index (here it took index 0 and
pushed the real webcam to 1); reading it would loop its placeholder image back into
itself, so static sources are skipped. To choose by hand, run `--list-cameras` and
pass `--camera N`.

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

Covers the loop builder, ring buffer, player, state machine (including the dissolve to
live), the preview ghost blend and the two-key hotkey mapping, without touching hardware.
