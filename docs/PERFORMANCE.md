# Performance

Measured with the emulated rig (`test/e2e/run.sh bench`): a virtual device
emits through the real kernel input core, the daemon under test grabs and
re-emits, and both sides are stamped with the same monotonic clock. CPU
numbers are `utime+stime` of the daemon process only.

## Environment

- kernel 6.12.0-108.64.6.3.el9uek.aarch64 (aarch64), shared 4-core box
- daemon in the standard E2E container (Arch Linux ARM + Python 3.14)
- `/proc/<pid>/stat` clock tick = 10ms: burst CPU figures quantize to 0.1µs
  per event at 60k events; single runs on a busy machine — numbers are
  indicative, not lab-grade.

## Method

- **Latency**: 100 pointer events paced at 5ms; source stamp → observer stamp
  on the daemon's sink. "Harness floor" is the same path with no daemon, to
  separate tool noise from daemon cost.
- **CPU per event**: unpaced bursts (pointer: REL_X/REL_Y/SYN frames;
  touch: MT position + SYN frames), daemon CPU ÷ input events.
- **Idle / momentum**: daemon CPU over 5s of silence and over 2s of inertial
  scrolling (the 60Hz tick loop).
- **Pure pipeline**: 60k synthetic events through `DevicePipeline` with no
  kernel, uninstrumented and under cProfile.

## Results

| Metric | Before | After |
|---|---|---|
| Latency via daemon (mean / p50 / p95 / max) | 0.26 / 0.19 / 0.49 / 2.79 ms | 0.24 / 0.18 / 0.66 / 0.69 ms |
| Harness floor, same run (mean) | 0.13 ms | 0.08 ms |
| Pointer burst CPU/event (60k events) | 1.3 µs | 1.0 µs |
| Pointer burst wall throughput | 53.1k events/s | 51.3k events/s |
| Touch burst CPU/event (10k events) | 1.0 µs | 1.0 µs |
| Idle CPU (5s, no input) | 0.20% | 0.00% |
| Momentum CPU (2s, 60Hz ticking) | 0.50% | 0.50% |
| Pure pipeline, profiled (60k events) | 0.301 s | 0.261 s |
| — of which event construction | 0.091 s | 0.042 s |
| Pure pipeline, uninstrumented | not measured before | 1.50 µs/event |

Micro-benchmark (host Python, 500k constructions):
`InputEvent` as frozen dataclass **1.085 µs** → namedtuple **0.501 µs**
(**2.16×** faster; the reason the type changed).

## What changed (and only what the numbers justified)

1. **`InputEvent` frozen dataclass → namedtuple** — one allocation per kernel
   event in the hot path; construction was ~30% of the pure-pipeline profile
   and is now 2.16× cheaper in isolation.
2. **uinput writes batched** — events buffer into one `os.write` per input
   frame (previously one syscall per event, 2–3 per pointer frame). The
   service flushes at each read batch and each tick; the rig validates
   end-to-end delivery.
3. **`read_events` uses `struct.iter_unpack`** — no per-event slicing.
4. **`service._pump` is fd-indexed** — O(1) instead of scanning devices.

## Interpretation

- **Latency is scheduling-bound, not code-bound.** The daemon's Python work is
  ~1.5µs per event; the observed end-to-end add over the harness floor stays
  ~0.15ms, dominated by wakeup + kernel round trips. Further code-level
  latency work would not move this number; the p95/max columns reflect
  shared-machine noise more than the daemon (they moved in both directions
  between runs).
- **CPU is µs-scale and stays there**: 1.0µs per event means a 1000Hz gaming
  mouse (3k events/s) costs ~0.3% of one core. Both bursts ran 10–50× above
  any real device rate.
- **Idle is truly idle** (select blocks; no polling), and inertial scrolling
  costs ~0.5% of a core while it runs (60Hz ticks), then returns to zero.
- The touch-burst figure sits at the 10ms `/proc` resolution floor: at that
  granularity "unchanged" means "below the measurement", not "identical".

## Reproduce

```bash
sudo modprobe uinput
test/e2e/run.sh bench
```
