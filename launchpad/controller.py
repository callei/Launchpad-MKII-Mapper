import mido
from typing import Optional, Callable, Tuple, Iterable

PadCoord = Tuple[int, int]  # (x, y) 0-7 for grid (y=0 is top row)

class LaunchpadController:
    """Low level MIDI IO wrapper for Launchpad MKII.

    Responsibilities:
    - Open input/output ports
    - Parse note on/off to pad coordinates
    - Send LED color updates (single + batch)
    """

    def __init__(self, input_name_substr: str = "Launchpad", output_name_substr: str = "Launchpad", allow_virtual: bool = True, mapping_mode: str = "mk2", rgb_highlight: bool = True):
        self.input_name_substr = input_name_substr
        self.output_name_substr = output_name_substr
        self.inport: Optional[mido.ports.BaseInput] = None
        self.outport: Optional[mido.ports.BaseOutput] = None
        self.on_press: Optional[Callable[[PadCoord], None]] = None
        self.on_release: Optional[Callable[[PadCoord], None]] = None
        self.allow_virtual = allow_virtual
        self.virtual = False  # When True, no hardware present and we just log actions
        # mapping_mode: 'linear' (0..63) or 'mk2'
        self.mapping_mode = mapping_mode
        self.rgb_highlight = rgb_highlight
        # Simple channel calibration factors (tuned to reduce orange tint & boost blue)
        self.calibration = {"r": 0.78, "g": 1.0, "b": 1.18}

    # --- Internal helpers ---
    def _safe_send(self, msg: mido.Message):
        if self.virtual or not self.outport:
            return
        try:
            self.outport.send(msg)
        except Exception:
            # suppress send errors (e.g., device disconnected during shutdown)
            pass

    # --- Device Discovery ---
    def open(self) -> None:
        """Open MIDI ports; if unavailable or opening fails, fall back to virtual mode (if allowed)."""
        inputs = mido.get_input_names()
        outputs = mido.get_output_names()
        in_name = next((n for n in inputs if self.input_name_substr in n), None)
        out_name = next((n for n in outputs if self.output_name_substr in n), None)
        if not in_name or not out_name:
            if self.allow_virtual:
                self.virtual = True
                print("[controller] Hardware not found; entering virtual mode. Incoming pad presses won't arrive.")
                return
            raise RuntimeError(f"Launchpad MIDI ports not found. Inputs={inputs} Outputs={outputs}")
        self.virtual = False
        try:
            # Open output first (some drivers behave better in this order)
            self.outport = mido.open_output(out_name)
            self.inport = mido.open_input(in_name, callback=self._handle_message)
        except Exception as e:
            # Clean up and optionally fall back to virtual mode
            try:
                if self.inport:
                    self.inport.close()
            except Exception:
                pass
            try:
                if self.outport:
                    self.outport.close()
            except Exception:
                pass
            self.inport = None
            self.outport = None
            if self.allow_virtual:
                self.virtual = True
                print(f"[controller] MIDI open failed: {e}. Entering virtual mode. Incoming pad presses won't arrive.")
                return
            raise
    # We avoid forcing programmer mode now to prevent unintended pad colors.

    def close(self) -> None:
        """Close ports after attempting to clear device LEDs."""
        try:
            self.clear(full=True)
        except Exception:
            pass
        if self.inport:
            try:
                self.inport.close()
            except Exception:
                pass
        if self.outport:
            try:
                self.outport.close()
            except Exception:
                pass

    # --- Event Handling ---
    def _handle_message(self, msg: mido.Message):
        # Note messages (grid + some controls in certain modes)
        if msg.type in ("note_on", "note_off"):
            pad = self._note_to_pad(msg.note)
            if pad is not None:
                if msg.type == "note_on" and msg.velocity > 0:
                    if self.on_press:
                        self.on_press(pad)
                else:
                    if self.on_release:
                        self.on_release(pad)
            return
        # Many Launchpad MKII top round buttons arrive as control_change 104..111
        if msg.type == "control_change":
            pad = self._note_to_pad(msg.control)
            if pad is not None:
                if msg.value > 0:
                    if self.on_press:
                        self.on_press(pad)
                else:
                    if self.on_release:
                        self.on_release(pad)

    # --- Note Mapping Modes ---
    def _note_to_pad(self, note: int) -> Optional[PadCoord]:
        """Translate MIDI note to (x,y) including top (-1) & side (x=8) for MkII."""
        # Top row (104..111) -> y = -1
        if 104 <= note <= 111:
            return (note - 104, -1)
        if self.mapping_mode == 'mk2':
            ones = note % 10
            tens = (note // 10) % 10
            # Side column ones=9 (notes 19,29,...,89)
            if 1 <= tens <= 8 and ones == 9:
                y = 8 - tens
                x = 8  # side column
                return (x, y)
            if 1 <= tens <= 8 and 1 <= ones <= 8:
                y = 8 - tens
                x = ones - 1
                if 0 <= x <= 7 and 0 <= y <= 7:
                    return (x, y)
            # fallthrough none
            return None
        # linear mode base grid only
        if 0 <= note <= 63:
            x = note % 8
            y = note // 8
            return (x, y)
        return None

    def pad_to_note(self, pad: PadCoord) -> int:
        x, y = pad
        # Top row
        if y == -1 and 0 <= x <= 7:
            return 104 + x
        if self.mapping_mode == 'mk2':
            if x == 8 and 0 <= y <= 7:  # side column
                tens = 8 - y
                ones = 9
                return tens * 10 + ones
            if 0 <= x <= 7 and 0 <= y <= 7:
                tens = 8 - y
                ones = x + 1
                return tens * 10 + ones
            raise ValueError("Pad out of range for mk2 mapping")
        # linear
        if not (0 <= x <= 7 and 0 <= y <= 7):
            raise ValueError("Pad out of range (linear)")
        return y * 8 + x

    # --- LED Output ---
    def set_pad_color(self, pad: PadCoord, rgb: Tuple[int, int, int]):
        if self.virtual or not self.outport:
            return
        note = self.pad_to_note(pad)
        r, g, b = rgb
        if self.mapping_mode == 'mk2' and self.rgb_highlight:
            # Apply calibration & clamp 0..63
            sr = max(0, min(63, int((r/255) * 63 * self.calibration['r'])))
            sg = max(0, min(63, int((g/255) * 63 * self.calibration['g'])))
            sb = max(0, min(63, int((b/255) * 63 * self.calibration['b'])))
            # Single LED RGB
            self._safe_send(mido.Message('sysex', data=[0x00,0x20,0x29,0x02,0x18,0x0B,note,sr,sg,sb]))
        else:
            # Monochrome fallback (linear grid devices)
            velocity = max(1, min(127, int((0.3*r + 0.59*g + 0.11*b)/255 * 127)))
            self._safe_send(mido.Message('note_on', note=note, velocity=velocity))

    def set_calibration(self, r: float, g: float, b: float):
        self.calibration = {"r": r, "g": g, "b": b}

    def set_many(self, items: Iterable[Tuple[PadCoord, Tuple[int, int, int]]]):
        for pad, rgb in items:
            self.set_pad_color(pad, rgb)

    def clear(self, full: bool = False):
        """Turn off all pad LEDs.

        If full=True and device supports SysEx, send an all-lights-off command for Mk2.
        Falls back to iterating over notes.
        """
        if self.virtual or not self.outport:
            return
        if full:
            # Attempt Mk2 all LEDs off (session lighting off) – sending reset to default state
            try:
                # Clear using Fader/LED All Off (0x0E) if supported; else fallback
                self._safe_send(mido.Message('sysex', data=[0x00,0x20,0x29,0x02,0x18,0x0E,0x00]))
                return
            except Exception:
                pass
        for note in range(64):
            self._safe_send(mido.Message('note_on', note=note, velocity=0))

    def highlight_pads(self, pads: Iterable[PadCoord], rgb=(0,64,255)):
        """Light only provided pads with provided RGB (default blue)."""
        items = list(pads)
        if not items:
            return
        self.set_many((pad, rgb) for pad in items)
