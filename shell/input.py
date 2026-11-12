"""Zarya shell - input devices (/dev/input/event* => mouse + keyboard)."""
import os
import struct
import glob
import select
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_WHEEL, REL_HWHEEL = 0, 1, 8, 6
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

SHIFT = 42          # KEY_LEFTSHIFT
RSHIFT = 54
CTRL = 29
RCTRL = 97
ALT = 56
RALT = 100


class Keymap:
    """Two layouts: us and ru (phonetic-ish, standard Russian)."""

    US = {
        1: "\x1b", 2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7",
        9: "8", 10: "9", 11: "0", 12: "-", 13: "=", 14: "\b", 15: "\t",
        16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u",
        23: "i", 24: "o", 25: "p", 26: "[", 27: "]", 28: "\n",
        30: "a", 31: "s", 32: "d", 33: "f", 34: "g", 35: "h", 36: "j",
        37: "k", 38: "l", 39: ";", 40: "'", 41: "`", 43: "\\",
        44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m",
        51: ",", 52: ".", 53: "/", 57: " ",
        71: "7", 72: "8", 73: "9", 74: "-", 75: "4", 76: "5", 77: "6",
        78: "+", 79: "1", 80: "2", 81: "3", 82: "0", 83: ".",
        86: "<",  # iso key
        103: "\x1b[A", 105: "\x1b[D", 106: "\x1b[C", 108: "\x1b[B",
        110: "\x1b[H", 102: "\x1b[H", 107: "\x1b[F", 104: "\x1b[5~",
        109: "\x1b[6~", 111: "\x1b[3~",
    }
    US_SHIFT = {
        2: "!", 3: "@", 4: "#", 5: "$", 6: "%", 7: "^", 8: "&", 9: "*",
        10: "(", 11: ")", 12: "_", 13: "+", 26: "{", 27: "}", 39: ":",
        40: '"', 41: "~", 43: "|", 51: "<", 52: ">", 53: "?", 86: ">",
    }
    RU = {
        16: "й", 17: "ц", 18: "у", 19: "к", 20: "е", 21: "н", 22: "г",
        23: "ш", 24: "щ", 25: "з", 26: "х", 27: "ъ", 30: "ф", 31: "ы",
        32: "в", 33: "а", 34: "п", 35: "р", 36: "о", 37: "л", 38: "д",
        39: "ж", 40: "э", 43: "\\", 44: "я", 45: "ч", 46: "с", 47: "м",
        48: "и", 49: "т", 50: "ь", 51: "б", 52: "ю", 53: ".",
    }
    RU_SHIFT = {26: "Х", 27: "Ъ", 39: "Ж", 40: "Э", 51: "Б", 52: "Ю", 53: ",",
                43: "/"}
    RU_UP = {16: "Й", 17: "Ц", 18: "У", 19: "К", 20: "Е", 21: "Н", 22: "Г",
             23: "Ш", 24: "Щ", 25: "З", 30: "Ф", 31: "Ы", 32: "В", 33: "А",
             34: "П", 35: "Р", 36: "О", 37: "Л", 38: "Д", 44: "Я", 45: "Ч",
             46: "С", 47: "М", 48: "И", 49: "Т", 50: "Ь", 53: ","}

    def __init__(self):
        self.layout = "us"

    def char(self, code, shift):
        if self.layout == "ru":
            if shift:
                if code in self.RU_SHIFT:
                    return self.RU_SHIFT[code]
                if code in self.RU_UP:
                    return self.RU_UP[code]
            if code in self.RU:
                return self.RU[code]
        if shift and code in self.US_SHIFT:
            return self.US_SHIFT[code]
        ch = self.US.get(code)
        if ch and len(ch) == 1 and ch.isalpha():
            return ch.upper() if shift else ch
        return ch


def _identify(fd):
    """Return 'mouse' or 'kbd' for an opened evdev device."""
    import fcntl
    EVIOCGBIT_KEY = (2 << 30) | (96 << 16) | (0x20 + 1) << 8 | ord("E")
    try:
        raw = bytearray(fcntl.ioctl(fd, EVIOCGBIT_KEY, b"\0" * 96))
    except Exception:
        return None

    def isset(code):
        return raw[code // 8] >> (code % 8) & 1

    if isset(BTN_LEFT):
        return "mouse"
    if isset(30) or isset(57):   # KEY_A, KEY_SPACE
        return "kbd"
    return None


class InputManager:
    def __init__(self, on_event, width, height):
        self.on_event = on_event
        self.w, self.h = width, height
        self.keymap = Keymap()
        self.mouse = (width // 2, height // 2)
        self.fds = {}
        self.pending_click = None
        self.shift = self.ctrl = self.alt = False
        if os.environ.get("ZARYA_FAKE"):
            return
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except Exception:
                continue
            kind = _identify(fd)
            if kind:
                self.fds[fd] = kind
        self._grab_kbd_state = {}

    # ------------------------------------------------------------------ poll
    def poll(self, timeout=0.05):
        if not self.fds:
            time.sleep(timeout)
            return
        try:
            r, _, _ = select.select(list(self.fds), [], [], timeout)
        except Exception:
            time.sleep(timeout)
            return
        for fd in r:
            try:
                data = os.read(fd, 24 * 64)
            except Exception:
                continue
            for off in range(0, len(data) - 23, 24):
                _s, _u, typ, code, value = struct.unpack_from("<llHHi", data, off)
                if self.fds.get(fd) == "mouse":
                    self._mouse_event(typ, code, value)
                else:
                    self._kbd_event(typ, code, value)

    def _mouse_event(self, typ, code, value):
        if typ == EV_REL and code == REL_X:
            self.mouse = (min(max(self.mouse[0] + value, 0), self.w - 1), self.mouse[1])
            self.on_event({"type": "move", "x": self.mouse[0], "y": self.mouse[1]})
        elif typ == EV_REL and code == REL_Y:
            self.mouse = (self.mouse[0], min(max(self.mouse[1] + value, 0), self.h - 1))
            self.on_event({"type": "move", "x": self.mouse[0], "y": self.mouse[1]})
        elif typ == EV_REL and code in (REL_WHEEL, REL_HWHEEL):
            self.on_event({"type": "wheel", "x": self.mouse[0], "y": self.mouse[1],
                           "dy": -value})
        elif typ == EV_KEY:
            btn = {BTN_LEFT: "left", BTN_RIGHT: "right", BTN_MIDDLE: "middle"}.get(code)
            if btn:
                self.on_event({"type": "down" if value else "up", "btn": btn,
                               "x": self.mouse[0], "y": self.mouse[1]})

    def _kbd_event(self, typ, code, value):
        if typ != EV_KEY:
            return
        if code in (SHIFT, RSHIFT):
            self.shift = bool(value)
            return
        if code in (CTRL, RCTRL):
            self.ctrl = bool(value)
            return
        if code in (ALT, RALT):
            self.alt = bool(value)
            return
        if value == 0:
            self.on_event({"type": "keyup", "code": code})
            return
        if value == 2:
            return  # ignore autorepeat, we generate our own if needed
        mods = {"shift": self.shift, "ctrl": self.ctrl, "alt": self.alt}
        if self.ctrl and self.alt:
            mods["ctrl"] = mods["alt"] = True
        ch = self.keymap.char(code, self.shift)
        if code == 58:  # caps lock toggles layout (handy on live media)
            self.keymap.layout = "ru" if self.keymap.layout == "us" else "us"
            self.on_event({"type": "layout", "layout": self.keymap.layout})
            return
        self.on_event({"type": "key", "code": code, "ch": ch, "mods": mods})
