import pytest

from launchpad.mapping import PadMapping, color_rgb, normalize_type, normalize_types, pad_key, parse_pad_key, to_int


@pytest.mark.parametrize("pad", [(0, 0), (7, 7), (2, -1), (8, 3)])
def test_pad_key_round_trip(pad):
    assert parse_pad_key(pad_key(pad)) == pad


def test_pad_key_format():
    assert pad_key((8, -1)) == "8,-1"


@pytest.mark.parametrize("key", ["", "1", "1,2,3", "a,b", None, 5])
def test_malformed_pad_key_parses_to_none(key):
    assert parse_pad_key(key) is None


def test_legacy_types_normalized():
    assert normalize_type("layer_nav") == "layer nav"
    assert normalize_type("switch_layer") == "switch layer"
    assert normalize_type("run_process") == "run process"
    assert normalize_type("color only") == "color"
    assert normalize_type("hotkey") == "hotkey"


def test_normalize_types_in_place_skips_junk():
    pads = {"0,0": {"type": "run_process"}, "1,0": "junk", "2,0": {"type": 3}, "3,0": {}}
    normalize_types(pads)
    assert pads == {"0,0": {"type": "run process"}, "1,0": "junk", "2,0": {"type": 3}, "3,0": {}}


def test_string_cell_is_medium():
    assert color_rgb("#ff8000") == (178, 89, 0)


@pytest.mark.parametrize(
    "intensity,expected",
    [
        ("low", (102, 0, 0)),
        ("medium", (178, 0, 0)),
        ("high", (255, 0, 0)),
        ("HIGH", (255, 0, 0)),
        ("bogus", (178, 0, 0)),
    ],
)
def test_dict_cell_intensity(intensity, expected):
    assert color_rgb({"color": "#ff0000", "intensity": intensity}) == expected


@pytest.mark.parametrize("raw,expected", [(1, (102, 0, 0)), (2, (178, 0, 0)), (3, (255, 0, 0)), ("3", (255, 0, 0))])
def test_numeric_legacy_intensity(raw, expected):
    assert color_rgb({"type": "color", "color": "#ff0000", "intensity": raw}) == expected


def test_mapping_dict_without_intensity_is_medium():
    assert color_rgb({"type": "hotkey", "keys": "a", "color": "#00ff00"}) == (0, 178, 0)


@pytest.mark.parametrize("cell", ["red", "#ff00", "#gg0000", None, 5, {"intensity": "high"}, {"color": None}, {}])
def test_invalid_color_is_none(cell):
    assert color_rgb(cell) is None


def test_to_int():
    assert to_int("5", 1) == 5
    assert to_int("lots", 1) == 1
    assert to_int(None, 300) == 300


def test_parse_defaults():
    m = PadMapping.from_dict({})
    assert m.type == "none"
    assert m.color is None
    assert m.intensity == "medium"
    assert m.iterations == 1
    assert m.steps is None
    assert m.path == m.args == m.command == m.keys == m.layer == m.name == m.action == ""
    assert m.nav == "prev"


def test_parse_non_dict_is_empty():
    assert PadMapping.from_dict("junk").type == "none"
    assert PadMapping.from_dict(None).type == "none"


def test_parse_bad_numbers_fall_back():
    m = PadMapping.from_dict({"type": "animation", "name": "x", "iterations": "twice", "steps": "lots"})
    assert m.iterations == 1
    assert m.steps == 1


def test_parse_normalizes_legacy_type_and_intensity():
    m = PadMapping.from_dict({"type": "layer_nav", "nav": "next", "color": "#123456", "intensity": 3})
    assert (m.type, m.nav, m.intensity) == ("layer nav", "next", "high")


def test_parse_none_values_become_empty():
    m = PadMapping.from_dict({"type": "launch app", "path": None, "args": None})
    assert (m.path, m.args) == ("", "")


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "launch app", "path": "C:/x.exe", "args": "-a"},
        {"type": "run process", "command": "notepad"},
        {"type": "hotkey", "keys": "ctrl+c", "color": "#ff0000", "intensity": "low"},
        {"type": "media control", "action": "mute"},
        {"type": "media control", "action": "volume up", "steps": 5},
        {"type": "switch layer", "layer": "b"},
        {"type": "layer nav", "nav": "next", "color": "#2980b9", "intensity": "medium"},
        {"type": "animation", "name": "blink", "iterations": 0},
        {"type": "color", "color": "#00ff00", "intensity": "high"},
    ],
)
def test_round_trip(raw):
    assert PadMapping.from_dict(raw).to_dict() == raw


def test_none_without_color_is_removed():
    assert PadMapping(type="none").to_dict() is None


def test_none_with_color_becomes_color():
    assert PadMapping(type="none", color="#ffffff").to_dict() == {
        "type": "color",
        "color": "#ffffff",
        "intensity": "medium",
    }


def test_color_without_color_is_removed():
    assert PadMapping(type="color").to_dict() is None


def test_intensity_only_stored_with_color():
    assert PadMapping(type="hotkey", keys="a", intensity="high").to_dict() == {"type": "hotkey", "keys": "a"}


def test_all_pads_lists_the_80_led_pads():
    from launchpad.mapping import ALL_PADS

    assert len(ALL_PADS) == 80 == len(set(ALL_PADS))
    assert {(x, -1) for x in range(8)} | {(8, y) for y in range(8)} <= set(ALL_PADS)
    assert (8, -1) not in ALL_PADS


def test_pad_colors_resolves_intensity_and_skips_junk():
    from launchpad.mapping import pad_colors

    pads = {
        "0,0": {"type": "color", "color": "#ff0000", "intensity": "high"},
        "1,0": {"type": "hotkey", "keys": "a"},
        "x": {"color": "#ff0000"},
        "2,0": "#ff0000",
    }
    assert pad_colors(pads) == {(0, 0): (255, 0, 0)}
    assert pad_colors(None) == {}


def test_animation_hold_round_trips_and_defaults_to_clear():
    raw = {"type": "animation", "name": "x", "iterations": 1, "hold": "until press"}
    assert PadMapping.from_dict(raw).to_dict() == raw
    assert "hold" not in PadMapping.from_dict({"type": "animation", "name": "x", "hold": "clear"}).to_dict()
    assert PadMapping.from_dict({"type": "animation", "name": "x", "hold": "bogus"}).hold == "clear"


def test_screen_colors_dim_harder_than_the_hardware():
    from launchpad.mapping import screen_hex

    assert color_rgb({"color": "#ffffff", "intensity": "low"}) == (102, 102, 102)  # hardware unchanged
    assert screen_hex({"color": "#ffffff", "intensity": "low"}) == "#3f3f3f"
    assert screen_hex({"color": "#ffffff", "intensity": "medium"}) == "#8c8c8c"
    assert screen_hex({"color": "#ffffff", "intensity": "high"}) == "#ffffff"
    assert screen_hex(None) is None
