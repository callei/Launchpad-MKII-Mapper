from conftest import FakeController

from launchpad.port import CONNECTED, LOST, VIRTUAL, LaunchpadPort


class Device:
    """A plug-in-able Launchpad: hands out FakeControllers that see its presence and can be made to fail."""

    def __init__(self, present=True, busy=False):
        self.present = present
        self.busy = busy
        self.made = []

    def make(self):
        device = self

        class Lp(FakeController):
            def open(self):
                if not device.present:
                    self.virtual, self.virtual_reason = True, "Launchpad not found."
                elif device.busy:
                    self.virtual, self.virtual_reason = True, "port busy"

            def is_present(self):
                return device.present

            def set_pad_color(self, pad, rgb):
                if not device.present:
                    raise OSError("device gone")
                super().set_pad_color(pad, rgb)

        lp = Lp()
        self.made.append(lp)
        return lp


def make_port(device):
    port = LaunchpadPort(device.make)
    states = []
    port.subscribe(lambda: states.append(port.state))
    return port, states


def test_connect_reports_connected_and_forwards_presses():
    port, states = make_port(Device())
    presses = []
    port.on_press = presses.append
    port.connect()
    assert states == [CONNECTED]
    port.controller.on_press((1, 2))  # what the MIDI thread calls
    assert presses == [(1, 2)]


def test_missing_device_is_virtual_with_reason_and_drops_writes():
    port, states = make_port(Device(present=False))
    port.connect()
    assert states == [VIRTUAL] and "not found" in port.reason
    port.set_pad_color((0, 0), (255, 0, 0))
    assert port.controller.colors == []


def test_open_raising_is_virtual_with_reason():
    class Broken(FakeController):
        def open(self):
            raise OSError("no MIDI backend")

    port, states = make_port(Device())
    port._make = Broken
    port.connect()
    assert states == [VIRTUAL] and "no MIDI backend" in port.reason


def test_unplug_while_connected_is_lost_and_replug_reconnects():
    device = Device()
    port, states = make_port(device)
    port.connect()
    port.poll()
    assert states == [CONNECTED]
    device.present = False
    port.poll()
    assert states == [CONNECTED, LOST] and "unplugged" in port.reason
    port.poll()
    assert states == [CONNECTED, LOST]  # no repeat notifications while it stays away
    device.present = True
    port.poll()
    assert states == [CONNECTED, LOST, CONNECTED]
    assert len(device.made) == 2  # reconnecting opens a new controller


def test_failed_write_is_lost():
    device = Device()
    port, states = make_port(device)
    port.connect()
    device.present = False
    port.set_pad_color((0, 0), (255, 0, 0))
    assert states == [CONNECTED, LOST] and "device gone" in port.reason
    port.set_pad_color((0, 0), (255, 0, 0))  # later writes are dropped, not re-reported
    assert states == [CONNECTED, LOST]


def test_plugging_in_after_startup_without_device_connects():
    device = Device(present=False)
    port, states = make_port(device)
    port.connect()
    port.poll()
    device.present = True
    port.poll()
    assert states == [VIRTUAL, CONNECTED]


def test_busy_device_is_not_retried_until_replugged():
    device = Device(busy=True)
    port, states = make_port(device)
    port.connect()
    port.poll()
    port.poll()
    assert states == [VIRTUAL] and len(device.made) == 1
    device.present, device.busy = False, False
    port.poll()
    device.present = True
    port.poll()
    assert states == [VIRTUAL, CONNECTED]


def test_poll_before_connect_does_nothing():
    port, states = make_port(Device())
    port.poll()
    assert states == [] and port.controller is None
