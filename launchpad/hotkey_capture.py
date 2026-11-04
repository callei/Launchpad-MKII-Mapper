"""Record a hotkey by pressing it (no Qt).

HotkeyRecorder turns key down/up events (Windows virtual-key codes) into hotkey strings in the
format launchpad.actions.parse_hotkey reads, e.g. 'alt+enter'. KeyboardHook is a Windows low-level
keyboard hook that delivers those events while swallowing the keys, so shortcuts like Win or Alt+Tab
are recorded instead of acted on. Ctrl+Alt+Del can't be intercepted by any program.
"""

import sys
from typing import Callable

# Left/right variants collapse to the generic key, which is what parse_hotkey sends
_MODIFIERS = {
    0x11: 'ctrl',
    0xA2: 'ctrl',
    0xA3: 'ctrl',
    0x10: 'shift',
    0xA0: 'shift',
    0xA1: 'shift',
    0x12: 'alt',
    0xA4: 'alt',
    0xA5: 'alt',
    0x5B: 'win',
    0x5C: 'win',
}
_MODIFIER_ORDER = ('ctrl', 'shift', 'alt', 'win')
_NAMED = {
    0x1B: 'esc',
    0x0D: 'enter',
    0x09: 'tab',
    0x20: 'space',
    0x08: 'backspace',
    0x2E: 'delete',
    0x2D: 'insert',
    0x24: 'home',
    0x23: 'end',
    0x21: 'pageup',
    0x22: 'pagedown',
    0x25: 'left',
    0x26: 'up',
    0x27: 'right',
    0x28: 'down',
    0x2C: 'printscreen',
    0x14: 'capslock',
}
_SYNTAX_CHARS = {'+': 'plus', ',': 'comma'}  # separators in hotkey strings


def key_name(vk: int, vk_char: Callable[[int], str | None]) -> str | None:
    """The hotkey-string name for a virtual key, or None if hotkeys can't express it.

    vk_char gives the unshifted character a key types on the active layout (punctuation keys).
    """
    if vk in _MODIFIERS:
        return _MODIFIERS[vk]
    if vk in _NAMED:
        return _NAMED[vk]
    if 0x70 <= vk <= 0x87:
        return f'f{vk - 0x6F}'
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:  # numpad digits
        return str(vk - 0x60)
    ch = vk_char(vk)
    if ch and len(ch) == 1 and not ch.isspace() and ch != '>':
        return _SYNTAX_CHARS.get(ch, ch.lower())
    return None


class HotkeyRecorder:
    """Collects the keys held together; when the last one is released, that chord is finished."""

    def __init__(self, vk_char: Callable[[int], str | None]):
        self._vk_char = vk_char
        self._down: set[int] = set()
        self._chord: list[str] = []

    def reset(self) -> None:
        self._down.clear()
        self._chord = []

    def key_event(self, vk: int, down: bool) -> str | None:
        """Feed one key event; returns the chord (e.g. 'ctrl+shift+c') once all its keys are up."""
        if down:
            name = key_name(vk, self._vk_char)
            if name is None:
                return None
            self._down.add(vk)
            if name not in self._chord:
                self._chord.append(name)
            return None
        if vk not in self._down:
            return None
        self._down.discard(vk)
        if self._down or not self._chord:
            return None
        mods = [m for m in _MODIFIER_ORDER if m in self._chord]
        chord = '+'.join(mods + [k for k in self._chord if k not in _MODIFIER_ORDER])
        self._chord = []
        return chord


def layout_char(vk: int) -> str | None:
    """The unshifted character vk types on the active keyboard layout (Windows), else None."""
    if sys.platform != 'win32':
        return None
    import ctypes

    code = ctypes.windll.user32.MapVirtualKeyW(vk, 2)  # MAPVK_VK_TO_CHAR; high bit marks dead keys
    code &= 0xFFFF
    return chr(code) if code else None


class KeyboardHook:
    """While started, every physical key event goes to on_key(vk, down) and is kept from Windows.

    Windows only (start() returns False elsewhere or if the hook can't be installed). The hook runs on
    the thread that started it, inside its message loop (the Qt GUI thread). Always stop() it: until
    then the whole keyboard is captured.
    """

    def __init__(self, on_key: Callable[[int, bool], None]):
        self._on_key = on_key
        self._handle = None
        self._proc = None  # keeps the ctypes callback alive while installed

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self) -> bool:
        if self._handle is not None:
            return True
        if sys.platform != 'win32':
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('vkCode', wintypes.DWORD),
                ('scanCode', wintypes.DWORD),
                ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_type, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def proc(n_code, w_param, l_param):
            if n_code == 0:  # HC_ACTION
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not info.flags & 0x10:  # LLKHF_INJECTED: let synthetic keys (e.g. our own hotkeys) through
                    try:
                        self._on_key(info.vkCode, w_param in (0x100, 0x104))  # WM_KEYDOWN / WM_SYSKEYDOWN
                    except Exception:
                        pass
                    return 1  # swallow: Windows and other apps never see the key
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = proc_type(proc)
        handle = user32.SetWindowsHookExW(13, self._proc, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        if not handle:
            self._proc = None
            return False
        self._handle = handle
        self._user32 = user32
        return True

    def stop(self) -> None:
        if self._handle is not None:
            self._user32.UnhookWindowsHookEx(self._handle)
            self._handle = None
            self._proc = None
