# Contributing

## Setup

```bash
git clone https://github.com/tsonubin/omarchy-magic-mouse
cd omarchy-magic-mouse
python3 -m unittest discover -s tests
```

Python 3.9+ and Bash are the only requirements. The end-to-end rig needs
docker and `/dev/uinput` (`sudo modprobe uinput`).

## Tests

Be constrained. A test must guard real observable behaviour or a contract
that would fail on a credible regression — no duplicates, no
implementation-coupled assertions, no snapshot spam. Prefer extending an
existing table (e.g. `tests/test_config.py`'s rejection cases) over new
fixtures. Inside omp, follow the test-audit skill
(`~/.omp/agent/skills/test-audit`): one primary owner per contract at the
strongest boundary, real transports over mocks, and delete test-only seams.

Run only what covers your change:

```bash
python3 -m unittest tests.test_scroll      # one area
python3 -m unittest discover -s tests      # full suite (run before pushing)
```

The end-to-end suite runs the daemon in an emulated Omarchy environment
(real kernel input core, real Hyprland + quickshell) and asserts on real
state; see [test/e2e/README.md](test/e2e/README.md). Run it when you touch
`service.py`, `evdev.py`, `pipeline.py`, or the install path.

## Code invariants

These exist because each one fixed a real bug; do not undo them casually:

- **stdlib only** — no runtime dependencies, ever.
- **Injected clocks** — engine code (`scrolling`, `touch`, `pipeline`) never
  reads a clock itself; callers pass `now`. This is what makes the feel
  testable.
- **Actions never raise** — a broken user command must never take the input
  path down (`actions.py` logs and continues).
- **Kernel UAPI facts** (`evdev.py`): `UI_SET_*BIT` takes the bit *by value*;
  output streams need `SYN_REPORT` frames or the kernel buffers events and
  consumers see nothing; unchanged ABS values are coalesced away, so consumers
  inherit per-code abs state; `REL_WHEEL_HI_RES` pairs with `REL_WHEEL` at
  120 units per detent.
- **Never grab our own virtual mouse**; re-check device identity on rescan
  (event numbers are recycled); grabs must release on any exit path so
  hardware reverts to stock.
- **User data is sacred** — install/uninstall may move config aside, never
  delete it.

## Performance

`test/e2e/run.sh bench` measures latency, CPU per event and a pure-pipeline
profile in the rig. Optimise only what a measurement justifies, record
before/after in [docs/PERFORMANCE.md](docs/PERFORMANCE.md), and revert
changes that do not move a number.

## Commits

Imperative summary line, body explaining *why*. Keep commits small and
coherent (one change per commit where practical).

## Scope

PRs against `main` are welcome; keep them focused and describe how you
tested. The license of this repository is an open decision — by contributing
you agree your changes may be relicensed under the license the maintainer
chooses.
