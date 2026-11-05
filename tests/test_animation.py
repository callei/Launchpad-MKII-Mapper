import threading

from launchpad.animation import AnimationEngine
from launchpad.leds import LedPainter
from launchpad.mapping import ALL_PADS, color_rgb

RED, GREEN = "#ff0000", "#00ff00"


def frames(*cells, duration=100):
    """One frame per cell: {"x,y": color} dicts, each lasting `duration` ms."""
    return [{"duration": duration, "pads": dict(cell)} for cell in cells]


def test_single_run_finishes_after_n_cycles():
    eng = AnimationEngine()
    eng.start("a", frames({"0,0": RED}, {"1,0": GREEN}), 2, now=0)
    shown = [eng.advance(t).overlay for t in (0, 100, 200, 300)]
    assert shown == [{(0, 0): color_rgb(RED)}, {(1, 0): color_rgb(GREEN)}] * 2
    tick = eng.advance(400)
    assert tick.overlay == {} and tick.next_due is None
    assert eng.running == []


def test_next_due_is_the_end_of_the_current_frame():
    eng = AnimationEngine()
    assert eng.advance(0).next_due is None
    eng.start("a", frames({"0,0": RED}, {"0,0": GREEN}, duration=250), 1, now=1000)
    assert eng.advance(1000).next_due == 1250
    assert eng.advance(1260).next_due == 1500


def test_loop_forever_start_toggles_a_running_loop_off():
    eng = AnimationEngine()
    blink = frames({"0,0": RED})
    eng.start("blink", blink, 0, now=0)
    assert eng.advance(10_000).overlay == {(0, 0): color_rgb(RED)}  # still looping
    eng.start("blink", blink, 0, now=10_000)  # pressed again: stop instead of stacking
    assert eng.running == []
    eng.start("blink", blink, 0, now=10_000)
    assert eng.running == ["blink"]


def test_finite_start_stacks_instead_of_toggling():
    eng = AnimationEngine()
    eng.start("blink", frames({"0,0": RED}), 0, now=0)
    eng.start("blink", frames({"0,0": RED}), 1, now=0)
    assert eng.running == ["blink", "blink"]


def test_later_runs_override_earlier_on_conflicts():
    eng = AnimationEngine()
    eng.start("a", frames({"0,0": RED, "1,0": RED}), 0, now=0)
    eng.start("b", frames({"0,0": GREEN}), 0, now=50)
    assert eng.advance(60).overlay == {(0, 0): color_rgb(GREEN), (1, 0): color_rgb(RED)}
    eng.stop("b")
    assert eng.advance(60).overlay == {(0, 0): color_rgb(RED), (1, 0): color_rgb(RED)}


def test_zero_duration_frames_do_not_spin():
    eng = AnimationEngine()
    eng.start("z", frames({"0,0": RED}, {"1,0": RED}, duration=0), 0, now=0)
    result = []
    t = threading.Thread(target=lambda: result.append(eng.advance(10**12)), daemon=True)
    t.start()
    t.join(2)
    assert not t.is_alive(), "advance never returned"
    assert result[0].next_due > 10**12


def test_bad_durations_fall_back_to_default():
    eng = AnimationEngine()
    eng.start("bad", [{"duration": "soon", "pads": {"0,0": RED}}], 1, now=0)
    assert eng.advance(0).next_due == 300


def test_malformed_frames_and_cells_are_skipped():
    eng = AnimationEngine()
    eng.start("m", [{"pads": {"0,0": "red", "x": RED, "1,0": {"color": RED, "intensity": "low"}}}, "junk"], 1, now=0)
    assert eng.advance(0).overlay == {(1, 0): color_rgb({"color": RED, "intensity": "low"})}


def test_editing_source_frames_after_start_does_not_affect_the_run():
    eng = AnimationEngine()
    src = frames({"0,0": RED}, {"1,0": GREEN})
    eng.start("a", src, 1, now=0)
    src.pop()
    src[0]["pads"]["0,0"] = GREEN
    assert eng.advance(0).overlay == {(0, 0): color_rgb(RED)}
    assert eng.advance(100).overlay == {(1, 0): color_rgb(GREEN)}


def test_animation_without_frames_does_not_start():
    eng = AnimationEngine()
    eng.start("empty", [], 0, now=0)
    assert eng.running == []


def test_rename_keeps_runs_toggleable():
    eng = AnimationEngine()
    eng.start("blink", frames({"0,0": RED}), 0, now=0)
    eng.rename("blink", "flash")
    eng.start("flash", frames({"0,0": RED}), 0, now=0)
    assert eng.running == []


def test_stop_all():
    eng = AnimationEngine()
    eng.start("a", frames({"0,0": RED}), 0, now=0)
    eng.start("b", frames({"1,0": RED}), 3, now=0)
    eng.stop()
    tick = eng.advance(0)
    assert tick.overlay == {} and tick.next_due is None


class Recorder:
    def __init__(self):
        self.colors = []

    def set_pad_color(self, pad, rgb):
        self.colors.append((pad, rgb))


def test_attach_paints_every_pad_once():
    lp = Recorder()
    painter = LedPainter()
    painter.paint(base={(0, 0): (1, 2, 3)})
    painter.attach(lp)
    painter.paint()
    assert len(lp.colors) == 80 == len({pad for pad, _ in lp.colors}) == len(ALL_PADS)
    assert ((0, 0), (1, 2, 3)) in lp.colors and ((5, 5), (0, 0, 0)) in lp.colors


def test_paint_writes_only_changes():
    lp = Recorder()
    painter = LedPainter(lp)
    painter.paint(base={})
    lp.colors.clear()
    painter.paint(base={(0, 0): (9, 9, 9)})
    painter.paint(overlay={(0, 0): (9, 9, 9), (1, 1): (5, 5, 5)})
    assert lp.colors == [((0, 0), (9, 9, 9)), ((1, 1), (5, 5, 5))]


def test_full_repaint_paints_every_pad_once():
    lp = Recorder()
    painter = LedPainter(lp)
    painter.paint(base={})
    lp.colors.clear()
    painter.paint(full=True)
    assert len(lp.colors) == 80 == len({pad for pad, _ in lp.colors})


def test_removing_the_overlay_restores_base_colors():
    lp = Recorder()
    painter = LedPainter(lp)
    painter.paint(base={(0, 0): (1, 1, 1)}, overlay={(0, 0): (9, 0, 0), (2, 2): (9, 0, 0)})
    lp.colors.clear()
    painter.paint(overlay={})
    assert sorted(lp.colors) == [((0, 0), (1, 1, 1)), ((2, 2), (0, 0, 0))]


def test_overlay_survives_a_base_change():
    lp = Recorder()
    painter = LedPainter(lp)
    painter.paint(base={}, overlay={(0, 0): (9, 0, 0)})
    lp.colors.clear()
    painter.paint(base={(0, 0): (1, 1, 1)}, full=True)
    assert ((0, 0), (9, 0, 0)) in lp.colors and ((0, 0), (1, 1, 1)) not in lp.colors


def test_failed_write_is_retried_next_paint():
    class Flaky(Recorder):
        fail = True

        def set_pad_color(self, pad, rgb):
            if self.fail and pad == (0, 0):
                raise OSError("port gone")
            super().set_pad_color(pad, rgb)

    lp = Flaky()
    painter = LedPainter(lp)
    painter.paint(base={})
    lp.fail = False
    lp.colors.clear()
    painter.paint()
    assert lp.colors == [((0, 0), (0, 0, 0))]


def test_painting_without_a_controller_is_a_no_op():
    LedPainter().paint(base={(0, 0): (1, 1, 1)}, overlay={}, full=True)


def _hold_engine(hold, name="a"):
    eng = AnimationEngine()
    eng.start(name, frames({"0,0": RED}, {"1,0": GREEN}), 1, now=0, hold=hold)
    return eng


def test_clear_hold_turns_the_pads_off_at_the_end():
    eng = _hold_engine("clear")
    assert eng.advance(200).overlay == {} and eng.held == []


def test_held_run_keeps_its_last_frame_without_ticking():
    eng = _hold_engine("forever")
    tick = eng.advance(200)
    assert tick.overlay == {(1, 0): color_rgb(GREEN)} and tick.next_due is None
    assert eng.running == [] and eng.held == ["a"]
    assert eng.advance(1_000_000).overlay == {(1, 0): color_rgb(GREEN)}


def test_forever_hold_is_cleared_by_its_own_pad_then_replays():
    eng = _hold_engine("forever")
    eng.advance(200)
    eng.start("a", frames({"0,0": RED}), 1, now=300, hold="forever")  # press 1: clears only
    assert eng.advance(300).overlay == {} and eng.held == []
    eng.start("a", frames({"0,0": RED}), 1, now=400, hold="forever")  # press 2: plays again
    assert eng.running == ["a"]


def test_forever_hold_survives_other_animations_and_presses_but_not_stop():
    eng = _hold_engine("forever")
    eng.advance(200)
    eng.start("b", frames({"5,5": RED}), 1, now=200)
    assert eng.release_press_holds() is False
    assert eng.held == ["a"]
    eng.stop()
    assert eng.advance(300).overlay == {} and eng.held == []


def test_until_press_hold_is_released_by_a_press():
    eng = _hold_engine("until press")
    eng.advance(200)
    eng.start("b", frames({"5,5": RED}), 1, now=200)
    assert eng.held == ["a"]  # another animation doesn't end it
    assert eng.release_press_holds() is True
    assert eng.held == [] and (1, 0) not in eng.advance(200).overlay


def test_until_next_animation_hold_ends_when_any_animation_starts():
    eng = _hold_engine("until next animation")
    eng.advance(200)
    assert eng.release_press_holds() is False and eng.held == ["a"]
    eng.start("b", frames({"5,5": RED}), 1, now=200)
    assert eng.held == [] and eng.advance(200).overlay == {(5, 5): color_rgb(RED)}


def test_replaying_a_held_animation_replaces_its_hold():
    eng = _hold_engine("until press")
    eng.advance(200)
    eng.start("a", frames({"0,0": RED}, {"1,0": GREEN}), 1, now=200, hold="until press")
    assert eng.held == [] and eng.running == ["a"]
