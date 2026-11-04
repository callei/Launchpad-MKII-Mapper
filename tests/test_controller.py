import mido
import pytest

from launchpad.controller import LaunchpadController

ALL_PADS = [(x, y) for y in range(8) for x in range(8)] + [(x, -1) for x in range(8)] + [(8, y) for y in range(8)]


class FakeOut:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def connected_controller():
    lp = LaunchpadController()
    lp.outport = FakeOut()
    return lp


@pytest.mark.parametrize("pad", ALL_PADS)
def test_mk2_pad_note_round_trip(pad):
    lp = LaunchpadController()
    assert lp._note_to_pad(lp.pad_to_note(pad)) == pad


def test_mk2_known_notes():
    lp = LaunchpadController()
    assert lp.pad_to_note((0, 0)) == 81  # top-left grid pad
    assert lp.pad_to_note((7, 7)) == 18  # bottom-right grid pad
    assert lp.pad_to_note((8, 0)) == 89  # top side button
    assert lp.pad_to_note((0, -1)) == 104  # first top round button


@pytest.mark.parametrize("note", [0, 10, 20, 90, 99, 103, 112, 118, 127])
def test_mk2_unmapped_notes_return_none(note):
    assert LaunchpadController()._note_to_pad(note) is None


def test_press_and_release_callbacks():
    lp = LaunchpadController()
    events = []
    lp.on_press = lambda pad: events.append(("press", pad))
    lp.on_release = lambda pad: events.append(("release", pad))
    lp._handle_message(mido.Message("note_on", note=81, velocity=100))
    lp._handle_message(mido.Message("note_on", note=81, velocity=0))
    lp._handle_message(mido.Message("control_change", control=104, value=127))
    lp._handle_message(mido.Message("control_change", control=104, value=0))
    assert events == [("press", (0, 0)), ("release", (0, 0)), ("press", (0, -1)), ("release", (0, -1))]


def test_set_pad_color_sends_mk2_rgb_sysex():
    lp = connected_controller()
    lp.set_calibration(1.0, 1.0, 1.0)
    lp.set_pad_color((0, 0), (255, 0, 255))
    (msg,) = lp.outport.sent
    assert msg.type == "sysex"
    assert list(msg.data) == [0x00, 0x20, 0x29, 0x02, 0x18, 0x0B, 81, 63, 0, 63]


def test_clear_turns_off_every_mk2_pad():
    lp = connected_controller()
    lp.clear()
    leds = {msg.data[6] for msg in lp.outport.sent}
    assert leds == {lp.pad_to_note(pad) for pad in ALL_PADS}


def test_open_without_device_records_reason(monkeypatch):
    monkeypatch.setattr(mido, "get_input_names", lambda: [])
    monkeypatch.setattr(mido, "get_output_names", lambda: [])
    lp = LaunchpadController()
    lp.open()
    assert lp.virtual and "not found" in lp.virtual_reason


def test_open_busy_port_records_reason(monkeypatch):
    monkeypatch.setattr(mido, "get_input_names", lambda: ["Launchpad MK2 0"])
    monkeypatch.setattr(mido, "get_output_names", lambda: ["Launchpad MK2 1"])

    def busy(name):
        raise OSError("MIDI port in use")

    monkeypatch.setattr(mido, "open_output", busy)
    lp = LaunchpadController()
    lp.open()
    assert lp.virtual and "MIDI port in use" in lp.virtual_reason


def test_close_twice_sends_clear_once():
    lp = connected_controller()
    out = lp.outport
    lp.close()
    lp.close()
    lp.clear(full=True)
    assert len(out.sent) == 1


def test_virtual_mode_sends_nothing():
    lp = connected_controller()
    lp.virtual = True
    lp.set_pad_color((0, 0), (255, 255, 255))
    lp.clear(full=True)
    assert lp.outport.sent == []


def test_send_errors_reach_the_caller():
    class GoneOut:
        def send(self, msg):
            raise OSError("device unplugged")

    lp = LaunchpadController()
    lp.outport = GoneOut()
    with pytest.raises(OSError):
        lp.set_pad_color((0, 0), (255, 0, 0))
    lp.close()  # shutdown after an unplug still closes quietly


def test_open_when_listing_devices_fails_records_reason(monkeypatch):
    def broken():
        raise OSError("no MIDI backend")

    monkeypatch.setattr(mido, "get_input_names", broken)
    lp = LaunchpadController()
    lp.open()
    assert lp.virtual and "no MIDI backend" in lp.virtual_reason


def test_is_present_needs_both_ports(monkeypatch):
    monkeypatch.setattr(mido, "get_input_names", lambda: ["Launchpad MK2 0"])
    monkeypatch.setattr(mido, "get_output_names", lambda: [])
    assert not LaunchpadController().is_present()
    monkeypatch.setattr(mido, "get_output_names", lambda: ["Launchpad MK2 1"])
    assert LaunchpadController().is_present()
