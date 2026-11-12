#!/usr/bin/env python3
"""Zarya OS - desktop shell (own compositor, no X11/Wayland needed).

Runs directly on /dev/fb0 (framebuffer) and /dev/input/event*.
"""
import os
import sys
import time
import math
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image
from draw import (Canvas, paste_icon, rounded_mask, circle_mask, text_mask,
                  measure, mix, lighten, darken)
import theme
from theme import Theme
from display import Screen
from input import InputManager

VERSION = "1.0 «Рассвет»"
SETTINGS_PATH = os.path.expanduser("~/.config/zarya/settings.json")


# ---------------------------------------------------------------- helpers
def now_ts():
    return time.time()


def now_hms():
    return time.strftime("%H:%M")


def now_date():
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
    days = ["понедельник", "вторник", "среда", "четверг", "пятница",
            "суббота", "воскресенье"]
    t = time.localtime()
    return "%s, %d %s" % (days[t.tm_wday], t.tm_mday, months[t.tm_mon - 1])


def read_cpu():
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(v) for v in parts[1:]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        return idle, total
    except Exception:
        return 0, 0


def meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])
    except Exception:
        pass
    return info


# ---------------------------------------------------------------- window
class Window:
    def __init__(self, app, x, y, w, h, title=None, icon=None):
        self.app = app
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)
        self.title = title or (app.name if app else "Окно")
        self.icon = icon or (app.icon if app else "term")
        self.minimized = False
        self.maximized = False
        self.state_before_max = None
        self.zoom = 0.0            # 0..1 open animation
        self.closing = False
        self.drag = None
        self.resize_edge = None
        self.app.window = self
        self.title_h = 38

    # geometry helpers
    @property
    def rect(self):
        return (self.x, self.y, self.w, self.h)

    def content_rect(self):
        return (self.x, self.y + self.title_h, self.w, self.h - self.title_h)


# ---------------------------------------------------------------- shell
class Zarya:
    def __init__(self):
        self.theme = Theme()
        self.scr = Screen()
        self.W, self.H = self.scr.width, self.scr.height
        self.canvas = Canvas(self.W, self.H)
        self.t = self.theme
        self.wallpaper = theme.render_wallpaper(
            1280, 800, self.theme.wallpaper_name,
            (self.theme.accent, self.theme.accent2))
        self.windows = []
        self.focus = None
        self.mouse = (self.W // 2, self.H // 2)
        self.down = None
        self.dragging = None
        self.launcher_open = False
        self.power_open = False
        self.locked = True
        self.lock_anim = 0.0
        self.toasts = []
        self.clock = now_hms()
        self.date = now_date()
        self.dirty = True
        self.running = True
        self.layout = "us"
        self.show_fps = os.environ.get("ZARYA_FPS") == "1"
        self.fps = 0.0
        self._frame_times = []
        self._last_click = (0, 0, 0)
        self._cpu_prev = read_cpu()
        self.cpu_load = 0.0
        self.cpu_hist = [0.0] * 60
        self.mem = {}
        self.mem_t = 0
        self.uptime0 = time.time()
        self._hover = None
        self.layout_scale = max(0.78, min(1.6, self.W / 1280.0))
        self.theme.scale = self.layout_scale
        self.load_settings()
        self.wallpaper = theme.render_wallpaper(
            int(self.W / 1.4), int(self.H / 1.4), self.theme.wallpaper_name,
            (self.theme.accent, self.theme.accent2))
        self.input = InputManager(self.on_event, self.W, self.H)
        self.status = {}
        self.lock_time = time.time()

    # ---------------------------------------------------------- settings
    def load_settings(self):
        try:
            with open(SETTINGS_PATH) as f:
                s = json.load(f)
            if s.get("accent") in theme.ACCENTS:
                self.theme.set_accent(s["accent"])
            if s.get("wallpaper") in theme.WALLPAPERS:
                self.theme.wallpaper_name = s["wallpaper"]
            self.theme.scale = float(s.get("scale", self.layout_scale)) * self.layout_scale
        except Exception:
            pass

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w") as f:
                json.dump({"accent": self.theme.accent_name,
                           "wallpaper": self.theme.wallpaper_name,
                           "scale": self.theme.scale / self.layout_scale}, f)
        except Exception:
            pass

    def set_accent(self, name):
        self.theme.set_accent(name)
        self.wallpaper = theme.render_wallpaper(
            int(self.W / 1.4), int(self.H / 1.4), self.theme.wallpaper_name,
            (self.theme.accent, self.theme.accent2))
        self.save_settings()
        self.dirty = True

    def set_wallpaper(self, name):
        self.theme.wallpaper_name = name
        self.wallpaper = theme.render_wallpaper(
            int(self.W / 1.4), int(self.H / 1.4), self.theme.wallpaper_name,
            (self.theme.accent, self.theme.accent2))
        self.save_settings()
        self.dirty = True

    # ---------------------------------------------------------- geometry
    def bar_h(self):
        return int(40 * self.theme.scale)

    def dock_h(self):
        return int(74 * self.theme.scale)

    def dock_rect(self):
        dh = self.dock_h()
        n = len(self.dock_items())
        iw = int(54 * self.theme.scale)
        gap = int(16 * self.theme.scale)
        pad = int(18 * self.theme.scale)
        w = n * iw + (n - 1) * gap + pad * 2
        h = dh
        x = (self.W - w) // 2
        y = self.H - h - int(10 * self.theme.scale)
        return (x, y, w, h, iw, gap, pad)

    def dock_items(self):
        return [("Терминал", "term"), ("Файлы", "files"), ("Заметки", "note"),
                ("Система", "cpu"), ("Настройки", "gear"), ("Справка", "info")]

    # ---------------------------------------------------------- windows
    def open_app(self, name):
        from apps import make_app
        for w in self.windows:
            if w.app.app_id == name:
                w.minimized = False
                self.focus_window(w)
                return
        n = len(self.windows)
        sw, sh = int(self.W * 0.72), int(self.H * 0.70)
        x = int((self.W - sw) / 2 + (n % 3 - 1) * 26 * self.theme.scale)
        y = int(self.bar_h() + (self.H - self.bar_h() - self.dock_h() - sh) / 2 + (n % 3 - 1) * 20)
        app = make_app(name, self)
        w = Window(app, x, y, sw, sh)
        self.windows.append(w)
        self.focus_window(w)
        self.toast("Открыто: " + w.title)
        return w

    def focus_window(self, w):
        if w in self.windows:
            self.windows.remove(w)
            self.windows.append(w)
        self.focus = w
        self.dirty = True

    def close_window(self, w):
        w.closing = True
        w.app.close()

    def _remove_window(self, w):
        if w in self.windows:
            self.windows.remove(w)
        if self.focus is w:
            self.focus = self.windows[-1] if self.windows else None
        try:
            w.app.close()
        except Exception:
            pass

    def maximize(self, w):
        if w.maximized:
            w.x, w.y, w.w, w.h = w.state_before_max
            w.maximized = False
        else:
            w.state_before_max = (w.x, w.y, w.w, w.h)
            w.x = 0
            w.y = self.bar_h()
            w.w = self.W
            w.h = self.H - self.bar_h() - self.dock_h() - int(14 * self.theme.scale)
            w.maximized = True
        w.app.on_resize(w.w, w.h - w.title_h)
        self.dirty = True

    # ---------------------------------------------------------- toasts
    def toast(self, text, icon="info"):
        self.toasts = [{"text": text, "icon": icon, "born": time.time()}]

    # ---------------------------------------------------------- events
    def on_event(self, ev):
        typ = ev["type"]
        if typ == "move":
            self.mouse = (ev["x"], ev["y"])
            self.update_hover()
            self.dirty = True
            return
        if self.locked:
            if typ in ("down", "key"):
                self.locked = False
                self.lock_time = time.time()
                self.dirty = True
            return
        if typ == "down":
            self.on_mouse_down(ev)
        elif typ == "up":
            self.on_mouse_up(ev)
        elif typ == "wheel":
            self.on_wheel(ev)
        elif typ == "key":
            self.on_key(ev)
        elif typ == "layout":
            self.layout = ev["layout"]
            self.theme.layout = ev["layout"]
            self.dirty = True
        self.dirty = True

    def update_hover(self):
        bx, by, bw, bh = self.dock_rect()
        hover = None
        if by <= self.mouse[1] <= by + bh:
            hover = "dock"
        elif self.mouse[1] <= self.bar_h():
            hover = "bar"
        if hover != self._hover:
            self._hover = hover

    def window_at(self, x, y):
        for w in reversed(self.windows):
            if w.minimized:
                continue
            if w.x <= x <= w.x + w.w and w.y <= y <= w.y + w.h:
                return w
        return None

    def on_mouse_down(self, ev):
        x, y = ev["x"], ev["y"]
        btn = ev["btn"]
        if self.power_open:
            act = self.power_hit(x, y)
            if act == "reboot":
                self.power_open = False
                self.do_power("reboot")
            elif act == "shutdown":
                self.power_open = False
                self.do_power("shutdown")
            elif act == "lock":
                self.power_open = False
                self.locked = True
                self.lock_time = time.time()
            else:
                self.power_open = False
            self.dirty = True
            return
        if self.launcher_open:
            act = self.launcher_hit(x, y)
            if act:
                self.launcher_open = False
                self.open_app(act)
            elif not self._in_launcher(x, y):
                self.launcher_open = False
            self.dirty = True
            return
        # top bar
        if y <= self.bar_h():
            if x < int(150 * self.theme.scale):
                self.launcher_open = not self.launcher_open
                self.launcher_query = ""
            elif x > self.W - int(46 * self.theme.scale):
                self.power_open = not self.power_open
            self.dirty = True
            return
        # dock
        bx, by, bw, bh, iw, gap, pad = self.dock_rect()
        if by <= y <= by + bh and bx <= x <= bx + bw:
            idx = int((x - bx - pad + gap // 2) / (iw + gap))
            items = self.dock_items()
            if 0 <= idx < len(items):
                name = items[idx][0]
                target = None
                for w in self.windows:
                    if w.app.app_id == name:
                        target = w
                if target and target is self.focus and not target.minimized:
                    target.minimized = True
                    self.focus = None
                elif target:
                    target.minimized = False
                    self.focus_window(target)
                else:
                    self.open_app(name)
            self.dirty = True
            return
        # window under cursor
        w = self.window_at(x, y)
        if w is None:
            self.focus = None
            self.dirty = True
            return
        self.focus_window(w)
        if y <= w.y + w.title_h:
            # title bar buttons
            b = self.title_button_at(w, x, y)
            if b == "close":
                self.close_window(w)
            elif b == "max":
                self.maximize(w)
            elif b == "min":
                w.minimized = True
                self.focus = None
            else:
                self.dragging = {"win": w, "dx": x - w.x, "dy": y - w.y,
                                 "ox": w.x, "oy": w.y,
                                 "was_max": w.maximized}
                self.last_pos = (x, y)
                # double click => maximize
                t = time.time()
                if t - self._last_click[2] < 0.4 and \
                        abs(x - self._last_click[0]) < 6 and abs(y - self._last_click[1]) < 6:
                    self.maximize(w)
                    self.dragging = None
                self._last_click = (x, y, t)
            self.dirty = True
            return
        # resize edges (bottom-right corner + right edge)
        if w is not None and not w.maximized:
            if x > w.x + w.w - 14 and y > w.y + w.h - 14:
                self.resizing = {"win": w, "ox": x, "oy": y, "ow": w.w, "oh": w.h}
                self.dirty = True
                return
            if y > w.y + w.h - 8:
                self.resizing = {"win": w, "oy": y, "oh": w.h, "vertical": True,
                                 "ox": x, "ow": w.w}
                self.dirty = True
                return
        # app content
        cx, cy = w.x, w.y + w.title_h
        w.app.on_mouse("down", btn, x - cx, y - cy, ev)
        self.dirty = True

    def on_mouse_up(self, ev):
        if self.dragging:
            w = self.dragging["win"]
            if self.dragging.get("was_max"):
                # restore from maximized when dragging away
                pass
            self.dragging = None
            self.dirty = True
        if getattr(self, "resizing", None):
            self.resizing = None
            self.dirty = True
        for w in self.windows:
            if w.minimized or w.app is None:
                continue
            cx, cy = w.x, w.y + w.title_h
            inside = w.x <= ev["x"] <= w.x + w.w and w.y <= ev["y"] <= w.y + w.h
            if inside:
                w.app.on_mouse("up", ev["btn"], ev["x"] - cx, ev["y"] - cy, ev)
        self.dirty = True

    def on_wheel(self, ev):
        w = self.window_at(ev["x"], ev["y"])
        if w:
            cx, cy = w.x, w.y + w.title_h
            w.app.on_mouse("wheel", "wheel", ev["x"] - cx, ev["y"] - cy, ev)
        self.dirty = True

    def title_button_at(self, w, x, y):
        s = self.theme.scale
        bw = int(30 * s)
        gap = int(6 * s)
        right = w.x + w.w - int(12 * s)
        by = w.y + self.title_h // 2
        # order from right: close, max, min
        for i, name in enumerate(("close", "max", "min")):
            bx = right - i * (bw + gap) - bw
            if bx <= x <= bx + bw and by - bw // 2 <= y <= by + bw // 2:
                return name
        return None

    # ------------------------------------------------------------- keys
    def on_key(self, ev):
        ch = ev.get("ch")
        code = ev.get("code")
        mods = ev.get("mods", {})
        ctrl, alt, shift = mods.get("ctrl"), mods.get("alt"), mods.get("shift")
        # global shortcuts
        if ctrl and alt:
            if code == 20:      # T
                self.open_app("Терминал")
                return
            if code == 33:      # F
                self.open_app("Файлы")
                return
            if code == 49:      # N
                self.open_app("Заметки")
                return
        if alt and code == 15:  # Tab
            self.cycle_windows()
            return
        if alt and code == 62:  # F4
            if self.focus:
                self.close_window(self.focus)
            return
        if code == 59 and not ctrl:      # F1
            self.open_app("Справка")
            return
        if code == 60:                    # F2
            self.open_app("Настройки")
            return
        if code == 61:                    # F3
            self.open_app("Файлы")
            return
        if code == 68 and not ctrl and not alt:   # F10 launcher
            self.launcher_open = not self.launcher_open
            self.launcher_query = getattr(self, "launcher_query", "")
            return
        if self.power_open and ch == "\x1b":
            self.power_open = False
            return
        if self.launcher_open:
            self.on_launcher_key(ev)
            return
        if self.focus is None:
            return
        self.focus.app.on_key(ch, code, mods)

    def cycle_windows(self):
        vis = [w for w in self.windows if not w.minimized]
        if len(vis) < 2:
            return
        self.focus_window(vis[0])

    # ------------------------------------------------------------ launcher
    def launcher_hit(self, x, y):
        gx, gy, cw, chh, cols = self.launcher_geom()
        items = self.launcher_items()
        for i, (name, icon, desc) in enumerate(items):
            cx = gx + (i % cols) * cw
            cy = gy + (i // cols) * chh
            if cx <= x <= cx + cw - 10 and cy <= y <= cy + chh - 10:
                return name
        return None

    def _in_launcher(self, x, y):
        pw, ph = self.launcher_size()
        px, py = (self.W - pw) // 2, int(self.bar_h() + 26 * self.theme.scale)
        return px - 20 <= x <= px + pw + 20 and py - 20 <= y <= py + ph + 20

    def launcher_items(self):
        return [("Терминал", "term", "Командная строка bash"),
                ("Файлы", "files", "Проводник по файлам"),
                ("Заметки", "note", "Быстрые текстовые заметки"),
                ("Система", "cpu", "Монитор ресурсов"),
                ("Настройки", "gear", "Оформление и поведение"),
                ("Справка", "info", "О системе и горячих клавишах")]

    def launcher_size(self):
        s = self.theme.scale
        return int(min(self.W * 0.72, 760 * s)), int(min(self.H * 0.58, 460 * s))

    def launcher_geom(self):
        pw, ph = self.launcher_size()
        s = self.theme.scale
        pad = int(22 * s)
        search_h = int(46 * s)
        cols = 3
        gx = int(pad)
        gy = int(pad + search_h + 16 * s)
        cw = (pw - pad * 2) // cols
        chh = int((ph - gy - pad) / 2)
        return gx, gy, cw, chh, cols

    def on_launcher_key(self, ev):
        ch = ev.get("ch")
        code = ev.get("code")
        q = getattr(self, "launcher_query", "")
        if not isinstance(q, str):
            q = ""
        if ch == "\x1b":
            self.launcher_open = False
        elif ch == "\n":
            matches = [i for i in self.launcher_items() if q.lower() in i[0].lower()]
            if matches:
                self.launcher_open = False
                self.open_app(matches[0][0])
        elif ch == "\b":
            self.launcher_query = q[:-1]
        elif ch and len(ch) == 1 and ch >= " ":
            self.launcher_query = q + ch
        self.dirty = True

    # ------------------------------------------------------------- power
    def power_hit(self, x, y):
        px, py, pw, ph = self.power_geom()
        rows = [("lock", "Заблокировать"), ("reboot", "Перезагрузить"),
                ("shutdown", "Выключить")]
        for i, (act, _lbl) in enumerate(rows):
            ry = py + int(52 * self.theme.scale) + i * int(46 * self.theme.scale)
            if px <= x <= px + pw and ry <= y <= ry + int(40 * self.theme.scale):
                return act
        return None

    def power_geom(self):
        s = self.theme.scale
        pw, ph = int(260 * s), int(52 * s + 3 * 46 * s + 14 * s)
        px = self.W - pw - int(10 * s)
        py = self.bar_h() + int(8 * s)
        return px, py, pw, ph

    def do_power(self, action):
        self.toast("Завершение работы…" if action == "shutdown" else "Перезагрузка…")
        self.render()
        self.scr.flush(self.canvas.image())
        try:
            os.makedirs("/run/zarya", exist_ok=True)
            with open("/run/zarya/quit", "w") as f:
                f.write(action)
        except Exception:
            pass
        time.sleep(0.6)
        self.running = False

    # ------------------------------------------------------------- frame
    def tick(self):
        self.clock = now_hms()
        self.date = now_date()
        idle, total = read_cpu()
        di, dt = idle - self._cpu_prev[0], total - self._cpu_prev[1]
        self._cpu_prev = (idle, total)
        if dt > 0:
            load = 100.0 * (1 - di / dt)
            self.cpu_load = self.cpu_load * 0.6 + load * 0.4
        self.cpu_hist.append(self.cpu_load)
        self.cpu_hist = self.cpu_hist[-90:]
        if time.time() - self.mem_t > 2:
            self.mem = meminfo()
            self.mem_t = time.time()
        for w in self.windows:
            w.zoom = min(1.0, w.zoom + 0.18)
            if w.app:
                w.app.tick()
        for w in [w for w in self.windows if w.closing]:
            self._remove_window(w)
        self.toasts = [t for t in self.toasts if time.time() - t["born"] < 2.2]
        if self.locked:
            self.lock_anim = min(1.0, self.lock_anim + 0.08)

    # ------------------------------------------------------------- draw
    def render(self):
        c = self.canvas
        s = self.theme.scale
        # wallpaper
        if getattr(self, "_wall_arr", None) is None or \
                self._wall_src is not self.wallpaper:
            wp = self.wallpaper
            if wp.size != (self.W, self.H):
                wp = wp.resize((self.W, self.H), Image.BILINEAR)
            self._wall_arr = np.asarray(wp, dtype=np.uint8)
            self._wall_src = self.wallpaper
        c.buf[:] = self._wall_arr
        if self.locked:
            self.draw_lock(c)
        else:
            self.draw_desktop(c)
        self.draw_cursor(c)

    def draw_desktop(self, c):
        s = self.theme.scale
        # ---- windows
        for w in self.windows:
            if w.minimized:
                continue
            self.draw_window(c, w)
        # dragging overlay is drawn inside draw_window (position updated)
        self.draw_topbar(c)
        self.draw_dock(c)
        if self.launcher_open:
            self.draw_launcher(c)
        if self.power_open:
            self.draw_power(c)
        self.draw_toasts(c)

    # ------------------------------------------------------- window chrome
    def draw_window(self, c, w):
        s = self.theme.scale
        if w.zoom < 1.0:
            e = 1 - (1 - w.zoom) ** 3
        else:
            e = 1.0
        x, y, ww, wh = w.x, w.y, w.w, w.h
        if e < 1.0:
            cx, cy = x + ww / 2, y + wh / 2
            ww2, wh2 = max(20, int(ww * (0.86 + 0.14 * e))), max(20, int(wh * (0.86 + 0.14 * e)))
            x, y = int(cx - ww2 / 2), int(cy - wh2 / 2) - int(14 * (1 - e))
            ww, wh = ww2, wh2
        focused = (self.focus is w)
        radius = int(14 * s)
        # shadow
        c.shadow(int(x), int(y + 6 * s), int(ww), int(wh), radius,
                 blur=int(16 * s), alpha=0.55 if focused else 0.35)
        # body
        c.rounded(int(x), int(y), int(ww), int(wh), radius, theme.PANEL, 0.985)
        c.border(int(x), int(y), int(ww), int(wh), radius,
                 theme.STROKE if focused else (38, 44, 60), int(max(1, s)))
        # title bar
        th = w.title_h
        c.clip = (int(x), int(y), int(x + ww), int(y + wh))
        c.rounded(int(x), int(y), int(ww), int(th + radius), radius, theme.PANEL_HI, 1.0)
        c.fill(int(x), int(y + th - radius // 2), int(ww), radius // 2 + 1, theme.PANEL_HI)
        c.fill(int(x), int(y + th), int(ww), int(max(1, s)), theme.STROKE)
        # icon + title
        isz = int(18 * s)
        paste_icon(c, w.icon, int(x + 14 * s), int(y + th // 2 - isz // 2), isz,
                   self.theme.accent if focused else theme.TEXT_DIM)
        c.text(int(x + 14 * s + isz + 8 * s), int(y + th // 2), w.title,
               self.t.fs(14), theme.TEXT if focused else theme.TEXT_DIM,
               bold=True, anchor="lc")
        # buttons
        bw = int(30 * s)
        gap = int(6 * s)
        right = int(x + ww - 12 * s)
        for i, (name, col) in enumerate((("close", (235, 110, 110)),
                                         ("max", theme.TEXT_DIM),
                                         ("min", theme.TEXT_DIM))):
            bx = right - i * (bw + gap) - bw
            by = int(y + th // 2 - bw // 2)
            hov = self._mouse_in(bx, by, bw, bw)
            if hov:
                c.rounded(bx, by, bw, bw, int(8 * s),
                          (58, 40, 46) if name == "close" else theme.STROKE, 0.9)
            paste_icon(c, name, int(bx + bw * 0.28), int(by + bw * 0.28),
                       int(bw * 0.44), col if name == "close" else theme.TEXT_DIM)
        # content
        cx0, cy0 = int(x), int(y + th)
        cw0, ch0 = int(ww), int(wh - th)
        c.clip = (cx0 + 1, cy0, cx0 + cw0 - 1, cy0 + ch0 - 1)
        saved = (c.ox, c.oy)
        c.ox, c.oy = cx0, cy0
        try:
            w.app.draw(c, cw0, ch0, focused)
        except Exception as ex:
            c.text(10, 10, "Ошибка приложения: %s" % ex, 12, (255, 120, 120))
        c.ox, c.oy = saved
        c.clip = None

    def _mouse_in(self, x, y, w, h):
        mx, my = self.mouse
        return x <= mx <= x + w and y <= my <= y + h

    # ------------------------------------------------------------ top bar
    def draw_topbar(self, c):
        s = self.theme.scale
        h = self.bar_h()
        # translucent bar
        band = c.region(0, 0, self.W, h)
        band.buf[:] = (band.buf.astype(np.float32) * 0.55 +
                       np.array(theme.BG, dtype=np.float32) * 0.45).astype(np.uint8)
        c.paste_canvas(band, 0, 0)
        c.fill(0, h - 1, self.W, 1, (0, 0, 0), 0.35)
        # launcher button: logo pill
        pill_w = int(96 * s)
        hot_pill = self._mouse_in(0, 0, pill_w, h)
        if hot_pill or self.launcher_open:
            c.rounded(int(6 * s), int(5 * s), pill_w - int(12 * s), h - int(10 * s),
                      int(9 * s), (255, 255, 255), 0.10)
        lx = int(16 * s)
        lr = int(11 * s)
        c.blend_mask(lx, h // 2 - lr, circle_mask(int(lr * 2)), self.theme.accent2, 1.0)
        c.blend_mask(lx + int(lr * 0.5) - 1, h // 2 - int(lr * 0.72),
                     circle_mask(int(lr * 1.05)), self.theme.accent, 1.0)
        c.text(lx + lr * 2 + int(10 * s), h // 2, "заря", self.t.fs(17),
               theme.TEXT, bold=True, anchor="lc")
        # window title / hint
        label = self.focus.title if self.focus else "Рабочий стол"
        c.text(int(112 * s), h // 2, label, self.t.fs(12), theme.TEXT_MUTE, anchor="lc")
        # clock centered
        c.text(self.W // 2, h // 2 - int(7 * s), self.clock, self.t.fs(15),
               theme.TEXT, bold=True, anchor="cc")
        c.text(self.W // 2, h // 2 + int(8 * s), self.date, self.t.fs(10),
               theme.TEXT_MUTE, anchor="cc")
        # right indicators
        rx = self.W - int(16 * s)
        # power button
        psz = int(18 * s)
        hot_pw = self._mouse_in(rx - int(34 * s), 0, int(40 * s), h)
        if hot_pw or self.power_open:
            c.rounded(rx - int(30 * s), int(5 * s), int(34 * s), h - int(10 * s),
                      int(9 * s), (255, 255, 255), 0.10)
        paste_icon(c, "power", rx - psz - int(4 * s), h // 2 - psz // 2, psz,
                   (255, 170, 170) if (hot_pw or self.power_open) else (235, 140, 140))
        rx -= psz + int(26 * s)
        # layout indicator
        lay = "РУС" if self.layout == "ru" else "ENG"
        c.rounded(rx - int(40 * s), h // 2 - int(11 * s), int(40 * s), int(22 * s),
                  int(6 * s), theme.STROKE, 0.55)
        c.text(rx - int(20 * s), h // 2, lay, self.t.fs(10), theme.TEXT_DIM, anchor="cc")
        rx -= int(54 * s)
        # cpu + mem
        mem = self.mem
        total = mem.get("MemTotal", 1) / 1048576.0
        avail = mem.get("MemAvailable", 0) / 1048576.0
        used = max(0.0, total - avail)
        for icon, val in (("cpu", "%d%%" % int(self.cpu_load)),
                          ("disk", "%.1f ГБ" % used)):
            isz = int(15 * s)
            paste_icon(c, icon, rx - int(60 * s), h // 2 - isz // 2, isz, theme.TEXT_DIM)
            c.text(rx - int(40 * s), h // 2, val, self.t.fs(11), theme.TEXT_DIM, anchor="lc")
            rx -= int(64 * s)

    # --------------------------------------------------------------- dock
    def draw_dock(self, c):
        s = self.theme.scale
        bx, by, bw, bh, iw, gap, pad = self.dock_rect()
        c.shadow(bx, by + int(4 * s), bw, bh, int(20 * s), blur=int(16 * s), alpha=0.55)
        c.rounded(bx, by, bw, bh, int(20 * s), theme.PANEL, 0.82)
        c.border(bx, by, bw, bh, int(20 * s), (255, 255, 255), int(max(1, s)), 0.08)
        items = self.dock_items()
        mx, my = self.mouse
        for i, (name, icon) in enumerate(items):
            ix = bx + pad + i * (iw + gap)
            iy = by + int(10 * s)
            isz = int(34 * s)
            hot = ix - 4 <= mx <= ix + iw + 4 and by <= my <= by + bh
            running = any(w.app.app_id == name for w in self.windows)
            if hot:
                c.rounded(ix - int(2 * s), iy - int(6 * s), iw + int(4 * s),
                          isz + int(12 * s), int(12 * s), (255, 255, 255), 0.10)
            col = (255, 255, 255) if hot else theme.TEXT_DIM
            paste_icon(c, icon, ix + (iw - isz) // 2, iy, isz, col)
            if running:
                c.circle(ix + iw / 2, by + bh - int(7 * s), int(2.6 * s),
                         self.theme.accent, 1.0)

    # ----------------------------------------------------------- launcher
    def draw_launcher(self, c):
        s = self.theme.scale
        # dim background
        c.fill(0, 0, self.W, self.H, (4, 6, 12), 0.55)
        pw, ph = self.launcher_size()
        px, py = (self.W - pw) // 2, int(self.bar_h() + 26 * s)
        c.shadow(px, py + int(6 * s), pw, ph, int(18 * s), blur=int(22 * s), alpha=0.6)
        c.rounded(px, py, pw, ph, int(18 * s), theme.PANEL, 0.97)
        c.border(px, py, pw, ph, int(18 * s), (255, 255, 255), int(max(1, s)), 0.10)
        # search field
        pad = int(22 * s)
        sh = int(46 * s)
        c.rounded(px + pad, py + pad, pw - pad * 2, sh, int(12 * s), theme.BG_ALT, 1.0)
        c.border(px + pad, py + pad, pw - pad * 2, sh, int(12 * s), theme.STROKE, 1)
        q = getattr(self, "launcher_query", "")
        paste_icon(c, "files", px + pad + int(14 * s), py + pad + int(14 * s),
                   int(18 * s), theme.TEXT_MUTE)
        if q:
            c.text(px + pad + int(44 * s), py + pad + sh // 2, q, self.t.fs(15),
                   theme.TEXT, anchor="lc")
        else:
            c.text(px + pad + int(44 * s), py + pad + sh // 2,
                   "Поиск приложений…  (F10 — открыть/закрыть)", self.t.fs(14),
                   theme.TEXT_MUTE, anchor="lc")
        # items grid
        gx, gy, cw, chh, cols = self.launcher_geom()
        ql = q.lower() if isinstance(q, str) else ""
        for i, (name, icon, desc) in enumerate(self.launcher_items()):
            if ql and ql not in name.lower():
                continue
            cx = px + gx + (i % cols) * cw
            cy = py + gy + (i // cols) * chh
            hot = self._mouse_in(cx, cy, cw - 10, chh - 10)
            if hot:
                c.rounded(cx, cy, cw - int(10 * s), chh - int(10 * s), int(12 * s),
                          (255, 255, 255), 0.08)
            isz = int(34 * s)
            paste_icon(c, icon, cx + int(16 * s), cy + int(14 * s), isz,
                       self.theme.accent if hot else theme.TEXT_DIM)
            c.text(cx + int(16 * s), cy + int(14 * s) + isz + int(8 * s), name,
                   self.t.fs(14), theme.TEXT, bold=True)
            c.text(cx + int(16 * s), cy + int(14 * s) + isz + int(26 * s), desc,
                   self.t.fs(11), theme.TEXT_MUTE)

    # ------------------------------------------------------------- power
    def draw_power(self, c):
        s = self.theme.scale
        px, py, pw, ph = self.power_geom()
        c.fill(0, 0, self.W, self.H, (4, 6, 12), 0.3)
        c.shadow(px, py + int(4 * s), pw, ph, int(14 * s), blur=int(16 * s), alpha=0.6)
        c.rounded(px, py, pw, ph, int(14 * s), theme.PANEL, 0.98)
        c.border(px, py, pw, ph, int(14 * s), (255, 255, 255), 1, 0.10)
        c.text(px + int(16 * s), py + int(16 * s), "Питание", self.t.fs(12),
               theme.TEXT_MUTE, bold=True)
        rows = [("lock", "Заблокировать", "eye"),
                ("reboot", "Перезагрузить", "refresh"),
                ("shutdown", "Выключить", "power")]
        for i, (act, lbl, icon) in enumerate(rows):
            ry = py + int(52 * s) + i * int(46 * s)
            hot = self._mouse_in(px + int(8 * s), ry, pw - int(16 * s), int(40 * s))
            if hot:
                c.rounded(px + int(8 * s), ry, pw - int(16 * s), int(40 * s),
                          int(9 * s), (255, 255, 255), 0.08)
            col = (240, 140, 140) if act == "shutdown" else theme.TEXT
            paste_icon(c, icon, px + int(20 * s), ry + int(10 * s), int(20 * s), col)
            c.text(px + int(50 * s), ry + int(20 * s), lbl, self.t.fs(13),
                   col, anchor="lc")

    # ------------------------------------------------------------ toasts
    def draw_toasts(self, c):
        s = self.theme.scale
        y = self.H - self.dock_h() - int(88 * s)
        for i, t in enumerate(reversed(self.toasts)):
            age = time.time() - t["born"]
            a = min(1.0, age * 8) * (1.0 if age < 1.4 else max(0.0, (2.2 - age) / 0.8))
            tw = int(min(self.W * 0.5, 320 * s))
            th = int(42 * s)
            x = self.W - tw - int(18 * s)
            yy = y - i * (th + int(8 * s))
            c.shadow(x, yy, tw, th, int(10 * s), blur=int(10 * s), alpha=0.4 * a)
            c.rounded(x, yy, tw, th, int(10 * s), theme.PANEL_HI, 0.96 * a)
            c.border(x, yy, tw, th, int(10 * s), (255, 255, 255), 1, 0.10 * a)
            paste_icon(c, t["icon"], x + int(12 * s), yy + int(12 * s),
                       int(18 * s), self.theme.accent, a)
            c.text(x + int(40 * s), yy + th // 2, t["text"], self.t.fs(12),
                   theme.TEXT, anchor="lc", alpha=a)

    # -------------------------------------------------------------- lock
    def draw_lock(self, c):
        s = self.theme.scale
        t = time.localtime()
        c.fill(0, 0, self.W, self.H, (5, 7, 14), 0.55)
        # card
        cw, chh = int(self.W * 0.30), int(self.H * 0.46)
        x, y = (self.W - cw) // 2, (self.H - chh) // 2
        c.shadow(x, y + int(8 * s), cw, chh, int(22 * s), blur=int(26 * s), alpha=0.6)
        c.rounded(x, y, cw, chh, int(22 * s), theme.PANEL, 0.94)
        c.border(x, y, cw, chh, int(22 * s), (255, 255, 255), 1, 0.12)
        # logo
        lr = int(26 * s)
        c.blend_mask(x + cw // 2 - lr, y + int(34 * s), circle_mask(int(lr * 2)),
                     self.theme.accent2, 1.0)
        c.blend_mask(x + cw // 2 - int(lr * 0.5), y + int(34 * s) + int(lr * 0.28),
                     circle_mask(int(lr * 1.05)), self.theme.accent, 1.0)
        c.text(x + cw // 2, y + int(105 * s), time.strftime("%H:%M"), int(58 * s),
               theme.TEXT, bold=True, anchor="cc")
        c.text(x + cw // 2, y + int(155 * s), self.date, self.t.fs(14),
               theme.TEXT_DIM, anchor="cc")
        c.text(x + cw // 2, y + int(190 * s), "Заря OS · %s" % VERSION, self.t.fs(12),
               theme.TEXT_MUTE, anchor="cc")
        c.text(x + cw // 2, y + int(218 * s), self.input.status(), self.t.fs(9),
               theme.TEXT_MUTE, anchor="cc")
        # button
        bw2, bh2 = int(cw * 0.62), int(46 * s)
        bx = x + (cw - bw2) // 2
        by = y + chh - int(76 * s)
        hov = self._mouse_in(bx, by, bw2, bh2)
        c.rounded_grad(bx, by, bw2, bh2, int(12 * s),
                       self.theme.accent if hov else mix(self.theme.accent, theme.BG, 0.15),
                       self.theme.accent2)
        c.text(bx + bw2 // 2, by + bh2 // 2, "Войти в систему", self.t.fs(14),
               (12, 14, 22), bold=True, anchor="cc")
        c.text(x + cw // 2, y + chh - int(18 * s), "нажмите Enter или кликните мышью",
               self.t.fs(10), theme.TEXT_MUTE, anchor="cc")

    # ------------------------------------------------------------ cursor
    _cursor_mask = None

    def draw_cursor(self, c):
        if Zarya._cursor_mask is None:
            from PIL import Image, ImageDraw
            s = 2
            img = Image.new("L", (24 * s, 32 * s), 0)
            d = ImageDraw.Draw(img)
            d.polygon([(0, 0), (0, 24), (6, 18), (10, 28), (14, 26), (10, 16),
                       (18, 16)], fill=255)
            arr = np.asarray(img.resize((24, 32), Image.LANCZOS), dtype=np.float32) / 255.0
            # outline
            img2 = Image.new("L", (24 * s, 32 * s), 0)
            d2 = ImageDraw.Draw(img2)
            pts = [(0, 0), (0, 24), (6, 18), (10, 28), (14, 26), (10, 16), (18, 16)]
            d2.line(pts + [pts[0]], fill=255, width=2 * s, joint="curve")
            outline = np.asarray(img2.resize((24, 32), Image.LANCZOS),
                                 dtype=np.float32) / 255.0
            Zarya._cursor_mask = (arr, outline)
        arr, outline = Zarya._cursor_mask
        mx, my = self.mouse
        c.blend_mask(mx - 1, my - 1, outline, (0, 0, 0), 0.85)
        c.blend_mask(mx, my, arr, (255, 255, 255), 0.98)

    # ------------------------------------------------------------- loop
    def run(self):
        self.scr.blank()
        self.render()
        self.scr.flush(self.canvas.image())
        # A trial OTA becomes the new rollback baseline only after the actual
        # desktop has initialized its framebuffer, drawing, and input stack.
        if os.path.isfile("/etc/zarya-slot") and os.path.isfile("/usr/local/bin/zarya-ota-init"):
            try:
                subprocess.Popen(
                    ["/usr/local/bin/zarya-ota-init", "--mark-good"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
            except Exception:
                pass
        last = time.time()
        frames = 0
        self.launcher_query = ""
        last_fb_check = time.time()
        while self.running:
            self.input.poll(0.03)
            if now_ts() - last_fb_check > 2.0:
                last_fb_check = now_ts()
                new_size = self.scr.check_resize()
                if new_size:
                    self.W, self.H = new_size
                    self.canvas = Canvas(self.W, self.H)
                    self.input.resize(self.W, self.H)
                    self._wall_arr = None
                    self.theme.scale = max(0.78, min(1.6, self.W / 1280.0))
                    self.dirty = True
            if self.dragging:
                mx, my = self.mouse
                w = self.dragging["win"]
                w.x = mx - self.dragging["dx"]
                w.y = max(int(self.bar_h()), my - self.dragging["dy"])
                if self.dragging.get("was_max") and abs(my - self.dragging["oy"]) > 10:
                    # un-maximize and keep dragging
                    w.maximized = False
                    w.w, w.h = self.dragging.get("ow", w.w), self.dragging.get("oh", w.h)
                    self.dragging["was_max"] = False
                    self.dragging["dx"] = w.w // 2
                self.dirty = True
            if getattr(self, "resizing", None):
                r = self.resizing
                w = r["win"]
                if r.get("vertical"):
                    w.h = max(int(200 * self.theme.scale), r["oh"] + (self.mouse[1] - r["oy"]))
                else:
                    w.w = max(int(360 * self.theme.scale), r["ow"] + (self.mouse[0] - r["ox"]))
                    w.h = max(int(240 * self.theme.scale), r["oh"] + (self.mouse[1] - r["oy"]))
                w.app.on_resize(w.w, w.h - w.title_h)
                self.dirty = True
            self.tick()
            now = time.time()
            need = self.dirty or any(w.app.needs_redraw() for w in self.windows if not w.minimized)
            if need or now - last > 0.9:   # idle refresh for clock/toasts
                self.render()
                self.scr.flush(self.canvas.image())
                self.dirty = False
                last = now
                frames += 1
                if self.show_fps:
                    self._frame_times.append(now)
                    self._frame_times = [t for t in self._frame_times if now - t < 1.0]
                    self.fps = len(self._frame_times)


def report_crash(tb):
    """Сохраняем traceback в лог, kmsg и пробуем показать на экране."""
    import traceback
    text = "".join(traceback.format_exception(*tb)) if isinstance(tb, tuple) else str(tb)
    for path in ("/run/zarya/log/shell.log",):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as f:
                f.write(text + "\n")
        except Exception:
            pass
    try:
        with open("/dev/kmsg", "w") as f:
            f.write("[zarya] CRASH\n" + text[-3500:] + "\n")
    except Exception:
        pass
    try:
        from display import Screen
        scr = Screen()
        cv = Canvas(scr.width, scr.height, (10, 6, 10))
        cv.text(24, 24, "Оболочка Заря OS остановлена из-за ошибки", 20,
                (255, 140, 140), bold=True)
        y = 60
        for line in text.strip().splitlines()[-28:]:
            cv.text(24, y, line[:150], 12, (220, 220, 220), mono=True)
            y += 16
        scr.flush(cv.image())
    except Exception:
        pass


def main():
    try:
        _main()
    except Exception:
        import sys as _sys
        report_crash(_sys.exc_info())
        raise


def _main():
    if os.geteuid() != 0:
        try:
            os.execvp("sudo", ["sudo", "-E", sys.executable] + sys.argv)
        except Exception:
            pass
    shell = Zarya()
    # development helper: render one frame to PNG and exit
    if len(sys.argv) > 1 and sys.argv[1] == "--snap":
        shell.render()
        shell.canvas.image().save(sys.argv[2] if len(sys.argv) > 2 else "/tmp/zarya.png")
        return
    shell.run()


if __name__ == "__main__":
    main()
