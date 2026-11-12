"""Dev harness: render the desktop with windows open to PNG (no real hardware)."""
import os
import sys
import time

os.environ.setdefault("ZARYA_FAKE", "1366x768")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/shell")

import shell as sh

app = sh.Zarya()
app.locked = False
app.lock_anim = 1.0

# open a set of windows for the shot
w1 = app.open_app("Файлы")
w2 = app.open_app("Терминал")
w3 = app.open_app("Система")
w1.x, w1.y, w1.w, w1.h = 60, 90, 620, 430
w2.x, w2.y, w2.w, w2.h = 420, 300, 700, 380
w3.x, w3.y, w3.w, w3.h = 760, 110, 520, 420
for w in app.windows:
    w.zoom = 1.0
app.focus_window(w2)

for _ in range(3):
    app.tick()
time.sleep(0.4)
app.tick()

t = time.time()
app.render()
print("render %.1f ms" % ((time.time() - t) * 1000))
app.canvas.image().save("/home/user/zarya/snap_desk.png")

# second shot: launcher open
app.launcher_open = True
app.launcher_query = ""
app.render()
app.canvas.image().save("/home/user/zarya/snap_launcher.png")
app.launcher_open = False

# third: settings window focused
w4 = app.open_app("Настройки")
w4.x, w4.y, w4.w, w4.h = 300, 120, 760, 560
w4.zoom = 1.0
app.focus_window(w4)
app.tick()
t = time.time()
app.render()
print("render %.1f ms" % ((time.time() - t) * 1000))
app.canvas.image().save("/home/user/zarya/snap_settings.png")
print("ok")
