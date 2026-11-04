"""Keeps the Launchpad LEDs showing the animation overlay over the base colors (no Qt)."""

from launchpad.mapping import ALL_PADS

OFF = (0, 0, 0)


class LedPainter:
    """Remembers what each pad last got and writes only changes to the controller.

    The controller needs `set_pad_color(pad, rgb)`. Pads missing from the base are off.
    """

    def __init__(self, controller=None):
        self._controller = controller
        self._base = {}
        self._overlay = {}
        self._sent = {}

    def attach(self, controller) -> None:
        """Paint to `controller` (None: nowhere); its LED state is unknown, so the next paint is full."""
        self._controller = controller
        self._sent = {}

    def paint(self, base=None, overlay=None, *, full: bool = False) -> None:
        """Replace the base colors and/or the overlay ({pad: rgb}; None keeps the current one) and write
        the pads whose color changed, or every pad (once) when `full`.
        """
        if base is not None:
            self._base = dict(base)
        if overlay is not None:
            self._overlay = dict(overlay)
        if full:
            self._sent = {}
        if self._controller is None:
            return
        for pad in ALL_PADS:
            rgb = self._overlay.get(pad, self._base.get(pad, OFF))
            if self._sent.get(pad) == rgb:
                continue
            try:
                self._controller.set_pad_color(pad, rgb)
            except Exception:
                continue  # not recorded as sent, so the next paint retries it
            self._sent[pad] = rgb
