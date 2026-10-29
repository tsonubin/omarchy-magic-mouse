# Hardening & packaging plan

Design for the hardening/packaging phase. Written before code; each work item
below states the decision and its rationale so later steps commit against it.

Scope: harden `omarchy-magic-mouse`, extend tests where they guard real
contracts, draft an AUR package, and finish the docs. No publishing to the
AUR, no PRs, no behaviour changes to gesture/scroll feel.

## Threat / robustness model

The daemon runs as the logged-in user, reads kernel input devices, holds an
`EVIOCGRAB`, writes a virtual mouse through `/dev/uinput`, and executes
user-configured action commands. Failure modes worth defending:

1. **Bad config** silently changing behaviour or crashing the daemon at start.
2. **Device churn** (unplug/replug, suspend, Bluetooth drops) wedging the
   pipeline or leaving a grab held on a dead device.
3. **Install/uninstall** damaging user data, breaking on re-run, or leaving
   the system half-configured.
4. **Privilege creep**: the udev rule, group membership, and service unit
   granting more access than the daemon needs.
5. **Action commands** are user code; they must never be able to take the
   input path down with them (already: failures are logged, never raised —
   keep that invariant in any change).

Out of scope: sandboxing action commands (they intentionally run as the
user with normal rights), multi-user systems, malicious local config.

## Work items

### 1. Config input validation (`magicmouse/config.py`)

- Reject non-standard JSON constants: Python's `json` accepts `NaN` /
  `Infinity` by default; a config with `"tau": NaN` would pass type checks and
  poison the engine's arithmetic. Reject at parse time with a clear error.
- Reject numbers that are not finite (`1e400` parses to `inf`).
- Reject booleans where numbers are expected (`True` is an `int`; a config
  with `"detents_per_mm": true` currently passes and means `1.0`).
- Reject NUL bytes inside command strings (subprocess would raise at invoke
  time; better to fail config load with the exact path).

Decision: tighten `validate()` only; no schema library, no new dependencies.

### 2. Device disconnect/reconnect resilience (`evdev.py`, `service.py`)

- `Device.read_events`: a read returning `b""` means the evdev node is gone
  (device removed). Today it silently yields no events forever while the
  device stays "managed". Raise `OSError(ENODEV)` so the service drops it and
  the 2s rescan re-attaches on replug.
- `Service.scan`: a *different* device appearing at a managed path (kernel
  recycles event numbers) must not be ignored. Compare device identity
  (name/vendor/product/version); on mismatch drop and re-attach.
- Sink loss (uinput fd dead) during `process`: exit cleanly with a logged
  reason instead of a traceback; systemd `Restart=always` restores service,
  and the kernel releases grabs on exit so the hardware reverts to stock.

Decision: identity comparison on the device's id tuple + name; a replaced
device is dropped and re-attached within one rescan period.

### 3. Least-privilege review (unit, udev, install)

- `systemd/omarchy-magic-mouse.service`: add `NoNewPrivileges=yes`,
  `ProtectKernelTunables=yes`, `ProtectControlGroups=yes`.
  Deliberately **not** adding `ProtectSystem`/`ProtectHome`/`DevicePolicy`:
  action commands are user code and must keep normal file access, and a
  device cgroup allow-list would break dynamically created evdev nodes.
- `udev/99-omarchy-magic-mouse.rules`: keep the `input`-group model (the
  standard for per-seat input access) but document the blast radius in the
  rule file. `OPTIONS+="static_node=uinput"` already avoids module-load
  ordering issues.
- No privilege changes to the daemon itself (needs rw `/dev/uinput` and
  rw grabbed evdev nodes — nothing else).

### 4. Safe install/uninstall (`install.sh`, `uninstall.sh`)

- Add `--dry-run` to both: print planned actions, change nothing.
- Guard the target path (`HOME` set; install dir strictly under `$HOME`).
- Idempotent re-install: already mostly true; keep and prove with a test.
- No data loss: `uninstall.sh --purge` must **back up**
  `~/.config/omarchy/magic-mouse.json` (timestamped `.bak`) instead of
  deleting it. Default uninstall already keeps the config.

### 5. Tests (constrained)

Only new tests that guard a contract with a credible regression, extending
existing tables where possible:

- `tests/test_config.py`: extend the rejected-values table with
  bool-as-number and NUL-in-command rows; add one load-path test for
  `NaN`/`Infinity`/`1e400` rejection (new parsing behaviour).
- `tests/test_service.py` (new, 2 tests): same-path device replacement is
  re-attached; vanished devices are dropped. Fakes at the `evdev` module
  seam; no kernel needed.
- `tests/test_install.py` (new, 2 tests): sandboxed install is idempotent
  and dry-run makes no changes; uninstall keeps config, `--purge` backs it
  up rather than deleting.

Not added: duplicated engine/gesture coverage (already 62 tests), snapshot
tests, tests of fakes echoing fakes. E2E rig scenarios stay as-is; run the
rig once at the end of the phase (targeted at service.py changes).

### 6. AUR packaging draft (`packaging/aur/`)

- `PKGBUILD` for `omarchy-magic-mouse` (arch=`any`, depends=`python`),
  packaged layout: library under `/usr/lib/omarchy-magic-mouse/`, wrapper in
  `/usr/bin/`, user unit under `/usr/lib/systemd/user/` (rewritten to the
  packaged path from the single shipped unit), udev rule under
  `/usr/lib/udev/rules.d/`, modules-load under `/usr/lib/modules-load.d/`.
- `.SRCINFO` hand-written; `makepkg`/`namcap` are not on this box — flagged
  as unvalidated in the file header and the report.
- `sha256sums=('SKIP')` with a `TODO(updpkgsums)` note: the source is a
  GitHub tag archive that does not exist yet (no tag is pushed by this
  phase). Draft only: nothing is published, no AUR remote is touched.
- PKGBUILD documents the conflict with the user-level install and an
  `.install` message about joining the `input` group.

### 7. Docs (`README.md`)

- Add an **Architecture** section (module responsibilities + invariants),
  distinct from the existing event-flow diagram.
- Document the packaged install path next to `install.sh`, and that the AUR
  package is a draft.
- Keep the hardware validation checklist; point it at `doctor` output as the
  report surface.

## Commit sequence

1. this plan
2. config validation (+ table rows, load-path test)
3. device resilience (+ 2 service tests)
4. least-privilege unit/udev
5. install/uninstall safety (+ sandbox tests)
6. AUR draft
7. README architecture/packaging docs

Each step runs only its targeted tests; the full unit suite and one rig run
close the phase.
