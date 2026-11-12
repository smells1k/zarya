"""Zarya shell input: Linux evdev devices plus a no-echo VT keyboard fallback."""
import atexit
import fcntl
import glob
import os
import select
import struct
import termios
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_WHEEL, REL_HWHEEL = 0, 1, 8, 6
ABS_X, ABS_Y = 0, 1
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOUCH, BTN_TOOL_FINGER, BTN_TOOL_PEN = 0x14A, 0x145, 0x140

SHIFT = 42          # KEY_LEFTSHIFT
RSHIFT = 54
CTRL = 29
RCTRL = 97
ALT = 56
RALT = 100

# Linux input_event is 24 bytes on the amd64 live image.
_EVENT_SIZE = 24
_KEY_BITS_SIZE = 96


def _iowr_read(number, size):
    """Linux _IOC(_IOC_READ, 'E', number, size), used by evdev ioctls."""
    return (2 << 30) | (size << 16) | (number << 8) | ord("E")


def _event_bits(fd, event_type, size):
    try:
        return fcntl.ioctl(fd, _iowr_read(0x20 + event_type, size), b"\0" * size)
    except (OSError, ValueError):
        return b""


def _isset(bits, code):
    return code >= 0 and code // 8 < len(bits) and bool(bits[code // 8] & (1 << (code % 8)))


def _identify(fd):
    """Return a device description for a keyboard/pointer, or None."""
    event_types = _event_bits(fd, EV_SYN, 8)
    key_bits = _event_bits(fd, EV_KEY, _KEY_BITS_SIZE)
    rel_bits = _event_bits(fd, EV_REL, 16)
    abs_bits = _event_bits(fd, EV_ABS, 16)

    has_rel_xy = _isset(event_types, EV_REL) and (
        _isset(rel_bits, REL_X) or _isset(rel_bits, REL_Y))
    abs_pair = None
    for x_code, y_code in ((ABS_X, ABS_Y), (ABS_MT_POSITION_X, ABS_MT_POSITION_Y)):
        if _isset(event_types, EV_ABS) and _isset(abs_bits, x_code) and _isset(abs_bits, y_code):
            abs_pair = (x_code, y_code)
            break
    has_abs_xy = abs_pair is not None
    pointer_buttons = any(_isset(key_bits, code) for code in
                          (BTN_LEFT, BTN_RIGHT, BTN_TOUCH, BTN_TOOL_FINGER, BTN_TOOL_PEN))

    if has_rel_xy or has_abs_xy:
        # Prefer a pointer classification before keyboard: some USB tablets
        # expose a small set of key capabilities as well as pointer axes.
        if pointer_buttons:
            return {"kind": "mouse", "abs_pair": abs_pair}
        keyboard_keys = any(_isset(key_bits, code) for code in
                            (1, 14, 15, 28, 30, 57, 103, 105, 106, 108))
        if not keyboard_keys:
            return {"kind": "mouse", "abs_pair": abs_pair}

    keyboard_keys = any(_isset(key_bits, code) for code in
                        (1, 14, 15, 28, 30, 57, 103, 105, 106, 108))
    if keyboard_keys:
        return {"kind": "kbd", "abs_pair": None}
    return None


def _abs_range(fd, code):
    """Read min/max for EV_ABS, falling back to the common tablet range."""
    try:
        raw = fcntl.ioctl(fd, _iowr_read(0x40 + code, 24), b"\0" * 24)
        _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack("<6i", raw)
        if maximum > minimum:
            return minimum, maximum
    except (OSError, struct.error, ValueError):
        pass
    return 0, 32767


class Keymap:
    """Two layouts: US and Russian (standard Russian keyboard layout)."""

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
        86: "<", 103: "\x1b[A", 105: "\x1b[D", 106: "\x1b[C", 108: "\x1b[B",
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


class InputManager:
    def __init__(self, on_event, width, height):
        self.on_event = on_event
        self.w, self.h = max(1, width), max(1, height)
        self.keymap = Keymap()
        self.mouse = (self.w // 2, self.h // 2)
        self.fds = {}       # fd -> device description
        self.paths = {}     # path -> fd
        self.pending_click = None
        self.shift = self.ctrl = self.alt = False
        self._last_scan = 0.0
        self._tty_fd = None
        self._tty_old = None
        self._tty_pending = bytearray()
        self._tty_escape_at = 0.0
        self._closed = False
        self._fake = bool(os.environ.get("ZARYA_FAKE"))
        if self._fake:
            return

        self._scan_devices()
        self._open_tty()
        atexit.register(self.close)

    def resize(self, width, height):
        self.w, self.h = max(1, width), max(1, height)
        self._move_to(*self.mouse)

    def _scan_devices(self):
        self._last_scan = time.monotonic()
        for path in sorted(glob.glob("/dev/input/event*")):
            if path in self.paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
            except OSError:
                continue
            info = _identify(fd)
            if not info:
                os.close(fd)
                continue
            info["path"] = path
            if info["kind"] == "mouse" and info.get("abs_pair"):
                x_code, y_code = info["abs_pair"]
                info["abs_x_code"], info["abs_y_code"] = x_code, y_code
                info["abs_x_range"] = _abs_range(fd, x_code)
                info["abs_y_range"] = _abs_range(fd, y_code)
                info["abs_x"] = sum(info["abs_x_range"]) // 2
                info["abs_y"] = sum(info["abs_y_range"]) // 2
            self.fds[fd] = info
            self.paths[path] = fd

    def _open_tty(self):
        """Stop VT line-echo; use its byte stream as a keyboard fallback."""
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))
            original = termios.tcgetattr(fd)
            raw = [original[0], original[1], original[2], original[3],
                   original[4], original[5], list(original[6])]
            raw[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                        termios.ISIG | getattr(termios, "IEXTEN", 0))
            raw[6][termios.VMIN] = 0
            raw[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, raw)
            self._tty_fd, self._tty_old = fd, original
        except (OSError, termios.error):
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._tty_fd is not None:
            try:
                if self._tty_old is not None:
                    termios.tcsetattr(self._tty_fd, termios.TCSANOW, self._tty_old)
            except (OSError, termios.error):
                pass
            try:
                os.close(self._tty_fd)
            except OSError:
                pass
            self._tty_fd = None
        for fd in list(self.fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()
        self.paths.clear()

    # --------------------------------------------------------------- polling
    def poll(self, timeout=0.05):
        now = time.monotonic()
        if now - self._last_scan >= 2.0:
            self._scan_devices()

        readers = list(self.fds)
        if self._tty_fd is not None:
            readers.append(self._tty_fd)
        if not readers:
            time.sleep(timeout)
            return
        try:
            ready, _, _ = select.select(readers, [], [], timeout)
        except (OSError, ValueError):
            time.sleep(timeout)
            return
        for fd in ready:
            if fd == self._tty_fd:
                self._poll_tty()
                continue
            info = self.fds.get(fd)
            if info is None:
                continue
            try:
                data = os.read(fd, _EVENT_SIZE * 64)
            except BlockingIOError:
                continue
            except OSError:
                self._drop_fd(fd)
                continue
            if not data:
                self._drop_fd(fd)
                continue
            for off in range(0, len(data) - (_EVENT_SIZE - 1), _EVENT_SIZE):
                _sec, _usec, typ, code, value = struct.unpack_from("<llHHi", data, off)
                if info["kind"] == "mouse":
                    self._mouse_event(info, typ, code, value)
                else:
                    self._kbd_event(typ, code, value)
        if self._tty_fd is not None and not any(
                fd == self._tty_fd for fd in ready):
            self._parse_tty()

    def _drop_fd(self, fd):
        info = self.fds.pop(fd, None)
        if info and self.paths.get(info.get("path")) == fd:
            self.paths.pop(info["path"], None)
        try:
            os.close(fd)
        except OSError:
            pass

    # --------------------------------------------------------------- pointer
    def _move_to(self, x, y):
        x = min(max(int(x), 0), self.w - 1)
        y = min(max(int(y), 0), self.h - 1)
        if (x, y) == self.mouse:
            return
        self.mouse = (x, y)
        self.on_event({"type": "move", "x": x, "y": y})

    @staticmethod
    def _scale_abs(value, limits, pixels):
        minimum, maximum = limits
        if maximum <= minimum:
            return min(max(int(value), 0), pixels - 1)
        pos = (int(value) - minimum) * (pixels - 1) / float(maximum - minimum)
        return min(max(int(round(pos)), 0), pixels - 1)

    def _mouse_event(self, info, typ, code, value):
        if typ == EV_REL and code == REL_X:
            self._move_to(self.mouse[0] + value, self.mouse[1])
        elif typ == EV_REL and code == REL_Y:
            self._move_to(self.mouse[0], self.mouse[1] + value)
        elif typ == EV_ABS and code == info.get("abs_x_code"):
            info["abs_x"] = value
            self._move_to(self._scale_abs(value, info["abs_x_range"], self.w),
                          self._scale_abs(info["abs_y"], info["abs_y_range"], self.h))
        elif typ == EV_ABS and code == info.get("abs_y_code"):
            info["abs_y"] = value
            self._move_to(self._scale_abs(info["abs_x"], info["abs_x_range"], self.w),
                          self._scale_abs(value, info["abs_y_range"], self.h))
        elif typ == EV_REL and code in (REL_WHEEL, REL_HWHEEL):
            self.on_event({"type": "wheel", "x": self.mouse[0], "y": self.mouse[1],
                           "dy": -value})
        elif typ == EV_KEY:
            btn = {BTN_LEFT: "left", BTN_TOUCH: "left", BTN_RIGHT: "right",
                   BTN_MIDDLE: "middle"}.get(code)
            if btn:
                self.on_event({"type": "down" if value else "up", "btn": btn,
                               "x": self.mouse[0], "y": self.mouse[1]})

    # ---------------------------------------------------------------- keyboard
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
            return  # ignore kernel repeat; widgets can implement their own
        mods = {"shift": self.shift, "ctrl": self.ctrl, "alt": self.alt}
        ch = self.keymap.char(code, self.shift)
        if code == 58:  # Caps Lock toggles US/Russian layout.
            self.keymap.layout = "ru" if self.keymap.layout == "us" else "us"
            self.on_event({"type": "layout", "layout": self.keymap.layout})
            return
        self.on_event({"type": "key", "code": code, "ch": ch, "mods": mods})

    # ---------------------------------------------------------- VT fallback
    def _poll_tty(self):
        try:
            data = os.read(self._tty_fd, 256)
        except BlockingIOError:
            data = b""
        except OSError:
            return
        if data:
            # Drain the VT even when evdev is active, preventing an input queue
            # from filling; evdev remains the authoritative key source then.
            has_evdev_keyboard = any(d["kind"] == "kbd" for d in self.fds.values())
            if has_evdev_keyboard:
                self._tty_pending.clear()
                return
            self._tty_pending.extend(data)
        self._parse_tty()

    def _tty_key(self, code, ch):
        self.on_event({"type": "key", "code": code, "ch": ch,
                       "mods": {"shift": False, "ctrl": False, "alt": False}})

    def _parse_tty(self):
        sequences = {
            b"\x1b[A": (103, "\x1b[A"), b"\x1b[B": (108, "\x1b[B"),
            b"\x1b[C": (106, "\x1b[C"), b"\x1b[D": (105, "\x1b[D"),
            b"\x1b[H": (102, "\x1b[H"), b"\x1b[F": (107, "\x1b[F"),
            b"\x1b[1~": (102, "\x1b[H"), b"\x1b[3~": (111, "\x1b[3~"),
            b"\x1b[5~": (104, "\x1b[5~"), b"\x1b[6~": (109, "\x1b[6~"),
            b"\x1b[[A": (59, ""), b"\x1b[[B": (60, ""),
            b"\x1b[[C": (61, ""), b"\x1b[[D": (62, ""),
        }
        while self._tty_pending:
            data = bytes(self._tty_pending)
            if data[0] == 0x1B:
                matched = None
                for seq in sorted(sequences, key=len, reverse=True):
                    if data.startswith(seq):
                        matched = seq
                        break
                if matched:
                    del self._tty_pending[:len(matched)]
                    code, ch = sequences[matched]
                    self._tty_key(code, ch)
                    self._tty_escape_at = 0.0
                    continue
                if any(seq.startswith(data) for seq in sequences):
                    if not self._tty_escape_at:
                        self._tty_escape_at = time.monotonic()
                    if time.monotonic() - self._tty_escape_at < 0.12:
                        break
                # Unknown CSI/function-key sequence: consume it rather than
                # leaking its bytes into the active app as typed characters.
                if data.startswith(b"\x1b["):
                    end = next((i for i in range(2, len(data)) if 0x40 <= data[i] <= 0x7E), None)
                    if end is None and len(data) < 16:
                        if not self._tty_escape_at:
                            self._tty_escape_at = time.monotonic()
                        if time.monotonic() - self._tty_escape_at < 0.12:
                            break
                        end = len(data) - 1
                    if end is not None:
                        del self._tty_pending[:end + 1]
                        self._tty_escape_at = 0.0
                        continue
                del self._tty_pending[:1]
                self._tty_key(1, "\x1b")
                self._tty_escape_at = 0.0
                continue

            first = data[0]
            if first < 0x80:
                size = 1
            elif 0xC2 <= first <= 0xDF:
                size = 2
            elif 0xE0 <= first <= 0xEF:
                size = 3
            elif 0xF0 <= first <= 0xF4:
                size = 4
            else:
                size = 1
            if len(data) < size:
                break
            raw = bytes(self._tty_pending[:size])
            del self._tty_pending[:size]
            ch = raw.decode("utf-8", "replace")
            if ch in ("\r", "\n"):
                self._tty_key(28, "\n")
            elif ch in ("\x08", "\x7f"):
                self._tty_key(14, "\b")
            elif ch == "\t":
                self._tty_key(15, "\t")
            elif ch == "\x1b":
                self._tty_key(1, ch)
            else:
                self._tty_key(0, ch)


__all__ = ["InputManager", "Keymap", "EV_KEY", "EV_REL", "EV_ABS", "REL_X", "REL_Y",
           "ABS_X", "ABS_Y", "BTN_LEFT"]
