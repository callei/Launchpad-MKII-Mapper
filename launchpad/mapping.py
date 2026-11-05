"""The pad mapping schema (no Qt).

A layer's pads are stored as {"x,y": mapping}, where a mapping is a plain dict such as
{"type": "hotkey", "keys": "ctrl+c", "color": "#ff0000", "intensity": "high"}. Animation frames
store {"x,y": cell}, where a cell is "#rrggbb" or {"color": "#rrggbb", "intensity": ...}.
This module owns the key encoding, the type names, colors and the per-type fields.
"""

import re
from dataclasses import dataclass

# Mapping types as stored in YAML (spaces, not underscores)
LAYER_NAV = "layer nav"
SWITCH_LAYER = "switch layer"
LAUNCH_APP = "launch app"
RUN_PROCESS = "run process"
HOTKEY = "hotkey"
MEDIA_CONTROL = "media control"
ANIMATION = "animation"
COLOR = "color"
NONE = "none"  # no action; never stored (a mapping without an action is COLOR or removed)
ACTION_TYPES = (RUN_PROCESS, LAUNCH_APP, HOTKEY, MEDIA_CONTROL, SWITCH_LAYER, LAYER_NAV, ANIMATION)

# What an animation pad leaves lit after its last cycle ends
HOLD_CLEAR = "clear"  # back to the base colors (default; not stored)
HOLD_FOREVER = "forever"  # until Stop, quitting, or a press of that animation's pad
HOLD_UNTIL_PRESS = "until press"  # until any Launchpad press
HOLD_UNTIL_ANIMATION = "until next animation"  # until any animation starts
HOLD_MODES = (HOLD_CLEAR, HOLD_FOREVER, HOLD_UNTIL_PRESS, HOLD_UNTIL_ANIMATION)

INTENSITIES = ("low", "medium", "high")
_INTENSITY_FACTORS = {"low": 0.4, "medium": 0.7, "high": 1.0}
# On screen the hardware's steps are barely visible, so the GUI dims harder
_SCREEN_INTENSITY_FACTORS = {"low": 0.25, "medium": 0.55, "high": 1.0}
_LEGACY_INTENSITIES = {1: "low", 2: "medium", 3: "high"}
_LEGACY_TYPES = {
    "layer_nav": LAYER_NAV,
    "switch_layer": SWITCH_LAYER,
    "run_process": RUN_PROCESS,
    "color only": COLOR,
}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


# Every pad with an LED: top round buttons (y=-1), then each grid row followed by its right-column button (x=8)
ALL_PADS = tuple([(x, -1) for x in range(8)] + [(x, y) for y in range(8) for x in range(9)])


def pad_key(pad) -> str:
    """The YAML key for pad (x, y): "x,y"."""
    return f"{pad[0]},{pad[1]}"


def parse_pad_key(key) -> tuple[int, int] | None:
    """(x, y) for an "x,y" key, or None if the key is malformed."""
    try:
        x, y = map(int, key.split(","))
    except (AttributeError, ValueError):
        return None
    return x, y


def normalize_type(type_: str) -> str:
    """The current name for a (possibly legacy) mapping type."""
    return _LEGACY_TYPES.get(type_, type_)


def normalize_types(pads: dict) -> None:
    """Rewrite legacy mapping type names (layer_nav, run_process, ...) in place."""
    for mapping in pads.values():
        if isinstance(mapping, dict) and isinstance(mapping.get("type"), str):
            mapping["type"] = normalize_type(mapping["type"])


def to_int(value, default: int) -> int:
    """int(value), or default for hand-edited YAML values that aren't numbers."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def intensity_name(raw) -> str:
    """'low' / 'medium' / 'high' for a stored intensity (legacy 1..3 accepted); 'medium' otherwise."""
    name = str(raw).lower()
    if name in _INTENSITY_FACTORS:
        return name
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    return _LEGACY_INTENSITIES.get(raw, "medium") if isinstance(raw, int) and not isinstance(raw, bool) else "medium"


def color_rgb(spec, screen: bool = False) -> tuple[int, int, int] | None:
    """RGB (0-255) for a frame cell or mapping: "#rrggbb", or a dict with "color" and optional "intensity".

    Intensity scales the color (default medium); `screen` uses the stronger on-screen steps.
    None when there is no valid color.
    """
    if isinstance(spec, dict):
        color, intensity = spec.get("color"), intensity_name(spec.get("intensity", "medium"))
    else:
        color, intensity = spec, "medium"
    if not (isinstance(color, str) and _HEX_COLOR.fullmatch(color)):
        return None
    f = (_SCREEN_INTENSITY_FACTORS if screen else _INTENSITY_FACTORS)[intensity]
    return tuple(min(255, int(int(color[i : i + 2], 16) * f)) for i in (1, 3, 5))


def screen_hex(spec) -> str | None:
    """ "#rrggbb" for showing a cell or mapping on screen (on-screen intensity applied), or None."""
    rgb = color_rgb(spec, screen=True)
    return None if rgb is None else "#%02x%02x%02x" % rgb


def pad_colors(pads, screen: bool = False) -> dict[tuple[int, int], tuple[int, int, int]]:
    """RGB per pad for a layer's {"x,y": mapping} dict; pads without a valid color are left out."""
    colors = {}
    for key, mapping in pads.items() if isinstance(pads, dict) else ():
        pad, rgb = parse_pad_key(key), color_rgb(mapping, screen) if isinstance(mapping, dict) else None
        if pad is not None and rgb is not None:
            colors[pad] = rgb
    return colors


def _str(value) -> str:
    return "" if value is None else str(value)


@dataclass
class PadMapping:
    """One pad's mapping with validated, defaulted values. Fields not used by `type` are ignored."""

    type: str = NONE
    color: str | None = None
    intensity: str = "medium"  # stored only alongside a color
    command: str = ""  # run process
    path: str = ""  # launch app
    args: str = ""  # launch app
    keys: str = ""  # hotkey
    action: str = ""  # media control
    steps: int | None = None  # media control; None: not stored (presses step once)
    layer: str = ""  # switch layer
    nav: str = "prev"  # layer nav: "prev" or "next"
    name: str = ""  # animation
    iterations: int = 1  # animation; 0 or less loops until pressed again
    hold: str = HOLD_CLEAR  # animation: one of HOLD_MODES

    @classmethod
    def from_dict(cls, raw) -> "PadMapping":
        """Parse a stored mapping dict; anything malformed falls back to its default."""
        if not isinstance(raw, dict):
            return cls()
        type_ = raw.get("type")
        color = raw.get("color")
        return cls(
            type=normalize_type(type_) if isinstance(type_, str) else NONE,
            color=color if isinstance(color, str) else None,
            intensity=intensity_name(raw.get("intensity", "medium")),
            command=_str(raw.get("command")),
            path=_str(raw.get("path")),
            args=_str(raw.get("args")),
            keys=_str(raw.get("keys")),
            action=_str(raw.get("action")),
            steps=to_int(raw["steps"], 1) if "steps" in raw else None,
            layer=_str(raw.get("layer")),
            nav=_str(raw.get("nav")) or "prev",
            name=_str(raw.get("name")),
            iterations=to_int(raw.get("iterations", 1), 1),
            hold=raw["hold"] if raw.get("hold") in HOLD_MODES else HOLD_CLEAR,
        )

    def to_dict(self) -> dict | None:
        """The dict to store for this mapping, or None when it does nothing (no action and no color)."""
        type_ = COLOR if self.type == NONE else self.type
        if type_ == COLOR and not self.color:
            return None
        data = {"type": type_}
        if type_ == RUN_PROCESS:
            data["command"] = self.command
        elif type_ == LAUNCH_APP:
            data["path"] = self.path
            data["args"] = self.args
        elif type_ == HOTKEY:
            data["keys"] = self.keys
        elif type_ == MEDIA_CONTROL:
            data["action"] = self.action
            if self.steps is not None:
                data["steps"] = self.steps
        elif type_ == SWITCH_LAYER:
            data["layer"] = self.layer
        elif type_ == LAYER_NAV:
            data["nav"] = self.nav
        elif type_ == ANIMATION:
            data["name"] = self.name
            data["iterations"] = self.iterations
            if self.hold != HOLD_CLEAR:
                data["hold"] = self.hold
        if self.color:
            data["color"] = self.color
            data["intensity"] = self.intensity
        return data
