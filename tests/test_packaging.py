import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class KubuntuPackagingContractTests(unittest.TestCase):
    def setUp(self):
        self.limits = (ROOT / "packaging/ota/limits.sh").read_text()
        self.build = (ROOT / "packaging/build-iso.sh").read_text()
        self.installer = (ROOT / "packaging/install/zarya-install").read_text()
        self.ota_builder = (ROOT / "packaging/ota/build-update.sh").read_text()
        self.updater = (ROOT / "packaging/ota/zarya-update").read_text()
        self.boot = (ROOT / "boot/grub.cfg").read_text()
        self.ab_boot = (ROOT / "packaging/ota/grub-ab.cfg").read_text()
        self.release = (ROOT / ".github/workflows/release.yml").read_text()

    def limit(self, name):
        match = re.search(rf"^{name}=([A-Za-z0-9_-]+)$", self.limits, re.MULTILINE)
        self.assertIsNotNone(match, f"missing {name} in shared limits")
        return match.group(1)

    def test_kubuntu_resolute_and_plasma_wayland_are_the_default_desktop(self):
        self.assertIn("UBUNTU_SUITE=resolute", self.build)
        self.assertRegex(self.build, r"\bkubuntu-desktop\b")
        self.assertIn("/usr/share/wayland-sessions/plasma.desktop", self.build)
        self.assertIn("Session=plasma.desktop", self.build)
        self.assertNotIn("zarya-session", self.build)
        self.assertNotIn("nomodeset", self.boot)
        self.assertIn("xserver-xorg-input-libinput", self.build)
        self.assertIn("xserver-xorg-video-vmware", self.build)
        self.assertIn("open-vm-tools-desktop", self.build)
        self.assertIn("systemd-resolved", self.build)
        self.assertIn("systemctl enable systemd-resolved.service", self.build)
        self.assertIn("systemctl enable open-vm-tools.service", self.build)
        self.assertIn("ln -s ../run/systemd/resolve/stub-resolv.conf", self.build)

    def test_release_splits_iso_under_asset_limit_and_publishes_reassembly_checksums(self):
        self.assertIn("split --bytes=1900M --numeric-suffixes=0", self.release)
        self.assertIn('cat "${parts[@]}" | cmp - "$iso"', self.release)
        self.assertIn("JOIN_ISO.txt", self.release)
        self.assertIn('sha256sum -c $base.sha256', self.release)
        self.assertIn('zarya-os-$VERSION-amd64.iso.part', self.release)
        self.assertNotIn('sha256sum "zarya-os-$VERSION-amd64.iso" > SHA256SUMS', self.release)

    def test_installer_and_ota_share_the_capacity_contract(self):
        self.assertEqual(self.limit("ZARYA_AB_SLOT_GIB"), "20")
        self.assertEqual(self.limit("ZARYA_MAX_ROOTFS_GIB"), "18")
        self.assertEqual(self.limit("ZARYA_MAX_OTA_PAYLOAD_GIB"), "12")
        self.assertEqual(self.limit("ZARYA_MIN_DISK_GIB"), "64")
        self.assertEqual(self.limit("ZARYA_OTA_FORMAT"), "2")
        self.assertEqual(self.limit("ZARYA_OTA_BASE"), "ubuntu-resolute")
        self.assertIn("+${ZARYA_AB_SLOT_GIB}G", self.installer)
        self.assertIn("ZARYA_MIN_DISK_GIB * 1024", self.installer)
        self.assertIn("ZARYA_MAX_ROOTFS_GIB", self.ota_builder)
        self.assertIn("ZARYA_MAX_OTA_PAYLOAD_GIB", self.ota_builder)
        self.assertIn("ZARYA_MAX_ROOTFS_GIB", self.updater)
        self.assertIn("ZARYA_MAX_OTA_PAYLOAD_GIB", self.updater)

    def test_base_migration_uses_a_new_ota_manifest_and_plasma_confirmation(self):
        self.assertIn('"format": int(format_id)', self.ota_builder)
        self.assertIn('"base": base', self.ota_builder)
        self.assertIn('m.get("format") != int(expected_format)', self.updater)
        self.assertIn('m.get("base") != expected_base', self.updater)
        self.assertIn("--confirm-desktop", (ROOT / "packaging/ota/zarya-ota-init").read_text())
        self.assertIn("kwin_wayland", (ROOT / "packaging/ota/zarya-ota-init").read_text())
        self.assertIn("plasmashell", (ROOT / "packaging/ota/zarya-ota-init").read_text())
        self.assertIn("zarya-ota-desktop-ready.service", self.build)
        self.assertIn("root=LABEL=ZARYA_A ro zarya.slot=A quiet splash", self.ab_boot)


if __name__ == "__main__":
    unittest.main()
