# AUR package draft — NOT PUBLISHED

Draft packaging for `omarchy-magic-mouse` (arch `any`, pure Python). Nothing
here is published anywhere; no AUR remote is configured or touched.

Build locally with:

```bash
cd packaging/aur
makepkg -si
```

The packaged layout differs from `install.sh` (which is for un-packaged
installs): library under `/usr/lib/omarchy-magic-mouse/`, entry script
symlinked from `/usr/bin`, the user unit rewritten to the packaged path, and
the udev rule/modules-load shipped under `/usr/lib`. Remove a previous
user-level install first (`./uninstall.sh --purge`).

## Must happen before this could be published

1. Tag `v1.0.0` in the repo, then run `updpkgsums` here (currently
   `sha256sums=('SKIP')` because the tag archive does not exist yet).
2. Decide and add a `LICENSE` file to the repo (PKGBUILD declares MIT as a
   placeholder — confirm with the owner).
3. Regenerate `.SRCINFO` with `makepkg --printsrcinfo` (the one here is
   hand-written).
4. Validate with `namcap PKGBUILD *.pkg.tar.*` and a clean-chroot build.

## Validation status on this box

`makepkg` and `namcap` are not installed here (no root), so steps 3–4 are
**unvalidated** — flagged deliberately rather than assumed. The file formats
follow the standard Arch layout, but treat this as a review draft.
