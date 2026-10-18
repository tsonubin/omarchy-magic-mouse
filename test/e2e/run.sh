#!/bin/bash
# Builds the emulated Omarchy environment and runs the E2E suite inside it.
#
#   test/e2e/run.sh              # everything
#   test/e2e/run.sh --help       # scenario runner options
#
# Requirements on the host: docker, /dev/uinput (modprobe uinput).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CACHE="${MM_E2E_CACHE:-/tmp/mm-e2e-cache}"
ROOTFS_URL="${MM_E2E_ROOTFS_URL:-https://fl.us.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz}"
BASE_IMAGE="${MM_E2E_BASE:-mm-e2e-base:latest}"
IMAGE="${MM_E2E_IMAGE:-mm-e2e:latest}"

if [[ ! -e /dev/uinput ]]; then
  echo "error: /dev/uinput missing — run: sudo modprobe uinput" >&2
  exit 1
fi

# The compositor scenarios need a DRM node for aquamarine's GBM allocator:
# use the host's GPU device, or make a virtual one with vkms (bare CI runners).
# The cgroup rule opens up the whole input major (13): the rig mknods its own
# evdev nodes at runtime, and docker's device whitelist would block them.
DEVICE_ARGS=(--device /dev/uinput --device-cgroup-rule "c 13:* rwm")
if ! compgen -G "/dev/dri/card*" >/dev/null; then
  echo "no DRM node on host — trying to load vkms (virtual KMS)"
  sudo modprobe vkms 2>/dev/null || modprobe vkms 2>/dev/null || true
fi
for node in /dev/dri/*; do
  [[ -c $node ]] && DEVICE_ARGS+=(--device "$node")
done

# 1. Base rootfs image (cached across runs; official ArchLinuxARM tarball)
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  mkdir -p "$CACHE"
  TARBALL="$CACHE/ArchLinuxARM-aarch64-latest.tar.gz"
  if [[ ! -s "$TARBALL" ]]; then
    echo "downloading ArchLinuxARM rootfs (~830MB, cached at $TARBALL)"
    curl -sL --retry 3 -o "$TARBALL.part" "$ROOTFS_URL"
    mv "$TARBALL.part" "$TARBALL"
  fi
  echo "importing base image $BASE_IMAGE"
  docker import "$TARBALL" "$BASE_IMAGE" >/dev/null
fi

# 2. Emulated Omarchy image (real omarchy packages, Hyprland, quickshell)
docker build --build-arg "BASE=$BASE_IMAGE" -t "$IMAGE" "$HERE"

# 3. Run the suite; /src is the live checkout so code edits don't rebuild.
exec docker run --rm \
  "${DEVICE_ARGS[@]}" \
  --shm-size=256m \
  -v "$REPO:/src" \
  "$IMAGE" "$@"
