"""Zarya shell - tiny anti-aliased 2D drawing toolkit on top of numpy + PIL fonts."""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
]


def _font_path(name):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


_REG = _font_path("DejaVuSans.ttf")
_BOLD = _font_path("DejaVuSans-Bold.ttf")
_MONO = _font_path("DejaVuSansMono.ttf")
_font_cache = {}
_mask_cache = {}
_MEASURE_CACHE = {}


def font(size, bold=False, mono=False):
    key = (size, bold, mono)
    f = _font_cache.get(key)
    if f is None:
        path = _MONO if mono else (_BOLD if bold else _REG)
        try:
            f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        except Exception:
            f = ImageFont.load_default()
        _font_cache[key] = f
    return f


def text_mask(s, size, bold=False, mono=False):
    """Cached uint8 alpha mask of a string, plus its (w, h) and bearing."""
    key = (s, size, bold, mono)
    m = _mask_cache.get(key)
    if m is not None:
        return m
    f = font(size, bold, mono)
    try:
        bbox = f.getbbox(s or " ")
    except Exception:
        bbox = (0, 0, size // 2, size)
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    img = Image.new("L", (w + 2, h + 2), 0)
    d = ImageDraw.Draw(img)
    d.text((1 - bbox[0], 1 - bbox[1]), s, fill=255, font=f)
    arr = (np.asarray(img, dtype=np.float32) / 255.0)
    # trim transparent rows/cols to get tight box
    ys = np.where(arr.max(axis=1) > 0)[0]
    xs = np.where(arr.max(axis=0) > 0)[0]
    if len(ys) and len(xs):
        arr = arr[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
        ox, oy = xs[0] - 1 + bbox[0], ys[0] - 1 + bbox[1]
    else:
        ox, oy = 0, bbox[1]
    arr = arr.astype(np.float32)
    if len(_mask_cache) > 3000:
        _mask_cache.clear()
    _mask_cache[key] = (arr, ox, oy)
    return _mask_cache[key]


def measure(s, size, bold=False, mono=False):
    key = (s, size, bold, mono)
    v = _MEASURE_CACHE.get(key)
    if v is None:
        f = font(size, bold, mono)
        try:
            w = f.getlength(s)
        except Exception:
            w = len(s) * size * 0.55
        v = int(round(w))
        _MEASURE_CACHE[key] = v
    return v


def mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def lighten(c, t):
    return mix(c, (255, 255, 255), t)


def darken(c, t):
    return mix(c, (0, 0, 0), t)


# ------------------------------------------------------------------ masks
_rrect_cache = {}


def rounded_mask(w, h, r, aa=3):
    """Anti-aliased rounded-rectangle coverage mask (float32 0..1), cached."""
    key = (int(w), int(h), int(r), aa)
    hit = _rrect_cache.get(key)
    if hit is not None:
        return hit
    if len(_rrect_cache) > 600:
        _rrect_cache.clear()
    m = _rounded_mask_uncached(w, h, r, aa)
    _rrect_cache[key] = m
    return m


def _rounded_mask_uncached(w, h, r, aa=3):
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    if r == 0:
        return np.ones((h, w), dtype=np.float32)
    s = aa
    W, H = w * s, h * s
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    xx = (xx + 0.5) / s - w / 2.0
    yy = (yy + 0.5) / s - h / 2.0
    qx = np.abs(xx) - (w / 2.0 - r)
    qy = np.abs(yy) - (h / 2.0 - r)
    d = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2) + \
        np.minimum(np.maximum(qx, qy), 0) - r
    cov = np.clip(0.5 - d, 0, 1)
    return cov.reshape(h, s, w, s).mean(axis=(1, 3)).astype(np.float32)


_circle_cache = {}


def circle_mask(d, aa=2):
    key = (int(d), aa)
    hit = _circle_cache.get(key)
    if hit is not None:
        return hit
    m = _circle_mask_uncached(d, aa)
    if len(_circle_cache) > 200:
        _circle_cache.clear()
    _circle_cache[key] = m
    return m


def _circle_mask_uncached(d, aa=2):
    d = max(1, int(d))
    r = d / 2.0
    yy, xx = np.mgrid[0:d, 0:d].astype(np.float32)
    xx += 0.5
    yy += 0.5
    dist = np.sqrt((xx - r) ** 2 + (yy - r) ** 2)
    return np.clip(r - dist + 0.5, 0, 1).astype(np.float32)


def ring_mask(d, width, aa=2):
    d = max(2, int(d))
    r = d / 2.0
    yy, xx = np.mgrid[0:d, 0:d].astype(np.float32)
    xx += 0.5
    yy += 0.5
    dist = np.sqrt((xx - r) ** 2 + (yy - r) ** 2)
    outer = np.clip(r - dist + 0.5, 0, 1)
    inner = np.clip(dist - (r - width) + 0.5, 0, 1)
    return (outer * inner).astype(np.float32)


def _box_blur(a, radius):
    if radius <= 0:
        return a
    k = 2 * radius + 1
    p = np.pad(a, ((radius + 1, radius), (0, 0)), mode="edge")
    c = np.cumsum(p, axis=0)
    a = (c[k:] - c[:-k]) / k
    p = np.pad(a, ((0, 0), (radius + 1, radius)), mode="edge")
    c = np.cumsum(p, axis=1)
    return (c[:, k:] - c[:, :-k]) / k


_shadow_cache = {}


def shadow_mask(w, h, r, blur, spread=0):
    """Blurred rounded-rect used as a drop shadow; returns (mask, pad)."""
    key = (w, h, r, blur, spread)
    if key in _shadow_cache:
        return _shadow_cache[key]
    pad = blur * 2 + spread + 2
    W, H = w + 2 * pad, h + 2 * pad
    canvas = np.zeros((H, W), dtype=np.float32)
    rm = rounded_mask(w + 2 * spread, h + 2 * spread, r + spread)
    canvas[pad - spread:pad - spread + h + 2 * spread,
           pad - spread:pad - spread + w + 2 * spread] = rm
    for _ in range(3):
        canvas = _box_blur(canvas, max(1, blur // 2))
    if len(_shadow_cache) > 200:
        _shadow_cache.clear()
    _shadow_cache[key] = (canvas.astype(np.float32), pad)
    return _shadow_cache[key]


# ------------------------------------------------------------------ canvas
class Canvas:
    """RGB canvas with alpha compositing helpers."""

    def __init__(self, w, h, color=(0, 0, 0)):
        self.w, self.h = w, h
        self.ox = 0
        self.oy = 0
        self.clip = None          # (x0, y0, x1, y1) in canvas coords
        self.buf = np.zeros((h, w, 3), dtype=np.uint8)
        if color != (0, 0, 0):
            self.buf[:] = color

    # -- low level -----------------------------------------------------
    def _clip_rect(self, x, y, w, h):
        """Apply origin + clip. Returns (x0,y0,x1,y1,sx,sy) or None."""
        x = int(x) + self.ox
        y = int(y) + self.oy
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + int(w)), min(self.h, y + int(h))
        if self.clip is not None:
            cx0, cy0, cx1, cy1 = self.clip
            x0, y0 = max(x0, cx0), max(y0, cy0)
            x1, y1 = min(x1, cx1), min(y1, cy1)
        if x0 >= x1 or y0 >= y1:
            return None
        return x0, y0, x1, y1, x0 - x, y0 - y

    def blend_mask(self, x, y, mask, color, alpha=1.0):
        if mask is None or alpha <= 0:
            return
        mh, mw = mask.shape
        if self.clip is not None:
            xc, yc = int(x) + self.ox, int(y) + self.oy
            if xc + mw < self.clip[0] or xc > self.clip[2] or \
               yc + mh < self.clip[1] or yc > self.clip[3]:
                return
        x, y = int(x) + self.ox, int(y) + self.oy
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + mw), min(self.h, y + mh)
        if self.clip is not None:
            x0, y0 = max(x0, self.clip[0]), max(y0, self.clip[1])
            x1, y1 = min(x1, self.clip[2]), min(y1, self.clip[3])
        if x0 >= x1 or y0 >= y1:
            return
        m = np.clip(mask[y0 - y:y1 - y, x0 - x:x1 - x], 0, 1)
        if alpha != 1.0:
            m = m * alpha
        sub = self.buf[y0:y1, x0:x1].astype(np.float32)
        col = np.array(color, dtype=np.float32)
        np.add(sub * (1 - m[..., None]), col[None, None, :] * m[..., None],
               out=sub, casting="unsafe")
        self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)

    def blend_mask_grad(self, x, y, mask, c1, c2, vertical=True, alpha=1.0):
        mh, mw = mask.shape
        x, y = int(x) + self.ox, int(y) + self.oy
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + mw), min(self.h, y + mh)
        if self.clip is not None:
            x0, y0 = max(x0, self.clip[0]), max(y0, self.clip[1])
            x1, y1 = min(x1, self.clip[2]), min(y1, self.clip[3])
        if x0 >= x1 or y0 >= y1:
            return
        x0, x1 = max(x0, x), min(x1, x + mw)
        y0, y1 = max(y0, y), min(y1, y + mh)
        if x0 >= x1 or y0 >= y1:
            return
        m = mask[y0 - y:y1 - y, x0 - x:x1 - x]
        n = mh if vertical else mw
        t = np.linspace(0, 1, n, dtype=np.float32)
        a = np.array(c1, dtype=np.float32)
        b = np.array(c2, dtype=np.float32)
        grad = a[None, None, :] + (b - a)[None, None, :] * t[:, None, None]
        if not vertical:
            grad = np.transpose(grad, (1, 0, 2))
        if vertical:
            grad = grad[y0 - y:y1 - y]
        else:
            grad = grad[:, x0 - x:x1 - x]
        if grad.shape != (m.shape[0], m.shape[1], 3):
            grad = np.broadcast_to(grad, (m.shape[0], m.shape[1], 3))
        m3 = np.clip(m * alpha, 0, 1)[..., None]
        sub = self.buf[y0:y1, x0:x1].astype(np.float32)
        np.add(sub * (1 - m3), grad * m3, out=sub, casting="unsafe")
        self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)

    def fill(self, x, y, w, h, color, alpha=1.0):
        r = self._clip_rect(x, y, w, h)
        if r is None:
            return
        x0, y0, x1, y1, _sx, _sy = r
        if alpha >= 1.0:
            self.buf[y0:y1, x0:x1] = color
        else:
            sub = self.buf[y0:y1, x0:x1].astype(np.float32)
            col = np.array(color, dtype=np.float32)
            np.add(sub * (1 - alpha), col * alpha, out=sub, casting="unsafe")
            self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)

    def vgrad(self, x, y, w, h, c1, c2, alpha=1.0):
        r = self._clip_rect(x, y, w, h)
        if r is None:
            return
        x0, y0, x1, y1, _sx, _sy = r
        n = y1 - y0
        t = np.linspace(0, 1, n, dtype=np.float32)
        a = np.array(c1, dtype=np.float32)
        b = np.array(c2, dtype=np.float32)
        grad = a[None, :] + (b - a)[None, :] * t[:, None]
        if alpha >= 1.0:
            self.buf[y0:y1, x0:x1] = grad[:, None, :].astype(np.uint8)
        else:
            sub = self.buf[y0:y1, x0:x1].astype(np.float32)
            np.add(sub * (1 - alpha), (grad[:, None, :]) * alpha, out=sub, casting="unsafe")
            self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)

    def hgrad(self, x, y, w, h, c1, c2, alpha=1.0):
        r = self._clip_rect(x, y, w, h)
        if r is None:
            return
        x0, y0, x1, y1, _sx, _sy = r
        n = x1 - x0
        t = np.linspace(0, 1, n, dtype=np.float32)
        a = np.array(c1, dtype=np.float32)
        b = np.array(c2, dtype=np.float32)
        grad = a[None, :] + (b - a)[None, :] * t[:, None]
        if alpha >= 1.0:
            self.buf[y0:y1, x0:x1] = grad[None, :, :].astype(np.uint8)
        else:
            sub = self.buf[y0:y1, x0:x1].astype(np.float32)
            np.add(sub * (1 - alpha), grad[None, :, :] * alpha, out=sub, casting="unsafe")
            self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)

    def rounded(self, x, y, w, h, r, color, alpha=1.0):
        r = int(min(r, w // 2, h // 2))
        self.blend_mask(x, y, rounded_mask(int(w), int(h), r), color, alpha)

    def rounded_grad(self, x, y, w, h, r, c1, c2, alpha=1.0, vertical=True):
        r = int(min(r, w // 2, h // 2))
        self.blend_mask_grad(x, y, rounded_mask(int(w), int(h), r), c1, c2,
                             vertical, alpha)

    def border(self, x, y, w, h, r, color, width=1, alpha=1.0):
        r = int(min(r, w // 2, h // 2))
        outer = rounded_mask(int(w), int(h), r)
        inner = np.zeros_like(outer)
        iw, ih = int(w) - 2 * width, int(h) - 2 * width
        if iw > 0 and ih > 0:
            im = rounded_mask(iw, ih, max(0, r - width))
            inner[width:width + ih, width:width + iw] = im
        self.blend_mask(x, y, np.clip(outer - inner, 0, 1), color, alpha)

    def circle(self, cx, cy, r, color, alpha=1.0):
        d = int(round(r * 2))
        self.blend_mask(cx - d / 2, cy - d / 2, circle_mask(d), color, alpha)

    def ring(self, cx, cy, r, width, color, alpha=1.0):
        d = int(round(r * 2)) + 1
        self.blend_mask(cx - d / 2, cy - d / 2, ring_mask(d, width), color, alpha)

    def shadow(self, x, y, w, h, r, blur=8, alpha=0.35, spread=0, color=(0, 0, 0)):
        m, pad = shadow_mask(int(w), int(h), int(r), blur, spread)
        self.blend_mask(x - pad, y - pad, m, color, alpha)

    def line(self, x1, y1, x2, y2, color, width=1, alpha=1.0):
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        if y1 == y2:
            self.fill(min(x1, x2), y1, abs(x2 - x1) + 1, width, color, alpha)
            return
        if x1 == x2:
            self.fill(x1, min(y1, y2), width, abs(y2 - y1) + 1, color, alpha)
            return
        steps = max(abs(x2 - x1), abs(y2 - y1))
        xs = np.linspace(x1, x2, steps + 1).astype(int)
        ys = np.linspace(y1, y2, steps + 1).astype(int)
        h = max(1, width)
        for px, py in zip(xs, ys):
            self.fill(px, py, h, h, color, alpha)

    def text(self, x, y, s, size=14, color=(255, 255, 255), bold=False, mono=False,
             anchor="lt", alpha=1.0):
        if not s:
            return 0
        m, ox, oy = text_mask(s, size, bold, mono)
        mh, mw = m.shape
        if anchor[0] == "c":
            x = x - mw / 2
        elif anchor[0] == "r":
            x = x - mw
        if anchor[1] in ("c", "m"):
            y = y - mh / 2
        elif anchor[1] == "b":
            y = y - mh
        self.blend_mask(x, y, m, color, alpha)
        return mw

    def text_width(self, s, size=14, bold=False, mono=False):
        return measure(s, size, bold, mono)

    # -- image ----------------------------------------------------------
    def image(self):
        return Image.fromarray(self.buf, "RGB")

    def blit_pil(self, img, x=0, y=0, alpha=1.0):
        arr = np.asarray(img.convert("RGB"), dtype=np.float32)
        h, w = arr.shape[:2]
        x, y = int(x) + self.ox, int(y) + self.oy
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if self.clip is not None:
            x0, y0 = max(x0, self.clip[0]), max(y0, self.clip[1])
            x1, y1 = min(x1, self.clip[2]), min(y1, self.clip[3])
        if x0 >= x1 or y0 >= y1:
            return
        sub = arr[y0 - y:y1 - y, x0 - x:x1 - x]
        dst = self.buf[y0:y1, x0:x1].astype(np.float32)
        np.add(dst * (1 - alpha), sub * alpha, out=dst, casting="unsafe")
        self.buf[y0:y1, x0:x1] = dst.astype(np.uint8)

    def region(self, x, y, w, h):
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, int(x + w)), min(self.h, int(y + h))
        c = Canvas(x1 - x0, y1 - y0)
        c.buf[:] = self.buf[y0:y1, x0:x1]
        return c

    def paste_canvas(self, other, x, y, alpha=1.0):
        h, w = other.h, other.w
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x0 >= x1 or y0 >= y1:
            return
        sub = other.buf[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
        if alpha >= 1.0:
            self.buf[y0:y1, x0:x1] = sub.astype(np.uint8)
        else:
            dst = self.buf[y0:y1, x0:x1].astype(np.float32)
            np.add(dst * (1 - alpha), sub * alpha, out=dst, casting="unsafe")
            self.buf[y0:y1, x0:x1] = dst.astype(np.uint8)


# ------------------------------------------------------------------ icons
_ICON_CACHE = {}


def draw_icon(c, name, x, y, size, color, alpha=1.0):
    """Hand-drawn vector-ish icons (scaled strokes)."""
    s = size
    u = s / 24.0
    lw = max(1, int(round(1.8 * u)))
    def P(px, py):
        return x + px * u, y + py * u
    def ln(pts, w=lw):
        for i in range(len(pts) - 1):
            c.line(*P(*pts[i]), *P(*pts[i + 1]), color, w, alpha)
    def dot(px, py, r):
        c.circle(*P(px, py), r * u, color, alpha)

    if name == "term":
        c.border(x, y, s, s, int(4 * u), color, lw, alpha)
        ln([(6.5, 8.5), (10, 12), (6.5, 15.5)])
        ln([(12.5, 16), (17.5, 16)])
    elif name == "folder":
        c.border(x, y + 3 * u, s, s - 3 * u, int(3 * u), color, lw, alpha)
        ln([(4, 7), (10, 7), (11.5, 4), (4, 4), (4, 7)])
    elif name == "files":
        c.border(x + 1 * u, y + 2 * u, s - 4 * u, s - 3 * u, int(3 * u), color, lw, alpha)
        ln([(4.5, 7.5), (20, 7.5)], max(1, lw - 1))
        ln([(4.5, 11.5), (16, 11.5)], max(1, lw - 1))
        ln([(4.5, 15.5), (18, 15.5)], max(1, lw - 1))
    elif name == "gear":
        c.ring(*P(12, 12), 5.2 * u, lw, color, alpha)
        c.ring(*P(12, 12), 2.0 * u, lw, color, alpha)
        for a in range(0, 360, 45):
            import math
            r1, r2 = 6.4 * u, 8.8 * u
            ax = math.radians(a)
            c.line(x + 12 * u + r1 * math.cos(ax), y + 12 * u + r1 * math.sin(ax),
                   x + 12 * u + r2 * math.cos(ax), y + 12 * u + r2 * math.sin(ax),
                   color, lw, alpha)
    elif name == "net":
        c.ring(*P(12, 12), 8.6 * u, lw, color, alpha)
        ln([(3.6, 12), (20.4, 12)], max(1, lw - 1))
        ln([(12, 3.6), (12, 20.4)], max(1, lw - 1))
        ln([(5.5, 6.5), (18.5, 6.5)], max(1, lw - 2))
        ln([(5.5, 17.5), (18.5, 17.5)], max(1, lw - 2))
        ln([(7.2, 12), (8.6, 8.4), (12, 7.2), (15.4, 8.4), (16.8, 12),
            (15.4, 15.6), (12, 16.8), (8.6, 15.6), (7.2, 12)], max(1, lw - 2))
    elif name == "power":
        c.ring(*P(12, 13), 7.6 * u, lw, color, alpha)
        c.fill(x + 12 * u - lw / 2, y + 3.2 * u, lw, 9 * u, color, alpha)
    elif name == "note":
        c.border(x + 3 * u, y + 2.5 * u, s - 6 * u, s - 5 * u, int(2.5 * u), color, lw, alpha)
        ln([(7, 8), (17, 8)], max(1, lw - 1))
        ln([(7, 12), (17, 12)], max(1, lw - 1))
        ln([(7, 16), (13, 16)], max(1, lw - 1))
    elif name == "info":
        c.ring(*P(12, 12), 8.6 * u, lw, color, alpha)
        dot(12, 7.5, 1.15)
        c.fill(x + 12 * u - lw / 2, y + 10.5 * u, lw, 6.5 * u, color, alpha)
    elif name == "close":
        ln([(7, 7), (17, 17)], lw)
        ln([(17, 7), (7, 17)], lw)
    elif name == "max":
        c.border(x + 6.5 * u, y + 6.5 * u, 11 * u, 11 * u, int(2 * u), color, lw, alpha)
    elif name == "min":
        ln([(7, 15.5), (17, 15.5)], lw)
    elif name == "chev_l":
        ln([(14, 6), (8, 12), (14, 18)], lw + 1)
    elif name == "chev_r":
        ln([(10, 6), (16, 12), (10, 18)], lw + 1)
    elif name == "chev_d":
        ln([(6, 10), (12, 16), (18, 10)], lw + 1)
    elif name == "up":
        ln([(12, 18), (12, 6)], lw + 1)
        ln([(6, 11), (12, 5), (18, 11)], lw + 1)
    elif name == "edit":
        ln([(5, 19), (8, 18.4), (19, 7.4), (16.6, 5), (5.6, 16), (5, 19)], lw)
    elif name == "trash":
        ln([(4.5, 6.5), (19.5, 6.5)], lw)
        ln([(9, 6.5), (9.6, 3.6), (14.4, 3.6), (15, 6.5)], lw)
        ln([(6.5, 6.5), (8, 20.5), (16, 20.5), (17.5, 6.5)], lw)
    elif name == "cpu":
        c.border(x + 6 * u, y + 6 * u, 12 * u, 12 * u, int(2 * u), color, lw, alpha)
        for k in (9.5, 12, 14.5):
            c.line(x + k * u, y + 2.5 * u, x + k * u, y + 6 * u, color, max(1, lw - 1), alpha)
            c.line(x + k * u, y + 18 * u, x + k * u, y + 21.5 * u, color, max(1, lw - 1), alpha)
            c.line(x + 2.5 * u, y + k * u, x + 6 * u, y + k * u, color, max(1, lw - 1), alpha)
            c.line(x + 18 * u, y + k * u, x + 21.5 * u, y + k * u, color, max(1, lw - 1), alpha)
    elif name == "disk":
        c.rounded(x + 2.5 * u, y + 3 * u, 19 * u, 18 * u, int(3 * u), color, alpha)
        c.fill(x + 6 * u, y + 3 * u, 12 * u, 7 * u, lighten(color, 0.0))
    elif name == "font":
        c.text(x, y - 2 * u, "A", int(s * 0.95), color, bold=True, alpha=alpha)
    elif name == "eye":
        ln([(3, 12), (7, 7), (12, 5.5), (17, 7), (21, 12)], lw)
        ln([(3, 12), (7, 17), (12, 18.5), (17, 17), (21, 12)], lw)
        c.circle(*P(12, 12), 2.6 * u, color, alpha)
    elif name == "check":
        ln([(5, 12.5), (10, 17.5), (19, 6.5)], lw + 1)
    elif name == "plus":
        ln([(12, 5), (12, 19)], lw)
        ln([(5, 12), (19, 12)], lw)
    elif name == "refresh":
        import math
        for a in range(-40, 240, 8):
            ax = math.radians(a)
        c.ring(*P(12, 12), 7.6 * u, lw, color, alpha)
        c.fill(x + 12 * u - 0.5 * lw, y + 2 * u, 5.5 * u, 3.5 * u, (0, 0, 0), 0)
    else:
        c.border(x + 2 * u, y + 2 * u, s - 4 * u, s - 4 * u, int(3 * u), color, lw, alpha)


def icon_canvas(name, size, color, alpha=1.0):
    key = (name, size, color, alpha)
    hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    pad = 2
    cv = Canvas(size + pad * 2, size + pad * 2, (0, 0, 0))
    # transparent-ish: we render on black then treat black as transparent via mask
    tmp = Canvas(size + pad * 2, size + pad * 2)
    tmp.buf[:] = color
    # draw icon on black bg then use difference as alpha
    draw_icon(tmp, name, pad, pad, size, (0, 0, 0), 1.0)
    m = (tmp.buf.max(axis=2) == 0).astype(np.float32)
    cv.buf[:] = color
    _ICON_CACHE[key] = (cv, m)
    return cv, m


def paste_icon(c, name, x, y, size, color, alpha=1.0):
    cv, m = icon_canvas(name, size, color)
    c.blend_mask(x - 2, y - 2, m, color, alpha)
