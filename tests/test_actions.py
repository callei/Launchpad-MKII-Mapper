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
