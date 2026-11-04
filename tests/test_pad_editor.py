import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

ANIMS = ["blink", "wave"]


@pytest.fixture
def editor(qapp):
    from widgets import PadEditorDialog

    made = []

    def _make(mapping):
        dlg = PadEditorDialog((0, 0), mapping, None, animation_names=ANIMS)
        made.append(dlg)
        return dlg

    yield _make
    for dlg in made:
        dlg.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize(
    "mapping",
    [
        {"type": "launch app", "path": "C:/Tools/app.exe", "args": "--fast -x", "color": "#ff0000", "intensity": "low"},
        {"type": "run process", "command": "notepad.exe C:/a.txt"},
        {"type": "hotkey", "keys": "ctrl+shift+esc"},
        {"type": "media control", "action": "volume down", "steps": 4},
        {"type": "media control", "action": "next"},
        {"type": "switch layer", "layer": "games"},
        {"type": "layer nav", "nav": "next", "color": "#8e44ad", "intensity": "medium"},
        {"type": "animation", "name": "wave", "iterations": 0},
        {"type": "animation", "name": "blink", "iterations": 7},
        {"type": "color", "color": "#00ff00", "intensity": "high"},
    ],
)
def test_prefilled_mapping_comes_back_unchanged(editor, mapping):
    assert editor(mapping).result_mapping() == mapping


def test_legacy_mapping_comes_back_normalized(editor):
    dlg = editor({"type": "layer_nav", "nav": "next", "color": "#2980b9", "intensity": 3})
    assert dlg.result_mapping() == {"type": "layer nav", "nav": "next", "color": "#2980b9", "intensity": "high"}


def test_none_without_color_removes_pad(editor):
    assert editor(None).result_mapping() is None


def test_color_only_without_color_removes_pad(editor):
    dlg = editor({"type": "color", "color": "#ffffff"})
    dlg.selected_color = None
    assert dlg.result_mapping() is None


def test_none_with_color_becomes_color(editor):
    dlg = editor({"type": "hotkey", "keys": "a", "color": "#ffffff"})
    dlg.action_type.setCurrentText("none")
    assert dlg.result_mapping() == {"type": "color", "color": "#ffffff", "intensity": "medium"}


def test_switching_type_drops_old_fields(editor):
    dlg = editor({"type": "media control", "action": "mute", "steps": 3})
    dlg.action_type.setCurrentText("hotkey")
    dlg.hotkey_edit.setText("win+d")
    assert dlg.result_mapping() == {"type": "hotkey", "keys": "win+d"}


def test_animation_without_selection_keeps_dialog_open(qapp, monkeypatch):
    from widgets import PadEditorDialog

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    dlg = PadEditorDialog((0, 0), {"type": "animation", "name": "gone"}, None, animation_names=[])
    QTimer.singleShot(0, dlg.accept)  # no animation to select: refused, so reject to end exec()
    QTimer.singleShot(0, dlg.reject)
    assert dlg.exec() == 0
    assert len(warnings) == 1
    dlg.deleteLater()
    QApplication.processEvents()


def _accept_next_dialog():
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())


def test_edit_pad_stores_dialog_result(win):
    win.layers[win.active_layer]["pads"]["0,0"] = {"type": "hotkey", "keys": "ctrl+c", "color": "#ff0000"}
    _accept_next_dialog()
    win.edit_pad((0, 0))
    assert win.get_pad_mapping((0, 0)) == {
        "type": "hotkey",
        "keys": "ctrl+c",
        "color": "#ff0000",
        "intensity": "medium",
    }
    assert win._dirty


def test_edit_pad_removes_empty_pad(win):
    win.layers[win.active_layer]["pads"]["0,0"] = {"type": "hotkey", "keys": "ctrl+c"}

    def _clear_and_accept():
        dlg = QApplication.activeModalWidget()
        dlg.action_type.setCurrentText("none")
        dlg.accept()

    QTimer.singleShot(0, _clear_and_accept)
    win.edit_pad((0, 0))
    assert win.get_pad_mapping((0, 0)) is None
