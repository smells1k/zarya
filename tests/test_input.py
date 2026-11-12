import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shell"))
from input import (ABS_X, ABS_Y, BTN_LEFT, EV_ABS, EV_KEY, EV_REL, REL_X,
                   InputManager)


class InputManagerTests(unittest.TestCase):
    def setUp(self):
        self.fake_env = patch.dict(os.environ, {"ZARYA_FAKE": "100x50"})
        self.fake_env.start()
        self.addCleanup(self.fake_env.stop)
        self.events = []
        self.manager = InputManager(self.events.append, 100, 50)
        self.addCleanup(self.manager.close)

    def test_absolute_tablet_position_is_scaled_to_framebuffer(self):
        device = {
            "abs_x_code": ABS_X, "abs_y_code": ABS_Y,
            "abs_x_range": (0, 1000), "abs_y_range": (0, 500),
            "abs_x": 500, "abs_y": 250,
        }
        self.manager._mouse_event(device, EV_ABS, ABS_X, 1000)
        self.manager._mouse_event(device, EV_ABS, ABS_Y, 500)
        self.assertEqual(self.manager.mouse, (99, 49))
        self.assertEqual(self.events[-1], {"type": "move", "x": 99, "y": 49})

    def test_absolute_position_is_clamped(self):
        self.assertEqual(self.manager._scale_abs(-10, (0, 1000), 100), 0)
        self.assertEqual(self.manager._scale_abs(1010, (0, 1000), 100), 99)

    def test_relative_mouse_and_click_events_are_kept(self):
        self.manager._mouse_event({}, EV_REL, REL_X, 10)
        self.assertEqual(self.manager.mouse, (60, 25))
        self.manager._mouse_event({}, EV_KEY, BTN_LEFT, 1)
        self.assertEqual(self.events[-1]["type"], "down")
        self.assertEqual(self.events[-1]["btn"], "left")

    def test_vt_fallback_delivers_text_enter_and_arrows(self):
        self.manager._tty_pending.extend(b"a\r\x1b[A")
        self.manager._parse_tty()
        self.assertEqual([(e["code"], e["ch"]) for e in self.events],
                         [(0, "a"), (28, "\n"), (103, "\x1b[A")])


if __name__ == "__main__":
    unittest.main()
