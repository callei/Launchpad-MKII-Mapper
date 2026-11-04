"""Animation playback, driven by an external clock (no Qt, no threads, no controller).

An animation is {"frames": [{"duration": ms, "pads": {"x,y": cell}}, ...]} (see mapping.py for cells).
The engine plays any number of runs at once. The caller passes the current time in ms to `start` and
`advance`; `advance` returns what animations show right now and when that next changes.
"""

from typing import NamedTuple

from launchpad.mapping import color_rgb, parse_pad_key, to_int

DEFAULT_FRAME_MS = 300

Pad = tuple[int, int]
RGB = tuple[int, int, int]


class Tick(NamedTuple):
    overlay: dict[Pad, RGB]  # colors animations show now; later-started runs win on shared pads
    next_due: float | None  # time of the next change; None when nothing is playing


def _compile(frames) -> list[tuple[int, dict[Pad, RGB]]]:
    """(duration, {pad: rgb}) per frame. Copies, so later edits to `frames` don't affect a run."""
    compiled = []
    for frame in frames if isinstance(frames, list) else ():
        frame = frame if isinstance(frame, dict) else {}
        pads = frame.get("pads")
        cells = {}
        for key, cell in pads.items() if isinstance(pads, dict) else ():
            pad, rgb = parse_pad_key(key), color_rgb(cell)
            if pad is not None and rgb is not None:
                cells[pad] = rgb
        # >= 1 ms: an all-zero-duration looping animation (possible via import) would otherwise spin
        compiled.append((max(1, to_int(frame.get("duration"), DEFAULT_FRAME_MS)), cells))
    return compiled


class _Run:
    def __init__(self, name: str, frames: list, cycles: int | None, now: float):
        self.name = name
        self.frames = frames
        self.cycles_left = cycles  # None: loop forever
        self.index = 0
        self.frame_end = now + frames[0][0]

    def advance(self, now: float) -> bool:
        """Move to the frame showing at `now`. False once the run has finished."""
        cycle = sum(duration for duration, _ in self.frames)
        skip = int((now - self.frame_end) // cycle)
        if self.cycles_left is not None:
            skip = min(skip, self.cycles_left - 1)
        if skip > 0:  # far behind (e.g. after sleep): jump whole cycles instead of stepping 1 ms frames
            self.frame_end += skip * cycle
            if self.cycles_left is not None:
                self.cycles_left -= skip
        while now >= self.frame_end:
            self.index += 1
            if self.index == len(self.frames):
                if self.cycles_left is not None:
                    self.cycles_left -= 1
                    if self.cycles_left <= 0:
                        return False
                self.index = 0
            self.frame_end += self.frames[self.index][0]
        return True


class AnimationEngine:
    """Plays animation runs; each run plays a snapshot of its frames taken at start."""

    def __init__(self):
        self._runs: list[_Run] = []  # in start order

    @property
    def running(self) -> list[str]:
        """Names of the runs still playing, in start order."""
        return [run.name for run in self._runs]

    def start(self, name: str, frames, iterations, now: float) -> None:
        """Play `frames` `iterations` times (0 or less: loop forever) from `now`.

        Starting a loop-forever animation whose loop-forever run is already playing stops that run
        instead (a pad press toggles it). Animations without frames don't start.
        """
        iterations = to_int(iterations, 1)
        if iterations <= 0:
            looping = [run for run in self._runs if run.name == name and run.cycles_left is None]
            if looping:
                self._runs = [run for run in self._runs if run not in looping]
                return
        compiled = _compile(frames)
        if compiled:
            self._runs.append(_Run(name, compiled, None if iterations <= 0 else iterations, now))

    def stop(self, name: str | None = None) -> None:
        """Stop the runs of `name`, or every run."""
        self._runs = [run for run in self._runs if name is not None and run.name != name]

    def rename(self, old: str, new: str) -> None:
        """Runs of `old` continue as `new` (so they still toggle off from a pad mapped to `new`)."""
        for run in self._runs:
            if run.name == old:
                run.name = new

    def advance(self, now: float) -> Tick:
        """Step every run to `now`, drop finished ones, and return what they show."""
        self._runs = [run for run in self._runs if run.advance(now)]
        overlay = {}
        for run in self._runs:
            overlay.update(run.frames[run.index][1])
        return Tick(overlay, min((run.frame_end for run in self._runs), default=None))
