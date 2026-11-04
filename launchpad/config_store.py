"""Persistence for the app's config file and layer presets (no Qt).

Everything that touches config.yaml or presets/*.yaml goes through here: parsing, defaults,
legacy type-name normalization, atomic writes and preset path-safety checks.
"""

import copy
import json
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from launchpad.mapping import ANIMATION, normalize_types

_DEFAULT_NAV_PADS = {
    "2,-1": {"type": "layer nav", "nav": "prev", "color": "#2980b9"},
    "3,-1": {"type": "layer nav", "nav": "next", "color": "#8e44ad"},
}


def default_nav_pads() -> dict:
    """A fresh copy of the prev/next layer-nav pads every new layer starts with."""
    return copy.deepcopy(_DEFAULT_NAV_PADS)


def default_config() -> dict:
    """A fresh config: one 'main' layer with the nav pads, no animations."""
    return {"active_layer": "main", "layers": {"main": {"pads": default_nav_pads()}}, "animations": {}}


def ui_settings(config: dict) -> dict:
    """The config's 'ui' section, created (or replaced, if malformed) in place."""
    ui = config.get("ui")
    if not isinstance(ui, dict):
        ui = config["ui"] = {}
    return ui


def sanitize_name(raw: str) -> str:
    """Reduce user input to a preset name: letters, digits, '-' and '_' only."""
    return "".join(c for c in raw if c.isalnum() or c in ('-', '_')).strip()


def read_mapping(path) -> dict:
    """Parse a YAML (or .json) file whose root must be a mapping; raises on anything else."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: root is not a mapping")
    return data


def export_file(data: dict, path) -> None:
    """Write data to path atomically, as JSON for .json files, YAML (order kept) otherwise."""
    _atomic_write(data, Path(path))


@dataclass
class LoadResult:
    config: dict
    loaded: bool  # False: nothing usable was read and config holds defaults
    problems: list[str] = field(default_factory=list)


@dataclass
class Preset:
    pads: dict
    animations: dict


class PresetError(Exception):
    """A preset could not be read or written; str() is a user-facing message."""


class UnsafePresetName(PresetError):
    """The preset name would resolve outside the presets directory."""


class ConfigStore:
    def __init__(self, app_dir, seed_dirs=()):
        self.app_dir = Path(app_dir)
        self.config_path = self.app_dir / "config.yaml"
        self.presets_dir = self.app_dir / "presets"
        self._seed_dirs = [Path(d) for d in seed_dirs]

    # --- config ---
    def load(self, path=None) -> LoadResult:
        """Load config.yaml (or another config file), validated, defaulted and normalized.

        An unreadable config.yaml is copied to config.yaml.corrupt so a later save can't destroy it.
        """
        own = path is None
        path = self.config_path if own else Path(path)
        if own and not path.exists():
            return LoadResult(default_config(), False)
        try:
            data = read_mapping(path)
        except Exception as e:
            problems = [f"Could not read {path.name}: {e}"]
            if own:
                with suppress(OSError):
                    shutil.copyfile(path, path.with_name("config.yaml.corrupt"))
                    problems.append("The unreadable file was kept as config.yaml.corrupt.")
            return LoadResult(default_config(), False, problems)
        _fill_defaults(data)
        for layer in data["layers"].values():
            normalize_types(layer["pads"])
        return LoadResult(data, True)

    def save(self, config: dict) -> None:
        """Atomically replace config.yaml, keeping the previous file as config.yaml.bak. Raises on failure."""
        self.app_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(config, self.config_path, backup=self.config_path.with_name("config.yaml.bak"))

    # --- presets ---
    def ensure_presets(self) -> None:
        """Create the presets dir, copy shipped presets in once, and make sure empty.yaml exists."""
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        marker = self.presets_dir / ".migrated"
        if not marker.exists():
            # The marker stops presets the user deleted from coming back
            for d in self._seed_dirs:
                if not d.is_dir():
                    continue
                for yml in d.glob("*.yaml"):
                    dest = self.presets_dir / yml.name
                    if not dest.exists():
                        with suppress(OSError):
                            shutil.copy2(yml, dest)
            with suppress(OSError):
                marker.write_text("migrated", encoding="utf-8")
        empty = self.presets_dir / "empty.yaml"
        if not empty.exists():
            _atomic_write({"layer": "main", "pads": default_nav_pads(), "animations": {}}, empty)

    def list_presets(self) -> list[str]:
        return sorted(p.stem for p in self.presets_dir.glob("*.yaml"))

    def has_preset(self, name: str) -> bool:
        try:
            return self._preset_path(name).exists()
        except UnsafePresetName:
            return False

    def read_preset(self, name: str, current_layer: str | None = None) -> Preset:
        """Read a preset's pads (types normalized) and embedded animations.

        Old full-config presets yield the pads of their active_layer (else current_layer), no animations.
        """
        path = self._preset_path(name)
        if not path.exists():
            raise PresetError("Preset file missing.")
        try:
            data = read_mapping(path)
        except Exception as e:
            raise PresetError(f"Failed to load: {e}") from e
        animations = {}
        if "layers" in data:
            layers = data.get("layers")
            layer = layers.get(data.get("active_layer", current_layer)) if isinstance(layers, dict) else None
            pads = layer.get("pads") if isinstance(layer, dict) else None
        else:
            pads = data.get("pads")
            if isinstance(data.get("animations"), dict):
                animations = data["animations"]
        pads = pads if isinstance(pads, dict) else {}
        normalize_types(pads)
        return Preset(pads, animations)

    def write_preset(self, name: str, layer: str, pads: dict, animations: dict) -> None:
        """Save one layer's pads as a preset, embedding only the animations those pads use."""
        path = self._preset_path(name)
        used = {m.get("name") for m in pads.values() if isinstance(m, dict) and m.get("type") == ANIMATION}
        data = {"layer": layer, "pads": pads, "animations": {k: v for k, v in animations.items() if k in used}}
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(data, path)

    def delete_preset(self, name: str) -> None:
        self._preset_path(name).unlink(missing_ok=True)

    def import_preset(self, src) -> str:
        """Copy a preset file in under a free, sanitized name and return that name."""
        src = Path(src)
        try:
            data = read_mapping(src)
        except Exception as e:
            raise PresetError(f"Failed to import: {e}") from e
        base = sanitize_name(src.stem) or "imported_preset"
        name, i = base, 1
        while self.has_preset(name):
            name, i = f"{base}_{i}", i + 1
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(data, self._preset_path(name))
        return name

    def _preset_path(self, name: str) -> Path:
        path = self.presets_dir / f"{name}.yaml"
        if not name or not _is_safe_path(self.presets_dir, path):
            raise UnsafePresetName("Invalid preset path.")
        return path


def _is_safe_path(base_dir: Path, user_path: Path) -> bool:
    """True if user_path resolves inside base_dir (blocks path traversal in preset names)."""
    try:
        user_path.resolve().relative_to(base_dir.resolve())
        return True
    except (OSError, ValueError):
        return False


def _fill_defaults(config: dict) -> None:
    layers = config.get("layers")
    if not isinstance(layers, dict) or not layers:
        layers = config["layers"] = {"main": {"pads": {}}}
    for name, layer in list(layers.items()):
        if not isinstance(layer, dict):
            layer = layers[name] = {}
        if not isinstance(layer.get("pads"), dict):
            layer["pads"] = {}
    if not isinstance(config.get("animations"), dict):
        config["animations"] = {}
    if config.get("active_layer") not in layers:  # e.g. '' saved by an older build
        config["active_layer"] = "main" if "main" in layers else next(iter(layers))


def _atomic_write(data: dict, path: Path, backup: Path | None = None) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if path.suffix.lower() == ".json":
                json.dump(data, f, indent=2)
            else:
                yaml.dump(data, f, sort_keys=False)  # key order matters: layer order drives prev/next nav
            f.flush()
            os.fsync(f.fileno())
        if backup is not None and path.exists():
            shutil.copy2(path, backup)
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
