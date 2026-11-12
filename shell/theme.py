"""Zarya shell - theme, colors and wallpaper rendering."""
import numpy as np
from PIL import Image, ImageFilter

# ------------------------------------------------------------------ palette
BG = (13, 15, 23)
BG_ALT = (18, 21, 31)
PANEL = (23, 27, 39)
PANEL_HI = (32, 37, 52)
STROKE = (48, 55, 76)
TEXT = (231, 236, 248)
TEXT_DIM = (158, 169, 193)
TEXT_MUTE = (112, 122, 146)

ACCENTS = {
    "Аврора": ((96, 165, 250), (139, 92, 246)),
    "Океан": ((56, 189, 248), (45, 212, 191)),
    "Закат": ((251, 113, 133), (251, 191, 36)),
    "Лес": ((74, 222, 128), (56, 189, 248)),
    "Гранат": ((244, 114, 182), (168, 85, 247)),
}

WALLPAPERS = ["Аврора", "Туманность", "Дюны", "Минимал"]


class Theme:
    def __init__(self):
        self.accent_name = "Аврора"
        self.accent, self.accent2 = ACCENTS[self.accent_name]
        self.wallpaper_name = "Аврора"
        self.scale = 1.0
        self.layout = "us"

    def set_accent(self, name):
        self.accent_name = name
        self.accent, self.accent2 = ACCENTS[name]

    # text sizes
    def fs(self, base):
        return max(9, int(round(base * self.scale)))


# ------------------------------------------------------------------ wallpaper
_cache = {}


def _smooth_noise(h, w, freq, seed=0):
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, int(freq)), max(2, int(freq * w / h))))
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32) / 255.0


def render_wallpaper(w, h, style="Аврора", accent=((96, 165, 250), (139, 92, 246))):
    key = (w, h, style, accent)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = xx / w
    ny = yy / h
    a1 = np.array(accent[0], dtype=np.float32)
    a2 = np.array(accent[1], dtype=np.float32)

    if style == "Аврора":
        top = np.array([7, 9, 18], dtype=np.float32)
        mid = np.array([16, 22, 40], dtype=np.float32)
        img = np.zeros((h, w, 3), dtype=np.float32)
        grad = np.clip(ny * 1.15, 0, 1)[..., None]
        img += top[None, None, :] * (1 - grad) + mid[None, None, :] * grad
        # warm dawn light near the horizon
        horizon = np.exp(-((ny - 0.86) ** 2) / (2 * 0.10 ** 2)) * \
                  np.exp(-((nx - 0.5) ** 2) / (2 * 0.42 ** 2))
        img += np.array([90, 54, 40], dtype=np.float32)[None, None, :] * horizon[..., None]
        # aurora ribbons
        for i, (col, amp) in enumerate(((a2, 1.0), (a1, 0.85), ((80, 220, 190), 0.55))):
            ph = 2.1 + i * 1.7
            band = 0.26 + i * 0.07 + 0.055 * np.sin(nx * (3.1 + i) * np.pi * 2 + ph) \
                + 0.03 * np.sin(nx * 7.3 * np.pi * 2 + ph * 2)
            ribbon = np.exp(-((ny - band) ** 2) / (2 * (0.045 + 0.012 * i) ** 2))
            wob = 0.55 + 0.45 * np.sin(nx * 4.4 * np.pi * 2 + i * 2.3)
            streak = _smooth_noise(8, w // 8, w // 8, seed=20 + i)[3:4, :]
            streak = np.asarray(Image.fromarray((streak * 255).astype(np.uint8))
                                .resize((w, 8), Image.BICUBIC), dtype=np.float32) / 255.0
            wob = wob * (0.70 + 0.55 * streak[:1, :])
            img += np.array(col, dtype=np.float32)[None, None, :] * \
                (ribbon * wob * 0.30 * amp)[..., None]
        # stars
        rng = np.random.default_rng(7)
        n = max(60, (w * h) // 9000)
        sx = rng.integers(0, w, n)
        sy = (rng.random(n) ** 1.5 * h * 0.62).astype(int)
        stars = np.zeros((h, w), dtype=np.float32)
        stars[sy, sx] = rng.random(n) * 0.9 + 0.1
        stars = np.asarray(Image.fromarray((np.clip(stars, 0, 1) * 255).astype(np.uint8))
                           .filter(ImageFilter.GaussianBlur(max(1, w // 900))),
                           dtype=np.float32) / 255.0
        img += stars[..., None] * np.array([210, 225, 255], dtype=np.float32)[None, None, :] * 1.6
        # mountain silhouettes
        for layer, (col, base_y, amp, freq) in enumerate((
                ((9, 12, 20), 0.755, 0.055, 3.0),
                ((6, 8, 14), 0.83, 0.040, 2.2))):
            ridge = base_y - amp * (0.55 + 0.45 * np.sin(nx * freq * np.pi * 2 + layer * 2.0)) \
                - amp * 0.35 * np.sin(nx * freq * 2.4 * np.pi * 2 + 1.0)
            mask = (ny > ridge).astype(np.float32)
            m = np.asarray(Image.fromarray((mask * 255).astype(np.uint8))
                           .filter(ImageFilter.GaussianBlur(1)), dtype=np.float32) / 255.0
            img = img * (1 - m[..., None]) + np.array(col, dtype=np.float32)[None, None, :] * m[..., None]
        # ground
        ground = (ny > 0.90).astype(np.float32)
        img = img * (1 - ground[..., None]) + np.array([7, 9, 15], dtype=np.float32)[None, None, :] * ground[..., None]

    elif style == "Туманность":
        img = np.zeros((h, w, 3), dtype=np.float32) + np.array([9, 8, 16], dtype=np.float32)
        for i, (col, cx, cy, r, s) in enumerate((
                (a2, 0.30, 0.34, 0.34, 0.55),
                (a1, 0.68, 0.52, 0.30, 0.45),
                ((240, 120, 190), 0.52, 0.78, 0.36, 0.22))):
            d = np.sqrt(((nx - cx) * (w / h)) ** 2 + (ny - cy) ** 2)
            glow = np.exp(-(d ** 2) / (2 * r ** 2))
            img += np.array(col, dtype=np.float32)[None, None, :] * (glow * s)[..., None]
        nz = np.zeros((h, w), dtype=np.float32)
        amp = 1.0
        tot = 0.0
        for oct_i, freq in enumerate((3, 6, 12, 24, 48)):
            nz += amp * _smooth_noise(h, w, freq, seed=3 + oct_i)
            tot += amp
            amp *= 0.55
        nz /= tot
        img *= (0.35 + 1.5 * nz)[..., None]
        rng = np.random.default_rng(11)
        n = max(80, (w * h) // 7000)
        sx = rng.integers(0, w, n)
        sy = rng.integers(0, h, n)
        stars = np.zeros((h, w), dtype=np.float32)
        stars[sy, sx] = rng.random(n) ** 2
        stars = np.asarray(Image.fromarray((stars * 255).astype(np.uint8))
                           .filter(ImageFilter.GaussianBlur(1)), dtype=np.float32) / 255.0
        img += stars[..., None] * 255 * 0.85

    elif style == "Дюны":
        img = np.zeros((h, w, 3), dtype=np.float32)
        grad = np.clip(ny, 0, 1)[..., None]
        top = np.array([24, 18, 34], dtype=np.float32)
        bot = np.array([255, 175, 110], dtype=np.float32)
        img += top * (1 - grad) ** 1.6 + bot * (grad ** 2.1)
        sun = np.exp(-(((nx - 0.72) * (w / h)) ** 2 + (ny - 0.44) ** 2) / (2 * 0.10 ** 2))
        img += np.array([255, 210, 150], dtype=np.float32)[None, None, :] * (sun * 0.9)[..., None]
        for i, (col, base_y, amp, freq) in enumerate((
                ((52, 34, 52), 0.62, 0.05, 1.4),
                ((34, 22, 38), 0.74, 0.045, 2.1),
                ((20, 13, 24), 0.86, 0.04, 3.0))):
            ridge = base_y - amp * (0.6 + 0.4 * np.sin(nx * freq * np.pi * 2 + i * 1.7))
            mask = (ny > ridge).astype(np.float32)
            m = np.asarray(Image.fromarray((mask * 255).astype(np.uint8))
                           .filter(ImageFilter.GaussianBlur(1)), dtype=np.float32) / 255.0
            img = img * (1 - m[..., None]) + np.array(col, dtype=np.float32)[None, None, :] * m[..., None]

    else:  # Минимал
        img = np.zeros((h, w, 3), dtype=np.float32)
        grad = np.clip(ny, 0, 1)[..., None]
        img += np.array([15, 18, 28], dtype=np.float32) * (1 - grad) + \
               np.array([10, 11, 17], dtype=np.float32) * grad
        d = np.sqrt((nx - 0.5) ** 2 * 1.7 + (ny - 0.46) ** 2)
        ring = np.exp(-((d - 0.30) ** 2) / (2 * 0.0012)) * 0.85
        img += a1[None, None, :] * ring[..., None]
        ring2 = np.exp(-((d - 0.345) ** 2) / (2 * 0.0009)) * 0.35
        img += a2[None, None, :] * ring2[..., None]
        glow = np.exp(-((d - 0.30) ** 2) / (2 * 0.02)) * 0.16
        img += a2[None, None, :] * glow[..., None]
        horizon = np.exp(-((ny - 0.80) ** 2) / (2 * 0.05 ** 2)) * 0.10
        img += a1[None, None, :] * horizon[..., None]
        sun = np.exp(-((nx - 0.5) ** 2 * 1.7 + (ny - 0.46) ** 2) / (2 * 0.004)) * 0.22
        img += np.array([255, 255, 255], dtype=np.float32)[None, None, :] * sun[..., None]

    grain = (np.random.default_rng(5).standard_normal((h, w, 1)) * 2.2).astype(np.float32)
    img += grain
    # vignette + slight grain
    vig = 1.0 - 0.35 * (((nx - 0.5) * 1.1) ** 2 + ((ny - 0.5) * 0.9) ** 2)
    img *= np.clip(vig, 0, 1)[..., None]
    img = np.clip(img, 0, 255).astype(np.uint8)
    out = Image.fromarray(img, "RGB")
    if len(_cache) > 6:
        _cache.clear()
    _cache[key] = out
    return out
