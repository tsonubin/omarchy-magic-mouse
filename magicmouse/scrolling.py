"""Scroll pipeline: touch scrolling, smooth wheel animation, inertial momentum.

Everything is expressed in hi-res wheel units where 120 = one detent (the
kernel's ``REL_WHEEL_HI_RES`` convention).  The engine is deterministic: it
never reads a clock itself, callers pass ``now`` in seconds.

Sign conventions (finger deltas in screen coordinates, +x right, +y down):

- natural scrolling (macOS default): content tracks the fingers, so
  ``hr_y = +k * dy`` and ``hr_x = -k * dx``.
- classic scrolling flips both.
Both match the hid-magicmouse kernel driver's wheel emulation for the
non-natural case, so behaviour is continuous across daemon restarts.
"""

import math

HI_RES_PER_DETENT = 120

FRAME_INTERVAL = 1 / 60
_VELOCITY_ALPHA = 0.5
_SAMPLE_GAP = 0.25  # seconds of silence after which velocity estimation resets


class _Animation:
    """Eases one detent-sized target into place over ``duration`` seconds."""

    def __init__(self, duration):
        self.duration = duration
        self.target = 0.0
        self.emitted = 0.0
        self.base = 0.0
        self.start = None

    def add(self, hr):
        if not self.active():
            self.start = None  # a fresh burst eases over the full duration
            self.base = self.emitted
        self.target += hr

    def active(self):
        return self.target != self.emitted

    def step(self, now):
        """Return the hi-res delta to emit for this frame."""
        if self.start is None:
            self.start = now
        if self.duration <= 0:
            delta = self.target - self.emitted
            self.emitted = self.target
            return delta
        progress = min((now - self.start) / self.duration, 1.0)
        eased = 1 - (1 - progress) ** 2  # ease-out quad
        goal = self.base + (self.target - self.base) * eased
        delta = goal - self.emitted
        self.emitted += delta
        return delta

    def cancel(self):
        self.target = self.emitted


class ScrollEngine:
    def __init__(self, scroll_cfg, emit):
        self.emit = emit  # callable(hr_x, hr_y), floats allowed
        self.natural = bool(scroll_cfg["natural"])

        touch = scroll_cfg["touch"]
        self.touch_enabled = bool(touch["enabled"])
        self.detents_per_mm = float(touch["detents_per_mm"])

        smooth = scroll_cfg["smooth"]
        self.smooth_enabled = bool(smooth["enabled"])
        self._smooth_duration = float(smooth["duration_ms"]) / 1000

        inertia = scroll_cfg["inertia"]
        self.inertia_enabled = bool(inertia["enabled"])
        self._tau = float(inertia["tau"])
        self._min_velocity = float(inertia["min_velocity"])  # detents/second
        self._max_duration = float(inertia["max_duration_ms"]) / 1000

        self._anim_x = _Animation(self._smooth_duration)
        self._anim_y = _Animation(self._smooth_duration)
        self._vel_x = 0.0  # detents/second, exponential moving average
        self._vel_y = 0.0
        self._last_sample = None
        self._momentum = None  # (vx, vy, started, last_step)

    # --- input ---------------------------------------------------------------

    def touch_scroll(self, now, dx_mm, dy_mm):
        """A finger moved on a touch surface: scroll immediately."""
        if not self.touch_enabled:
            return
        if self.natural:
            det_x = -self.detents_per_mm * dx_mm
            det_y = self.detents_per_mm * dy_mm
        else:
            det_x = self.detents_per_mm * dx_mm
            det_y = -self.detents_per_mm * dy_mm

        self._cancel_momentum()
        self._record_velocity(now, det_x, det_y)
        self.emit(det_x * HI_RES_PER_DETENT, det_y * HI_RES_PER_DETENT)

    def wheel(self, now, detents_x, detents_y):
        """Discrete wheel input from an ordinary mouse."""
        sign = -1 if self.natural else 1
        detents_x *= sign
        detents_y *= sign
        self._cancel_momentum()
        hr_x = detents_x * HI_RES_PER_DETENT
        hr_y = detents_y * HI_RES_PER_DETENT
        if self.smooth_enabled and self._smooth_duration > 0:
            self._anim_x.add(hr_x)
            self._anim_y.add(hr_y)
        else:
            self.emit(hr_x, hr_y)

    def end_touch(self, now):
        """The finger lifted: start inertial scrolling if there is momentum."""
        if not self.inertia_enabled:
            self._vel_x = self._vel_y = 0.0
            self._last_sample = None
            return
        if self._last_sample is None:
            return
        if math.hypot(self._vel_x, self._vel_y) < self._min_velocity:
            self._vel_x = self._vel_y = 0.0
            self._last_sample = None
            return
        self._momentum = [self._vel_x, self._vel_y, now, now]
        self._vel_x = self._vel_y = 0.0
        self._last_sample = None

    def cancel(self):
        """Any new input or gesture stops momentum and pending animation."""
        self._cancel_momentum()
        self._anim_x.cancel()
        self._anim_y.cancel()
        self._vel_x = self._vel_y = 0.0
        self._last_sample = None

    # --- ticking -------------------------------------------------------------

    def is_active(self):
        return self._momentum is not None or self._anim_x.active() or self._anim_y.active()

    def tick(self, now):
        """Advance animations/momentum one frame; emit if there is output."""
        hr_x = 0.0
        hr_y = 0.0

        if self._anim_x.active():
            hr_x += self._anim_x.step(now)
        if self._anim_y.active():
            hr_y += self._anim_y.step(now)

        if self._momentum is not None:
            vx, vy, started, last = self._momentum
            dt = min(max(now - last, 1e-4), 0.1)
            hr_x += vx * HI_RES_PER_DETENT * dt
            hr_y += vy * HI_RES_PER_DETENT * dt
            decay = math.exp(-dt / self._tau)
            vx *= decay
            vy *= decay
            self._momentum[0], self._momentum[1], self._momentum[3] = vx, vy, now
            if (
                math.hypot(vx, vy) < self._min_velocity
                or now - started >= self._max_duration
            ):
                self._momentum = None

        if hr_x or hr_y:
            self.emit(hr_x, hr_y)
        return self.is_active()

    # --- internals -----------------------------------------------------------

    def _record_velocity(self, now, det_x, det_y):
        if self._last_sample is None or now - self._last_sample > _SAMPLE_GAP:
            self._vel_x, self._vel_y = 0.0, 0.0
            self._last_sample = now
            return
        dt = min(max(now - self._last_sample, 1e-4), _SAMPLE_GAP)
        self._vel_x = _VELOCITY_ALPHA * (det_x / dt) + (1 - _VELOCITY_ALPHA) * self._vel_x
        self._vel_y = _VELOCITY_ALPHA * (det_y / dt) + (1 - _VELOCITY_ALPHA) * self._vel_y
        self._last_sample = now

    def _cancel_momentum(self):
        self._momentum = None
