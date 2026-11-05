import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "gui")]
# Must happen before launchpad_mapper is imported: it reads APPDATA at import time.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lpm-test-")
os.environ["QT_QPA_PLATFORM"] = "offscreen"


class FakeController:
    """Stands in for LaunchpadController so tests never open real MIDI ports."""

    def __init__(self, *args, **kwargs):
        self.virtual = False
        self.virtual_reason = None
        self.present = True  # what is_present() reports (the MIDI device list)
        self.on_press = None
        self.on_release = None
        self.colors = []
        self.clears = 0

    def open(self):
        pass

    def close(self):
        pass

    def is_present(self):
        return self.present

    def clear(self, full=False):
        self.clears += 1

    def set_pad_color(self, pad, rgb):
        self.colors.append((pad, rgb))


class FakeOsAdapter:
    """Stands in for actions.SystemAdapter: records process launches and key events instead of doing them."""

    def __init__(self, char_keys=None, fail=None):
        self.calls = []  # ("spawn", cmd, quiet) / ("open", path, args) / ("down", vk) / ("up", vk)
        self.char_keys = char_keys or {}  # char -> VK codes, like the active keyboard layout
        self.fail = fail  # exception raised by spawn/open_file

    def spawn(self, cmd, quiet):
        self.calls.append(("spawn", cmd, quiet))
        if self.fail:
            raise self.fail

    def open_file(self, path, args):
        self.calls.append(("open", path, args))
        if self.fail:
            raise self.fail

    def key_down(self, vk):
        self.calls.append(("down", vk))

    def key_up(self, vk):
        self.calls.append(("up", vk))

    def char_vks(self, ch):
        return self.char_keys.get(ch)


class FakeKeyboardHook:
    """Stands in for hotkey_capture.KeyboardHook so tests never capture the real keyboard."""

    def __init__(self, on_key):
        self.on_key = on_key
        self.active = False

    def start(self):
        self.active = True
        return True

    def stop(self):
        self.active = False


@pytest.fixture(autouse=True)
def _no_real_keyboard_hook(monkeypatch):
    import widgets

    monkeypatch.setattr(widgets, "KeyboardHook", FakeKeyboardHook)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def lpm(monkeypatch):
    import launchpad_mapper

    monkeypatch.setattr(launchpad_mapper, "LaunchpadController", FakeController)
    monkeypatch.setattr(launchpad_mapper, "SystemAdapter", FakeOsAdapter)
    return launchpad_mapper


@pytest.fixture
def win(qapp, lpm, monkeypatch):
    import shutil

    # Fresh app data per window: earlier tests' autosaves must not leak into the next one
    shutil.rmtree(lpm.APPDATA_DIR, ignore_errors=True)
    lpm.APPDATA_DIR.mkdir(parents=True)

    monkeypatch.setattr(lpm, "show_message", lambda *a, **k: None)
    monkeypatch.setattr(lpm, "ask_yes_no", lambda *a, **k: True)
    w = lpm.MainWindow()
    w.init_midi(auto=True)
    qapp.processEvents()
    yield w
    w._force_quit = True
    w.close()
    w.deleteLater()
    qapp.processEvents()
