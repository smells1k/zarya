import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shell"))
from input import (ABS_X, ABS_Y, BTN_LEFT, EV_ABS, EV_KEY, EV_REL, REL_X,
                   _EVENT_SIZE, _EVENT_STRUCT, InputManager)


class InputManagerTests(unittest.TestCase):
    def setUp(self):
        self.fake_env = patch.dict(os.environ, {"ZARYA_FAKE": "100x50"})
        self.fake_env.start()
        self.addCleanup(self.fake_env.stop)
        self.events = []
        self.manager = InputManager(self.events.append, 100, 50)
        self.addCleanup(self.manager.close)

    def test_amd64_input_event_uses_linux_64_bit_timeval_layout(self):
        self.assertEqual(_EVENT_SIZE, 24)
        # The seconds and microseconds are 64-bit fields in Linux's native
        # struct input_event on this amd64-only image; standard-size longs are
        # 32-bit and silently shift every mouse event field.
        data = _EVENT_STRUCT.pack(1_700_000_000, 123_456, EV_REL, REL_X, 7)
        sec, usec, event_type, code, value = _EVENT_STRUCT.unpack(data)
        self.assertEqual((sec, usec, event_type, code, value),
                         (1_700_000_000, 123_456, EV_REL, REL_X, 7))

    def test_poll_decodes_amd64_evdev_mouse_events(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        self.manager.fds[read_fd] = {"kind": "mouse"}
        self.manager._last_scan = time.monotonic()
        stream = b"".join((
            _EVENT_STRUCT.pack(1_700_000_000, 123_456, EV_REL, REL_X, 10),
            _EVENT_STRUCT.pack(1_700_000_000, 123_457, EV_KEY, BTN_LEFT, 1),
        ))
        os.write(write_fd, stream)

        with patch("input.select.select", return_value=([read_fd], [], [])):
            self.manager.poll(timeout=0)

        self.assertEqual(self.manager.mouse, (60, 25))
        self.assertEqual(self.events, [
            {"type": "move", "x": 60, "y": 25},
            {"type": "down", "btn": "left", "x": 60, "y": 25},
        ])

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
        self.assertEqual(self.manager.mouse_moves, 1)
        self.manager._mouse_event({}, EV_KEY, BTN_LEFT, 1)
        self.assertEqual(self.events[-1]["type"], "down")
        self.assertEqual(self.events[-1]["btn"], "left")

    def test_vt_fallback_delivers_text_enter_and_arrows(self):
        self.manager._tty_pending.extend(b"a\r\x1b[A")
        self.manager._parse_tty()
        self.assertEqual([(e["code"], e["ch"]) for e in self.events],
                         [(0, "a"), (28, "\n"), (103, "\x1b[A")])
        self.assertEqual(self.manager.key_events, 3)

    def test_status_makes_missing_input_visible(self):
        status = self.manager.status()
        self.assertIn("мышь нет/0", status)
        self.assertIn("кл. нет/0", status)


if __name__ == "__main__":
    unittest.main()
