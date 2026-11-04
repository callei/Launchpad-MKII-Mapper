"""The Launchpad connection: its state, why it is in it, and hot-plug (no Qt).

States: CONNECTED (LED writes reach the device, presses arrive), VIRTUAL (open() found no usable
device) and LOST (it was connected, then a send failed or it left the MIDI device list).
"""

from collections.abc import Callable

CONNECTED = "connected"
VIRTUAL = "virtual"
LOST = "lost"


class LaunchpadPort:
    """Owns the controller made by `make_controller` and notifies subscribers of every state change.

    The controller needs `open()`, `close()`, `set_pad_color(pad, rgb)`, `is_present()`, the
    `virtual`/`virtual_reason` attributes and the `on_press`/`on_release` callbacks.
    `on_press`/`on_release` here are called on the MIDI thread, so the owner must marshal them.
    """

    def __init__(self, make_controller: Callable):
        self._make = make_controller
        self._listeners: list[Callable[[], None]] = []
        self._was_present = False
        self.controller = None
        self.state = VIRTUAL
        self.reason = "Not connected yet."
        self.on_press: Callable | None = None
        self.on_release: Callable | None = None

    def subscribe(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def connect(self) -> None:
        """Close the current controller and open a new one (CONNECTED or VIRTUAL; always notifies)."""
        self.close()
        lp = self._make()
        lp.on_press = lambda pad: self.on_press and self.on_press(pad)
        lp.on_release = lambda pad: self.on_release and self.on_release(pad)
        self.controller = lp
        try:
            lp.open()
        except Exception as e:
            lp.virtual, lp.virtual_reason = True, f"Could not open the Launchpad ({e})."
        if lp.virtual:
            self._set(VIRTUAL, getattr(lp, "virtual_reason", None) or "No Launchpad connected.")
        else:
            self._set(CONNECTED, "Launchpad connected")
        # A device that is listed but can't be opened (busy in a DAW) is not retried until it is replugged
        self._was_present = self.state == CONNECTED or lp.is_present()

    def poll(self) -> None:
        """Check the MIDI device list: an unplug while CONNECTED is LOST; a replug while not connected reconnects."""
        if self.controller is None:
            return
        present = self.controller.is_present()
        if self.state == CONNECTED:
            if not present:
                self._lose("Launchpad was unplugged.")
        elif present and not self._was_present:
            self.connect()
            return
        self._was_present = present

    def set_pad_color(self, pad, rgb) -> None:
        """Write one LED; dropped unless CONNECTED. A failed write marks the port LOST."""
        if self.state != CONNECTED:
            return
        try:
            self.controller.set_pad_color(pad, rgb)
        except Exception as e:
            self._lose(f"Lost the Launchpad connection ({e}).")

    def close(self) -> None:
        """Clear the LEDs and close the ports (idempotent; the state is left as it is)."""
        if self.controller is not None:
            try:
                self.controller.close()
            except Exception:
                pass

    def _lose(self, reason: str) -> None:
        self.close()
        self._was_present = False
        self._set(LOST, reason)

    def _set(self, state: str, reason: str) -> None:
        self.state, self.reason = state, reason
        for callback in list(self._listeners):
            callback()
