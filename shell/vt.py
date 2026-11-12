"""Zarya shell - small VT100/xterm screen emulator (no external deps)."""
import re

CSI_RE = re.compile(rb"\x1b\[([0-9;?]*)([@-~])")
OSC_RE = re.compile(rb"\x1b\][^\x07]*\x07")


class Cell:
    __slots__ = ("ch", "fg", "bg", "bold", "italic", "under")

    def __init__(self, ch=" ", fg=None, bg=None, bold=False, under=False):
        self.ch, self.fg, self.bg, self.bold, self.under = ch, fg, bg, bold, under

    def copy(self):
        c = Cell(self.ch, self.fg, self.bg, self.bold, self.under)
        return c


class Screen:
    """Grid of cells with cursor + scrollback."""

    def __init__(self, cols, rows, scrollback=1000):
        self.cols, self.rows = cols, rows
        self.scrollback = scrollback
        self.reset()

    def reset(self):
        self.grid = [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]
        self.history = []
        self.x = self.y = 0
        self.fg = None
        self.bg = None
        self.bold = False
        self.under = False
        self.pending_wrap = False
        self.scroll_top, self.scroll_bot = 0, self.rows - 1
        self.alt = False
        self.saved = None

    def resize(self, cols, rows):
        if cols == self.cols and rows == self.rows:
            return
        new = [[Cell() for _ in range(cols)] for _ in range(rows)]
        for y in range(min(rows, self.rows)):
            for x in range(min(cols, self.cols)):
                new[y][x] = self.grid[y][x]
        self.grid = new
        self.cols, self.rows = cols, rows
        self.x = min(self.x, cols - 1)
        self.y = min(self.y, rows - 1)
        self.scroll_top, self.scroll_bot = 0, rows - 1

    # -------------------------------------------------------------- output
    def feed(self, data: bytes):
        if OSC_RE.search(data):
            data = OSC_RE.sub(b"", data)
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b == 0x1B:
                m = CSI_RE.match(data, i)
                if m:
                    self._csi(m.group(1), m.group(2).decode("latin1"))
                    i = m.end()
                    continue
                if i + 1 < n:
                    c = chr(data[i + 1])
                    self._esc(c, data, i)
                    i += 2
                    continue
                i += 1
                continue
            if b == 0x0A:
                self.newline()
            elif b == 0x0D:
                self.x = 0
                self.pending_wrap = False
            elif b == 0x08:
                self.x = max(0, self.x - 1)
                self.pending_wrap = False
            elif b == 0x09:
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif b == 0x07:
                pass
            else:
                # decode utf-8 character
                ch, ln = self._decode(data, i)
                if ch is not None:
                    self.put(ch)
                    i += ln
                    continue
            i += 1

    def _decode(self, data, i):
        b = data[i]
        if b < 0x80:
            return chr(b), 1
        for ln in (2, 3, 4):
            chunk = data[i:i + ln]
            if len(chunk) < ln:
                continue
            try:
                return chunk.decode("utf-8"), ln
            except UnicodeDecodeError:
                continue
        return None, 1

    def _esc(self, c, data, i):
        if c == "[":
            return
        if c == "7":
            self.saved = (self.x, self.y)
        elif c == "8":
            if self.saved:
                self.x, self.y = self.saved
        elif c == "D":
            self.newline()
        elif c == "M":
            self.reverse_index()
        elif c == "c":
            self.reset()
        elif c == ")":
            pass

    def _csi(self, params, final):
        raw = params.decode("latin1")
        priv = raw.startswith("?")
        if priv:
            raw = raw[1:]
        nums = [int(p) if p else 0 for p in raw.split(";")] if raw else []
        p1 = nums[0] if nums else 0
        p2 = nums[1] if len(nums) > 1 else 0

        if final == "m":
            self._sgr(nums or [0])
        elif final in "Hf":
            self.y = min(self.rows - 1, max(0, (p1 or 1) - 1))
            self.x = min(self.cols - 1, max(0, (p2 or 1) - 1))
            self.pending_wrap = False
        elif final == "A":
            self.y = max(self.scroll_top, self.y - (p1 or 1))
        elif final == "B":
            self.y = min(self.rows - 1, self.y + (p1 or 1))
        elif final == "C":
            self.x = min(self.cols - 1, self.x + (p1 or 1))
        elif final == "D":
            self.x = max(0, self.x - (p1 or 1))
        elif final == "E":
            self.y = min(self.rows - 1, self.y + (p1 or 1))
            self.x = 0
        elif final == "F":
            self.y = max(0, self.y - (p1 or 1))
            self.x = 0
        elif final == "G" or final == "`":
            self.x = min(self.cols - 1, max(0, (p1 or 1) - 1))
        elif final == "d":
            self.y = min(self.rows - 1, max(0, (p1 or 1) - 1))
        elif final == "J":
            self._erase_display(p1)
        elif final == "K":
            self._erase_line(p1)
        elif final == "L":
            self._insert_lines(p1 or 1)
        elif final == "M":
            self._delete_lines(p1 or 1)
        elif final == "P":
            self._delete_chars(p1 or 1)
        elif final == "@":
            self._insert_chars(p1 or 1)
        elif final == "X":
            for k in range(p1 or 1):
                if self.x + k < self.cols:
                    self.grid[self.y][self.x + k] = self._blank()
        elif final == "r" and not priv:
            self.scroll_top = max(0, (p1 or 1) - 1)
            self.scroll_bot = min(self.rows - 1, (p2 or self.rows) - 1)
            self.x = self.y = self.scroll_top
        elif final in "hl":
            if priv and "1049" in (raw or ""):
                self._alt_screen(final == "h")
            elif priv and "25" in (raw or ""):
                pass
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            if self.saved:
                self.x, self.y = self.saved

    def _alt_screen(self, on):
        if on and not self.alt:
            self.alt = True
            self._saved_grid = self.grid
            self._saved_hist = self.history
            self.grid = [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]
            self.x = self.y = 0
        elif not on and self.alt:
            self.alt = False
            self.grid = getattr(self, "_saved_grid", self.grid)
            self.history = getattr(self, "_saved_hist", [])

    def _sgr(self, nums):
        i = 0
        while i < len(nums):
            n = nums[i]
            if n == 0:
                self.fg = self.bg = None
                self.bold = self.under = False
            elif n == 1:
                self.bold = True
            elif n == 4:
                self.under = True
            elif n == 22:
                self.bold = False
            elif n == 24:
                self.under = False
            elif 30 <= n <= 37:
                self.fg = n - 30
            elif n == 39:
                self.fg = None
            elif 40 <= n <= 47:
                self.bg = n - 40
            elif n == 49:
                self.bg = None
            elif 90 <= n <= 97:
                self.fg = n - 90 + 8
            elif 100 <= n <= 107:
                self.bg = n - 100 + 8
            elif n in (38, 48):
                target = "fg" if n == 38 else "bg"
                if i + 1 < len(nums) and nums[i + 1] == 5 and i + 2 < len(nums):
                    setattr(self, target, 16 + nums[i + 2])
                    i += 2
                elif i + 1 < len(nums) and nums[i + 1] == 2 and i + 4 < len(nums):
                    r, g, b = nums[i + 2], nums[i + 3], nums[i + 4]
                    setattr(self, target, (r, g, b))
                    i += 4
            i += 1

    def _blank(self):
        return Cell(" ", None, self.bg, False, False)

    def _erase_display(self, mode):
        if mode == 0:
            self._erase_line(0)
            for y in range(self.y + 1, self.rows):
                self.grid[y] = [self._blank() for _ in range(self.cols)]
        elif mode == 1:
            self._erase_line(1)
            for y in range(0, self.y):
                self.grid[y] = [self._blank() for _ in range(self.cols)]
        else:
            for y in range(self.rows):
                self.grid[y] = [self._blank() for _ in range(self.cols)]

    def _erase_line(self, mode):
        row = self.grid[self.y]
        if mode == 0:
            rng = range(self.x, self.cols)
        elif mode == 1:
            rng = range(0, self.x + 1)
        else:
            rng = range(0, self.cols)
        for x in rng:
            row[x] = self._blank()

    def _insert_lines(self, n):
        for _ in range(n):
            if self.scroll_top <= self.y <= self.scroll_bot:
                del self.grid[self.scroll_bot]
                self.grid.insert(self.y, [self._blank() for _ in range(self.cols)])

    def _delete_lines(self, n):
        for _ in range(n):
            if self.scroll_top <= self.y <= self.scroll_bot:
                del self.grid[self.y]
                self.grid.insert(self.scroll_bot, [self._blank() for _ in range(self.cols)])

    def _delete_chars(self, n):
        row = self.grid[self.y]
        for _ in range(n):
            if self.x < self.cols:
                del row[self.x]
                row.append(self._blank())

    def _insert_chars(self, n):
        row = self.grid[self.y]
        for _ in range(n):
            row.insert(self.x, self._blank())
            row.pop()

    # ----------------------------------------------------------------- in
    def newline(self):
        self.pending_wrap = False
        if self.y == self.scroll_bot:
            self.scroll_up()
        else:
            self.y = min(self.rows - 1, self.y + 1)

    def reverse_index(self):
        if self.y == self.scroll_top:
            self.grid.insert(self.scroll_top, [self._blank() for _ in range(self.cols)])
            del self.grid[self.scroll_bot + 1]
        else:
            self.y = max(0, self.y - 1)

    def scroll_up(self):
        line = self.grid.pop(self.scroll_top)
        self.grid.insert(self.scroll_bot, [self._blank() for _ in range(self.cols)])
        if not self.alt:
            self.history.append(line)
            if len(self.history) > self.scrollback:
                self.history.pop(0)

    def put(self, ch):
        if self.pending_wrap:
            self.x = 0
            self.newline()
            self.pending_wrap = False
        if self.x >= self.cols:
            self.x = self.cols - 1
        cell = Cell(ch, self.fg, self.bg, self.bold, self.under)
        self.grid[self.y][self.x] = cell
        if self.x == self.cols - 1:
            self.pending_wrap = True
        else:
            self.x += 1

    def line_text(self, y):
        return "".join(c.ch for c in self.grid[y]).rstrip()

    def all_lines(self, include_history=60):
        lines = ["".join(c.ch for c in row).rstrip() for row in self.history[-include_history:]]
        lines += ["".join(c.ch for c in row).rstrip() for row in self.grid]
        return lines
