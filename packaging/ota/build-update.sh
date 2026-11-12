#!/usr/bin/env bash
# Create a signed, complete A/B root-slot update without staging a rootfs in the repo.
set -Eeuo pipefail

usage() {
    echo "Usage: $0 ROOTFS VERSION PRIVATE_KEY OUTDIR" >&2
    exit 2
}
[ "$#" -eq 4 ] || usage
ROOTFS="$(realpath "$1")"
VERSION="$2"
PRIVATE_KEY="$(realpath "$3")"
OUTDIR="$(realpath -m "$4")"
PUBKEY="$(cd "$(dirname "$0")" && pwd)/ota-public.pem"

[ -d "$ROOTFS" ] || { echo "Missing rootfs: $ROOTFS" >&2; exit 1; }
[ -s "$PRIVATE_KEY" ] || { echo "Missing OTA signing key: $PRIVATE_KEY" >&2; exit 1; }
[ -s "$PUBKEY" ] || { echo "Missing OTA public key: $PUBKEY" >&2; exit 1; }
[[ "$VERSION" =~ ^[0-9][A-Za-z0-9.+~-]*$ ]] || { echo "Invalid version: $VERSION" >&2; exit 1; }
for cmd in tar zstd openssl sha256sum python3; do
    command -v "$cmd" >/dev/null || { echo "Missing build tool: $cmd" >&2; exit 1; }
done

# Refuse to sign with a key which does not match the key pinned into the OS.
priv_pub=$(openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER | sha256sum | awk '{print $1}')
repo_pub=$(openssl pkey -pubin -in "$PUBKEY" -outform DER | sha256sum | awk '{print $1}')
[ "$priv_pub" = "$repo_pub" ] || { echo 'Signing key does not match packaging/ota/ota-public.pem' >&2; exit 1; }

mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"
TMPBASE="${RUNNER_TEMP:-${TMPDIR:-/var/tmp}}"
STAGE=$(mktemp -d "$TMPBASE/zarya-update.XXXXXX")
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT INT TERM

# Do not put mutable user data or live pseudo-filesystems into the slot image.
# /home is mounted from the separate data partition on installed systems.
(
    cd "$ROOTFS"
    tar --sort=name --mtime="@${SOURCE_DATE_EPOCH:-0}" --clamp-mtime \
        --numeric-owner --xattrs --acls --one-file-system \
        --exclude='./dev/*' --exclude='./proc/*' --exclude='./sys/*' \
        --exclude='./run/*' --exclude='./tmp/*' --exclude='./var/tmp/*' \
        --exclude='./var/cache/apt/*' --exclude='./var/lib/apt/lists/*' \
        --exclude='./home/*' --exclude='./boot/efi/*' \
        -cf - .
) | zstd -T0 -19 -q -o "$STAGE/rootfs.tar.zst"

ROOTFS_SHA256=$(sha256sum "$STAGE/rootfs.tar.zst" | awk '{print $1}')
ROOTFS_COMPRESSED_BYTES=$(stat -c '%s' "$STAGE/rootfs.tar.zst")
[ "$ROOTFS_COMPRESSED_BYTES" -le $((6 * 1024 * 1024 * 1024)) ] || {
    echo 'Compressed OTA payload exceeds the supported bundle limit.' >&2
    exit 1
}
ROOTFS_BYTES=$(du -sb --one-file-system \
    --exclude="$ROOTFS/home/*" --exclude="$ROOTFS/dev/*" \
    --exclude="$ROOTFS/proc/*" --exclude="$ROOTFS/sys/*" \
    --exclude="$ROOTFS/run/*" --exclude="$ROOTFS/tmp/*" \
    --exclude="$ROOTFS/var/tmp/*" "$ROOTFS" | awk '{print $1}')
[ "$ROOTFS_BYTES" -le $((7 * 1024 * 1024 * 1024)) ] || {
    echo 'The rootfs is too large for the fixed 8 GiB A/B slots.' >&2
    exit 1
}

python3 - "$VERSION" "$ROOTFS_SHA256" "$ROOTFS_COMPRESSED_BYTES" "$ROOTFS_BYTES" \
    > "$STAGE/manifest.json" <<'PY'
import json, re, sys
version, digest, compressed, uncompressed = sys.argv[1:]
if not re.fullmatch(r"[0-9][A-Za-z0-9.+~-]*", version):
    raise SystemExit("invalid version")
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit("invalid sha256")
manifest = {
    "format": 1,
    "version": version,
    "architecture": "amd64",
    "repository": "smells1k/zarya",
    "payload": "rootfs.tar.zst",
    "payload_sha256": digest,
    "payload_bytes": int(compressed),
    "rootfs_bytes": int(uncompressed),
}
json.dump(manifest, sys.stdout, sort_keys=True, separators=(",", ":"))
sys.stdout.write("\n")
PY

openssl dgst -sha256 -sign "$PRIVATE_KEY" \
    -out "$STAGE/manifest.sig" "$STAGE/manifest.json"
OUT="$OUTDIR/zarya-os-${VERSION}-amd64.zup"
tar --sort=name --owner=0 --group=0 --numeric-owner \
    -C "$STAGE" -cf "$OUT" manifest.json manifest.sig rootfs.tar.zst
(cd "$OUTDIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
ls -lh "$OUT" "$OUT.sha256"
printf '%s\n' "$OUT"
