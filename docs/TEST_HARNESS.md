# Integration / E2E test harness outline

Where verification lives for `omarchy-magic-mouse`, what each layer mocks,
what genuinely needs real input, and what is CI-safe. Written before the
in-process integration skeleton (`tests/harness.py`) landed.

## Layers

| Layer | What runs | Needs | CI-safe |
|---|---|---|---|
| Unit (`tests/test_*.py`) | engines, config, actions — injected clocks, no I/O | python | yes |
| **In-process integration (`tests/harness.py`)** | the **real** `Service` select loop, real pipeline, real config; kernel endpoints faked | python | **yes** |
| Sandbox (`tests/test_install/test_setup/test_cli/test_lock`) | real scripts/CLI against a throwaway `$HOME` | bash, python | yes |
| Emulated rig (`test/e2e/`) | real kernel input core (uinput + evdev + grabs), real Hyprland + quickshell + omarchy packages | docker, `/dev/uinput`, a DRM node | arm64 CI with those devices |

The rig is the strongest proof but cannot run on an arbitrary runner or a
locked-down dev box. The in-process harness fills the middle: loop wiring,
batching/flush points, fd indexing, device churn, and config reload without a
kernel or root.

## What to mock vs what needs real input

| Concern | Mock here | Real input required (rig owns it) |
|---|---|---|
| evdev/uinput device nodes | pipe-backed devices speaking the real `input_event` framing | node permissions, udev, `EVIOCGRAB` semantics |
| event delivery | real `select`/pipes, real `struct` framing | kernel coalescing (ABS dedup), hi-res/legacy wheel pairing |
| compositor / action commands | `RecordingSink` + recording dispatcher | `hyprctl` state, `omarchy menu` surfaces |
| systemd / journal | none needed in-process | unit lifecycle, `JOURNAL_STREAM` logging |
| hotplug, crash-safety | simulated by closing pipe endpoints | kernel recycling event numbers, grab release on SIGKILL |

Rule of thumb: mock only *transport endpoints*; everything between them (the
loop, engines, config) stays real. Assertions go to observable sinks, never
to call shapes.

## CI-safe subset

Unit + in-process integration + sandbox: no root, no `/dev`, no docker,
seconds on one core. That subset must stay green on any runner; the rig is an
additional gate where its devices exist.

## The skeleton (implemented next)

- `tests/harness.py`: `FakeKernelDevice` (pipe-backed, same interface as
  `evdev.Device`, reuses the real `read_events` parser so framing and the
  EOF→disconnect contract are exercised), and `ServiceRunner` (runs
  `Service.run_forever` on a thread; captures the signal handlers the service
  registers so `stop()` drives the real SIGTERM path).
- `tests/test_service_loop.py`: one smoke contract — an event injected at the
  fake kernel endpoint reaches the recording sink through the real loop, and
  the real stop path shuts the loop down cleanly.

Extensions later (each one new contract, one owner): disconnect via EOF,
SIGHUP reload through the loop, flush-point behaviour under burst.
