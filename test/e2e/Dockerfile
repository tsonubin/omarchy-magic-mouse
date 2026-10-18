# Emulated Omarchy environment for end-to-end tests.
#
# Arch Linux ARM (the base of Omarchy on Apple Silicon) + the real Omarchy
# packages from pkgs.omarchy.org + Hyprland + quickshell.  The container shares
# the host kernel, so /dev/uinput and evdev behave exactly as on a real install.
#
# Base image is created by run.sh from the official ArchLinuxARM rootfs tarball
# (`docker import`); override with --build-arg BASE=... for CI mirrors.

ARG BASE=mm-e2e-base:latest
FROM ${BASE}

# pacman's download sandbox needs Landlock/seccomp that container kernels and
# profiles may not provide; the rig runs downloads unsandboxed (root anyway).
RUN sed -i 's/^#DisableSandboxFilesystem/DisableSandboxFilesystem/; s/^#DisableSandboxSyscalls/DisableSandboxSyscalls/' /etc/pacman.conf \
 && pacman-key --init \
 && pacman-key --populate archlinuxarm

# The Omarchy repository, bootstrapped through its own keyring package:
# TrustAll only for the keyring fetch, then real signature verification.
RUN printf '\n[omarchy]\nServer = https://pkgs.omarchy.org/stable/$arch\nSigLevel = Optional TrustAll\n' >> /etc/pacman.conf \
 && pacman -Sy --noconfirm \
 && pacman -S --noconfirm omarchy-keyring \
 && sed -i '/^\[omarchy\]/,/^$/ s/SigLevel = Optional TrustAll/SigLevel = Required DatabaseOptional/' /etc/pacman.conf \
 && pacman -Sy --noconfirm

# The real Omarchy desktop runtime (brings Hyprland + quickshell + the omarchy
# CLI/router) plus the tools the rig itself uses.
RUN pacman -S --noconfirm --needed omarchy omarchy-settings python libinput libinput-tools util-linux jq socat dbus \
 && pacman -Scc --noconfirm

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
ENTRYPOINT ["/src/test/e2e/entrypoint.sh"]
