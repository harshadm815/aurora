"""
The Windows low-level keyboard hook that feeds keys.HoldKey.

Windows calls ``_callback`` for every key press in every program (that's
what a global shortcut needs). It only looks at the key code, never
stores or logs what you type, and answers within microseconds: Windows
drops hooks that are slow. Keys the state machine holds back (Space,
when it's the activation key) are typed again with SendInput, marked as
injected so the hook lets them through.

Must be installed on a thread with a message loop (the helper's Qt GUI
thread has one).
"""

import ctypes
import logging
import time
from ctypes import wintypes

from aurora.wake.keys import HoldKey

logger = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_INJECTED = 0x10
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)      # Shift, Ctrl, Alt, left and right Windows keys

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):     # only for the union's size
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", _MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KeyboardHook:
    """``install()`` / ``uninstall()``; ``on_event()`` is called after each relevant key event."""

    def __init__(self, machine: HoldKey, on_event=lambda: None):
        self.machine = machine
        self.on_event = on_event
        self._handle = None
        self._proc = HOOKPROC(self._callback)          # kept referenced: Windows calls it
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        self.user32.CallNextHookEx.restype = LRESULT
        self.user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

    def install(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._handle = self.user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "Couldn't watch the keyboard (SetWindowsHookEx failed)")
        logger.info("Wake: keyboard hook installed (%s)", self.machine.key)

    def uninstall(self) -> None:
        if self._handle:
            self.user32.UnhookWindowsHookEx(self._handle)
            self._handle = None
            logger.info("Wake: keyboard hook removed")

    def _modifiers(self) -> bool:
        return any(self.user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIERS)

    def _callback(self, code, wparam, lparam):
        if code == 0:      # HC_ACTION
            try:
                data = KBDLLHOOKSTRUCT.from_address(lparam)
                injected = bool(data.flags & LLKHF_INJECTED)
                now = time.monotonic()
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    decision = self.machine.key_down(data.vkCode, now, self._modifiers(), injected)
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    decision = self.machine.key_up(data.vkCode, now, injected)
                else:
                    decision = None
                if decision is not None:
                    if decision.inject:
                        self.send(decision.inject)
                    if self.machine.actions or data.vkCode == self.machine.vk:
                        self.on_event()            # the helper looks at the new state (outside this call)
                    if decision.swallow:
                        return 1
            except Exception:          # never let an error block the keyboard
                logger.exception("Wake: keyboard hook error")
        return self.user32.CallNextHookEx(None, code, wparam, lparam)

    def send(self, keys: list[tuple[int, bool]]) -> None:
        inputs = (INPUT * len(keys))()
        for item, (vk, down) in zip(inputs, keys):
            item.type = INPUT_KEYBOARD
            item.union.ki = KEYBDINPUT(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0, 0)
        self.user32.SendInput(len(keys), inputs, ctypes.sizeof(INPUT))
