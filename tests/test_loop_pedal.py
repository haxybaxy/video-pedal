import queue

import numpy as np
import pytest

from loop_pedal import (LIVE, LOOPING, RECORDING, LoopPedal, Pedal, Player, RawCodec, Recorder,
                        blend_overlay, build_loop)


def gray(value, size=4):
    return np.full((size, size, 3), value, dtype=np.uint8)


def frames(values):
    return [gray(v) for v in values]


def level(frame):
    return int(frame[0, 0, 0])


class TestBuildLoop:
    def test_no_crossfade_returns_frames_unchanged(self):
        src = frames([10, 20, 30])
        out = build_loop(src, 0, RawCodec())
        assert [level(f) for f in out] == [10, 20, 30]

    def test_loop_is_shorter_by_crossfade(self):
        out = build_loop(frames(range(0, 100, 10)), 3, RawCodec())
        assert len(out) == 10 - 3

    def test_crossfade_is_clamped_to_half_the_recording(self):
        out = build_loop(frames([0, 50, 100, 150]), 10, RawCodec())
        assert len(out) == 4 - 2

    def test_seam_dissolves_from_tail_into_head(self):
        # 8 frames ramping 0..70, crossfade 2: body = F2..F5, seam = blend(F6,F0), blend(F7,F1)
        out = build_loop(frames([0, 10, 20, 30, 40, 50, 60, 70]), 2, RawCodec())
        levels = [level(f) for f in out]
        assert levels[:4] == [20, 30, 40, 50]
        seam0, seam1 = levels[4], levels[5]
        assert seam0 == round(60 * 2 / 3 + 0 * 1 / 3)   # mostly tail
        assert seam1 == round(70 * 1 / 3 + 10 * 2 / 3)  # mostly head
        # last seam frame should be close to the first body frame it wraps into
        assert abs(seam1 - 20) < abs(seam0 - 20)


class TestBlendOverlay:
    def test_alpha_mixes_the_loop_over_the_live_frame(self):
        live, loop = gray(0), gray(100)
        assert level(blend_overlay(live, loop, 0.5)) == 50
        assert level(blend_overlay(live, loop, 1.0)) == 100
        assert level(blend_overlay(live, loop, 0.0)) == 0

    def test_alpha_is_clamped(self):
        assert level(blend_overlay(gray(0), gray(100), 2.0)) == 100
        assert level(blend_overlay(gray(0), gray(100), -1.0)) == 0

    def test_returns_a_new_array(self):
        live, loop = gray(0), gray(100)
        out = blend_overlay(live, loop, 0.0)
        assert out is not live and out is not loop

    def test_resizes_a_mismatched_loop_to_the_live_frame(self):
        out = blend_overlay(gray(0, size=4), gray(100, size=2), 0.5)
        assert out.shape == (4, 4, 3)
        assert level(out) == 50


class TestRecorder:
    def test_keeps_only_newest_frames(self):
        rec = Recorder(RawCodec(), max_frames=3)
        for v in range(6):
            rec.push(gray(v))
        assert [level(f) for f in rec.take()] == [3, 4, 5]
        assert len(rec) == 0


class TestPlayer:
    def test_wraps_and_honours_start(self):
        p = Player(frames([1, 2, 3]), RawCodec(), start=2)
        assert [level(p.next()) for _ in range(4)] == [3, 1, 2, 3]


class TestLoopPedal:
    def make(self, min_seconds=0.2, crossfade=0.0, max_seconds=10, log=None):
        # fps=10 so seconds map to frame counts simply
        return LoopPedal(RawCodec(), fps=10, max_seconds=max_seconds, min_seconds=min_seconds,
                         crossfade_seconds=crossfade, log=log or (lambda _m: None))

    def feed(self, pedal, values):
        return [level(pedal.process(gray(v))) for v in values]

    def test_live_passes_frames_through(self):
        pedal = self.make()
        assert pedal.state == LIVE
        assert self.feed(pedal, [5, 6]) == [5, 6]

    def test_hold_records_while_passing_live_through_then_loops_on_release(self):
        pedal = self.make()
        pedal.pedal_down()
        assert pedal.state == RECORDING
        assert self.feed(pedal, [1, 2, 3]) == [1, 2, 3]
        pedal.pedal_up()
        assert pedal.state == LOOPING
        # live camera now shows 9s, but the call gets the loop, cycling
        assert self.feed(pedal, [9, 9, 9, 9]) == [1, 2, 3, 1]

    def test_short_tap_while_live_stays_live(self):
        pedal = self.make(min_seconds=0.5)  # 5 frames
        pedal.pedal_down()
        self.feed(pedal, [1, 2])
        pedal.pedal_up()
        assert pedal.state == LIVE
        assert self.feed(pedal, [7]) == [7]

    def test_short_tap_while_looping_keeps_looping(self):
        pedal = self.make(min_seconds=0.5)
        pedal.pedal_down()
        self.feed(pedal, range(6))
        pedal.pedal_up()
        assert pedal.state == LOOPING
        pedal.pedal_down()        # tap: down...
        assert pedal.state == RECORDING
        assert self.feed(pedal, [42]) == [42]   # live goes out while held
        pedal.pedal_up()          # ...and up too quickly to be a recording
        assert pedal.state == LOOPING
        assert self.feed(pedal, [9, 9]) == [0, 1]   # the loop resumes where it was paused

    def test_hold_while_looping_replaces_the_loop(self):
        pedal = self.make()
        pedal.pedal_down(); self.feed(pedal, [1, 2, 3]); pedal.pedal_up()
        pedal.pedal_down(); self.feed(pedal, [7, 8, 9]); pedal.pedal_up()
        assert self.feed(pedal, [0, 0, 0]) == [7, 8, 9]

    def test_release_starts_playback_just_before_the_seam(self):
        pedal = self.make(crossfade=0.2)  # 2 frames
        pedal.pedal_down()
        self.feed(pedal, [0, 10, 20, 30, 40, 50, 60, 70])
        pedal.pedal_up()
        # loop = [20,30,40,50, seam0, seam1]; playback starts at 50 (last pure frame),
        # then dissolves through the seam into 20.
        first = self.feed(pedal, [0] * 4)
        assert first[0] == 50
        assert first[3] == 20

    def test_repeated_down_events_do_not_restart_recording(self):
        pedal = self.make()
        pedal.pedal_down()
        self.feed(pedal, [1, 2])
        pedal.pedal_down()  # key-repeat while held
        self.feed(pedal, [3])
        pedal.pedal_up()
        assert self.feed(pedal, [0, 0, 0]) == [1, 2, 3]

    def test_toggle_and_go_live(self):
        pedal = self.make()
        pedal.toggle_record(); assert pedal.state == RECORDING
        self.feed(pedal, [1, 2, 3])
        pedal.toggle_record(); assert pedal.state == LOOPING
        pedal.go_live();       assert pedal.state == LIVE
        assert self.feed(pedal, [4]) == [4]

    # -- the live key --------------------------------------------------------

    def test_go_live_while_live_is_a_noop(self):
        logs = []
        pedal = self.make(log=logs.append)
        pedal.go_live()
        assert pedal.state == LIVE
        assert logs == []
        assert self.feed(pedal, [3]) == [3]

    def test_go_live_while_recording_aborts_and_goes_live(self):
        pedal = self.make()
        pedal.pedal_down()
        self.feed(pedal, [1, 2, 3])
        pedal.go_live()
        assert pedal.state == LIVE
        assert self.feed(pedal, [7]) == [7]
        pedal.pedal_up()          # the stale release of the pedal key
        assert pedal.state == LIVE
        assert self.feed(pedal, [8]) == [8]

    def test_zero_crossfade_go_live_is_instant(self):
        pedal = self.make()  # crossfade 0
        pedal.pedal_down(); self.feed(pedal, [1, 2, 3]); pedal.pedal_up()
        pedal.go_live()
        assert pedal.state == LIVE
        assert self.feed(pedal, [7]) == [7]

    def start_fade(self):
        """A constant all-0 loop with a 2-frame crossfade, told to go live."""
        pedal = self.make(crossfade=0.2)  # 2 frames
        pedal.pedal_down(); self.feed(pedal, [0] * 6); pedal.pedal_up()
        assert pedal.state == LOOPING
        pedal.go_live()
        assert pedal.state == LOOPING   # still playing while it dissolves
        return pedal

    def test_go_live_dissolves_loop_into_live(self):
        pedal = self.start_fade()
        # live weight ramps 1/3, 2/3, then pure live
        assert self.feed(pedal, [90, 90, 90]) == [30, 60, 90]
        assert pedal.state == LIVE

    def test_second_go_live_during_fade_does_not_restart_it(self):
        pedal = self.start_fade()
        assert self.feed(pedal, [90]) == [30]
        pedal.go_live()
        assert self.feed(pedal, [90, 90]) == [60, 90]
        assert pedal.state == LIVE

    def test_pedal_down_mid_fade_cancels_fade_and_records(self):
        pedal = self.start_fade()
        self.feed(pedal, [90])
        pedal.pedal_down()
        assert pedal.state == RECORDING
        assert self.feed(pedal, [5] * 4) == [5] * 4   # pure live, no blend
        pedal.pedal_up()
        assert pedal.state == LOOPING
        assert self.feed(pedal, [0, 0, 0]) == [5, 5, 5]   # the new loop; the old all-0 one is gone

    def test_short_tap_mid_fade_goes_live(self):
        pedal = self.start_fade()
        self.feed(pedal, [90])
        pedal.pedal_down()
        pedal.pedal_up()
        assert pedal.state == LIVE
        assert self.feed(pedal, [7]) == [7]


class TestPedal:
    @pytest.fixture
    def Key(self):
        return pytest.importorskip("pynput.keyboard").Key

    def make(self, key="alt_r", live_key="cmd_r"):
        events = queue.Queue()
        return Pedal(key, events, live_key), events   # never started, so no Input Monitoring needed

    @staticmethod
    def drain(events):
        out = []
        while not events.empty():
            out.append(events.get_nowait())
        return out

    def test_pedal_key_queues_down_and_up_once(self, Key):
        pedal, events = self.make()
        pedal._on_press(Key.alt_r)
        pedal._on_press(Key.alt_r)   # key-repeat
        pedal._on_release(Key.alt_r)
        assert self.drain(events) == ["down", "up"]

    def test_live_key_queues_live_on_release_of_a_solo_press(self, Key):
        pedal, events = self.make()
        pedal._on_press(Key.cmd_r)
        pedal._on_press(Key.cmd_r)   # repeat / double report
        assert self.drain(events) == []
        pedal._on_release(Key.cmd_r)
        assert self.drain(events) == ["live"]

    def test_live_key_as_part_of_a_shortcut_does_nothing(self, Key):
        pedal, events = self.make()
        pedal._on_press(Key.cmd_r)
        pedal._on_press(Key.tab)
        pedal._on_release(Key.tab)
        pedal._on_release(Key.cmd_r)
        assert self.drain(events) == []
        pedal._on_press(Key.cmd_r); pedal._on_release(Key.cmd_r)   # the next clean tap still works
        assert self.drain(events) == ["live"]

    def test_pedal_key_while_live_key_is_down_is_a_chord(self, Key):
        pedal, events = self.make()
        pedal._on_press(Key.cmd_r)
        pedal._on_press(Key.alt_r)
        pedal._on_release(Key.alt_r)
        pedal._on_release(Key.cmd_r)
        assert self.drain(events) == ["down", "up"]

    def test_unrelated_keys_queue_nothing(self, Key):
        pedal, events = self.make()
        pedal._on_press(Key.shift); pedal._on_release(Key.shift)
        assert self.drain(events) == []

    def test_same_key_for_both_is_rejected(self, Key):
        with pytest.raises(SystemExit):
            self.make(key="alt_r", live_key="alt_r")
