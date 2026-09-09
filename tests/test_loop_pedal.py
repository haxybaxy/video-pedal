import numpy as np

from loop_pedal import (LIVE, LOOPING, RECORDING, LoopPedal, Player, RawCodec, Recorder,
                        build_loop)


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
    def make(self, min_seconds=0.2, crossfade=0.0, max_seconds=10):
        # fps=10 so seconds map to frame counts simply
        return LoopPedal(RawCodec(), fps=10, max_seconds=max_seconds, min_seconds=min_seconds,
                         crossfade_seconds=crossfade, log=lambda _m: None)

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

    def test_tap_shorter_than_min_returns_to_live(self):
        pedal = self.make(min_seconds=0.5)  # 5 frames
        pedal.pedal_down()
        self.feed(pedal, [1, 2])
        pedal.pedal_up()
        assert pedal.state == LIVE
        assert self.feed(pedal, [7]) == [7]

    def test_tap_while_looping_goes_live(self):
        pedal = self.make(min_seconds=0.5)
        pedal.pedal_down()
        self.feed(pedal, range(6))
        pedal.pedal_up()
        assert pedal.state == LOOPING
        pedal.pedal_down()        # tap: down...
        assert pedal.state == RECORDING
        assert self.feed(pedal, [42]) == [42]   # live goes out while held
        pedal.pedal_up()          # ...and up quickly
        assert pedal.state == LIVE

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
