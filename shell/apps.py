#!/usr/bin/env python3
"""Zarya OS - built-in applications."""
import os
import pty
import fcntl
import termios
import struct
import select
import time
import json
import subprocess
import shutil

import numpy as np

from draw import Canvas, paste_icon, mix, lighten, darken, measure
import theme
from vt import Screen

HOME = os.path.expanduser("~")
VERSION = "1.0 «Рассвет»"

PALETTE = [
    (28, 31, 42), (222, 96, 104), (126, 205, 132), (226, 194, 106),
    (108, 156, 232), (191, 130, 225), (94, 200, 212), (206, 212, 224),
    (100, 106, 124), (255, 126, 132), (150, 232, 156), (255, 224, 132),
    (140, 184, 255), (224, 156, 255), (120, 232, 240), (255, 255, 255),
]
TERM_FG = (219, 225, 238)
TERM_BG = (16, 18, 26)


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024:
            return ("%d %s" % (n, unit)) if unit == "Б" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f ПБ" % n


class App:
    app_id = "app"
    name = "Приложение"
    icon = "gear"

    def __init__(self, shell):
        self.sh = shell
        self.window = None
        self._dirty = True

    # lifecycle -------------------------------------------------------
    def draw(self, c, w, h, focused):
        pass

    def on_mouse(self, kind, btn, x, y, ev):
        pass

    def on_key(self, ch, code, mods):
        pass

    def on_resize(self, w, h):
        pass

    def tick(self):
        pass

    def close(self):
        pass

    def invalidate(self):
        self._dirty = True

    def needs_redraw(self):
        d = self._dirty
        self._dirty = False
        return d

    # helpers ---------------------------------------------------------
    def sc(self):
        return self.sh.theme.scale

    def fs(self, n):
        return self.sh.t.fs(n)


# ======================================================================
#  Терминал
# ======================================================================
class TerminalApp(App):
    app_id = "Терминал"
    name = "Терминал"
    icon = "term"

    def __init__(self, shell):
        super().__init__(shell)
        self.font_size = 0
        self.cols, self.rows = 80, 24
        self.screen = Screen(80, 24)
        self.fd = None
        self.pid = None
        self.view_offset = 0
        self.blink = 0.0
        self.cw = self.chh = 10
        self.exited = False
        self.cursor_vis = True

    # -- process ------------------------------------------------------
    def spawn(self):
        shell_cmd = ["/bin/bash", "-i"]
        env = dict(os.environ)
        env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor",
                    "LANG": "C.UTF-8", "HOME": HOME})
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(HOME)
                os.execvpe("/bin/bash", shell_cmd, env)
            except Exception:
                os._exit(1)
        self.pid, self.fd = pid, fd
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self.exited = False
        self.update_size()

    def update_size(self):
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.rows, self.cols, 0, 0))
        except Exception:
            pass

    def compute_metrics(self, w, h):
        s = self.sc()
        size = max(10, int(14 * s))
        if size != self.font_size:
            self.font_size = size
            self.cw = max(5, int(round(measure("M", size, mono=True))))
            self.chh = max(9, int(round(size * 1.42)))
        pad = int(10 * s)
        cols = max(20, (w - pad * 2) // self.cw)
        rows = max(5, (h - pad * 2) // self.chh)
        if cols != self.cols or rows != self.rows:
            self.cols, self.rows = cols, rows
            self.screen.resize(cols, rows)
            self.update_size()

    # -- io ------------------------------------------------------------
    def tick(self):
        self.blink += 0.05
        if self.blink > 1.0:
            self.blink = 0.0
            self.cursor_vis = not self.cursor_vis
            self._dirty = True
        if self.fd is None:
            return
        got = False
        for _ in range(8):
            try:
                r, _, _ = select.select([self.fd], [], [], 0)
            except Exception:
                break
            if not r:
                break
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
                self.exited = True
                break
            if not data:
                self.exited = True
                break
            self.screen.feed(data)
            got = True
        if got:
            self.view_offset = 0
            self._dirty = True

    def write(self, data):
        if self.fd is None:
            return
        try:
            os.write(self.fd, data)
        except OSError:
            pass

    def close(self):
        if self.pid:
            try:
                os.kill(self.pid, 15)
            except Exception:
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None

    # -- input ---------------------------------------------------------
    def on_key(self, ch, code, mods):
        if self.exited:
            self.spawn()
            self._dirty = True
            return
        if ch is None:
            return
        if mods.get("ctrl") and ch and len(ch) == 1 and ch.isalpha():
            self.write(bytes([ord(ch.lower()) - 96]))
        elif ch == "\x1b" and code == 111:
            self.write(b"\x1b[3~")
        else:
            self.write(ch.encode("utf-8", "ignore"))
        self._dirty = True

    def on_mouse(self, kind, btn, x, y, ev):
        if kind == "wheel":
            self.view_offset = max(0, min(len(self.screen.history),
                                          self.view_offset + (1 if ev.get("dy", 0) > 0 else -1) * 3))
            self._dirty = True
            return
        if kind == "down":
            if ev.get("btn") == "right":
                self.write(b"\x03")
                self._dirty = True

    # -- draw ----------------------------------------------------------
    def draw(self, c, w, h, focused):
        self.compute_metrics(w, h)
        s = self.sc()
        pad = int(10 * s)
        c.fill(0, 0, w, h, TERM_BG, 1.0)
        fg = TERM_FG
        rows = self.screen.grid
        hist = self.screen.history
        start_row = 0
        # scrolled view: blend history + live grid
        total = len(hist) + len(rows)
        first = max(0, total - self.rows - self.view_offset)
        lines = hist + rows
        visible = lines[first:first + self.rows]
        y = pad
        for ri, row in enumerate(visible):
            x = pad
            run = []
            run_fg = None
            run_bold = False
            def flush():
                nonlocal x, run, run_fg, run_bold
                if run:
                    txt = "".join(run)
                    c.text(x, y, txt, self.font_size, run_fg or fg, bold=run_bold, mono=True)
                    x += self.cw * len(run)
                    run = []
            for cell in row:
                bg = cell.bg
                if bg is not None:
                    flush()
                    col = PALETTE[bg] if isinstance(bg, int) else bg
                    if isinstance(bg, int) and bg >= 16:
                        col = self._256(bg)
                    c.fill(x, y - 1, self.cw, self.chh, col, 1.0)
                cc = cell.fg
                if isinstance(cc, int):
                    colr = PALETTE[cc] if cc < 16 else self._256(cc)
                else:
                    colr = cc or fg
                if cell.bold and (cc is None or (isinstance(cc, int) and cc < 8)):
                    colr = lighten(colr, 0.28)
                if run and (colr != run_fg or cell.bold != run_bold):
                    flush()
                run_fg, run_bold = colr, cell.bold
                run.append(cell.ch)
            flush()
            y += self.chh
        # cursor
        live_row = self.screen.y - (first - len(hist)) if self.view_offset == 0 else -1
        if 0 <= live_row < self.rows and self.cursor_vis and focused:
            cx = pad + self.screen.x * self.cw
            cy = pad + live_row * self.chh
            c.fill(cx, cy - 1, self.cw, self.chh, (255, 255, 255), 0.55)
        # scrollback indicator
        if self.view_offset:
            c.rounded(w - int(70 * s), int(8 * s), int(58 * s), int(22 * s), int(6 * s),
                      theme.PANEL_HI, 0.95)
            c.text(w - int(41 * s), int(19 * s), "▲ %d" % self.view_offset,
                   self.fs(10), theme.TEXT_DIM, anchor="cc")
        if self.exited:
            c.rounded(pad, h - int(34 * s), int(260 * s), int(26 * s), int(8 * s),
                      (60, 30, 36), 0.95)
            c.text(pad + int(10 * s), h - int(21 * s),
                   "Процесс завершён — нажмите любую клавишу", self.fs(11),
                   (255, 170, 170), anchor="lc")

    @staticmethod
    def _256(n):
        if n < 16:
            return PALETTE[n]
        if n < 232:
            n -= 16
            r, g, b = n // 36, (n % 36) // 6, n % 6
            lv = [0, 95, 135, 175, 215, 255]
            return (lv[r], lv[g], lv[b])
        v = 8 + (n - 232) * 10
        return (v, v, v)


# ======================================================================
#  Файлы
# ======================================================================
class FilesApp(App):
    app_id = "Файлы"
    name = "Файлы"
    icon = "files"

    def __init__(self, shell):
        super().__init__(shell)
        self.path = HOME
        self.sel = 0
        self.scroll = 0
        self.hover = -1
        self.preview = ""
        self.preview_name = ""
        self.entries = []
        self.refresh()

    def refresh(self):
        try:
            names = os.listdir(self.path)
        except Exception:
            names = []
        items = []
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(self.path, n)
            try:
                isdir = os.path.isdir(p)
                size = 0 if isdir else os.path.getsize(p)
            except Exception:
                isdir, size = False, 0
            items.append((isdir, n, size))
        items.sort(key=lambda t: (not t[0], t[1].lower()))
        if self.path != "/":
            items.insert(0, (True, "..", 0))
        self.entries = items
        self.sel = min(self.sel, max(0, len(items) - 1))
        self._dirty = True

    def open_entry(self, i):
        if not (0 <= i < len(self.entries)):
            return
        isdir, name, _sz = self.entries[i]
        p = os.path.normpath(os.path.join(self.path, name))
        if isdir:
            self.path = p
            self.sel = 0
            self.scroll = 0
            self.preview = ""
            self.refresh()
        elif name.lower().endswith(".exe"):
            if not shutil.which("wine") or not shutil.which("xinit"):
                self.preview = (
                    "Для запуска Windows-приложений нужен Wine и X11. "
                    "Установите их командой: sudo zarya-wine install"
                )
                self.preview_name = name
                self._dirty = True
                return
            if "\n" in p or "\r" in p:
                self.preview = "Путь с переводом строки не поддерживается Wine-сессией."
                self.preview_name = name
                self._dirty = True
                return
            try:
                os.makedirs("/run/zarya", exist_ok=True)
                with open("/run/zarya/wine-request", "w", encoding="utf-8") as f:
                    f.write(os.path.realpath(p) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                self.sh.running = False
            except Exception as e:
                self.preview = "Не удалось запустить Wine: %s" % e
                self.preview_name = name
                self._dirty = True
        else:
            self.load_preview(p)

    def load_preview(self, p):
        try:
            if os.path.getsize(p) > 400000:
                self.preview = "Файл слишком большой для просмотра."
            else:
                with open(p, "rb") as f:
                    data = f.read(20000)
                if b"\0" in data:
                    self.preview = "Двоичный файл (%s)" % human_size(os.path.getsize(p))
                else:
                    self.preview = data.decode("utf-8", "replace")
        except Exception as e:
            self.preview = "Не удалось открыть: %s" % e
        self.preview_name = os.path.basename(p)
        self._dirty = True

    # input -----------------------------------------------------------
    def on_key(self, ch, code, mods):
        if ch == "\x1b[A":
            self.sel = max(0, self.sel - 1)
        elif ch in ("\x1b[B",):
            self.sel = min(len(self.entries) - 1, self.sel + 1)
        elif ch in ("\n", "\r"):
            self.open_entry(self.sel)
        elif ch == "\x1b[D" or ch == "\b":
            self.path = os.path.dirname(self.path) or "/"
            self.refresh()
        elif ch == "\x1b[C":
            self.open_entry(self.sel)
        self._dirty = True

    def on_mouse(self, kind, btn, x, y, ev):
        s = self.sc()
        sidebar = int(150 * s)
        row_h = int(30 * s)
        top = int(46 * s)
        if kind == "down" and btn == "left":
            if x < sidebar:
                idx = (y - top) // row_h
                places = self.places()
                if 0 <= idx < len(places):
                    self.path = places[idx][1]
                    self.sel = 0
                    self.refresh()
                return
            idx = int((y - top) // row_h) + self.scroll
            if 0 <= idx < len(self.entries):
                self.sel = idx
                if self._last_click_idx == idx and time.time() - self._last_click_t < 0.4:
                    self.open_entry(idx)
                else:
                    self.open_entry(idx) if self.entries[idx][0] else self.load_preview(
                        os.path.join(self.path, self.entries[idx][1]))
                self._last_click_idx = idx
                self._last_click_t = time.time()
        elif kind == "wheel":
            self.scroll = max(0, self.scroll - (1 if ev.get("dy", 0) > 0 else -1) * 2)
        elif kind == "move":
            self.hover = int((y - top) // row_h) + self.scroll
        self._dirty = True

    _last_click_idx = -1
    _last_click_t = 0.0

    def places(self):
        return [("Домой", HOME), ("Документы", os.path.join(HOME, "Документы")),
                ("Загрузки", os.path.join(HOME, "Загрузки")),
                ("Рабочий стол", os.path.join(HOME, "Рабочий стол")),
                ("Корень /", "/"), ("Настройки /etc", "/etc"),
                ("Устройства", "/dev"), ("Журналы", "/var/log")]

    # draw ------------------------------------------------------------
    def draw(self, c, w, h, focused):
        s = self.sc()
        c.fill(0, 0, w, h, theme.BG, 1.0)
        sidebar = int(150 * s)
        row_h = int(30 * s)
        top = int(46 * s)
        # header
        c.fill(0, 0, w, top, theme.BG_ALT, 1.0)
        c.fill(0, top - 1, w, 1, theme.STROKE, 1.0)
        paste_icon(c, "folder", int(14 * s), int(14 * s), int(18 * s), self.sh.theme.accent)
        path_disp = self.path.replace(HOME, "~")
        c.text(int(38 * s), top // 2, path_disp, self.fs(13), theme.TEXT, anchor="lc")
        # sidebar
        c.fill(0, top, sidebar, h - top, theme.BG_ALT, 0.7)
        c.fill(sidebar, top, 1, h - top, theme.STROKE, 1.0)
        for i, (label, p) in enumerate(self.places()):
            y = top + i * row_h
            active = os.path.normpath(p) == os.path.normpath(self.path)
            hot = self.sh._mouse_in(0, y, sidebar, row_h)
            if active or hot:
                c.rounded(int(6 * s), y + int(3 * s), sidebar - int(12 * s), row_h - int(6 * s),
                          int(7 * s), (255, 255, 255), 0.09 if hot else 0.06)
            c.text(int(14 * s), y + row_h // 2, label, self.fs(12),
                   theme.TEXT if active else theme.TEXT_DIM, anchor="lc")
        # entries
        c.clip = (sidebar + 1, top, w, h)
        visible = max(1, (h - top) // row_h)
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + visible:
            self.scroll = self.sel - visible + 1
        for i, (isdir, name, size) in enumerate(self.entries[self.scroll:self.scroll + visible]):
            idx = i + self.scroll
            y = top + i * row_h
            sel = idx == self.sel
            hot = idx == self.hover
            if sel or hot:
                c.rounded(sidebar + int(8 * s), y + int(2 * s), w - sidebar - int(20 * s),
                          row_h - int(4 * s), int(7 * s),
                          self.sh.theme.accent if sel else (255, 255, 255),
                          0.20 if sel else 0.06)
            isz = int(17 * s)
            paste_icon(c, "folder" if isdir else "note", sidebar + int(16 * s),
                       y + row_h // 2 - isz // 2, isz,
                       self.sh.theme.accent if isdir else theme.TEXT_MUTE)
            c.text(sidebar + int(16 * s) + isz + int(10 * s), y + row_h // 2, name,
                   self.fs(13), theme.TEXT if isdir else theme.TEXT_DIM, anchor="lc")
            if not isdir:
                c.text(w - int(18 * s), y + row_h // 2, human_size(size), self.fs(11),
                       theme.TEXT_MUTE, anchor="rc")
        c.clip = None
        # preview
        if self.preview:
            ph = int(min(h * 0.42, 240 * s))
            py = h - ph
            c.fill(0, py, w, ph, theme.PANEL, 1.0)
            c.fill(0, py, w, 1, theme.STROKE, 1.0)
            c.text(int(14 * s), py + int(12 * s), self.preview_name, self.fs(12),
                   self.sh.theme.accent, bold=True)
            c.clip = (0, py + int(28 * s), w, h)
            lines = self.preview.splitlines()[:int((ph - int(36 * s)) / (self.fs(12) + 4))]
            for i, line in enumerate(lines):
                c.text(int(14 * s), py + int(30 * s) + i * int(self.fs(12) + 4), line,
                       self.fs(12), theme.TEXT_DIM, mono=True)
            c.clip = None
        # status
        c.fill(0, h - int(22 * s), w, int(22 * s), theme.BG_ALT, 1.0)
        c.text(int(12 * s), h - int(11 * s), "Элементов: %d" % len(self.entries),
               self.fs(10), theme.TEXT_MUTE, anchor="lc")
        c.text(w - int(12 * s), h - int(11 * s), "Enter — открыть · Backspace — вверх",
               self.fs(10), theme.TEXT_MUTE, anchor="rc")


# ======================================================================
#  Заметки
# ======================================================================
class NotesApp(App):
    app_id = "Заметки"
    name = "Заметки"
    icon = "note"

    def __init__(self, shell):
        super().__init__(shell)
        self.lines = [""]
        self.cx = self.cy = 0
        self.saved_at = 0.0
        self.status = "новая заметка"
        self.path = os.path.join(HOME, "Документы", "заметки.txt")
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                text = f.read()
            self.lines = text.split("\n") or [""]
            self.cy = min(len(self.lines) - 1, 0)
            self.status = "загружено: %s" % self.path.replace(HOME, "~")
        except Exception:
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines))
            self.saved_at = time.time()
            self.status = "сохранено " + time.strftime("%H:%M:%S")
        except Exception as e:
            self.status = "ошибка сохранения: %s" % e
        self._dirty = True

    def on_key(self, ch, code, mods):
        line = self.lines[self.cy]
        if ch == "\n":
            self.lines.insert(self.cy + 1, line[self.cx:])
            self.lines[self.cy] = line[:self.cx]
            self.cy += 1
            self.cx = 0
        elif ch == "\b":
            if self.cx > 0:
                self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
                self.cx -= 1
            elif self.cy > 0:
                prev = self.lines[self.cy - 1]
                self.cx = len(prev)
                self.lines[self.cy - 1] = prev + line
                del self.lines[self.cy]
                self.cy -= 1
        elif ch == "\x1b[A":
            self.cy = max(0, self.cy - 1)
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif ch == "\x1b[B":
            self.cy = min(len(self.lines) - 1, self.cy + 1)
            self.cx = min(self.cx, len(self.lines[self.cy]))
        elif ch == "\x1b[D":
            self.cx = max(0, self.cx - 1)
        elif ch == "\x1b[C":
            self.cx = min(len(line), self.cx + 1)
        elif ch == "\x1b[H":
            self.cx = 0
        elif ch == "\x1b[F":
            self.cx = len(line)
        elif ch and len(ch) == 1 and ch >= " ":
            self.lines[self.cy] = line[:self.cx] + ch + line[self.cx:]
            self.cx += 1
        if mods.get("ctrl") and ch == "s":
            self.save()
        self._dirty = True

    def tick(self):
        if self.lines != getattr(self, "_last", None):
            self._last = [l for l in self.lines]
        if time.time() - self.saved_at > 2.5 and self.status.startswith("изменено"):
            self.save()

    def draw(self, c, w, h, focused):
        s = self.sc()
        c.fill(0, 0, w, h, theme.BG, 1.0)
        pad = int(18 * s)
        lh = self.fs(15) + int(8 * s)
        top = int(34 * s)
        # toolbar
        c.fill(0, 0, w, top, theme.BG_ALT, 1.0)
        c.fill(0, top - 1, w, 1, theme.STROKE, 1.0)
        paste_icon(c, "note", int(10 * s), int(8 * s), int(18 * s), self.sh.theme.accent)
        c.text(int(36 * s), top // 2, "Заметки — " + self.path.replace(HOME, "~"),
               self.fs(12), theme.TEXT_DIM, anchor="lc")
        c.rounded(w - int(96 * s), int(6 * s), int(84 * s), int(22 * s), int(6 * s),
                  self.sh.theme.accent, 0.16)
        c.text(w - int(54 * s), top // 2, "Сохранить", self.fs(11),
               self.sh.theme.accent, anchor="cc")
        # text
        c.clip = (0, top, w, h - int(24 * s))
        y = top + int(14 * s)
        for i, line in enumerate(self.lines):
            if y > h:
                break
            c.text(pad, y, line, self.fs(15), theme.TEXT)
            if i == self.cy:
                cx = pad + measure(line[:self.cx], self.fs(15)) + 1
                c.fill(cx, y - 1, max(2, int(2 * s)), self.fs(15) + 4,
                       self.sh.theme.accent, 0.9)
            y += lh
        c.clip = None
        # status bar
        c.fill(0, h - int(24 * s), w, int(24 * s), theme.BG_ALT, 1.0)
        c.text(int(12 * s), h - int(12 * s), self.status, self.fs(10),
               theme.TEXT_MUTE, anchor="lc")
        c.text(w - int(12 * s), h - int(12 * s),
               "строка %d, колонка %d   Ctrl+S — сохранить" % (self.cy + 1, self.cx + 1),
               self.fs(10), theme.TEXT_MUTE, anchor="rc")

    def on_mouse(self, kind, btn, x, y, ev):
        s = self.sc()
        if kind == "down" and x > self.window.w - int(120 * s) and y < int(34 * s):
            self.save()
        elif kind == "down":
            pad = int(18 * s)
            lh = self.fs(15) + int(8 * s)
            idx = int((y - int(34 * s) - int(14 * s)) // lh)
            if 0 <= idx < len(self.lines):
                self.cy = idx
                line = self.lines[idx]
                best, bx = 0, 10 ** 9
                for k in range(len(line) + 1):
                    d = abs(pad + measure(line[:k], self.fs(15)) - x)
                    if d < bx:
                        best, bx = k, d
                self.cx = best
                self._dirty = True


# ======================================================================
#  Система
# ======================================================================
class SystemApp(App):
    app_id = "Система"
    name = "Система"
    icon = "cpu"

    def __init__(self, shell):
        super().__init__(shell)
        self.t0 = time.time()
        self.scroll = 0
        self._max_scroll = 0

    @staticmethod
    def _fit(text, width, size):
        """Сократить длинное значение, чтобы оно не залезало на соседнюю колонку."""
        text = str(text)
        width = max(0, int(width))
        if width <= 0:
            return ""
        if measure(text, size) <= width:
            return text
        ellipsis = "…"
        while text and measure(text + ellipsis, size) > width:
            text = text[:-1]
        return text + ellipsis if text else ellipsis

    def draw(self, c, w, h, focused):
        s = self.sc()
        sh = self.sh
        c.fill(0, 0, w, h, theme.BG, 1.0)
        pad = max(12, int(20 * s))
        card_w = max(1, w - pad * 2)

        # Header stays fixed while the cards below it scroll.
        c.text(pad, int(16 * s), "Монитор системы", self.fs(19),
               theme.TEXT, bold=True)
        c.text(pad, int(40 * s), "Показатели ПК · прокрутка колёсиком мыши",
               self.fs(11), theme.TEXT_MUTE)
        header_bottom = int(62 * s)
        c.fill(pad, header_bottom, card_w, max(1, int(s)), theme.STROKE, 0.65)

        viewport_top = header_bottom + int(6 * s)
        viewport_bottom = max(viewport_top + 1, h - int(12 * s))
        viewport_h = viewport_bottom - viewport_top
        gap = max(10, int(14 * s))
        cpu_h = int(124 * s)
        mem_h = int(124 * s)

        try:
            with open("/etc/zarya-version", encoding="utf-8") as f:
                os_version = f.read().strip()
        except Exception:
            os_version = VERSION
        try:
            with open("/etc/zarya-slot", encoding="utf-8") as f:
                slot = f.read().strip()
        except Exception:
            slot = "Live USB"
        facts = [
            ("Система", "Заря OS %s" % os_version),
            ("Ядро", os.uname().release),
            ("Хост", os.uname().nodename),
            ("Время работы", self._uptime()),
            ("Разрешение", "%d × %d" % (sh.W, sh.H)),
            ("Тема", "%s · %s" % (sh.theme.accent_name, sh.theme.wallpaper_name)),
            ("Системный слот", slot or "unknown"),
            ("Wine", "установлен" if shutil.which("wine") else "не установлен"),
        ]
        columns = 2 if card_w >= int(480 * s) else 1
        rows_per_col = (len(facts) + columns - 1) // columns
        details_h = int((40 + rows_per_col * 42) * s)
        content_h = cpu_h + gap + mem_h + gap + details_h
        self._max_scroll = max(0, content_h - viewport_h)
        self.scroll = max(0, min(int(self.scroll), self._max_scroll))
        offset = self.scroll

        # Canvas clips are absolute screen coordinates; retain the outer window
        # clip and intersect it with this app's scrolling viewport.
        old_clip = c.clip
        view_clip = (c.ox + pad, c.oy + viewport_top,
                     c.ox + w - pad, c.oy + viewport_bottom)
        if old_clip is not None:
            view_clip = (max(view_clip[0], old_clip[0]),
                         max(view_clip[1], old_clip[1]),
                         min(view_clip[2], old_clip[2]),
                         min(view_clip[3], old_clip[3]))
        c.clip = view_clip

        y = viewport_top - offset
        # CPU card
        c.rounded(pad, y, card_w, cpu_h, int(12 * s), theme.PANEL, 1.0)
        c.border(pad, y, card_w, cpu_h, int(12 * s), theme.STROKE, 1)
        inner = int(16 * s)
        label_size = self.fs(13)
        cpu_label = "Процессор"
        cpu_metric = self._fit("%d%% · %d ядра" %
                               (int(sh.cpu_load), os.cpu_count() or 1),
                               card_w - int(132 * s), label_size)
        c.text(pad + inner, y + int(15 * s), cpu_label, label_size,
               theme.TEXT, bold=True)
        c.text(pad + card_w - inner, y + int(15 * s), cpu_metric,
               label_size, sh.theme.accent, bold=True, anchor="rt")
        gx = pad + inner
        gy = y + int(42 * s)
        gw = max(1, card_w - inner * 2)
        gh = max(1, cpu_h - int(56 * s))
        c.rounded(gx, gy, gw, gh, int(6 * s), theme.BG_ALT, 1.0)
        hist = list(getattr(sh, "cpu_hist", []))[-max(2, int(gw // max(2, 3 * s))):]
        if len(hist) > 1 and gw > 1:
            pts = np.linspace(0, gw - 1, len(hist)).astype(int)
            vals = np.clip(np.asarray(hist, dtype=np.float32) / 100.0, 0, 1)
            for i in range(len(hist) - 1):
                x1, x2 = gx + pts[i], gx + pts[i + 1]
                v1 = gy + gh - int(vals[i] * gh)
                v2 = gy + gh - int(vals[i + 1] * gh)
                c.line(x1, v1, x2, v2, sh.theme.accent, max(1, int(2 * s)))
            for i in range(len(hist)):
                x1 = gx + pts[i]
                v = gy + gh - int(vals[i] * gh)
                if gy + gh > v:
                    c.fill(x1, v, max(1, int(3 * s)), gy + gh - v,
                           sh.theme.accent, 0.10)

        # Memory card
        y += cpu_h + gap
        c.rounded(pad, y, card_w, mem_h, int(12 * s), theme.PANEL, 1.0)
        c.border(pad, y, card_w, mem_h, int(12 * s), theme.STROKE, 1)
        mem = sh.mem
        total = mem.get("MemTotal", 1) / 1048576.0
        avail = mem.get("MemAvailable", 0) / 1048576.0
        used = max(0.0, total - avail)
        swap_total = mem.get("SwapTotal", 0) / 1048576.0
        swap_free = mem.get("SwapFree", 0) / 1048576.0
        c.text(pad + inner, y + int(15 * s), "Память", label_size,
               theme.TEXT, bold=True)
        mem_metric = self._fit("%.1f из %.1f ГБ" % (used, total),
                               card_w - int(132 * s), label_size)
        c.text(pad + card_w - inner, y + int(15 * s), mem_metric,
               label_size, sh.theme.accent2, bold=True, anchor="rt")
        bar_x = pad + inner
        bar_y = y + int(46 * s)
        bar_w = max(1, card_w - inner * 2)
        bar_h = max(8, int(13 * s))
        c.rounded(bar_x, bar_y, bar_w, bar_h, bar_h // 2, theme.BG_ALT, 1.0)
        frac = max(0.0, min(1.0, used / total if total > 0 else 0.0))
        if frac > 0:
            c.rounded(bar_x, bar_y, max(bar_h, int(bar_w * frac)), bar_h,
                      bar_h // 2, sh.theme.accent2, 1.0)
        detail_size = self.fs(10)
        free_line = "Свободно: %.1f ГБ" % avail
        swap_line = "Swap: %.1f / %.1f ГБ" % (max(0.0, swap_total - swap_free), swap_total)
        if card_w < int(360 * s):
            c.text(bar_x, y + int(78 * s), self._fit(free_line, bar_w, detail_size),
                   detail_size, theme.TEXT_DIM)
            c.text(bar_x, y + int(99 * s), self._fit(swap_line, bar_w, detail_size),
                   detail_size, theme.TEXT_DIM)
        else:
            c.text(bar_x, y + int(82 * s), self._fit(free_line, bar_w // 2, detail_size),
                   detail_size, theme.TEXT_DIM)
            c.text(bar_x + bar_w // 2, y + int(82 * s),
                   self._fit(swap_line, bar_w - bar_w // 2, detail_size),
                   detail_size, theme.TEXT_DIM)

        # Facts card adapts to narrow windows and is reachable by scrolling.
        y += mem_h + gap
        c.rounded(pad, y, card_w, details_h, int(12 * s), theme.PANEL, 1.0)
        c.border(pad, y, card_w, details_h, int(12 * s), theme.STROKE, 1)
        c.text(pad + inner, y + int(17 * s), "Сведения о системе", label_size,
               theme.TEXT, bold=True)
        grid_top = y + int(42 * s)
        col_gap = int(16 * s)
        col_w = max(1, (card_w - inner * 2 - col_gap * (columns - 1)) // columns)
        row_step = int(42 * s)
        value_size = self.fs(11)
        key_size = self.fs(10)
        for i, (key, value) in enumerate(facts):
            col = i // rows_per_col
            row = i % rows_per_col
            x = pad + inner + col * (col_w + col_gap)
            yy = grid_top + row * row_step
            c.text(x, yy, self._fit(key, col_w, key_size), key_size,
                   theme.TEXT_MUTE, anchor="lc")
            c.text(x, yy + int(15 * s), self._fit(value, col_w, value_size),
                   value_size, theme.TEXT, anchor="lc")

        c.clip = old_clip
        if self._max_scroll > 0 and viewport_h > 0:
            track_w = max(2, int(3 * s))
            track_x = w - max(5, int(7 * s))
            c.rounded(track_x, viewport_top, track_w, viewport_h,
                      track_w, theme.BG_ALT, 0.85)
            thumb_h = max(int(24 * s), int(viewport_h * viewport_h / max(1, content_h)))
            thumb_h = min(viewport_h, thumb_h)
            travel = max(0, viewport_h - thumb_h)
            thumb_y = viewport_top + int(travel * self.scroll / max(1, self._max_scroll))
            c.rounded(track_x, thumb_y, track_w, thumb_h, track_w,
                      sh.theme.accent, 0.85)

    def on_mouse(self, kind, btn, x, y, ev):
        if kind != "wheel" or self._max_scroll <= 0:
            return
        step = max(28, int(44 * self.sc()))
        self.scroll = max(0, min(self._max_scroll,
                                 self.scroll + int(ev.get("dy", 0)) * step))
        self._dirty = True

    def on_key(self, ch, code, mods):
        if ch in ("\x1b[A", "\x1b[D"):
            self.scroll = max(0, self.scroll - int(40 * self.sc()))
            self._dirty = True
        elif ch in ("\x1b[B", "\x1b[C"):
            self.scroll = min(self._max_scroll, self.scroll + int(40 * self.sc()))
            self._dirty = True

    def _uptime(self):
        up = int(time.time() - self.t0)
        try:
            with open("/proc/uptime") as f:
                up = int(float(f.read().split()[0]))
        except Exception:
            pass
        hh, mm = up // 3600, (up % 3600) // 60
        return "%d ч %02d мин" % (hh, mm)


# ======================================================================
#  Настройки
# ======================================================================
class SettingsApp(App):
    app_id = "Настройки"
    name = "Настройки"
    icon = "gear"

    def __init__(self, shell):
        super().__init__(shell)
        self.page = 0
        self.scroll = 0
        self._max_scroll = 0

    def draw(self, c, w, h, focused):
        s = self.sc()
        sh = self.sh
        c.fill(0, 0, w, h, theme.BG, 1.0)
        pad = int(22 * s)
        c.text(pad, int(18 * s), "Настройки", self.fs(20), theme.TEXT, bold=True)
        c.text(pad, int(44 * s), "Оформление применяется сразу и сохраняется",
               self.fs(11), theme.TEXT_MUTE)
        view_top = int(66 * s)
        view_bottom = max(view_top + 1, h - int(10 * s))
        view_h = view_bottom - view_top
        old_clip = c.clip
        viewport_clip = (c.ox + 1, c.oy + view_top,
                         c.ox + w - 1, c.oy + view_bottom)
        if old_clip is not None:
            viewport_clip = (max(viewport_clip[0], old_clip[0]),
                             max(viewport_clip[1], old_clip[1]),
                             min(viewport_clip[2], old_clip[2]),
                             min(viewport_clip[3], old_clip[3]))
        c.clip = viewport_clip
        old_oy = c.oy
        c.oy -= self.scroll
        y = int(76 * s)
        # accents
        c.text(pad, y, "Акцентный цвет", self.fs(13), theme.TEXT, bold=True)
        y += int(26 * s)
        x = pad
        for name in theme.ACCENTS:
            col1, col2 = theme.ACCENTS[name]
            sw = int(54 * s)
            hot = sh._mouse_in(x, y, sw, sw)
            c.rounded_grad(x, y, sw, sw, int(10 * s), col1, col2)
            if name == sh.theme.accent_name:
                c.border(x - int(3 * s), y - int(3 * s), sw + int(6 * s), sw + int(6 * s),
                         int(12 * s), (255, 255, 255), int(2 * s), 0.9)
                paste_icon(c, "check", x + int(14 * s), y + int(14 * s), int(26 * s),
                           (255, 255, 255))
            c.text(x + sw // 2, y + sw + int(14 * s), name, self.fs(10),
                   theme.TEXT_DIM if name != sh.theme.accent_name else theme.TEXT,
                   anchor="cc")
            x += sw + int(18 * s)
        y += int(96 * s)
        # wallpapers
        c.text(pad, y, "Обои", self.fs(13), theme.TEXT, bold=True)
        y += int(24 * s)
        tw = int((w - pad * 2 - 3 * int(14 * s)) / 4)
        th = int(tw * 0.56)
        for i, name in enumerate(theme.WALLPAPERS):
            tx = pad + i * (tw + int(14 * s))
            prev = theme.render_wallpaper(int(tw / 2), int(th / 2), name,
                                          (sh.theme.accent, sh.theme.accent2))
            c.blit_pil(prev, tx, y)
            sel = name == sh.theme.wallpaper_name
            c.border(tx, y, tw, th, int(8 * s), (255, 255, 255),
                     int(2 * s) if sel else 1, 0.9 if sel else 0.15)
            c.text(tx + tw // 2, y + th + int(14 * s), name, self.fs(10),
                   theme.TEXT if sel else theme.TEXT_MUTE, anchor="cc")
        y += th + int(34 * s)
        # scale
        c.text(pad, y, "Масштаб интерфейса", self.fs(13), theme.TEXT, bold=True)
        y += int(30 * s)
        bx0 = pad + int(170 * s)
        bwid = w - bx0 - pad
        c.rounded(bx0, y - int(4 * s), bwid, int(8 * s), int(4 * s), theme.BG_ALT, 1.0)
        t = (sh.theme.scale / sh.layout_scale - 0.8) / 0.7
        t = max(0.0, min(1.0, t))
        c.rounded(bx0, y - int(4 * s), max(8, int(bwid * t)), int(8 * s), int(4 * s),
                  sh.theme.accent, 1.0)
        c.circle(bx0 + bwid * t, y, int(9 * s), (255, 255, 255))
        c.circle(bx0 + bwid * t, y, int(7 * s), sh.theme.accent)
        c.text(pad, y, "%d %%" % int(sh.theme.scale / sh.layout_scale * 100),
               self.fs(12), theme.TEXT_DIM, anchor="lm")
        y += int(34 * s)
        # keyboard layout row
        c.rounded(pad, y, w - pad * 2, int(52 * s), int(10 * s), theme.PANEL, 1.0)
        c.border(pad, y, w - pad * 2, int(52 * s), int(10 * s), theme.STROKE, 1)
        paste_icon(c, "font", pad + int(14 * s), y + int(12 * s), int(26 * s),
                   sh.theme.accent)
        c.text(pad + int(52 * s), y + int(18 * s), "Раскладка клавиатуры",
               self.fs(12), theme.TEXT)
        c.text(pad + int(52 * s), y + int(34 * s),
               "Caps Lock переключает ENG / РУС · сейчас: %s" %
               ("РУС" if sh.layout == "ru" else "ENG"), self.fs(10), theme.TEXT_MUTE)
        y += int(64 * s)
        # about block
        c.text(pad, y, "О системе", self.fs(13), theme.TEXT, bold=True)
        y += int(22 * s)
        lines = ["Заря OS %s — живая система с флешки" % VERSION,
                 "Оболочка написана на Python: свой композитор, фреймбуфер, без X11",
                 "Ядро Linux %s · Debian base" % os.uname().release]
        for line in lines:
            c.text(pad, y, line, self.fs(11), theme.TEXT_DIM)
            y += int(18 * s)

        self._max_scroll = max(0, int(y + 12 * s) - view_bottom)
        self.scroll = max(0, min(self.scroll, self._max_scroll))
        c.oy = old_oy
        c.clip = old_clip
        if self._max_scroll > 0 and view_h > 0:
            track_w = max(2, int(3 * s))
            track_x = w - max(5, int(7 * s))
            c.rounded(track_x, view_top, track_w, view_h, track_w, theme.BG_ALT, 0.85)
            thumb_h = max(int(24 * s), int(view_h * view_h / max(1, view_h + self._max_scroll)))
            thumb_h = min(view_h, thumb_h)
            thumb_y = view_top + int((view_h - thumb_h) * self.scroll / max(1, self._max_scroll))
            c.rounded(track_x, thumb_y, track_w, thumb_h, track_w,
                      sh.theme.accent, 0.85)

    def on_mouse(self, kind, btn, x, y, ev):
        s = self.sc()
        view_top = int(66 * s)
        if kind == "wheel":
            if y >= view_top and self._max_scroll > 0:
                step = max(28, int(44 * s))
                self.scroll = max(0, min(self._max_scroll,
                                         self.scroll + int(ev.get("dy", 0)) * step))
                self._dirty = True
            return
        if kind != "down":
            return
        if y < view_top:
            return
        y += self.scroll
        sh = self.sh
        pad = int(22 * s)
        # accents row
        ay = int(76 * s) + int(26 * s)
        xpos = pad
        for name in theme.ACCENTS:
            sw = int(54 * s)
            if xpos <= x <= xpos + sw and ay <= y <= ay + sw:
                sh.set_accent(name)
                return
            xpos += sw + int(18 * s)
        # wallpapers
        wy = int(76 * s) + int(26 * s) + int(96 * s) + int(24 * s)
        tw = int((self.window.w - pad * 2 - 3 * int(14 * s)) / 4)
        th = int(tw * 0.56)
        for i, name in enumerate(theme.WALLPAPERS):
            tx = pad + i * (tw + int(14 * s))
            if tx <= x <= tx + tw and wy <= y <= wy + th:
                sh.set_wallpaper(name)
                return
        # scale slider
        sy = wy + th + int(34 * s) + int(30 * s)
        bx0 = pad + int(170 * s)
        bwid = self.window.w - bx0 - pad
        if bx0 - int(30 * s) <= x <= bx0 + bwid + int(30 * s) and sy - int(20 * s) <= y <= sy + int(20 * s):
            t = max(0.0, min(1.0, (x - bx0) / max(1, bwid)))
            sh.theme.scale = (0.8 + 0.7 * t) * sh.layout_scale
            sh.save_settings()
            sh.dirty = True


# ======================================================================
#  Справка
# ======================================================================
class AboutApp(App):
    app_id = "Справка"
    name = "Справка"
    icon = "info"

    def draw(self, c, w, h, focused):
        s = self.sc()
        sh = self.sh
        c.fill(0, 0, w, h, theme.BG, 1.0)
        pad = int(26 * s)
        # logo
        lr = int(30 * s)
        from draw import circle_mask
        c.blend_mask(pad, int(20 * s), circle_mask(int(lr * 2)), sh.theme.accent2, 1.0)
        c.blend_mask(pad + int(lr * 0.5), int(20 * s) + int(lr * 0.28),
                     circle_mask(int(lr * 1.05)), sh.theme.accent, 1.0)
        c.text(pad + lr * 2 + int(18 * s), int(34 * s), "Заря OS", self.fs(24),
               theme.TEXT, bold=True, anchor="lm")
        c.text(pad + lr * 2 + int(18 * s), int(62 * s), "версия " + VERSION,
               self.fs(12), theme.TEXT_MUTE, anchor="lm")
        y = int(100 * s)
        c.text(pad, y, "Что это такое", self.fs(14), theme.TEXT, bold=True)
        y += int(22 * s)
        for line in ["Live-система: загружается с флешки и работает из памяти,",
                     "ничего не записывая на диск компьютера.",
                     "Вся графика — собственная оболочка на Python поверх фреймбуфера."]:
            c.text(pad, y, line, self.fs(12), theme.TEXT_DIM)
            y += int(19 * s)
        y += int(14 * s)
        c.text(pad, y, "Горячие клавиши", self.fs(14), theme.TEXT, bold=True)
        y += int(24 * s)
        keys = [("F10", "открыть/закрыть лаунчер приложений"),
                ("Ctrl+Alt+T", "терминал"),
                ("Ctrl+Alt+F", "файлы"),
                ("Ctrl+Alt+N", "заметки"),
                ("Alt+Tab", "переключение окон"),
                ("F1", "эта справка"),
                ("F2", "настройки"),
                ("Caps Lock", "переключение раскладки ENG/РУС"),
                ("Alt+F4 / кнопка ×", "закрыть окно"),
                ("двойной клик", "развернуть окно")]
        for k, v in keys:
            c.rounded(pad, y - int(2 * s), int(110 * s), int(22 * s), int(6 * s),
                      theme.PANEL_HI, 1.0)
            c.text(pad + int(55 * s), y + int(9 * s), k, self.fs(11),
                   sh.theme.accent, bold=True, anchor="cc")
            c.text(pad + int(124 * s), y + int(9 * s), v, self.fs(12),
                   theme.TEXT_DIM, anchor="lc")
            y += int(26 * s)
        # footer
        c.rounded(pad, h - int(56 * s), w - pad * 2, int(38 * s), int(9 * s),
                  theme.PANEL, 1.0)
        c.text(w // 2, h - int(37 * s),
               "Собрано %s · ядро %s · %d×%d" %
               (time.strftime("%d.%m.%Y"), os.uname().release, sh.W, sh.H),
               self.fs(10), theme.TEXT_MUTE, anchor="cc")


APPS = {
    "Терминал": TerminalApp,
    "Файлы": FilesApp,
    "Заметки": NotesApp,
    "Система": SystemApp,
    "Настройки": SettingsApp,
    "Справка": AboutApp,
}


def make_app(name, shell):
    cls = APPS.get(name)
    if cls is None:
        cls = TerminalApp
    app = cls(shell)
    if name == "Терминал":
        app.spawn()
    return app
