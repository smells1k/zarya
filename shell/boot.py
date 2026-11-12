#!/usr/bin/env python3
"""Заря OS — загрузочная заставка и подготовка оборудования.

Рисует анимированную заставку прямо во фреймбуфер и параллельно грузит
драйверы ввода, звука и сеть. В конце передаёт эстафету оболочке (shell.py).
"""
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from draw import Canvas, circle_mask, mix, rounded_mask
import theme

STEPS = [
    ("Ядро Linux", "modprobe_done"),
    ("Определение оборудования", "hardware"),
    ("Драйверы ввода", "input"),
    ("Звук", "sound"),
    ("Сеть", "network"),
    ("Графическая оболочка", "shell"),
]


def log(msg):
    try:
        with open("/run/zarya/log/boot.log", "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def sh(cmd, timeout=25):
    try:
        return subprocess.run(cmd, shell=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode(
                                  "utf-8", "replace")
    except Exception as e:
        return "%s" % e


def modprobe_list(mods):
    ok = []
    for m in mods:
        r = subprocess.run(["modprobe", m], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            ok.append(m)
    return ok


class BootSplash:
    def __init__(self, scr):
        self.scr = scr
        self.W, self.H = scr.width, scr.height
        self.canvas = Canvas(self.W, self.H)
        self.s = max(0.75, min(1.7, self.W / 1280.0))
        self.state = {k: 0 for _, k in STEPS}   # 0 ждёт, 1 в работе, 2 готово
        self.t0 = time.time()
        self.message = ""
        self.draw()

    def draw(self):
        c = self.canvas
        W, H = self.W, self.H
        s = self.s
        # фон: вертикальный градиент + мягкое свечение
        yy = np.linspace(0, 1, H, dtype=np.float32)
        top = np.array([10, 12, 22], dtype=np.float32)
        bot = np.array([20, 18, 38], dtype=np.float32)
        grad = top[None, :] + (bot - top)[None, :] * (yy ** 1.3)[:, None]
        c.buf[:] = np.repeat(grad[:, None, :], W, axis=1).astype(np.uint8)
        cx, cy = W // 2, int(H * 0.34)
        for r, a, col in ((int(220 * s), 0.055, theme.ACCENTS["Аврора"][1]),
                          (int(150 * s), 0.07, theme.ACCENTS["Аврора"][0])):
            d = r * 2
            m = circle_mask(d)
            c.blend_mask(cx - r, cy - r, m, col, a)
        # логотип: круг + солнце
        lr = int(34 * s)
        c.circle(cx, cy, lr, theme.ACCENTS["Аврора"][1], 1.0)
        c.circle(cx - int(lr * 0.18), cy - int(lr * 0.20), int(lr * 0.62),
                 theme.ACCENTS["Аврора"][0], 1.0)
        # название
        c.text(cx, cy + int(56 * s), "ЗАРЯ", int(40 * s), theme.TEXT, bold=True, anchor="cc")
        c.text(cx, cy + int(90 * s), "живая система с флешки", int(13 * s),
               theme.TEXT_DIM, anchor="cc")
        # индикатор прогресса
        done = sum(1 for k in self.state if self.state[k] == 2)
        frac = done / float(len(STEPS))
        bw, bh = int(320 * s), int(4 * s)
        bx, by = cx - bw // 2, int(H * 0.60)
        c.rounded(bx, by, bw, bh, bh // 2, (255, 255, 255), 0.10)
        c.rounded(bx, by, max(bh, int(bw * frac)), bh, bh // 2,
                  theme.ACCENTS["Аврора"][0], 1.0)
        # список шагов
        y = by + int(28 * s)
        for name, key in STEPS:
            st = self.state[key]
            ix = bx
            if st == 2:
                c.circle(ix + int(5 * s), y + int(6 * s), int(5 * s),
                         theme.ACCENTS["Аврора"][0])
                c.circle(ix + int(5 * s), y + int(6 * s), int(2 * s), (12, 14, 22))
                col = theme.TEXT_DIM
            elif st == 1:
                c.ring(ix + int(5 * s), y + int(6 * s), int(5 * s), max(1, int(2 * s)),
                       theme.ACCENTS["Аврора"][0])
                col = theme.TEXT
            else:
                c.ring(ix + int(5 * s), y + int(6 * s), int(5 * s), 1,
                       (255, 255, 255), 0.15)
                col = theme.TEXT_MUTE
            c.text(ix + int(20 * s), y, name, int(12 * s), col, anchor="lt")
            y += int(21 * s)
        if self.message:
            c.text(cx, H - int(34 * s), self.message, int(11 * s),
                   theme.TEXT_MUTE, anchor="cc")
        c.text(cx, H - int(16 * s), "Заря OS · Debian/Ubuntu-подобное ядро Linux",
               int(10 * s), (90, 98, 120), anchor="cc")
        self.scr.flush(c.image())

    def step(self, key, state=1, message=None):
        self.state[key] = state
        if message:
            self.message = message
        self.draw()

    # ------------------------------------------------------------------
    def run(self):
        t = time.time()
        self.step("modprobe_done", 1, "проверка ядра…")
        for f in ("/proc", "/sys", "/dev"):
            os.makedirs(f, exist_ok=True)
        self.step("modprobe_done", 2)

        # --- оборудование -------------------------------------------------
        self.step("hardware", 1, "определение оборудования…")
        # намеренно НЕ вызываем "udevadm trigger": он автозагружает DRM-драйверы
        # (i915/amdgpu/bochs), которые отбирают /dev/fb0 у efifb/simplefb.
        sh("cat /sys/class/graphics/fb0/virtual_size 2>/dev/null >> /run/zarya/log/boot.log")
        time.sleep(0.15)
        self.step("hardware", 2)

        # --- ввод ---------------------------------------------------------
        self.step("input", 1, "клавиатура и мышь…")
        modprobe_list(["evdev", "usbhid", "hid_generic", "hid", "usbhid",
                       "psmouse", "atkbd", "serio_raw", "i8042", "uinput",
                       "hid_multitouch", "usb_common", "xhci_hcd", "ehci_pci",
                       "usb_storage", "uas", "virtio_input"])
        time.sleep(0.35)
        self.step("input", 2)

        # --- звук ---------------------------------------------------------
        self.step("sound", 1, "звуковая карта…")
        modprobe_list(["snd", "soundcore", "snd_hda_intel", "snd_hda_codec",
                       "snd_hda_codec_realtek", "snd_hda_codec_hdmi",
                       "snd_usb_audio", "snd_pcm", "snd_timer", "snd_intel8x0",
                       "snd_ens1371"])
        self.step("sound", 2)

        # --- сеть ---------------------------------------------------------
        self.step("network", 1, "поднимаю сеть…")
        modprobe_list(["e1000", "e1000e", "r8169", "virtio_net", "8139cp",
                       "8139too", "atl1c", "igb", "i40e", "realtek",
                       "usbnet", "cdc_ether", "rtl8152", "r8152",
                       "cfg80211", "mac80211", "iwlwifi", "iwlmvm",
                       "ath9k", "ath10k_core", "rndis_wlan", "rtl8xxxu",
                       "8188eu", "rtw88_core", "brcmfmac", "mt76"])
        for dev in sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []:
            if dev == "lo":
                sh("ip link set lo up")
                continue
            if os.path.exists("/sys/class/net/%s/wireless" % dev):
                # Wi-Fi: поднимаем интерфейс, подключение — вручную (nmcli нет)
                sh("ip link set %s up" % dev)
                log("wifi интерфейс: %s" % dev)
            else:
                sh("ip link set %s up" % dev)
        if os.path.exists("/bin/udhcpc") or os.path.exists("/sbin/udhcpc"):
            for dev in sorted(os.listdir("/sys/class/net")):
                if dev == "lo" or os.path.exists("/sys/class/net/%s/wireless" % dev):
                    continue
                sh("udhcpc -i %s -n -q -t 4 -T 2 >/dev/null 2>&1" % dev, timeout=12)
        sh("printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf")
        self.step("network", 2)

        # --- пользователь -------------------------------------------------
        sh("id zarya >/dev/null 2>&1 || (useradd -m -u 1000 -s /bin/bash -G "
           "sudo,audio,video,input,plugdev,netdev zarya && "
           "echo 'zarya:zarya' | chpasswd && "
           "echo 'zarya ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/zarya && "
           "chmod 440 /etc/sudoers.d/zarya)")
        sh("mkdir -p /home/zarya/Документы /home/zarya/Загрузки /home/zarya/Рабочий\\ стол "
           "/home/zarya/.config/zarya && chown -R zarya:zarya /home/zarya")
        sh("id root >/dev/null 2>&1 && (echo 'root:zarya' | chpasswd)")
        sh("chown -R zarya:zarya /home/zarya/.config 2>/dev/null")

        # --- оболочка -----------------------------------------------------
        self.step("shell", 1, "запускаю оболочку…")
        for i in range(6):
            self.draw()
            time.sleep(0.04)
        self.step("shell", 2, "готово")
        # Прячем курсор и чистим консоль через escape-последовательности:
        # отвязывать vtconsole нельзя — путь исчезает и консоль уже не вернуть.
        try:
            with open("/dev/console", "wb") as f:
                f.write(b"\033[?25l\033[2J\033[H")
        except Exception:
            pass
        log("готово за %.1f c" % (time.time() - t))


def main():
    os.makedirs("/run/zarya/log", exist_ok=True)
    try:
        from display import Screen
        scr = Screen()
    except Exception as e:
        log("нет фреймбуфера: %s" % e)
        # без экрана всё равно подготовим оборудование
        class Dummy:
            width, height = 1280, 720
            def flush(self, img):
                pass
            def blank(self):
                pass
        scr = Dummy()

    try:
        splash = BootSplash(scr)
        splash.run()
    except Exception as e:
        log("ошибка заставки: %s" % e)


if __name__ == "__main__":
    main()
