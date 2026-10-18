"""Shared builders for synthetic Magic Mouse input streams."""

from magicmouse import evdev

RES_X = 26  # units/mm, hid-magicmouse Magic Mouse X axis
RES_Y = 71  # units/mm, hid-magicmouse Magic Mouse Y axis


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now


class TouchBuilder:
    """Builds MT slot-protocol events in millimetres (y grows downward)."""

    def __init__(self, res_x=RES_X, res_y=RES_Y):
        self.res_x = res_x
        self.res_y = res_y
        self._next_id = 100

    def down(self, slot, x_mm, y_mm):
        self._next_id += 1
        return [
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_SLOT, slot),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_TRACKING_ID, self._next_id),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_POSITION_X, round(x_mm * self.res_x)),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_POSITION_Y, round(y_mm * self.res_y)),
        ]

    def move(self, slot, x_mm, y_mm):
        return [
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_SLOT, slot),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_POSITION_X, round(x_mm * self.res_x)),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_POSITION_Y, round(y_mm * self.res_y)),
        ]

    def up(self, slot):
        return [
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_SLOT, slot),
            evdev.InputEvent(evdev.EV_ABS, evdev.ABS_MT_TRACKING_ID, -1),
        ]

    def syn(self):
        return [evdev.InputEvent(evdev.EV_SYN, evdev.SYN_REPORT, 0)]


def feed(engine, events, now):
    """Feed events to a TouchEngine, returning events produced at the SYN."""
    produced = []
    for event in events:
        if event.type == evdev.EV_SYN:
            produced.extend(engine.frame(now))
        else:
            engine.abs_event(event.code, event.value)
    return produced
