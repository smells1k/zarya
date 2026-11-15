#!/usr/bin/env bash
# Build a hybrid BIOS/UEFI Kubuntu 26.04 live ISO with KDE Plasma and Zarya A/B tools.
set -Eeuo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${ZARYA_VERSION:-2.0.0}"
WORK="${ZARYA_WORKDIR:-${RUNNER_TEMP:-/var/tmp}/zarya-build}"
WORK="$(realpath -m "$WORK")"
ROOT="$WORK/rootfs"
ISODIR="$WORK/iso"
OUT="${ZARYA_OUT:-$WORK/zarya-os-${VERSION}-amd64.iso}"
UBUNTU_SUITE=resolute
UBUNTU_MIRROR="${ZARYA_MIRROR:-https://archive.ubuntu.com/ubuntu}"
UBUNTU_SECURITY_MIRROR="${ZARYA_SECURITY_MIRROR:-https://security.ubuntu.com/ubuntu}"
# shellcheck source=packaging/ota/limits.sh
. "$SRC/packaging/ota/limits.sh"

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
    exec sudo -E env \
        ZARYA_VERSION="$VERSION" \
        ZARYA_WORKDIR="$WORK" \
        ZARYA_OUT="$OUT" \
        ZARYA_MIRROR="$UBUNTU_MIRROR" \
        ZARYA_SECURITY_MIRROR="$UBUNTU_SECURITY_MIRROR" \
        bash "$0"
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

echo "==> debootstrap Ubuntu $UBUNTU_SUITE (Kubuntu 26.04 LTS base) in $WORK"
debootstrap --arch=amd64 --variant=minbase \
    --components=main,restricted,universe,multiverse \
    --include=apt,ca-certificates,wget,ubuntu-keyring \
    "$UBUNTU_SUITE" "$ROOT" "$UBUNTU_MIRROR"

mkdir -p "$ROOT/dev/pts" "$ROOT/proc" "$ROOT/sys" "$ROOT/run"
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sysfs "$ROOT/sys"

# Chroot package installation needs a working resolver. The builder's resolver
# is temporary; the finished image uses Ubuntu's systemd-resolved stub link.
rm -f "$ROOT/etc/resolv.conf"
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"

rm -f "$ROOT/etc/apt/sources.list" \
    "$ROOT/etc/apt/sources.list.d/ubuntu.sources" \
    "$ROOT/etc/apt/sources.list.d/ubuntu.list"
cat > "$ROOT/etc/apt/sources.list.d/zarya.sources" <<EOF
Types: deb
URIs: $UBUNTU_MIRROR
Suites: $UBUNTU_SUITE $UBUNTU_SUITE-updates
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: $UBUNTU_SECURITY_MIRROR
Suites: $UBUNTU_SUITE-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

# Wine is retained as an optional desktop capability and requires i386 packages.
chroot "$ROOT" dpkg --add-architecture i386

# Prevent package post-install scripts from trying to start daemons in the chroot.
cat > "$ROOT/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 0755 "$ROOT/usr/sbin/policy-rc.d"

# Choose the KDE display manager and prevent any GRUB post-install prompt in chroot.
chroot "$ROOT" debconf-set-selections <<'EOF'
sddm shared/default-x-display-manager select sddm
grub-pc grub-pc/install_devices_empty boolean true
grub-pc grub-pc/install_devices multiselect
grub-efi-amd64 grub-efi/install_devices_empty boolean true
grub-efi-amd64 grub-efi/install_devices multiselect
EOF

PKGS=(
    systemd-sysv udev live-boot live-boot-initramfs-tools
    network-manager systemd-resolved sudo curl openssl python3 iproute2 util-linux procps
    e2fsprogs dosfstools gdisk parted rsync grub-common grub-pc-bin
    grub-efi-amd64-bin mtools ca-certificates less nano xz-utils zstd
    linux-generic initramfs-tools squashfs-tools
    kubuntu-desktop
    xserver-xorg-core xserver-xorg-input-libinput xserver-xorg-video-vmware
    xwayland libinput-tools dbus-x11 open-vm-tools open-vm-tools-desktop
    wine wine64 wine32:i386 fonts-wine wine-binfmt
)
echo '==> install full Kubuntu desktop, Wayland input/VMware support, kernel, Wine, and Zarya tools'
chroot "$ROOT" /usr/bin/env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none \
    apt-get update
chroot "$ROOT" /usr/bin/env DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none \
    apt-get install -y "${PKGS[@]}"

rm -f "$ROOT/usr/sbin/policy-rc.d"
# Do not ship the build host's nameserver address in a live or installed system.
rm -f "$ROOT/etc/resolv.conf" "$ROOT/etc/.resolv.conf.systemd-resolved.bak"
ln -s ../run/systemd/resolve/stub-resolv.conf "$ROOT/etc/resolv.conf"

[ -s "$ROOT/usr/share/wayland-sessions/plasma.desktop" ] || {
    echo 'Kubuntu Plasma Wayland session was not installed (missing plasma.desktop)' >&2
    exit 1
}
[ -x "$ROOT/usr/sbin/sddm" ] || [ -x "$ROOT/usr/bin/sddm" ] || {
    echo 'Kubuntu SDDM display manager was not installed' >&2
    exit 1
}

# Users and system identity. The live ISO and installed A/B slots use the same
# Kubuntu Plasma autologin; the installer replaces machine-id per installation.
# shellcheck disable=SC2016
chroot "$ROOT" /bin/bash -euc '
    for group in sudo video input audio plugdev; do
        getent group "$group" >/dev/null || groupadd "$group"
    done
    if ! id zarya >/dev/null 2>&1; then
        useradd -m -u 1000 -s /bin/bash -G sudo,video,input,audio,plugdev zarya
    else
        usermod -aG sudo,video,input,audio,plugdev zarya
    fi
    echo "zarya:zarya" | chpasswd
    echo "root:zarya" | chpasswd
    printf "zarya ALL=(ALL) NOPASSWD: ALL\\n" > /etc/sudoers.d/10-zarya
    chmod 0440 /etc/sudoers.d/10-zarya
    echo zarya > /etc/hostname
    printf "127.0.0.1 localhost zarya\\n::1 localhost\\n" > /etc/hosts
    systemctl enable NetworkManager.service
    systemctl enable systemd-resolved.service
    systemctl enable open-vm-tools.service
    systemctl enable sddm.service
    systemctl set-default graphical.target
    systemctl disable getty@tty1.service >/dev/null 2>&1 || true
'

mkdir -p "$ROOT/usr/lib/zarya" "$ROOT/usr/local/bin" \
    "$ROOT/usr/share/zarya" "$ROOT/etc/modules-load.d" \
    "$ROOT/etc/udev/rules.d" "$ROOT/etc/sddm.conf.d" \
    "$ROOT/etc/systemd/system"
install -m 0644 "$SRC/packaging/ota/limits.sh" "$ROOT/usr/lib/zarya/ota-limits.sh"
install -m 0644 "$SRC/packaging/ota/grub-ab.cfg" "$ROOT/usr/share/zarya/grub-ab.cfg"
install -m 0755 "$SRC/packaging/ota/zarya-update" "$ROOT/usr/local/bin/zarya-update"
install -m 0755 "$SRC/packaging/ota/zarya-ota-init" "$ROOT/usr/local/bin/zarya-ota-init"
install -m 0755 "$SRC/packaging/install/zarya-install" "$ROOT/usr/local/bin/zarya-install"
install -m 0755 "$SRC/packaging/wine/zarya-wine" "$ROOT/usr/local/bin/zarya-wine"
install -m 0644 "$SRC/packaging/ota/ota-public.pem" "$ROOT/etc/zarya-ota-public.pem"
printf '%s\n' 'smells1k/zarya' > "$ROOT/etc/zarya-ota-repo"
printf '%s\n' "$VERSION" > "$ROOT/etc/zarya-version"

cat > "$ROOT/etc/sddm.conf.d/99-zarya-autologin.conf" <<'EOF'
[Autologin]
User=zarya
Session=plasma.desktop
Relogin=false
EOF

# Native KMS/Wayland stays enabled. Do not restore the old fbdev or no-KMS setup.
rm -f "$ROOT/etc/modprobe.d/zarya-no-kms.conf" \
    "$ROOT/etc/X11/xorg.conf.d/20-zarya-fbdev.conf"
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
cat > "$ROOT/etc/udev/rules.d/71-zarya-input.rules" <<'EOF'
SUBSYSTEM=="input", GROUP="input", MODE="0660"
EOF

install -m 0644 "$SRC/packaging/ota/zarya-ota-watchdog.service" \
    "$ROOT/etc/systemd/system/zarya-ota-watchdog.service"
install -m 0644 "$SRC/packaging/ota/zarya-ota-desktop-ready.service" \
    "$ROOT/etc/systemd/system/zarya-ota-desktop-ready.service"
chroot "$ROOT" systemctl enable zarya-ota-watchdog.service zarya-ota-desktop-ready.service

cat > "$ROOT/etc/issue" <<EOF
Zarya OS $VERSION (Kubuntu 26.04 LTS, KDE Plasma)\\n\\l
EOF
: > "$ROOT/etc/machine-id"
rm -f "$ROOT/var/lib/dbus/machine-id"
ln -s /etc/machine-id "$ROOT/var/lib/dbus/machine-id"

KVER="$(basename "$(find "$ROOT/boot" -maxdepth 1 -name 'vmlinuz-*' -print | sort -V | tail -1)" | sed 's/^vmlinuz-//')"
[ -n "$KVER" ] || { echo 'No Ubuntu generic kernel installed in rootfs' >&2; exit 1; }
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
sed "s/@VERSION@/$VERSION/g" "$SRC/boot/grub.cfg" > "$ISODIR/boot/grub/grub.cfg"
install -m 0644 /usr/share/grub/unicode.pf2 "$ISODIR/boot/grub/unicode.pf2"
printf 'Zarya OS %s (Kubuntu 26.04 LTS)\n' "$VERSION" > "$ISODIR/.disk-info"

# Keep the live root compact; the installed system stores /home separately.
chroot "$ROOT" apt-get clean
rm -rf "$ROOT/var/lib/apt/lists"/* "$ROOT/var/cache/apt/archives"/*
rm -rf "$ROOT/tmp"/* "$ROOT/var/tmp"/* "$ROOT/run"/*

ROOTFS_BYTES=$(du -sb --one-file-system \
    --exclude="$ROOT/home/*" --exclude="$ROOT/dev/*" \
    --exclude="$ROOT/proc/*" --exclude="$ROOT/sys/*" \
    --exclude="$ROOT/run/*" --exclude="$ROOT/tmp/*" \
    --exclude="$ROOT/var/tmp/*" "$ROOT" | awk '{print $1}')
MAX_ROOTFS_BYTES=$((ZARYA_MAX_ROOTFS_GIB * 1024 * 1024 * 1024))
[ "$ROOTFS_BYTES" -le "$MAX_ROOTFS_BYTES" ] || {
    echo "Root filesystem is ${ROOTFS_BYTES} bytes; exceeds the ${ZARYA_MAX_ROOTFS_GIB} GiB A/B slot payload limit." >&2
    exit 1
}
echo "==> Kubuntu rootfs size: $((ROOTFS_BYTES / 1024 / 1024 / 1024)) GiB (A/B slot: ${ZARYA_AB_SLOT_GIB} GiB)"

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
# The finished ISO contains the staging tree. Keep only the rootfs, which the
# release workflow may use to make a separately signed A/B update bundle.
rm -rf "$ISODIR"
