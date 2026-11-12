"""Zarya shell - framebuffer backend.

Works on a real Linux framebuffer (/dev/fb0) and in a "fake" memory
framebuffer used for development/testing (ZARYA_FAKE=1024x768).
"""
import os
import struct
import fcntl
import numpy as np
from PIL import Image

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
FBIOPAN_DISPLAY = 0x4606


class Screen:
    def __init__(self, path="/dev/fb0"):
        self.fake = False
        fake = os.environ.get("ZARYA_FAKE")
        if not fake and not os.path.exists(path):
            # даём ядру/загрузчику время поднять фреймбуфер
            import time as _t
            for _ in range(20):
                if os.path.exists(path):
                    break
                _t.sleep(0.25)
            if not os.path.exists(path):
                raise RuntimeError(
                    "нет %s: ядро не создало фреймбуфер (нужен gfxpayload=keep "
                    "или video=)." % path)
        if fake or not os.path.exists(path):
            w, h = (int(v) for v in (fake or "1024x768").split("x"))
            self.fake = True
            self.path = None
            self.width, self.height = w, h
            self.bpp, self.stride, self.fb_size = 32, w * 4, w * h * 4
            self.ro, self.go, self.bo, self.ao = 0, 1, 2, 3
            self.membuf = np.zeros((h, self.stride), dtype=np.uint8)
            self.fd = None
            return

        self.fd = os.open(path, os.O_RDWR)
        self.path = path
        # fixed info (id, smem_start, smem_len, type, ..., line_length)
        fbuf = fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, b"\0" * 160)
        self.stride = struct.unpack_from("<I", fbuf, 48)[0] or 0
        self.fb_size = struct.unpack_from("<I", fbuf, 52)[0] or 0
        # variable info: xres,yres,xres_v,yres_v,xoff,yoff,bpp
        vbuf = fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, b"\0" * 160)
        xres, yres, _xv, _yv, _xo, _yo, bpp = struct.unpack_from("<7I", vbuf, 0)
        self.width, self.height, self.bpp = xres, yres, bpp
        if not self.stride:
            self.stride = xres * bpp // 8
        ro = struct.unpack_from("<I", vbuf, 32)[0]
        go = struct.unpack_from("<I", vbuf, 40)[0]
        bo = struct.unpack_from("<I", vbuf, 48)[0]
        ao = struct.unpack_from("<I", vbuf, 56)[0]
        self.ro, self.go, self.bo, self.ao = ro // 8, go // 8, bo // 8, ao // 8
        self.mm = None
        try:
            import mmap as _mmap
            self.mm = _mmap.mmap(self.fd, max(self.fb_size, self.stride * self.height),
                                 mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        except Exception:
            self.mm = None

    # ---------------------------------------------------------------- flush
    def flush(self, img):
        """Copy a PIL RGB image (screen sized) to the framebuffer."""
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height), Image.BILINEAR)
        if self.fake:
            self.membuf[:, : self.width * 4] = self._to_bytes(img).reshape(
                self.height, self.width * 4)
            return
        data = self._to_bytes(img)
        if self.mm is not None:
            if self.stride == self.width * (self.bpp // 8):
                self.mm.seek(0)
                self.mm.write(data.tobytes())
            else:
                bpp = self.bpp // 8
                for y in range(self.height):
                    self.mm.seek(y * self.stride)
                    self.mm.write(data[y].tobytes())
        else:
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, data.tobytes())

    def _to_bytes(self, img):
        a = np.asarray(img, dtype=np.uint8)  # (h, w, 3) RGB
        if self.bpp == 32:
            out = np.empty((self.height, self.width, 4), dtype=np.uint8)
            out[..., self.ro] = a[..., 0]
            out[..., self.go] = a[..., 1]
            out[..., self.bo] = a[..., 2]
            out[..., self.ao] = 0
            return out
        if self.bpp == 16:
            r = (a[..., 0].astype(np.uint16) >> 3) << 11
            g = (a[..., 1].astype(np.uint16) >> 2) << 5
            b = (a[..., 2].astype(np.uint16) >> 3)
            return (r | g | b).astype("<u2")
        return a

    def check_resize(self):
        """Вернуть новые (w, h), если фреймбуфер переинициализировался."""
        if self.fake or not self.path:
            return None
        try:
            with open("/sys/class/graphics/fb0/virtual_size") as f:
                w, h = (int(v) for v in f.read().strip().split(","))
        except Exception:
            return None
        if (w, h) != (self.width, self.height) and w > 0 and h > 0:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.__init__(self.path)
            return (self.width, self.height)
        return None

    def snapshot(self, path):
        """Save what is currently on screen as PNG (used for testing)."""
        if self.fake:
            buf = self.membuf[:, : self.width * 4].reshape(self.height, self.width, 4)
            img = Image.fromarray(buf[..., [self.ro, self.go, self.bo]].copy(), "RGB")
        else:
            arr = np.frombuffer(bytes(self.mm[: self.stride * self.height]), dtype=np.uint8)
            arr = arr.reshape(self.height, self.stride)[:, : self.width * 4]
            arr = arr.reshape(self.height, self.width, 4)
            img = Image.fromarray(arr[..., [self.ro, self.go, self.bo]].copy(), "RGB")
        img.save(path)
        return path

    def blank(self):
        if self.fake:
            self.membuf[:] = 0
        elif self.mm is not None:
            b = b"\0" * self.stride
            for y in range(self.height):
                self.mm.seek(y * self.stride)
                self.mm.write(b)
