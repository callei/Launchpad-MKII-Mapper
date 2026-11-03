import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line semantics")


@pytest.fixture
def launched(lpm, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(("popen", args, kwargs.get("shell")))

    monkeypatch.setattr(lpm.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(lpm.os, "startfile", lambda path, *a, **k: calls.append(("startfile", path, k)), raising=False)
    return calls


def test_run_process_keeps_quoted_paths(win, launched):
    cmd = r'"C:\Program Files\App\a.exe" --x "my file.txt"'
    win.layers["main"]["pads"]["0,0"] = {"type": "run process", "command": cmd}
    win.on_pad_press((0, 0))
    assert launched == [("popen", cmd, False)]


def test_launch_app_passes_quoted_args(win, launched, tmp_path):
    exe = tmp_path / "My App" / "a.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    win._launch_app_silent(str(exe), args='"my file.txt" -v')
    assert launched == [("popen", f'"{exe}" "my file.txt" -v', False)]


def test_launch_app_opens_non_executables_with_default_app(win, launched, tmp_path):
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"")
    win._launch_app_silent(str(doc))
    assert launched == [("startfile", str(doc), {})]


def test_hotkey_chords_and_sequences(lpm):
    assert lpm._parse_hotkey("ctrl+shift+esc") == ([[0x11, 0x10, 0x1B]], [])
    assert lpm._parse_hotkey("win+d > f5") == ([[0x5B, 0x44], [0x74]], [])
    assert lpm._parse_hotkey("ctrl+enter") == ([[0x11, 0x0D]], [])
    assert lpm._parse_hotkey("alt+tab, space") == ([[0x12, 0x09], [0x20]], [])
    assert lpm._parse_hotkey("ctrl+comma") == ([[0x11, 0xBC]], [])
    assert lpm._parse_hotkey("f13") == ([[0x7C]], [])


def test_hotkey_punctuation_uses_layout_key_not_ascii(lpm):
    (minus,), _ = lpm._parse_hotkey("-")
    (period,), _ = lpm._parse_hotkey(".")
    assert minus != [0x2D] and period != [0x2E]  # ord('-') is VK_INSERT, ord('.') is VK_DELETE


def test_hotkey_with_unknown_key_sends_nothing_for_that_chord(lpm):
    assert lpm._parse_hotkey("ctrl+bogus>f5") == ([[0x74]], ["bogus"])


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


def test_launch_app_does_not_retry_without_args(win, lpm, monkeypatch, tmp_path):
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"")
    calls = []

    def failing_popen(args, **kwargs):
        calls.append(args)
        raise OSError("boom")

    monkeypatch.setattr(lpm.subprocess, "Popen", failing_popen)
    with pytest.raises(OSError):
        win._launch_app_silent(str(exe), args="--flag")
    assert len(calls) == 1
