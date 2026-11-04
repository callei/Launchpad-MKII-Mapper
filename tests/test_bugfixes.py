import shutil

import pytest
import yaml
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture
def make_win(qapp, lpm, monkeypatch):
    """Build a MainWindow on top of whatever config.yaml the test wrote first."""
    shutil.rmtree(lpm.APPDATA_DIR, ignore_errors=True)
    lpm.PRESETS_DIR.mkdir(parents=True)
    for name in ("information", "warning", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    made = []

    def _make():
        w = lpm.MainWindow()
        made.append(w)
        return w

    yield _make
    for w in made:
        w._force_quit = True
        w.close()
        w.deleteLater()
    qapp.processEvents()


def _write_config(lpm, cfg):
    lpm.CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def test_restart_keeps_autosaved_pad_edits(make_win, lpm):
    _write_config(
        lpm,
        {
            "active_layer": "main",
            "layers": {"main": {"pads": {"0,0": {"type": "color", "color": "#ff0000"}}}},
            "ui": {"last_preset": "empty"},
        },
    )
    win = make_win()
    assert win.get_pad_mapping((0, 0)) == {"type": "color", "color": "#ff0000"}


def test_fresh_install_starts_with_nav_pads(make_win):
    win = make_win()
    assert win.get_pad_mapping((3, -1))["type"] == "layer nav"


def test_layer_order_survives_save(make_win, lpm):
    win = make_win()
    win.layers.clear()
    win.layers.update({"zeta": {"pads": {}}, "alpha": {"pads": {}}})
    win.active_layer = "zeta"
    win.save_config(silent=True)
    saved = yaml.safe_load(lpm.CONFIG_PATH.read_text(encoding="utf-8"))
    assert list(saved["layers"]) == ["zeta", "alpha"]


def test_failed_save_stays_dirty(make_win, lpm, monkeypatch):
    win = make_win()
    win._dirty = True
    lpm.CONFIG_PATH.unlink(missing_ok=True)
    lpm.CONFIG_PATH.mkdir()  # a directory in the file's place: the write can't land
    win.save_config(silent=True)
    assert win._dirty


def test_unreadable_config_is_kept_aside(make_win, lpm):
    lpm.CONFIG_PATH.write_text("layers: [unclosed", encoding="utf-8")
    win = make_win()
    win.save_config(silent=True)
    backup = lpm.CONFIG_PATH.with_name("config.yaml.corrupt")
    assert backup.read_text(encoding="utf-8") == "layers: [unclosed"


def _fake_clock(win, monkeypatch):
    clock = [0]
    monkeypatch.setattr(win, "_now", lambda: clock[0])
    return clock


def test_editing_frames_during_playback_keeps_animations_working(make_win, monkeypatch):
    win = make_win()
    clock = _fake_clock(win, monkeypatch)
    win.animations["blink"] = {
        "frames": [{"duration": 10, "pads": {"0,0": "#ff0000"}}, {"duration": 10, "pads": {"1,0": "#00ff00"}}]
    }
    win._play_animation("blink", 1)
    clock[0] = 10
    win._animate()  # now on frame 2
    win.animations["blink"]["frames"].pop()  # user deletes the frame being played
    clock[0] = 20
    win._animate()
    assert win.anim.running == []
    assert not win._anim_timer.isActive()
    win._play_animation("blink", 1)
    assert win.anim.running == ["blink"]


def test_malformed_frame_duration_stops_playback_cleanly(make_win, monkeypatch):
    win = make_win()
    clock = _fake_clock(win, monkeypatch)
    win.animations["bad"] = {"frames": [{"duration": "soon", "pads": {"0,0": "#ff0000"}}]}
    win._play_animation("bad", 1)
    assert win._anim_timer.isActive()
    clock[0] = 300  # the default frame length
    win._animate()
    assert win.anim.running == []
    assert not win._anim_timer.isActive()


def test_failing_tick_stops_playback(make_win):
    win = make_win()
    win.animations["blink"] = {"frames": [{"duration": 10, "pads": {"0,0": "#ff0000"}}]}
    win._play_animation("blink", 0)

    def boom(now):
        raise RuntimeError("tick failed")

    win.anim.advance = boom
    win._animate()
    del win.anim.advance
    assert win.anim.running == []
    assert not win._anim_timer.isActive()
    win._play_animation("blink", 0)  # playback works again
    assert win.anim.running == ["blink"]


def _accept_next_dialog():
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())


def test_editing_media_pad_keeps_steps(make_win):
    win = make_win()
    win.layers[win.active_layer]["pads"]["0,0"] = {"type": "media control", "action": "volume up", "steps": 5}
    _accept_next_dialog()
    win.edit_pad((0, 0))
    assert win.get_pad_mapping((0, 0))["steps"] == 5


def test_editing_animation_pad_without_iterations_stays_single_run(make_win):
    win = make_win()
    win.animations["blink"] = {"frames": [{"duration": 10, "pads": {"0,0": "#ff0000"}}]}
    win.layers[win.active_layer]["pads"]["0,0"] = {"type": "animation", "name": "blink"}
    _accept_next_dialog()
    win.edit_pad((0, 0))
    assert win.get_pad_mapping((0, 0))["iterations"] == 1


def test_hand_edited_numbers_do_not_break_pad_press(make_win, monkeypatch):
    win = make_win()
    clock = _fake_clock(win, monkeypatch)
    calls = []
    monkeypatch.setattr(win, "_perform_media_control", lambda act, steps: calls.append((act, steps)))
    win.animations["blink"] = {"frames": [{"duration": 10, "pads": {"0,0": "#ff0000"}}]}
    pads = win.layers[win.active_layer]["pads"]
    pads["0,0"] = {"type": "animation", "name": "blink", "iterations": "twice"}
    pads["1,0"] = {"type": "media control", "action": "mute", "steps": "lots"}
    win.on_pad_press((0, 0))
    win.on_pad_press((1, 0))
    assert win.anim.running == ["blink"]
    clock[0] = 10
    win._animate()
    assert win.anim.running == []  # "twice" fell back to a single cycle
    assert calls == [("mute", 1)]
