import sys

import pytest

from conftest import FakeOsAdapter
from launchpad.actions import CHORD_GAP_MS, ActionRunner, SystemAdapter, parse_hotkey
from launchpad.mapping import PadMapping

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line semantics")


@pytest.fixture
def os_fake():
    return FakeOsAdapter()


@pytest.fixture
def runner(os_fake):
    return ActionRunner(os_fake)


@windows_only
def test_run_process_keeps_quoted_paths(runner, os_fake):
    cmd = r'"C:\Program Files\App\a.exe" --x "my file.txt"'
    assert runner.run({"type": "run process", "command": cmd}).ok
    assert os_fake.calls == [("spawn", cmd, False)]


@windows_only
def test_launch_app_passes_quoted_args(runner, os_fake, tmp_path):
    exe = tmp_path / "My App" / "a.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert runner.run({"type": "launch app", "path": str(exe), "args": '"my file.txt" -v'}).ok
    assert os_fake.calls == [("spawn", f'"{exe}" "my file.txt" -v', True)]


@windows_only
def test_launch_app_opens_non_executables_with_default_app(runner, os_fake, tmp_path):
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"")
    assert runner.run(PadMapping(type="launch app", path=str(doc))).ok
    assert os_fake.calls == [("open", str(doc), "")]


def test_launch_app_missing_file_is_an_error_result(runner, os_fake, tmp_path):
    result = runner.run({"type": "launch app", "path": str(tmp_path / "nope.exe")})
    assert not result.ok and "not found" in result.error
    assert os_fake.calls == []


def test_launch_app_failure_is_a_result_and_not_retried(tmp_path):
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"")
    fake = FakeOsAdapter(fail=OSError("boom"))
    result = ActionRunner(fake).run({"type": "launch app", "path": str(exe), "args": "--flag"})
    assert not result.ok and "boom" in result.error
    assert len(fake.calls) == 1


def test_run_process_failure_is_a_result():
    fake = FakeOsAdapter(fail=FileNotFoundError("no such program"))
    result = ActionRunner(fake).run({"type": "run process", "command": "missing.exe"})
    assert not result.ok and "no such program" in result.error


@pytest.mark.parametrize(
    "mapping",
    [
        {"type": "launch app", "path": ""},
        {"type": "run process", "command": "  "},
        {"type": "hotkey", "keys": ""},
        {"type": "media control", "action": ""},
    ],
)
def test_empty_fields_are_error_results(runner, os_fake, mapping):
    result = runner.run(mapping)
    assert not result.ok and result.error
    assert os_fake.calls == []


def test_unknown_type_is_an_error_result(runner, os_fake):
    result = runner.run({"type": "teleport"})
    assert not result.ok and "teleport" in result.error
    assert os_fake.calls == []


@pytest.mark.parametrize("mapping", [{"type": "color", "color": "#ff0000"}, {}, None])
def test_mappings_without_an_action_do_nothing(runner, os_fake, mapping):
    assert runner.run(mapping).ok
    assert os_fake.calls == []


@pytest.mark.parametrize("steps, presses", [(None, 1), (0, 1), (-3, 1), (3, 3), (100, 25), ("lots", 1)])
def test_media_steps_are_clamped(runner, os_fake, steps, presses):
    mapping = {"type": "media control", "action": "Volume Up"}
    if steps is not None:
        mapping["steps"] = steps
    assert runner.run(mapping).ok
    assert os_fake.calls == [("down", 0xAF), ("up", 0xAF)] * presses


def test_unknown_media_action_is_an_error_result(runner, os_fake):
    result = runner.run({"type": "media control", "action": "rewind"})
    assert not result.ok and "rewind" in result.error
    assert os_fake.calls == []


def test_hotkey_chords_and_sequences():
    assert parse_hotkey("ctrl+shift+esc") == ([[0x11, 0x10, 0x1B]], [])
    assert parse_hotkey("win+d > f5") == ([[0x5B, 0x44], [0x74]], [])
    assert parse_hotkey("ctrl+enter") == ([[0x11, 0x0D]], [])
    assert parse_hotkey("alt+tab, space") == ([[0x12, 0x09], [0x20]], [])
    assert parse_hotkey("ctrl+comma") == ([[0x11, 0xBC]], [])
    assert parse_hotkey("f13") == ([[0x7C]], [])


def test_hotkey_punctuation_comes_from_the_layout():
    layout = {"?": [0x10, 0xBF]}.get
    assert parse_hotkey("ctrl+?", layout) == ([[0x11, 0x10, 0xBF]], [])
    assert parse_hotkey("?") == ([], ["?"])  # no layout: unknown


@windows_only
def test_hotkey_punctuation_uses_layout_key_not_ascii():
    char_vks = SystemAdapter().char_vks
    (minus,), _ = parse_hotkey("-", char_vks)
    (period,), _ = parse_hotkey(".", char_vks)
    assert minus != [0x2D] and period != [0x2E]  # ord('-') is VK_INSERT, ord('.') is VK_DELETE


def test_hotkey_with_unknown_key_sends_nothing_for_that_chord(os_fake):
    assert parse_hotkey("ctrl+bogus>f5") == ([[0x74]], ["bogus"])
    result = ActionRunner(os_fake).run({"type": "hotkey", "keys": "ctrl+bogus>f5"})
    assert not result.ok and "bogus" in result.error
    assert os_fake.calls == [("down", 0x74), ("up", 0x74)]


def test_hotkey_sequence_sends_first_chord_now_and_defers_the_rest(os_fake):
    deferred = []
    runner = ActionRunner(os_fake, defer=lambda ms, fn: deferred.append((ms, fn)))
    assert runner.run({"type": "hotkey", "keys": "ctrl+c>win+d>f5"}).ok
    assert os_fake.calls == [("down", 0x11), ("down", 0x43), ("up", 0x43), ("up", 0x11)]
    assert [ms for ms, _ in deferred] == [CHORD_GAP_MS, 2 * CHORD_GAP_MS]
    os_fake.calls.clear()
    for _, fn in deferred:
        fn()
    assert os_fake.calls == [("down", 0x5B), ("down", 0x44), ("up", 0x44), ("up", 0x5B), ("down", 0x74), ("up", 0x74)]


# --- MainWindow wiring ---


def test_pad_press_runs_action_through_the_adapter(win):
    win.layers["main"]["pads"]["0,0"] = {"type": "media control", "action": "mute"}
    win.on_pad_press((0, 0))
    assert win.action_runner.adapter.calls == [("down", 0xAD), ("up", 0xAD)]


def test_pad_press_hotkey_sequence_does_not_block(win):
    from PySide6.QtTest import QTest

    calls = win.action_runner.adapter.calls
    win.layers["main"]["pads"]["0,0"] = {"type": "hotkey", "keys": "f5>f6"}
    win.on_pad_press((0, 0))
    assert calls == [("down", 0x74), ("up", 0x74)]  # returned before the second chord
    QTest.qWait(CHORD_GAP_MS * 4)
    assert calls[2:] == [("down", 0x75), ("up", 0x75)]


def test_failing_action_reports_in_status_bar_not_a_dialog(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    dialogs = []
    for name in ("information", "warning", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: dialogs.append(a)))
    balloons = []
    win.tray = type("Tray", (), {"showMessage": lambda self, *a: balloons.append(a)})()
    win.layers["main"]["pads"]["0,0"] = {"type": "launch app", "path": str(tmp_path / "gone.exe")}
    win.on_pad_press((0, 0))
    assert dialogs == []
    assert "not found" in win.statusBar().currentMessage()
    assert len(balloons) == 1  # the window is hidden (tests never show it), so the tray tells the user
    win.show()
    win.on_pad_press((0, 0))
    assert len(balloons) == 1


@windows_only
def test_dev_autostart_uses_pythonw(win, lpm, monkeypatch):
    import contextlib
    import types

    written = {}
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=0,
        KEY_ALL_ACCESS=0,
        REG_SZ=1,
        OpenKey=lambda *a: contextlib.nullcontext("key"),
        SetValueEx=lambda key, name, _r, _t, value: written.update({name: value}),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert win._update_autostart_registry(True)
    cmd = written["LaunchpadMapper"]
    assert "pythonw.exe" in cmd.lower() and cmd.endswith("--autostart")
