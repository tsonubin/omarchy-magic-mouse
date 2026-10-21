"""Configuration: defaults, deep-merge of the user file, validation.

The user file lives at ``~/.config/omarchy/magic-mouse.json`` (Omarchy's
user-config directory).  It is strict JSON so parse errors surface loudly
instead of half-applying.
"""

import copy
import json
import math
import os

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "omarchy",
    "magic-mouse.json",
)

GESTURE_NAMES = ("swipe_left", "swipe_right", "swipe_up", "swipe_down", "double_tap", "tap")

# Device classes the daemon knows how to grab and drive.  Apple trackpads are
# recognized in `omarchy-magic-mouse devices` but left to libinput.
MANAGEABLE_KINDS = ("apple_touch_mouse", "wheel_mouse")

BUILTIN_ACTIONS = (
    "workspace_next",
    "workspace_prev",
    "workspace_last",
    "app_grid",
    "omarchy_menu",
    "browser_back",
    "browser_forward",
    "smart_zoom",
    "none",
)

DEFAULTS = {
    "devices": {
        # Device classes to grab and manage.  apple_trackpad is recognized but
        # not managed by default: libinput already handles trackpaps well.
        "manage": ["apple_touch_mouse", "wheel_mouse"],
    },
    "scroll": {
        # macOS default: content tracks finger movement.
        "natural": True,
        "touch": {"enabled": True, "detents_per_mm": 1.2},
        "smooth": {"enabled": True, "duration_ms": 90},
        "inertia": {
            "enabled": True,
            "tau": 0.35,
            "min_velocity": 0.3,
            "max_duration_ms": 1500,
        },
    },
    "gestures": {
        "enabled": True,
        "fingers": 2,
        "swipe": {"min_distance_mm": 5.0, "axis_ratio": 1.8, "max_duration_ms": 500},
        "tap": {
            "max_duration_ms": 250,
            "slop_mm": 1.5,
            "double_interval_ms": 350,
            "double_distance_mm": 5.0,
        },
        "map": {
            "swipe_left": "workspace_next",
            "swipe_right": "workspace_prev",
            "swipe_up": "app_grid",
            "swipe_down": "omarchy_menu",
            "double_tap": "smart_zoom",
            "tap": "none",
        },
    },
    "commands": {
        "workspace_next": ["hyprctl", "dispatch", "workspace", "e+1"],
        "workspace_prev": ["hyprctl", "dispatch", "workspace", "e-1"],
        "workspace_last": ["hyprctl", "dispatch", "workspace", "previous"],
        "app_grid": ["omarchy-shell", "omarchy.dock", "apps"],
        "omarchy_menu": ["omarchy", "menu", "toggle", "root"],
        "browser_back": ["hyprctl", "dispatch", "sendshortcut", "ALT, Left"],
        "browser_forward": ["hyprctl", "dispatch", "sendshortcut", "ALT, Right"],
        "smart_zoom": ["hyprctl", "dispatch", "sendshortcut", "CTRL, 0"],
        "none": [],
    },
}


class ConfigError(ValueError):
    """Configuration is unusable; ``problems`` lists every issue found."""

    def __init__(self, message, problems=None, path=None):
        self.problems = list(problems) if problems is not None else [message]
        self.path = path
        super().__init__(message)

    def __str__(self):
        return "; ".join(self.problems) if len(self.problems) > 1 else self.problems[0]


def default_config():
    return copy.deepcopy(DEFAULTS)


def _deep_merge(base, override, path=""):
    for key, value in override.items():
        here = f"{path}.{key}" if path else key
        if path == "gestures.map":
            base[key] = value  # gesture names are checked in validate()
            continue
        if key not in base:
            raise ConfigError(f"unknown setting: {here}")
        if isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value, here)
        else:
            base[key] = value
    return base


def _reject_constant(name):
    # Python's json accepts NaN/Infinity; they are not valid JSON and would
    # poison the engine's arithmetic
    raise ValueError(f"{name} is not a valid JSON number")


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r} (the file would silently take the last)")
        obj[key] = value
    return obj


def _is_number(value):
    """A finite JSON number.  ``bool`` is an ``int`` in Python: exclude it."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large to convert to float
        return False


def _is_command(value):
    return (
        isinstance(value, list)
        and value
        and all(isinstance(part, str) and part and "\0" not in part for part in value)
    )


def validate(cfg):
    errors = []

    manage = cfg["devices"]["manage"]
    if not isinstance(manage, list) or not all(
        kind in MANAGEABLE_KINDS for kind in manage
    ):
        errors.append(
            "devices.manage must be a list of manageable device classes: "
            + ", ".join(MANAGEABLE_KINDS)
        )

    scroll = cfg["scroll"]
    if not isinstance(scroll["natural"], bool):
        errors.append("scroll.natural must be true or false")
    if not isinstance(scroll["touch"]["enabled"], bool):
        errors.append("scroll.touch.enabled must be true or false")
    if not _is_number(scroll["touch"]["detents_per_mm"]) or scroll["touch"]["detents_per_mm"] <= 0:
        errors.append("scroll.touch.detents_per_mm must be > 0")
    if not isinstance(scroll["smooth"]["enabled"], bool):
        errors.append("scroll.smooth.enabled must be true or false")
    if not _is_number(scroll["smooth"]["duration_ms"]) or scroll["smooth"]["duration_ms"] < 0:
        errors.append("scroll.smooth.duration_ms must be >= 0")
    inertia = scroll["inertia"]
    if not isinstance(inertia["enabled"], bool):
        errors.append("scroll.inertia.enabled must be true or false")
    if not _is_number(inertia["tau"]) or inertia["tau"] <= 0:
        errors.append("scroll.inertia.tau must be > 0")
    if not _is_number(inertia["min_velocity"]) or inertia["min_velocity"] < 0:
        errors.append("scroll.inertia.min_velocity must be >= 0")
    if not _is_number(inertia["max_duration_ms"]) or inertia["max_duration_ms"] <= 0:
        errors.append("scroll.inertia.max_duration_ms must be > 0")

    gestures = cfg["gestures"]
    if not isinstance(gestures["enabled"], bool):
        errors.append("gestures.enabled must be true or false")
    if not isinstance(gestures["fingers"], int) or not 2 <= gestures["fingers"] <= 3:
        errors.append("gestures.fingers must be 2 or 3")
    swipe = gestures["swipe"]
    if not _is_number(swipe["min_distance_mm"]) or swipe["min_distance_mm"] <= 0:
        errors.append("gestures.swipe.min_distance_mm must be > 0")
    if not _is_number(swipe["axis_ratio"]) or swipe["axis_ratio"] < 1:
        errors.append("gestures.swipe.axis_ratio must be >= 1")
    if not _is_number(swipe["max_duration_ms"]) or swipe["max_duration_ms"] <= 0:
        errors.append("gestures.swipe.max_duration_ms must be > 0")
    tap = gestures["tap"]
    for field in ("max_duration_ms", "slop_mm", "double_interval_ms", "double_distance_mm"):
        if not _is_number(tap[field]) or tap[field] <= 0:
            errors.append(f"gestures.tap.{field} must be > 0")

    commands = cfg["commands"]
    for name, command in commands.items():
        if name not in BUILTIN_ACTIONS:
            errors.append(
                "commands: unknown action %r (valid: %s)" % (name, ", ".join(BUILTIN_ACTIONS))
            )
            continue
        if name != "none" and not _is_command(command):
            errors.append(f"commands.{name} must be a non-empty list of strings")
        if name == "none" and command != []:
            errors.append("commands.none must be []")

    gesture_map = gestures["map"]
    for gesture, target in gesture_map.items():
        if gesture not in GESTURE_NAMES:
            errors.append(f"gestures.map: unknown gesture {gesture!r}")
            continue
        if isinstance(target, str):
            if target not in BUILTIN_ACTIONS:
                errors.append(f"gestures.map.{gesture}: unknown action {target!r}")
        elif isinstance(target, dict):
            if set(target) != {"command"} or not _is_command(target["command"]):
                errors.append(
                    'gestures.map.%s: custom action must be {"command": [...]}' % gesture
                )
        else:
            errors.append(f"gestures.map.{gesture}: must be an action name or an object with a command")

    if errors:
        raise ConfigError("configuration is invalid", problems=errors)
    return cfg


def load_config(path=None):
    """Load, merge and validate the config; ``path=None`` uses the default."""
    path = path or CONFIG_PATH
    cfg = default_config()
    if os.path.exists(path):
        try:
            # utf-8-sig: tolerate the BOM that GUI editors put in "UTF-8" files
            with open(path, encoding="utf-8-sig") as handle:
                override = json.load(
                    handle,
                    parse_constant=_reject_constant,
                    object_pairs_hook=_no_duplicate_keys,
                )
        except ValueError as error:
            raise ConfigError(f"{path}: invalid JSON: {error}") from error
        except OSError as error:
            raise ConfigError(f"{path}: cannot read config: {error}") from error
        if not isinstance(override, dict):
            raise ConfigError(f"{path}: top level must be a JSON object")
        try:
            _deep_merge(cfg, override)
        except ConfigError as error:
            raise ConfigError(f"{path}: {error}") from error
    try:
        return validate(cfg)
    except ConfigError as error:
        error.path = error.path or path
        raise


def resolve_actions(cfg):
    """Map each gesture to ``{"name": ..., "command": [...] | None}``."""
    resolved = {}
    for gesture, target in cfg["gestures"]["map"].items():
        if isinstance(target, dict):
            resolved[gesture] = {"name": "custom", "command": list(target["command"])}
        else:
            command = cfg["commands"].get(target, [])
            resolved[gesture] = {"name": target, "command": list(command) if command else None}
    return resolved
