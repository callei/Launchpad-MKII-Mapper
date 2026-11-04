import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QDialog

LALT, ENTER, LCTRL, C, LWIN, D = 0xA4, 0x0D, 0xA2, 0x43, 0x5B, 0x44


@pytest.fixture
def editor(qapp):
    from widgets import PadEditorDialog

    made = []

    def _make(mapping=None):
        dlg = PadEditorDialog((0, 0), mapping, None, animation_names=["blink"])
        made.append(dlg)
        return dlg

    yield _make
    for dlg in made:
        dlg.deleteLater()
    qapp.processEvents()


# --- color preview ---


def test_color_button_shows_prefilled_color(editor):
    dlg = editor({"type": "color", "color": "#ff0000", "intensity": "high"})
    assert "#ff0000" in dlg.color_btn.text()
    assert "rgb(255,0,0)" in dlg.color_btn.styleSheet()


def test_color_button_shows_picked_color_before_ok(editor, monkeypatch):
    import widgets

    class FakePicker:
        def __init__(self, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def get_selected_color(self):
            return "#00ff00"

        def get_selected_intensity(self):
            return "medium"

    monkeypatch.setattr(widgets, "ColorPickerPopup", FakePicker)
    dlg = editor({"type": "launch app", "path": "C:/x.exe"})
    assert "#" not in dlg.color_btn.text()
    dlg.pick_color()
    assert "#00ff00" in dlg.color_btn.text()
    assert "rgb(0,178,0)" in dlg.color_btn.styleSheet()  # medium = 70%, as the pad shows it
    assert dlg.result_mapping()["color"] == "#00ff00"


def test_clearing_the_color_resets_the_button(editor, monkeypatch):
    import widgets

    class ClearingPicker:
        def __init__(self, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def get_selected_color(self):
            return None

        def get_selected_intensity(self):
            return "medium"

    monkeypatch.setattr(widgets, "ColorPickerPopup", ClearingPicker)
    dlg = editor({"type": "color", "color": "#ff0000"})
    dlg.pick_color()
    assert dlg.color_btn.styleSheet() == ""


# --- hotkey capture ---


def _focus(edit):
    edit.focusInEvent(QFocusEvent(QEvent.FocusIn))


def _unfocus(edit):
    edit.focusOutEvent(QFocusEvent(QEvent.FocusOut))


def _press(edit, *vks):
    for vk in vks:
        edit._hook.on_key(vk, True)
    for vk in reversed(vks):
        edit._hook.on_key(vk, False)


def test_pressed_shortcut_is_recorded(editor):
    edit = editor({"type": "hotkey", "keys": "ctrl+c"}).hotkey_edit
    _focus(edit)
    assert edit.capturing
    _press(edit, LALT, ENTER)
    assert edit.text() == "alt+enter"  # replaced, not appended to the old value


def test_more_presses_make_a_sequence(editor):
    edit = editor({"type": "hotkey"}).hotkey_edit
    _focus(edit)
    _press(edit, LCTRL, C)
    _press(edit, LWIN, D)
    assert edit.text() == "ctrl+c>win+d"


def test_capture_stops_when_focus_leaves(editor):
    edit = editor({"type": "hotkey"}).hotkey_edit
    _focus(edit)
    _unfocus(edit)
    assert not edit.capturing


def test_refocusing_starts_a_new_shortcut(editor):
    edit = editor({"type": "hotkey"}).hotkey_edit
    _focus(edit)
    _press(edit, LCTRL, C)
    _unfocus(edit)
    _focus(edit)
    _press(edit, LWIN, D)
    assert edit.text() == "win+d"


def test_clear_button_empties_the_shortcut(editor):
    dlg = editor({"type": "hotkey", "keys": "ctrl+c"})
    dlg.hotkey_clear_btn.click()
    assert dlg.hotkey_edit.text() == ""


def test_recorded_shortcut_is_saved(editor):
    dlg = editor({"type": "hotkey"})
    _focus(dlg.hotkey_edit)
    _press(dlg.hotkey_edit, LALT, ENTER)
    assert dlg.result_mapping() == {"type": "hotkey", "keys": "alt+enter"}


# --- Store apps in the app picker ---


def test_app_picker_lists_store_apps(qapp, monkeypatch):
    import widgets

    monkeypatch.setattr(widgets._AppScanWorker, "start", lambda self: None)
    dlg = widgets.AppPickerDialog()
    spotify = r"shell:AppsFolder\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
    dlg._on_scan_done([("Spotify", spotify, spotify, "", False)])
    assert dlg.list.count() == 1
    dlg.list.setCurrentRow(0)
    assert dlg.get_selected() == (spotify, "")
    dlg.deleteLater()
