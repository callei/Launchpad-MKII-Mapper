import threading

from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication, QDialog


def _reject_next_dialog():
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())


def _flush_deletes():
    loop = QEventLoop()
    QTimer.singleShot(20, loop.quit)
    loop.exec()


def test_closed_pad_editors_are_freed(win):
    for _ in range(5):
        _reject_next_dialog()
        win.edit_pad((0, 0))
    _flush_deletes()
    assert len(win.findChildren(QDialog)) == 0


def test_infinite_animation_pad_toggles(win):
    win.animations["blink"] = {"frames": [{"duration": 500, "pads": {"0,0": "#ff0000"}}]}
    win.layers[win.active_layer]["pads"]["1,1"] = {"type": "animation", "name": "blink", "iterations": 0}
    win.on_pad_press((1, 1))
    assert [r["name"] for r in win._anim_runs] == ["blink"]
    win._anim_scheduler_tick()  # light the frame
    win.on_pad_press((1, 1))
    assert win._anim_runs == []
    assert win.lp.colors[-1] == ((0, 0), (0, 0, 0))  # pad restored to its (unmapped) base color
    win.on_pad_press((1, 1))
    assert [r["name"] for r in win._anim_runs] == ["blink"]


def test_loaded_preset_legacy_types_are_normalized(win):
    win.load_preset("empty")  # shipped preset uses legacy 'layer_nav'
    assert win.get_pad_mapping((3, -1))["type"] == "layer nav"


def _reset_led_log(win):
    win.lp.colors.clear()
    win.lp.clears = 0


def test_layer_switch_paints_each_pad_once(win):
    win.layers["second"] = {"pads": {}}
    _reset_led_log(win)
    win.switch_layer("second")
    assert win.lp.clears == 0
    assert len(win.lp.colors) == 80 == len({pad for pad, _ in win.lp.colors})


def test_hardware_layer_nav_paints_each_pad_once(win):
    win.layers["second"] = {"pads": {}}
    win.layer_list.addItem("second")
    _reset_led_log(win)
    win.on_pad_press((3, -1))  # default "next" nav pad
    assert win.active_layer == "second"
    assert len(win.lp.colors) == 80


def test_load_preset_paints_each_pad_once(win, lpm):
    _reset_led_log(win)
    win.load_preset("empty")
    assert len(win.lp.colors) == 80


def test_stopping_animations_does_not_leave_a_second_tick_chain(win, qapp, monkeypatch):
    win.animations["blink"] = {"frames": [{"duration": 30, "pads": {"0,0": "#ff0000"}}]}
    win._start_animation_playback("blink", 0)
    _flush_deletes()  # let the first tick run and schedule the next one
    win._stop_animation_playback()
    ticks = []
    real_tick = win._anim_scheduler_tick
    monkeypatch.setattr(win, "_anim_scheduler_tick", lambda: (ticks.append(1), real_tick()))
    win._start_animation_playback("blink", 0)
    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    win._stop_animation_playback()
    assert len(ticks) <= 200 // 30 + 2  # one chain: ~one tick per 30 ms frame


def test_zero_duration_infinite_animation_does_not_hang(win, monkeypatch):
    win.animations["imported"] = {"frames": [{"duration": 0, "pads": {"0,0": "#ff0000"}}]}
    win._start_animation_playback("imported", 0)
    monkeypatch.setattr(win, "_restart_anim_timer", lambda ms: None)  # tick runs off the GUI thread here
    result = []
    t = threading.Thread(target=lambda: result.append(win._anim_scheduler_tick()), daemon=True)
    t.start()
    t.join(2)
    win._stop_animation_playback()
    assert not t.is_alive(), "scheduler tick never returned"


def test_app_picker_stops_scan_thread_on_close(win):
    import widgets

    dlg = widgets.AppPickerDialog(win)
    worker = dlg._worker
    dlg.reject()
    assert not worker.isRunning()


def test_hardware_press_is_handled_on_gui_thread(win, qapp):
    seen = []
    win.on_pad_press = lambda pad: seen.append((pad, QThread.currentThread() is qapp.thread()))
    win.init_midi()  # re-bind controller callbacks
    t = threading.Thread(target=win.lp.on_press, args=((0, 0),))
    t.start()
    t.join()
    qapp.processEvents()
    assert seen == [((0, 0), True)]
