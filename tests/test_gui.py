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
    assert win.anim.running == ["blink"]
    assert win.lp.colors[-1][0] == (0, 0) and win.lp.colors[-1][1] != (0, 0, 0)  # first frame lit right away
    win.on_pad_press((1, 1))
    assert win.anim.running == []
    assert win.lp.colors[-1] == ((0, 0), (0, 0, 0))  # pad restored to its (unmapped) base color
    win.on_pad_press((1, 1))
    assert win.anim.running == ["blink"]


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
    win._play_animation("blink", 0)
    _flush_deletes()  # let the first tick run and schedule the next one
    win._stop_animation_playback()
    ticks = []
    real_advance = win.anim.advance
    monkeypatch.setattr(win.anim, "advance", lambda now: (ticks.append(1), real_advance(now))[1])
    win._play_animation("blink", 0)
    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    win._stop_animation_playback()
    assert len(ticks) <= 200 // 30 + 2  # one chain: ~one tick per 30 ms frame


def test_zero_duration_infinite_animation_does_not_hang(win, monkeypatch):
    win.animations["imported"] = {"frames": [{"duration": 0, "pads": {"0,0": "#ff0000"}}]}
    win._play_animation("imported", 0)
    monkeypatch.setattr(win, "_now", lambda: 10**12)  # hours later: a 1 ms loop must not be stepped frame by frame
    win._animate()
    assert win.anim.running == ["imported"]
    win._stop_animation_playback()


def test_rebuilding_layer_list_keeps_active_layer(win):
    from launchpad import config_store

    win._apply_config(config_store.default_config())  # clears + refills the layer list
    assert win.active_layer == "main"
    assert win.layer_list.currentItem().text() == "main"


def test_startup_repairs_missing_active_layer(qapp, lpm, win):
    import yaml

    lpm.CONFIG_PATH.write_text(yaml.dump({"active_layer": "", "layers": {"main": {"pads": {}}}}), encoding="utf-8")
    w2 = lpm.MainWindow()
    try:
        assert w2.active_layer == "main"
        assert w2.layer_list.currentItem().text() == "main"
    finally:
        w2._force_quit = True
        w2.close()
        w2.deleteLater()


def test_switch_layer_pad(win):
    win.layers["drums"] = {"pads": {}}
    win.layer_list.addItem("drums")
    win.layers["main"]["pads"]["0,0"] = {"type": "switch layer", "layer": "drums"}
    win.on_pad_press((0, 0))
    assert win.active_layer == "drums"
    assert win.layer_list.currentItem().text() == "drums"


def test_switch_layer_pad_to_missing_layer_is_ignored(win):
    win.layers["main"]["pads"]["0,0"] = {"type": "switch layer", "layer": "nope"}
    win.on_pad_press((0, 0))
    assert win.active_layer == "main"


def _virtual_controller(reason):
    from conftest import FakeController

    class Virtual(FakeController):
        def open(self):
            self.virtual = True
            self.virtual_reason = reason

    return Virtual


def test_manual_connect_explains_virtual_mode(win, lpm, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda parent, title, text: shown.append(text)))
    monkeypatch.setattr(lpm, "LaunchpadController", _virtual_controller("port busy"))
    win.init_midi(auto=False)
    assert len(shown) == 1 and "port busy" in shown[0]


def test_auto_connect_reports_virtual_mode_in_status_bar_only(win, lpm, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: shown.append(a)))
    monkeypatch.setattr(lpm, "LaunchpadController", _virtual_controller("port busy"))
    win.init_midi(auto=True)
    assert shown == []
    assert "port busy" in win.statusBar().currentMessage()


def test_import_config_loads_animations_normalizes_and_saves(win, lpm, monkeypatch, tmp_path):
    import yaml
    from PySide6.QtWidgets import QFileDialog

    src = tmp_path / "cfg.yaml"
    src.write_text(
        yaml.dump(
            {
                "active_layer": "main",
                "layers": {"main": {"pads": {"0,0": {"type": "run_process", "command": "x"}}}},
                "animations": {"wave": {"frames": []}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(src), "")))
    dirty = []
    monkeypatch.setattr(win, "mark_dirty", lambda: dirty.append(1))
    win.import_config()
    assert "wave" in win.animations
    assert win.get_pad_mapping((0, 0))["type"] == "run process"
    assert dirty


def test_load_config_dialog_rejects_bad_file_and_stops_animations(win, monkeypatch, tmp_path):
    import yaml
    from PySide6.QtWidgets import QFileDialog

    bad, good = tmp_path / "bad.yaml", tmp_path / "good.yaml"
    bad.write_text("- not\n- a mapping\n", encoding="utf-8")
    good.write_text(yaml.dump({"layers": {"b": {"pads": {}}, "a": {"pads": {}}}}, sort_keys=False), encoding="utf-8")
    picked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(picked[-1]), "")))
    picked.append(bad)
    win.load_config_dialog()
    assert "main" in win.layers  # unchanged
    win.animations["blink"] = {"frames": [{"duration": 500, "pads": {"0,0": "#ff0000"}}]}
    win._play_animation("blink", 0)
    picked.append(good)
    win.load_config_dialog()
    assert win.anim.running == []
    assert win.active_layer == "b" and win.layer_list.count() == 2


def test_add_and_delete_layer_trigger_autosave(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    dirty = []
    monkeypatch.setattr(win, "mark_dirty", lambda: dirty.append(1))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("extra", True)))
    win.add_layer_dialog()
    assert dirty == [1]
    win.delete_current_layer()  # QMessageBox.question is patched to Yes
    assert "extra" not in win.layers and dirty == [1, 1]


def _start_looping(win, name):
    win.animations[name] = {"frames": [{"duration": 500, "pads": {"0,0": "#ff0000"}}]}
    win.animation_list.addItem(name)
    win.layers[win.active_layer]["pads"]["1,1"] = {"type": "animation", "name": name, "iterations": 0}
    win.on_pad_press((1, 1))
    win.current_animation = name


def test_renamed_running_animation_still_toggles(win, monkeypatch):
    import widgets

    _start_looping(win, "blink")
    monkeypatch.setattr(widgets.TextInputPopup, "get_text", lambda self: ("flash", True))
    win._rename_animation()
    win.on_pad_press((1, 1))  # pad now refers to 'flash': must stop the running loop, not add one
    assert win.anim.running == []


def test_deleting_running_animation_stops_it(win):
    _start_looping(win, "blink")
    win._delete_animation()  # QMessageBox.question is patched to Yes
    assert win.anim.running == []
    assert win.lp.colors[-1] == ((0, 0), (0, 0, 0))  # its pad is restored


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
