#!/usr/bin/env bash
set -Eeuo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-${ZARYA_VERSION:-1.0.3}}"
OUTDIR="${2:-$SRC/dist}"
STAGE="${TMPDIR:-/var/tmp}/zarya-deb-${VERSION}-$$"
PKG="$STAGE/zarya-shell"

command -v dpkg-deb >/dev/null || { echo 'dpkg-deb is required' >&2; exit 1; }
rm -rf "$STAGE"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/lib/zarya/fonts"
cp -a "$SRC/shell/"*.py "$PKG/usr/lib/zarya/"
if [ -d "$SRC/shell/fonts" ]; then
    cp -a "$SRC/shell/fonts/." "$PKG/usr/lib/zarya/fonts/"
fi
cat > "$PKG/DEBIAN/control" <<EOF
Package: zarya-shell
Version: $VERSION
Section: x11
Priority: optional
Architecture: all
Depends: python3, python3-numpy, python3-pil
Maintainer: Zarya OS maintainers
Description: Zarya OS framebuffer desktop shell
 Custom Python desktop shell, applications and framebuffer renderer.
EOF
chmod 0644 "$PKG/usr/lib/zarya/"*.py
mkdir -p "$OUTDIR"
dpkg-deb --root-owner-group --build "$PKG" "$OUTDIR/zarya-shell_${VERSION}_all.deb" >/dev/null
rm -rf "$STAGE"
echo "$OUTDIR/zarya-shell_${VERSION}_all.deb"
