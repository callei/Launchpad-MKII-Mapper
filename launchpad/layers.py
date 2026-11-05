"""The ordered set of pad layers and which one is active (no Qt).

Invariants: there is always at least one layer, the active layer always exists, and layer
order is the dict's insertion order (it drives prev/next nav and is saved as-is).
"""

from collections.abc import Callable

from launchpad.config_store import default_nav_pads


class LayerSet:
    """Owns the layers dict ({name: {"pads": {...}}}) and the active layer name.

    The dict is adopted, not copied, so a config that holds it keeps saving the live layers.
    Every change (active layer or layer list) calls each subscriber once, with no arguments.
    """

    def __init__(self, layers: dict | None = None, active: str | None = None):
        self._listeners: list[Callable[[], None]] = []
        self._adopt({} if layers is None else layers, active)

    def subscribe(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    @property
    def layers(self) -> dict:
        return self._layers

    @property
    def names(self) -> list[str]:
        return list(self._layers)

    @property
    def active(self) -> str:
        return self._active

    @property
    def active_pads(self) -> dict:
        """The active layer's live pads dict ("x,y" -> mapping)."""
        return self._layers[self._active].setdefault("pads", {})

    def __contains__(self, name) -> bool:
        return name in self._layers

    def replace(self, layers: dict, active: str | None) -> None:
        """Adopt a whole new layers dict (e.g. a loaded config); a missing active layer falls back to the first."""
        self._adopt(layers, active)
        self._notify()

    def switch_to(self, name: str) -> bool:
        """Make `name` active; False (and no change) if there is no such layer."""
        if name not in self._layers:
            return False
        if name != self._active:
            self._active = name
            self._notify()
        return True

    def next(self) -> str:
        return self._step(1)

    def prev(self) -> str:
        return self._step(-1)

    def add(self, name: str) -> bool:
        """Append a layer with the default nav pads and make it active; False if the name is empty or taken."""
        if not name or name in self._layers:
            return False
        self._layers[name] = {"pads": default_nav_pads()}
        self._active = name
        self._notify()
        return True

    def delete(self, name: str) -> bool:
        """Remove a layer; False if it doesn't exist or is the last one.

        Deleting the active layer activates 'main' if present, else the first layer.
        """
        if name not in self._layers or len(self._layers) == 1:
            return False
        del self._layers[name]
        if self._active == name:
            self._active = "main" if "main" in self._layers else next(iter(self._layers))
        self._notify()
        return True

    def clear(self, name: str) -> bool:
        """Remove every pad mapping of a layer except the nav pads (kept as mapped, else the defaults)."""
        if name not in self._layers:
            return False
        old = self._layers[name].get("pads", {})
        self._layers[name]["pads"] = {k: old.get(k, v) for k, v in default_nav_pads().items()}
        self._notify()
        return True

    def _adopt(self, layers: dict, active: str | None) -> None:
        if not layers:
            layers["main"] = {"pads": {}}
        self._layers = layers
        self._active = active if active in layers else next(iter(layers))

    def _step(self, offset: int) -> str:
        names = self.names
        target = names[(names.index(self._active) + offset) % len(names)]
        self.switch_to(target)
        return target

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()
