import pytest

from launchpad.actions import parse_hotkey
from launchpad.hotkey_capture import HotkeyRecorder, key_name

LCTRL, RCTRL, LSHIFT, LALT, RALT, LWIN, RWIN = 0xA2, 0xA3, 0xA0, 0xA4, 0xA5, 0x5B, 0x5C
ENTER, TAB, C, D, F5, OEM_PLUS, OEM_COMMA, OEM_MINUS = 0x0D, 0x09, 0x43, 0x44, 0x74, 0xBB, 0xBC, 0xBD
CHARS = {OEM_PLUS: "+", OEM_COMMA: ",", OEM_MINUS: "-"}


def name(vk):
    return key_name(vk, CHARS.get)


@pytest.mark.parametrize(
    "vk, expected",
    [
        (LCTRL, "ctrl"),
        (RCTRL, "ctrl"),
        (LSHIFT, "shift"),
        (RALT, "alt"),
        (RWIN, "win"),
        (ENTER, "enter"),
        (0x1B, "esc"),
        (0x25, "left"),
        (F5, "f5"),
        (C, "c"),
        (0x37, "7"),
        (0x63, "3"),  # numpad 3
        (OEM_PLUS, "plus"),  # '+' and ',' are hotkey syntax, so they get names
        (OEM_COMMA, "comma"),
        (OEM_MINUS, "-"),
        (0xFF, None),
    ],
)
def test_key_names(vk, expected):
    assert name(vk) == expected


@pytest.mark.parametrize(
    "vk", [LCTRL, LSHIFT, LALT, LWIN, ENTER, TAB, 0x2E, 0x21, 0x28, 0x2C, F5, C, OEM_PLUS, OEM_COMMA]
)
def test_names_parse_back_to_the_same_key(vk):
    chords, unknown = parse_hotkey(name(vk))
    assert unknown == []
    canonical = {LCTRL: 0x11, LSHIFT: 0x10, LALT: 0x12}.get(vk, vk)
    assert chords == [[canonical]]


def press(rec, *vks):
    """Press vks in order, then release them in reverse; return the recorded chord."""
    out = None
    for vk in vks:
        assert rec.key_event(vk, True) is None
    for vk in reversed(vks):
        out = rec.key_event(vk, False) or out
    return out


def test_chord_is_recorded_when_all_keys_are_released():
    rec = HotkeyRecorder(CHARS.get)
    assert press(rec, LALT, ENTER) == "alt+enter"


def test_modifiers_come_first_in_a_fixed_order():
    rec = HotkeyRecorder(CHARS.get)
    assert press(rec, C, LSHIFT, RCTRL) == "ctrl+shift+c"


def test_key_repeat_and_unknown_keys_are_ignored():
    rec = HotkeyRecorder(CHARS.get)
    rec.key_event(LCTRL, True)
    rec.key_event(LCTRL, True)  # auto-repeat
    rec.key_event(0xFF, True)
    rec.key_event(D, True)
    rec.key_event(0xFF, False)
    rec.key_event(D, False)
    assert rec.key_event(LCTRL, False) == "ctrl+d"


def test_lone_modifier_is_a_chord():
    assert press(HotkeyRecorder(CHARS.get), LWIN) == "win"


def test_release_without_press_records_nothing():
    rec = HotkeyRecorder(CHARS.get)
    assert rec.key_event(ENTER, False) is None


def test_reset_drops_a_half_pressed_chord():
    rec = HotkeyRecorder(CHARS.get)
    rec.key_event(LCTRL, True)
    rec.reset()
    assert press(rec, ENTER) == "enter"
