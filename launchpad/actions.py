"""Run a pad's OS action: launch app, run process, hotkey, media control (no Qt).

ActionRunner turns a mapping into calls on an OS adapter and returns an ActionResult instead of raising.
SystemAdapter is the real adapter (subprocess with shell=False, os.startfile, user32 keybd_event);
tests use a recording fake with the same methods.
"""

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from launchpad import mapping as padmap
from launchpad.mapping import PadMapping

# A packaged (Microsoft Store) app: 'shell:AppsFolder\<PackageFamilyName>!<AppId>'. Strict on purpose:
# it is handed to os.startfile, so nothing else (paths, other shell: folders) may pass.
STORE_APP_PATH = re.compile(r'shell:AppsFolder\\[A-Za-z0-9._-]+![A-Za-z0-9._-]+', re.IGNORECASE)
CHORD_GAP_MS = 55  # pause between the chords of a hotkey sequence, like natural typing
MAX_MEDIA_STEPS = 25

VK_SHIFT = 0x10
MEDIA_KEYS = {
    'next': 0xB0,  # VK_MEDIA_NEXT_TRACK
    'previous': 0xB1,  # VK_MEDIA_PREV_TRACK
    'play/pause': 0xB3,  # VK_MEDIA_PLAY_PAUSE
    'volume up': 0xAF,
    'volume down': 0xAE,
    'mute': 0xAD,
}
_SPECIAL_KEY_MAP = {
    'ctrl': 0x11,
    'control': 0x11,
    'shift': 0x10,
    'alt': 0x12,
    'win': 0x5B,
    'super': 0x5B,
    'esc': 0x1B,
    'escape': 0x1B,
    'enter': 0x0D,
    'return': 0x0D,
    'tab': 0x09,
    'space': 0x20,
    'backspace': 0x08,
    'delete': 0x2E,
    'del': 0x2E,
    'insert': 0x2D,
    'ins': 0x2D,
    'home': 0x24,
    'end': 0x23,
    'pageup': 0x21,
    'pgup': 0x21,
    'pagedown': 0x22,
    'pgdn': 0x22,
    'left': 0x25,
    'up': 0x26,
    'right': 0x27,
    'down': 0x28,
    'printscreen': 0x2C,
    'prtsc': 0x2C,
    'capslock': 0x14,
    # '+' and ',' are syntax (chord / sequence separators), so they need names
    'plus': 0xBB,
    'comma': 0xBC,
}
# Keys that must be sent with KEYEVENTF_EXTENDEDKEY (otherwise e.g. arrows act as numpad keys)
_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B} | set(range(0xAD, 0xB4))


@dataclass(frozen=True)
class ActionResult:
    error: str = ""  # message for the user; empty when the action ran

    @property
    def ok(self) -> bool:
        return not self.error


class OsAdapter(Protocol):
    def spawn(self, cmd: str | list[str], quiet: bool) -> None:
        """Start a process with shell=False; quiet: no console window, output discarded."""

    def open_file(self, path: str, args: str) -> None:
        """Open a document/shortcut with its default app (Windows)."""

    def key_down(self, vk: int) -> None: ...

    def key_up(self, vk: int) -> None: ...

    def char_vks(self, ch: str) -> list[int] | None:
        """VK codes (shift first if needed) typing ch on the active keyboard layout, or None."""


class SystemAdapter:
    """The real OS. Off Windows, keys and open_file do nothing (as before) and only spawn works."""

    def __init__(self):
        self._user32 = None
        if sys.platform == 'win32':
            import ctypes

            self._user32 = ctypes.WinDLL('user32', use_last_error=True)
            self._user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
            self._user32.VkKeyScanW.restype = ctypes.c_short  # SHORT; -1 = no key for this char

    def spawn(self, cmd, quiet):
        kwargs = {}
        if quiet:
            kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
            if sys.platform == 'win32':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs['startupinfo'] = si
                kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        # Security: shell=False, so no shell interprets the command
        subprocess.Popen(cmd, shell=False, **kwargs)

    def open_file(self, path, args):
        if sys.platform == 'win32':
            if args:
                os.startfile(path, arguments=args)
            else:
                os.startfile(path)

    def _key(self, vk, up):
        if self._user32 is not None:
            flags = (0x0001 if vk in _EXTENDED_VKS else 0) | (0x0002 if up else 0)  # EXTENDEDKEY / KEYUP
            self._user32.keybd_event(vk, 0, flags, 0)

    def key_down(self, vk):
        self._key(vk, up=False)

    def key_up(self, vk):
        self._key(vk, up=True)

    def char_vks(self, ch):
        if self._user32 is None:
            return None
        res = self._user32.VkKeyScanW(ch)
        if res == -1 or res & 0xFF == 0xFF:
            return None
        return ([VK_SHIFT] if (res >> 8) & 1 else []) + [res & 0xFF]


def _vks_for_token(tok: str, char_vks) -> list[int] | None:
    """The virtual-key codes for one token (a char may need shift), or None if unknown."""
    t = tok.lower().strip()
    if not t:
        return None
    # Function keys F1-F24
    if t.startswith('f') and t[1:].isdigit():
        num = int(t[1:])
        if 1 <= num <= 24:
            return [0x70 + (num - 1)]  # VK_F1 = 0x70
    if t in _SPECIAL_KEY_MAP:
        return [_SPECIAL_KEY_MAP[t]]
    if len(t) == 1:
        if t.isascii() and t.isalnum():
            return [ord(t.upper())]  # VK codes for A-Z / 0-9 equal their ASCII codes
        # Punctuation: ask the active keyboard layout (ord() gave unrelated keys, e.g. '-' -> Insert)
        return char_vks(t)
    return None


def parse_hotkey(combo: str, char_vks: Callable[[str], list[int] | None] = lambda ch: None):
    """Parse 'ctrl+c>win+d' into chords of VK codes. Returns (chords, unknown_tokens).

    Chords are separated by '>' (or ','); inside a chord '+' joins simultaneous keys. Use 'plus' /
    'comma' for those keys. A chord containing an unknown key is dropped entirely rather than pressed
    partially. char_vks looks up punctuation on the keyboard layout. Ctrl+Alt+Del can't be sent by any program.
    """
    chords, unknown = [], []
    for chord in re.split(r'[>,]', combo or ''):
        parts = [p.strip() for p in chord.split('+') if p.strip()]
        if not parts:
            continue
        resolved = [_vks_for_token(p, char_vks) for p in parts]
        bad = [p for p, vks in zip(parts, resolved, strict=True) if vks is None]
        if bad:
            unknown.extend(bad)
            continue
        vks = []
        for part_vks in resolved:
            vks.extend(vk for vk in part_vks if vk not in vks)
        chords.append(vks)
    return chords, unknown


class ActionRunner:
    """Runs launch app / run process / hotkey / media control mappings through an OsAdapter.

    defer(delay_ms, fn) schedules the later chords of a hotkey sequence (the GUI passes QTimer.singleShot
    so it never sleeps); the default runs them at once.
    """

    def __init__(self, adapter: OsAdapter, defer: Callable[[int, Callable[[], None]], None] | None = None):
        self.adapter = adapter
        self._defer = defer or (lambda _ms, fn: fn())

    def run(self, mapping) -> ActionResult:
        """Run a PadMapping or stored mapping dict. Never raises; failures come back as ActionResult.error."""
        if not isinstance(mapping, PadMapping):
            mapping = PadMapping.from_dict(mapping)
        handler = {
            padmap.LAUNCH_APP: self._launch_app,
            padmap.RUN_PROCESS: self._run_process,
            padmap.HOTKEY: self._hotkey,
            padmap.MEDIA_CONTROL: self._media_control,
        }.get(mapping.type)
        if handler is None:
            if mapping.type in (padmap.NONE, padmap.COLOR):
                return ActionResult()
            return ActionResult(f"Unknown action type '{mapping.type}'")
        try:
            return handler(mapping)
        except Exception as e:  # OSError from Popen/startfile, ValueError from shlex, ...
            return ActionResult(f"{mapping.type.capitalize()} failed: {e}")

    def _launch_app(self, m: PadMapping) -> ActionResult:
        path, args = m.path, m.args
        if not path:
            return ActionResult("Launch app: no application set")
        if path.lower().startswith('shell:'):
            # Microsoft Store apps have no exe to run; Windows starts them by app ID
            if not STORE_APP_PATH.fullmatch(path):
                return ActionResult(f"Not a valid Store app: {path}")
            self.adapter.open_file(path, args)
            return ActionResult()
        # Security: only launch files that exist
        if not os.path.isfile(path):
            return ActionResult(f"Application not found: {path}")
        if sys.platform == 'win32' and Path(path).suffix.lower() not in ('.exe', '.com', '.bat', '.cmd'):
            # Documents, shortcuts (.lnk/.url) etc. can't be executed; open them with their default app
            self.adapter.open_file(path, args)
        elif sys.platform == 'win32':
            # Pass args through verbatim; splitting them kept quote characters that then got escaped
            quoted = subprocess.list2cmdline([path])
            self.adapter.spawn(f'{quoted} {args}' if args else quoted, quiet=True)
        else:
            self.adapter.spawn([path, *shlex.split(args)], quiet=True)
        return ActionResult()

    def _run_process(self, m: PadMapping) -> ActionResult:
        cmd = m.command
        if not cmd.strip():
            return ActionResult("Run process: no command set")
        # Windows parses the command line itself; splitting first broke quoted paths
        self.adapter.spawn(cmd if sys.platform == 'win32' else shlex.split(cmd), quiet=False)
        return ActionResult()

    def _hotkey(self, m: PadMapping) -> ActionResult:
        if not m.keys.strip():
            return ActionResult("Hotkey: no keys set")
        chords, unknown = parse_hotkey(m.keys, self.adapter.char_vks)
        for i, vks in enumerate(chords):
            if i == 0:
                self._send_chord(vks)
            else:
                self._defer(i * CHORD_GAP_MS, lambda vks=vks: self._send_chord(vks))
        return ActionResult(f"Hotkey: unknown key(s) {', '.join(unknown)}" if unknown else "")

    def _media_control(self, m: PadMapping) -> ActionResult:
        if not m.action:
            return ActionResult("Media control: no action set")
        vk = MEDIA_KEYS.get(m.action.lower())
        if vk is None:
            return ActionResult(f"Media control: unknown action '{m.action}'")
        steps = 1 if m.steps is None else max(1, min(MAX_MEDIA_STEPS, m.steps))
        for _ in range(steps):
            self._send_chord([vk])
        return ActionResult()

    def _send_chord(self, vks: list[int]) -> None:
        """Press the keys in order, release them in reverse."""
        for vk in vks:
            self.adapter.key_down(vk)
        for vk in reversed(vks):
            self.adapter.key_up(vk)
