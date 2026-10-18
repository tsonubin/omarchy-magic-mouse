"""Output sinks: real uinput passthrough is in :mod:`magicmouse.evdev`;
this module holds the recording/pretty-printing sink used by ``simulate``,
``run --dry-run`` and the tests."""

from .evdev import CODE_NAMES, EVENT_NAMES, ScrollAccumulator, event_name


class RecordingSink:
    """Collects everything the pipeline would send to the virtual mouse.

    ``printer`` (optional) receives ``(label, detail)`` lines as they happen.
    Scroll is kept as raw hi-res floats; the legacy/hi-res pairing contract
    lives in :class:`magicmouse.evdev.ScrollAccumulator`.
    """

    def __init__(self, printer=None, pair_scroll=False):
        self.printer = printer
        self.writes = []
        self.scrolls = []
        self._pair = ScrollAccumulator() if pair_scroll else None

    def write(self, ev_type, code, value):
        self.writes.append((ev_type, code, value))
        if self.printer:
            self.printer("out", f"{EVENT_NAMES.get(ev_type, ev_type)} {event_name(ev_type, code)} {value}")

    def write_event(self, event):
        self.write(event.type, event.code, event.value)

    def scroll(self, hr_x, hr_y):
        if not hr_x and not hr_y:
            return
        self.scrolls.append((hr_x, hr_y))
        if self.printer:
            self.printer("scroll", f"dx={hr_x:+.1f} dy={hr_y:+.1f} hi-res units")
        if self._pair is not None:
            for code, value in self._pair.add(hr_x, hr_y):
                self.writes.append((2, code, value))
                if self.printer:
                    self.printer("out", f"EV_REL {CODE_NAMES.get((2, code), code)} {value}")

    def sync(self):
        if self.printer:
            self.printer("out", "EV_SYN SYN_REPORT 0")

    def close(self):
        pass
