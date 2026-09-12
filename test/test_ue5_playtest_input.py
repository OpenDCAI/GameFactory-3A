"""Regression tests for the UE5 Windows playtest input phases."""
from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest import mock

from engine_adapters.ue5.playtest import client as playtest


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    """The Win32 INPUT layout used by the SendInput call under test."""

    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ki", _KEYBDINPUT),
        ("padding", ctypes.c_byte * 8),
    ]


class UE5WindowsPlaytestInputTests(unittest.TestCase):
    """Keep the Windows phase contract independent of a running UE project."""

    process_id = 1234
    window_handle = 5678
    attack_vk = 0x4A
    keyup_flag = 0x0002

    def make_user32(self) -> mock.Mock:
        user32 = mock.Mock()
        user32.EnumWindows.side_effect = (
            lambda callback, lparam: callback(self.window_handle, lparam)
        )
        user32.GetWindowThreadProcessId.side_effect = self.set_process_id
        user32.IsWindowVisible.return_value = True
        return user32

    def set_process_id(self, _hwnd: int, owner: object) -> None:
        owner._obj.value = self.process_id

    def windows_api(self, user32: mock.Mock):
        def winfunctype(*_args):
            def identity(callback):
                return callback

            return identity

        return mock.patch.multiple(
            playtest.ctypes,
            windll=SimpleNamespace(user32=user32),
            WINFUNCTYPE=winfunctype,
            create=True,
        )

    def call_windows_phase(self, phase: str):
        user32 = self.make_user32()
        sent_edges: list[tuple[int, int]] = []

        def send_input(_count: int, item_pointer: object, _size: int):
            item = ctypes.cast(item_pointer, ctypes.POINTER(_INPUT)).contents
            sent_edges.append((item.ki.wVk, item.ki.dwFlags))
            return 1

        user32.SendInput.side_effect = send_input

        with (
            self.windows_api(user32),
            # Foreground-window behavior is outside this phase regression.
            mock.patch.object(
                playtest,
                "_windows_foreground_window",
                return_value=True,
                create=True,
            ),
            mock.patch.object(playtest.time, "sleep") as sleep,
        ):
            result = playtest._windows_key_event(
                self.process_id, "j", phase
            )
        return result, sent_edges, sleep

    def test_attack_tap_reaches_windows_as_key_down_then_key_up(self):
        events = playtest.UE5PlaytestClient._timeline(
            [{"action": "attack", "duration_ms": 200}]
        )
        self.assertEqual(events, [(0, "tap", "j")])
        result, sent_edges, sleep = self.call_windows_phase(events[0][1])

        self.assertEqual(result, (True, ""))
        self.assertEqual(
            sent_edges,
            [(self.attack_vk, 0), (self.attack_vk, self.keyup_flag)],
        )
        sleep.assert_called_once_with(playtest._TAP_HOLD_SECONDS)

    def test_windows_down_and_up_remain_single_edges(self):
        for phase, expected_down in (("down", True), ("up", False)):
            with self.subTest(phase=phase):
                result, sent_edges, sleep = self.call_windows_phase(phase)

                self.assertEqual(result, (True, ""))
                self.assertEqual(
                    sent_edges,
                    [
                        (
                            self.attack_vk,
                            0 if expected_down else self.keyup_flag,
                        ),
                    ],
                )
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
