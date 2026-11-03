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
        self.on_press = None
        self.on_release = None
        self.colors = []
        self.clears = 0

    def open(self):
        pass

    def close(self):
        pass

    def clear(self, full=False):
        self.clears += 1

    def set_pad_color(self, pad, rgb):
        self.colors.append((pad, rgb))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def lpm(monkeypatch):
    import launchpad_mapper

    monkeypatch.setattr(launchpad_mapper, "LaunchpadController", FakeController)
    return launchpad_mapper


@pytest.fixture
def win(qapp, lpm, monkeypatch):
    import shutil

    from PySide6.QtWidgets import QMessageBox

    # Fresh app data per window: earlier tests' autosaves must not leak into the next one
    shutil.rmtree(lpm.APPDATA_DIR, ignore_errors=True)
    lpm.APPDATA_DIR.mkdir(parents=True)

    for name in ("information", "warning", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    w = lpm.MainWindow()
    w.init_midi(auto=True)
    qapp.processEvents()
    yield w
    w._force_quit = True
    w.close()
    w.deleteLater()
    qapp.processEvents()
