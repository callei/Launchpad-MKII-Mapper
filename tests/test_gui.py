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
    pad, rgb = win.port.controller.colors[-1]
    assert pad == (0, 0) and rgb != (0, 0, 0)  # first frame lit right away
    win.on_pad_press((1, 1))
    assert win.anim.running == []
    assert win.port.controller.colors[-1] == ((0, 0), (0, 0, 0))  # pad restored to its (unmapped) base color
    win.on_pad_press((1, 1))
    assert win.anim.running == ["blink"]


def test_loaded_preset_legacy_types_are_normalized(win):
    win.load_preset("empty")  # shipped preset uses legacy 'layer_nav'
    assert win.get_pad_mapping((3, -1))["type"] == "layer nav"


def _reset_led_log(win):
    win.port.controller.colors.clear()
    win.port.controller.clears = 0


def test_layer_switch_paints_each_pad_once(win):
    win.layer_set.add("second")
    win.layer_set.switch_to("main")
    _reset_led_log(win)
    win.layer_list.setCurrentRow(1)  # user picks "second" in the list
    assert win.active_layer == "second"
    assert win.port.controller.clears == 0
    assert len(win.port.controller.colors) == 80 == len({pad for pad, _ in win.port.controller.colors})


def test_hardware_layer_nav_paints_each_pad_once(win):
    win.layer_set.add("second")
    win.layer_set.switch_to("main")
    _reset_led_log(win)
    win.on_pad_press((3, -1))  # default "next" nav pad
    assert win.active_layer == "second"
    assert len(win.port.controller.colors) == 80


def test_load_preset_paints_each_pad_once(win, lpm):
    _reset_led_log(win)
    win.load_preset("empty")
    assert len(win.port.controller.colors) == 80


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
    # One chain: ~one tick per 30 ms frame, plus the direct ticks from start and stop and timer jitter.
    # A second chain would roughly double this.
    assert len(ticks) <= 200 // 30 + 4


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
    win.layer_set.add("drums")
    win.layer_set.switch_to("main")
    win.layers["main"]["pads"]["0,0"] = {"type": "switch layer", "layer": "drums"}
    win.on_pad_press((0, 0))
    assert win.active_layer == "drums"
    assert win.layer_list.currentItem().text() == "drums"


def test_switch_layer_pad_to_missing_layer_is_ignored(win):
    win.layers["main"]["pads"]["0,0"] = {"type": "switch layer", "layer": "nope"}
    win.on_pad_press((0, 0))
    assert win.active_layer == "main"
    assert win.statusBar().currentMessage() == "Layer 'nope' not found"


def test_hardware_nav_and_layer_list_stay_in_sync(win):
    win.layer_set.add("b")
    win.layer_set.add("c")
    assert [win.layer_list.item(i).text() for i in range(win.layer_list.count())] == ["main", "b", "c"]
    win.on_pad_press((3, -1))  # next: wraps c -> main
    assert win.active_layer == "main" and win.layer_list.currentItem().text() == "main"
    win.on_pad_press((2, -1))  # prev: wraps main -> c
    assert win.active_layer == "c" and win.layer_list.currentItem().text() == "c"
    win.layer_list.setCurrentRow(1)  # user clicks "b"
    assert win.active_layer == "b"
    win.on_pad_press((3, -1))
    assert win.active_layer == "c" and win.layer_list.currentRow() == 2
    assert win.layer_list.count() == 3


def _virtual_controller(reason):
    from conftest import FakeController

    class Virtual(FakeController):
        def open(self):
            self.virtual = True
            self.virtual_reason = reason

    return Virtual


def test_manual_connect_explains_virtual_mode(win, lpm, monkeypatch):
    shown = []
    monkeypatch.setattr(lpm, "show_message", lambda parent, title, text: shown.append(text))
    monkeypatch.setattr(lpm, "LaunchpadController", _virtual_controller("port busy"))
    win.init_midi(auto=False)
    assert len(shown) == 1 and "port busy" in shown[0]


def test_auto_connect_reports_virtual_mode_in_status_bar_only(win, lpm, monkeypatch):
    shown = []
    monkeypatch.setattr(lpm, "show_message", lambda *a: shown.append(a))
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
    assert win.active_layer == "extra" and win.layer_list.currentItem().text() == "extra"
    assert win.get_pad_mapping((3, -1))["type"] == "layer nav"
    win.delete_current_layer()  # ask_yes_no is patched to Yes
    assert "extra" not in win.layers and dirty == [1, 1]
    assert win.active_layer == "main" and win.layer_list.count() == 1


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
    win._delete_animation()  # ask_yes_no is patched to Yes
    assert win.anim.running == []
    assert win.port.controller.colors[-1] == ((0, 0), (0, 0, 0))  # its pad is restored


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
    t = threading.Thread(target=win.port.controller.on_press, args=((0, 0),))
    t.start()
    t.join()
    qapp.processEvents()
    assert seen == [((0, 0), True)]


def test_unplug_is_reported_and_replug_repaints_every_pad(win):
    old = win.port.controller
    old.present = False
    win.port.poll()
    assert win.port.state == "lost"
    assert "unplugged" in win.statusBar().currentMessage()
    old.present = True  # replugged
    win.port.poll()
    assert win.port.controller is not old
    assert win.port.state == "connected"
    assert "reconnected" in win.statusBar().currentMessage()
    assert len(win.port.controller.colors) == 80


def test_about_shows_version_and_author_without_a_message_box(win, lpm, monkeypatch):
    import tomllib

    from PySide6.QtWidgets import QLabel

    version = tomllib.loads((lpm.PROJECT_ROOT / "pyproject.toml").read_text())["project"]["version"]
    texts = []
    monkeypatch.setattr(lpm.QDialog, "exec", lambda dlg: texts.extend(lbl.text() for lbl in dlg.findChildren(QLabel)))
    monkeypatch.setattr(lpm, "show_message", lambda *a, **k: texts.append("message box"))
    win.show_help()
    (text,) = texts
    assert f"Version {version}" in text and "https://github.com/callei" in text


def test_ask_yes_no_is_a_silent_popup_not_a_message_box(qapp):
    import widgets

    for button, expected in (("accept", True), ("reject", False)):
        QTimer.singleShot(0, lambda b=button: getattr(QApplication.activeModalWidget(), b)())
        assert widgets.ask_yes_no(None, "Delete", "Sure?") is expected


def test_clear_layer_button_repaints_and_autosaves(win, monkeypatch):
    dirty = []
    monkeypatch.setattr(win, "mark_dirty", lambda: dirty.append(1))
    win.layers["main"]["pads"]["0,0"] = {"type": "color", "color": "#ff0000"}
    win.update_grid()
    win.clear_layer_btn.click()  # ask_yes_no is patched to Yes
    assert "0,0" not in win.layers["main"]["pads"] and dirty == [1]
    assert ((0, 0), (0, 0, 0)) in win.port.controller.colors


def test_animation_export_starts_outside_the_install_dir(win, lpm, monkeypatch):
    starts = []
    monkeypatch.setattr(lpm.QFileDialog, "getSaveFileName", staticmethod(lambda *a: starts.append(a[2]) or ("", "")))
    monkeypatch.setattr(lpm.QFileDialog, "getOpenFileName", staticmethod(lambda *a: starts.append(a[2]) or ("", "")))
    win._export_animations()
    win._import_animations()
    assert len(starts) == 2
    assert all(not s.startswith(str(lpm.PROJECT_ROOT)) for s in starts)


def test_animation_export_explains_a_protected_folder(win, lpm, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(lpm, "show_message", lambda parent, title, text: shown.append(text))
    monkeypatch.setattr(lpm.QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(tmp_path / "a.yaml"), "")))
    monkeypatch.setattr(lpm.config_store, "export_file", lambda *a: (_ for _ in ()).throw(PermissionError()))
    win._export_animations()
    assert len(shown) == 1 and "No permission" in shown[0]


def test_held_animation_stays_lit_until_its_pad_is_pressed_again(win):
    win.animations["flash"] = {"frames": [{"duration": 1, "pads": {"0,0": "#ff0000"}}]}
    win.layers["main"]["pads"]["1,1"] = {"type": "animation", "name": "flash", "iterations": 1, "hold": "forever"}
    win.on_pad_press((1, 1))
    _flush_deletes()  # let the 1 ms frame end
    assert win.anim.held == ["flash"]
    assert win.port.controller.colors[-1][0] == (0, 0) and win.port.controller.colors[-1][1] != (0, 0, 0)
    win.on_pad_press((3, -1))  # another pad (layer nav) doesn't end a 'forever' hold
    assert win.anim.held == ["flash"]
    win.layer_set.switch_to("main")
    win.on_pad_press((1, 1))
    assert win.anim.held == [] and win.anim.running == []
    assert win.port.controller.colors[-1] == ((0, 0), (0, 0, 0))


def test_until_press_hold_ends_on_any_press(win):
    win.animations["flash"] = {"frames": [{"duration": 1, "pads": {"0,0": "#ff0000"}}]}
    win.layers["main"]["pads"]["1,1"] = {"type": "animation", "name": "flash", "iterations": 1, "hold": "until press"}
    win.on_pad_press((1, 1))
    _flush_deletes()
    assert win.anim.held == ["flash"]
    win.on_pad_press((5, 5))  # unmapped pad
    assert win.anim.held == []
    assert win.port.controller.colors[-1] == ((0, 0), (0, 0, 0))


def test_both_grids_show_intensity(win):
    win.layers["main"]["pads"]["0,0"] = {"type": "color", "color": "#ffffff", "intensity": "low"}
    win.update_grid()
    assert "#3f3f3f" in win.pad_buttons[(0, 0)].styleSheet()
    win.animations["a"] = {"frames": [{"duration": 100, "pads": {"0,0": {"color": "#ffffff", "intensity": "low"}}}]}
    win.animation_list.addItem("a")
    win.animation_list.setCurrentRow(win.animation_list.count() - 1)
    assert "#3f3f3f" in win.anim_pad_buttons[(0, 0)].styleSheet()


def test_saved_custom_colors_survive_a_restart(win, lpm):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    QColorDialog.setCustomColor(2, QColor("#123456"))
    _reject_next_dialog()
    win.edit_pad((0, 0))  # any picker/editor use syncs the slots into the config
    win.save_config(silent=True)
    QColorDialog.setCustomColor(2, QColor("#ffffff"))
    again = lpm.MainWindow()
    try:
        assert QColorDialog.customColor(2).name() == "#123456"
    finally:
        again._force_quit = True
        again.close()
        again.deleteLater()


def test_unchanged_custom_colors_do_not_dirty_the_config(win, monkeypatch):
    dirty = []
    monkeypatch.setattr(win, "mark_dirty", lambda: dirty.append(1))
    win._sync_custom_colors()
    assert dirty == []


class _FakePicker:
    """Stands in for ColorPickerPopup: records what it opened with, returns `result` as the pick."""

    opened = []
    result = ("#00ff00", "high")

    def __init__(self, initial_color, original_color, parent=None, initial_intensity=None):
        _FakePicker.opened.append((initial_color, initial_intensity))

    def exec(self):
        return QDialog.Accepted

    def get_selected_color(self):
        return _FakePicker.result[0]

    def get_selected_intensity(self):
        return _FakePicker.result[1]


def _anim_with_frame(win, pads):
    win.animations["a"] = {"frames": [{"duration": 100, "pads": pads}]}
    win.animation_list.addItem("a")
    win.animation_list.setCurrentRow(win.animation_list.count() - 1)


def test_animation_picker_opens_on_the_pads_color_else_the_last_pick(win, lpm, monkeypatch):
    monkeypatch.setattr(lpm, "ColorPickerPopup", _FakePicker)
    _FakePicker.opened = []
    _anim_with_frame(win, {"0,0": {"color": "#ff0000", "intensity": "low"}})
    win._edit_animation_pad((0, 0))
    win._edit_animation_pad((1, 1))  # empty pad: starts on the green just picked
    assert _FakePicker.opened == [("#ff0000", "low"), ("#00ff00", "high")]


def test_mapping_picker_starts_on_the_last_pick_for_an_uncolored_pad(win, monkeypatch):
    import widgets

    monkeypatch.setattr(widgets, "ColorPickerPopup", _FakePicker)
    _FakePicker.opened = []
    win._last_color, win._last_intensity = "#0000ff", "low"
    real_exec = widgets.PadEditorDialog.exec

    def pick_then_ok(dlg):
        dlg.pick_color()
        QTimer.singleShot(0, dlg.accept)
        return real_exec(dlg)

    monkeypatch.setattr(widgets.PadEditorDialog, "exec", pick_then_ok)
    win.edit_pad((0, 0))
    assert _FakePicker.opened == [("#0000ff", "low")]
    assert (win._last_color, win._last_intensity) == ("#00ff00", "high")
    win.edit_pad((0, 0))  # now colored: starts on its own color
    assert _FakePicker.opened[-1] == ("#00ff00", "high")


def test_typing_a_frame_duration_does_not_crash(win, qapp):
    from PySide6.QtTest import QTest

    win.animations["a"] = {"frames": [{"duration": 300, "pads": {}}, {"duration": 300, "pads": {}}]}
    win.animation_list.addItem("a")
    win.animation_list.setCurrentRow(win.animation_list.count() - 1)
    win.frame_list.setCurrentRow(1)
    spin = win.frame_duration_spin
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin, "1500")  # this used to overflow the stack and kill the app
    qapp.processEvents()
    assert [f["duration"] for f in win.animations["a"]["frames"]] == [300, 1500]
    assert win.frame_list.currentRow() == 1 and win.frame_list.item(1).text() == "Frame 2 (1500ms)"
    win.frame_list.setCurrentRow(0)  # selecting a frame shows its duration without editing it
    assert spin.value() == 300 and win.animations["a"]["frames"][1]["duration"] == 1500


def test_paint_mode_applies_the_brush_without_a_picker(win, lpm, monkeypatch):
    monkeypatch.setattr(lpm, "ColorPickerPopup", _FakePicker)
    _FakePicker.opened, _FakePicker.result = [], ("#00ff00", "low")
    _anim_with_frame(win, {})
    win.brush_btn.click()  # pick the brush: turns paint mode on
    assert win.paint_btn.isChecked() and len(_FakePicker.opened) == 1
    for pad in ((0, 0), (1, 0), (8, 0)):
        win.anim_pad_buttons[pad].click()
    pads = win.animations["a"]["frames"][0]["pads"]
    assert pads == {k: {"color": "#00ff00", "intensity": "low"} for k in ("0,0", "1,0", "8,0")}
    assert len(_FakePicker.opened) == 1  # no picker per pad
    win.anim_pad_buttons[(1, 0)].click()  # same brush again: clears it
    assert "1,0" not in pads
    win.paint_btn.setChecked(False)
    win.anim_pad_buttons[(2, 2)].click()  # paint off: the picker opens again
    assert len(_FakePicker.opened) == 2


def test_paint_toggle_shows_its_state(win):
    assert win.paint_btn.text() == "Paint: off"
    win.paint_btn.click()
    assert win.paint_btn.isChecked() and win.paint_btn.text() == "Paint: ON"


def test_clicking_the_animation_grid_without_a_frame_explains_why(win):
    win.anim_pad_buttons[(0, 0)].click()
    assert win.statusBar().currentMessage() == "Select an animation first"
    win.animations["empty"] = {"frames": []}
    win.animation_list.addItem("empty")
    win.animation_list.setCurrentRow(win.animation_list.count() - 1)
    win.paint_btn.setChecked(True)
    win.anim_pad_buttons[(0, 0)].click()
    assert win.statusBar().currentMessage() == "Add a frame first"
    assert win.animations["empty"] == {"frames": []}


def test_brush_follows_the_last_color_picked_in_either_editor(win, lpm, monkeypatch):
    import widgets

    monkeypatch.setattr(lpm, "ColorPickerPopup", _FakePicker)
    monkeypatch.setattr(widgets, "ColorPickerPopup", _FakePicker)
    _FakePicker.opened, _FakePicker.result = [], ("#00ff00", "low")
    _anim_with_frame(win, {})
    win._edit_animation_pad((0, 0))  # picker in the animation editor
    assert win._brush == ("#00ff00", "low") and "#00ff00" in win.brush_btn.text()
    assert not win.paint_btn.isChecked()  # following a pick doesn't switch paint mode on

    _FakePicker.result = ("#0000ff", "high")
    real_exec = widgets.PadEditorDialog.exec

    def pick_then_ok(dlg):
        dlg.pick_color()
        QTimer.singleShot(0, dlg.accept)
        return real_exec(dlg)

    monkeypatch.setattr(widgets.PadEditorDialog, "exec", pick_then_ok)
    win.edit_pad((0, 0))  # picker in the mapping editor
    assert win._brush == ("#0000ff", "high")
