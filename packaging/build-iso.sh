#!/usr/bin/env bash
# Build a hybrid BIOS/UEFI live ISO. Large build trees stay outside the repo.
set -Eeuo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${ZARYA_VERSION:-1.0.3}"
WORK="${ZARYA_WORKDIR:-${RUNNER_TEMP:-/var/tmp}/zarya-build}"
WORK="$(realpath -m "$WORK")"
ROOT="$WORK/rootfs"
ISODIR="$WORK/iso"
OUT="${ZARYA_OUT:-$WORK/zarya-os-${VERSION}-amd64.iso}"
DEBIAN_MIRROR="${ZARYA_MIRROR:-https://deb.debian.org/debian}"

[[ "$VERSION" =~ ^[0-9][A-Za-z0-9.+~-]*$ ]] || { echo "Invalid version: $VERSION" >&2; exit 2; }
case "$WORK" in
    /tmp/*|/var/tmp/*|"${RUNNER_TEMP:-/nonexistent}"/*) ;;
    *) echo "ZARYA_WORKDIR must be under /tmp, /var/tmp, or RUNNER_TEMP: $WORK" >&2; exit 2 ;;
esac
if [ "$WORK" = /tmp ] || [ "$WORK" = /var/tmp ]; then
    echo 'Refusing an unsafe work directory' >&2
    exit 2
fi
case "$WORK/" in "$SRC/"*) echo 'Build workdir must not be inside the source tree' >&2; exit 2 ;; esac
[ -s "$SRC/packaging/ota/ota-public.pem" ] || { echo 'Missing OTA public key' >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E env ZARYA_VERSION="$VERSION" ZARYA_WORKDIR="$WORK" \
        ZARYA_OUT="$OUT" ZARYA_MIRROR="$DEBIAN_MIRROR" bash "$0"
fi

for cmd in debootstrap mksquashfs grub-mkrescue xorriso cpio; do
    command -v "$cmd" >/dev/null || { echo "Missing build tool: $cmd" >&2; exit 1; }
done

# Only delete a previous work tree if our marker proves that we created it.
if [ -e "$WORK" ]; then
    [ -f "$WORK/.zarya-build-workdir" ] || {
        echo "Refusing to remove unmarked work directory: $WORK" >&2; exit 1;
    }
    rm -rf -- "$WORK"
fi
mkdir -p "$WORK" "$ROOT" "$ISODIR/live" "$ISODIR/boot/grub"
printf 'zarya-build\n' > "$WORK/.zarya-build-workdir"

cleanup_mounts() {
    for p in "$ROOT/dev/pts" "$ROOT/dev" "$ROOT/proc" "$ROOT/sys"; do
        if mountpoint -q "$p"; then umount -lf "$p" 2>/dev/null || true; fi
    done
}
trap cleanup_mounts EXIT INT TERM

echo "==> debootstrap Debian trixie in $WORK"
debootstrap --arch=amd64 --variant=minbase \
    --include=ca-certificates,apt,wget \
    trixie "$ROOT" "$DEBIAN_MIRROR"

mkdir -p "$ROOT/dev/pts" "$ROOT/proc" "$ROOT/sys" "$ROOT/run"
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sysfs "$ROOT/sys"
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"

cat > "$ROOT/etc/apt/sources.list.d/zarya.sources" <<'EOF'
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://security.debian.org/debian-security
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

# Wine's 32-bit dependencies are needed for legacy Windows applications.
chroot "$ROOT" dpkg --add-architecture i386
PKGS="systemd-sysv udev live-boot network-manager sudo curl openssl \
python3 python3-numpy python3-pil fonts-dejavu-core iproute2 util-linux \
e2fsprogs dosfstools gdisk parted rsync grub-common grub-pc-bin \
grub-efi-amd64-bin mtools ca-certificates less nano xz-utils zstd \
linux-image-amd64 initramfs-tools wine wine64 wine32:i386 fonts-wine \
xserver-xorg-core xserver-xorg-legacy xserver-xorg-video-fbdev \
xserver-xorg-input-libinput xinit x11-xserver-utils dbus-x11"
echo '==> install kernel, desktop dependencies, Wine, and installer/OTA tools'
chroot "$ROOT" /bin/bash -euc "export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y --no-install-recommends $PKGS"

# Users and login
# shellcheck disable=SC2016
chroot "$ROOT" /bin/bash -euc '
    for group in sudo video input audio plugdev; do
        getent group "$group" >/dev/null || groupadd "$group"
    done
    id zarya >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash -G sudo,video,input,audio,plugdev zarya
    echo "zarya:zarya" | chpasswd
    echo "root:zarya" | chpasswd
    printf "zarya ALL=(ALL) NOPASSWD: ALL\n" > /etc/sudoers.d/10-zarya
    chmod 0440 /etc/sudoers.d/10-zarya
    echo zarya > /etc/hostname
    printf "127.0.0.1 localhost zarya\n::1 localhost\n" > /etc/hosts
    systemctl enable NetworkManager.service
'

mkdir -p "$ROOT/usr/lib/zarya" "$ROOT/usr/local/bin" "$ROOT/usr/share/zarya" \
    "$ROOT/usr/lib/tmpfiles.d" "$ROOT/etc/modules-load.d" \
    "$ROOT/etc/udev/rules.d" "$ROOT/etc/systemd/system/getty@tty1.service.d" \
    "$ROOT/etc/X11/xorg.conf.d" "$ROOT/etc/modprobe.d" \
    "$ROOT/etc/live"
cp -a "$SRC/shell/"*.py "$ROOT/usr/lib/zarya/"
if [ -d "$SRC/shell/fonts" ]; then
    mkdir -p "$ROOT/usr/lib/zarya/fonts"
    cp -a "$SRC/shell/fonts/." "$ROOT/usr/lib/zarya/fonts/"
fi
chmod 0644 "$ROOT/usr/lib/zarya/"*.py

install -m 0644 "$SRC/packaging/ota/grub-ab.cfg" "$ROOT/usr/share/zarya/grub-ab.cfg"
install -m 0755 "$SRC/packaging/system/zarya-session" "$ROOT/usr/local/bin/zarya-session"
install -m 0755 "$SRC/packaging/ota/zarya-update" "$ROOT/usr/local/bin/zarya-update"
install -m 0755 "$SRC/packaging/ota/zarya-ota-init" "$ROOT/usr/local/bin/zarya-ota-init"
install -m 0755 "$SRC/packaging/install/zarya-install" "$ROOT/usr/local/bin/zarya-install"
install -m 0755 "$SRC/packaging/wine/zarya-wine" "$ROOT/usr/local/bin/zarya-wine"
install -m 0644 "$SRC/packaging/ota/ota-public.pem" "$ROOT/etc/zarya-ota-public.pem"
printf '%s\n' 'smells1k/zarya' > "$ROOT/etc/zarya-ota-repo"
printf '%s\n' "$VERSION" > "$ROOT/etc/zarya-version"

cat > "$ROOT/etc/X11/xorg.conf.d/20-zarya-fbdev.conf" <<'EOF'
Section "Device"
    Identifier "ZaryaFramebuffer"
    Driver "fbdev"
    Option "fbdev" "/dev/fb0"
EndSection

Section "Screen"
    Identifier "ZaryaScreen"
    Device "ZaryaFramebuffer"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080" "1600x900" "1366x768" "1280x800" "1024x768" "800x600"
    EndSubSection
EndSection
EOF
printf 'allowed_users=console\nneeds_root_rights=yes\n' > "$ROOT/etc/X11/Xwrapper.config"

cat > "$ROOT/home/zarya/.profile" <<'EOF'
# Start the Zarya session on the live desktop VT.
if [ "$(tty 2>/dev/null)" = /dev/tty1 ]; then
    exec /usr/local/bin/zarya-session
fi
EOF
chroot "$ROOT" chown zarya:zarya /home/zarya/.profile
cat > "$ROOT/etc/systemd/system/getty@tty1.service.d/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin zarya --noclear %I $TERM
EOF
chroot "$ROOT" systemctl enable getty@tty1.service

# Make evdev nodes available for the custom framebuffer input loop in VMs and
# on physical machines; systemd loads these drivers before the autologin VT.
cat > "$ROOT/etc/modules-load.d/zarya-input.conf" <<'EOF'
evdev
psmouse
atkbd
i8042
usbhid
hid_generic
hid_multitouch
xhci_hcd
xhci_pci
ehci_pci
EOF

# Keep keyboard and pointer event nodes readable by the live user as a fallback
# when the shell cannot acquire its root framebuffer/input session.
cat > "$ROOT/etc/udev/rules.d/71-zarya-input.rules" <<'EOF'
SUBSYSTEM=="input", GROUP="input", MODE="0660"
EOF

# /run is a tmpfs, so create writable per-session state at every boot.
cat > "$ROOT/usr/lib/tmpfiles.d/zarya.conf" <<'EOF'
d /run/zarya 0755 zarya zarya -
d /run/zarya/log 0755 zarya zarya -
EOF

install -m 0644 "$SRC/packaging/ota/zarya-ota-watchdog.service"   \
    "$ROOT/etc/systemd/system/zarya-ota-watchdog.service"
chroot "$ROOT" systemctl enable zarya-ota-watchdog.service

cat > "$ROOT/etc/modprobe.d/zarya-no-kms.conf" <<'EOF'
blacklist i915
blacklist amdgpu
blacklist radeon
blacklist nouveau
blacklist bochs
blacklist virtio_gpu
blacklist nvidia_drm
EOF

cat > "$ROOT/etc/live/boot.conf" <<'EOF'
LIVE_HOSTNAME=zarya
LIVE_USERNAME=zarya
LIVE_USER_FULLNAME="Zarya OS user"
EOF

KVER="$(basename "$(find "$ROOT/boot" -maxdepth 1 -name 'vmlinuz-*' -print | sort -V | tail -1)" | sed 's/^vmlinuz-//')"
[ -n "$KVER" ] || { echo 'No kernel installed in rootfs' >&2; exit 1; }
if [ -s "$ROOT/boot/initrd.img-$KVER" ]; then
    chroot "$ROOT" update-initramfs -u -k "$KVER"
else
    chroot "$ROOT" update-initramfs -c -k "$KVER"
fi
[ -s "$ROOT/boot/initrd.img-$KVER" ] || { echo 'No initramfs produced' >&2; exit 1; }
cp "$ROOT/boot/vmlinuz-$KVER" "$ROOT/boot/zarya-vmlinuz"
cp "$ROOT/boot/initrd.img-$KVER" "$ROOT/boot/zarya-initrd.img"
cp "$ROOT/boot/vmlinuz-$KVER" "$ISODIR/live/vmlinuz"
cp "$ROOT/boot/initrd.img-$KVER" "$ISODIR/live/initrd.img"
cp "$SRC/boot/grub.cfg" "$ISODIR/boot/grub/grub.cfg"
install -m 0644 /usr/share/grub/unicode.pf2 "$ISODIR/boot/grub/unicode.pf2"
printf 'Zarya OS %s\n' "$VERSION" > "$ISODIR/.disk-info"

# Keep the live root compact; the installed system stores /home separately.
rm -rf "$ROOT/var/lib/apt/lists"/* "$ROOT/var/cache/apt/archives"/*
rm -rf "$ROOT/tmp"/* "$ROOT/var/tmp"/* "$ROOT/run"/*

echo '==> create compressed live root filesystem'
mksquashfs "$ROOT" "$ISODIR/live/filesystem.squashfs" \
    -comp zstd -Xcompression-level 19 -noappend -wildcards \
    -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'var/tmp/*' \
       'var/cache/apt/*' 'var/lib/apt/lists/*' > "$WORK/squashfs.log" 2>&1

mkdir -p "$(dirname "$OUT")"
echo '==> create hybrid BIOS + UEFI ISO'
grub-mkrescue -o "$OUT" "$ISODIR" --volid 'ZARYA_OS' 2>&1 | tail -4
(cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
ls -lh "$OUT"
echo "ISO built at $OUT"
