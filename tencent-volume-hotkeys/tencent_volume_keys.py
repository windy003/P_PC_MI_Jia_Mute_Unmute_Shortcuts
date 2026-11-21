# -*- coding: utf-8 -*-
"""
腾讯应用引擎 · 音量开关
======================

用可自定义的全局快捷键给「腾讯应用引擎」（AndrowsVm.exe）开 / 关声音
（静音切换），以及按 5% 一步加减音量。

音量通过 Windows Core Audio API 的「每个应用独立音量」控制（就是系统音量合成器里
那一列），只影响目标应用，不影响其他程序。

程序常驻系统托盘，托盘图标和窗口图标使用程序目录下的 .ico 文件。

依赖:
    pip install pycaw comtypes psutil pystray pillow

运行:
    python tencent_volume_keys.py
    python tencent_volume_keys.py --minimized   # 启动后只留托盘图标

自检代码不在这个文件里，单独放在同目录的 selftest.py 中：
    python selftest.py                        # 自检（不注册快捷键、不改音量）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:
    import pystray
    from PIL import Image
except Exception as _tray_exc:  # noqa: BLE001
    TRAY_IMPORT_ERROR = _tray_exc
else:
    TRAY_IMPORT_ERROR = None

APP_TITLE = "腾讯应用引擎 · 音量开关"
APP_ID = "TencentAppVolumeKeys"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "app.log"

ACTION_ORDER = ("mute", "up", "down")
ACTION_LABELS = {"mute": "音量开关", "up": "音量 +", "down": "音量 −"}

DEFAULT_HOTKEYS = {
    "mute": "ctrl+alt+m",
    "up": "ctrl+alt+up",
    "down": "ctrl+alt+down",
}

# 每次按音量快捷键调整的幅度
VOLUME_STEP = 0.05

DEFAULT_CONFIG = {
    "hotkeys": dict(DEFAULT_HOTKEYS),
}

# 固定的目标进程：腾讯应用引擎
TARGET_EXE = "androwsvm.exe"


def find_icon_path():
    """项目目录下的 .ico 文件（优先 256x256.ico）。"""
    base = Path(__file__).resolve().parent
    preferred = base / "256x256.ico"
    if preferred.exists():
        return preferred
    for path in sorted(base.glob("*.ico")):
        return path
    return None


def icon_tk_path(path: Path) -> str:
    """Tk 在 Windows 上对反斜杠路径不友好，统一用正斜杠。"""
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------- 日志与配置

def log(message: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            LOG_FILE.write_text("", encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + message + "\n")
    except OSError:
        pass


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for key, value in raw.items():
        if key == "hotkeys" and isinstance(value, dict):
            for action in ACTION_ORDER:
                if isinstance(value.get(action), str):
                    cfg["hotkeys"][action] = value[action]
        elif key in cfg:
            cfg[key] = value
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- Windows 常量

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
ERROR_ALREADY_EXISTS = 183

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_uint]
user32.PeekMessageW.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostThreadMessageW.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.OpenProcess.argtypes = [ctypes.c_uint, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000


def enable_dpi_awareness() -> bool:
    """必须在创建任何窗口之前调用。

    否则 Windows 会把整个窗口按位图拉伸（150% 缩放下文字发虚）。
    """
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [wt.HANDLE]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        if user32.SetProcessDpiAwarenessContext(wt.HANDLE(-4)):  # PER_MONITOR_AWARE_V2
            return True
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:  # 每显示器 DPI 感知
            return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def apply_tk_scaling(root: tk.Tk) -> None:
    """让 Tk 的字号与真实 DPI 一致（Tk 一般能自己算对，这里只是兜底）。"""
    try:
        dpi = int(user32.GetDpiForSystem())
        current = float(root.tk.call("tk", "scaling")) * 72.0
        if dpi and abs(current - dpi) > 6:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except (tk.TclError, OSError, ValueError):
        pass


def wait_for_process_exit(pid: int, timeout_ms: int = 15000) -> bool:
    """等另一个进程退出（重启时用，确保它释放热键和单实例锁）。"""
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True  # 进程已经不存在，或没有权限打开，按已退出处理
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def relaunch_command(wait_pid: int, minimized: bool):
    """构造重新启动本程序的命令行。"""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        interpreter = Path(sys.executable)
        pythonw = interpreter.with_name("pythonw.exe")
        if pythonw.exists():
            interpreter = pythonw
        command = [str(interpreter), str(Path(__file__).resolve())]
    if minimized:
        command.append("--minimized")
    command.extend(["--restart-of", str(wait_pid)])
    return command


# ---------------------------------------------------------------- 快捷键解析

MOD_TOKENS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

KEY_TABLE = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
    "backspace": 0x08, "print": 0x2C, "pause": 0x13,
    "scrolllock": 0x91, "capslock": 0x14, "numlock": 0x90,
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "bracketleft": 0xDB, "bracketright": 0xDD, "grave": 0xC0,
    "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_multiply": 0x6A,
    "kp_divide": 0x6F, "kp_decimal": 0x6E, "kp_enter": 0x0D,
}
for _i in range(1, 25):
    KEY_TABLE[f"f{_i}"] = 0x6F + _i
for _i in range(10):
    KEY_TABLE[f"kp_{_i}"] = 0x60 + _i
for _ch in "abcdefghijklmnopqrstuvwxyz":
    KEY_TABLE[_ch] = ord(_ch.upper())
for _ch in "0123456789":
    KEY_TABLE[_ch] = ord(_ch)

ALIASES = {
    "prior": "pageup", "next": "pagedown",
    "page_up": "pageup", "page_down": "pagedown",
    "return": "enter", "escape": "esc",
    "del": "delete", "ins": "insert",
    "↑": "up", "↓": "down", "←": "left", "→": "right",
    "空格": "space", "回车": "enter",
}

_PRETTY_KEYS = {
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "space": "空格", "enter": "回车", "esc": "Esc",
    "pageup": "PageUp", "pagedown": "PageDown",
    "insert": "Insert", "delete": "Delete",
    "home": "Home", "end": "End", "tab": "Tab", "backspace": "Backspace",
    "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "quote": "'",
    "bracketleft": "[", "bracketright": "]", "grave": "`",
    "print": "PrintScreen", "pause": "Pause",
}

VK_TO_TOKEN = {}
VK_DISPLAY = {}
for _token, _code in KEY_TABLE.items():
    VK_TO_TOKEN.setdefault(_code, _token)
    if _code not in VK_DISPLAY:
        if _token in _PRETTY_KEYS:
            VK_DISPLAY[_code] = _PRETTY_KEYS[_token]
        elif _token.startswith("f") and _token[1:].isdigit():
            VK_DISPLAY[_code] = _token.upper()
        elif _token.startswith("kp_"):
            VK_DISPLAY[_code] = "Num" + _token[3:].capitalize()
        else:
            VK_DISPLAY[_code] = _token.upper()


def parse_hotkey(text: str):
    """把 "ctrl+alt+m" / "Ctrl + Alt + M" 解析为 (mods, vk)，非法时抛 ValueError。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("快捷键为空")
    mods = 0
    vk = None
    for part in text.replace("＋", "+").split("+"):
        token = part.strip().lower()
        if not token:
            continue
        token = ALIASES.get(token, token)
        if token in MOD_TOKENS:
            mods |= MOD_TOKENS[token]
        else:
            if vk is not None:
                raise ValueError("只能有一个主键")
            if token not in KEY_TABLE:
                raise ValueError("无法识别的按键：" + part.strip())
            vk = KEY_TABLE[token]
    if vk is None:
        raise ValueError("缺少主键（例如 M、F8）")
    if not mods and not 0x70 <= vk <= 0x87:
        raise ValueError("至少要包含 Ctrl、Alt、Shift、Win 中的一个")
    return mods, vk


def hotkey_text(pair) -> str:
    mods, vk = pair
    parts = []
    if mods & MOD_CONTROL:
        parts.append("ctrl")
    if mods & MOD_ALT:
        parts.append("alt")
    if mods & MOD_SHIFT:
        parts.append("shift")
    if mods & MOD_WIN:
        parts.append("win")
    parts.append(VK_TO_TOKEN.get(vk, "m"))
    return "+".join(parts)


def format_hotkey(text: str) -> str:
    try:
        mods, vk = parse_hotkey(text)
    except ValueError:
        return (text or "").strip()
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(VK_DISPLAY.get(vk, VK_TO_TOKEN.get(vk, "?")))
    return " + ".join(parts)


def keysym_to_token(keysym: str):
    if len(keysym) == 1:
        char = keysym.lower()
        if char.isalnum():
            return char
        return {
            "-": "minus", "=": "equal", ",": "comma", ".": "period",
            "/": "slash", "\\": "backslash", ";": "semicolon", "'": "quote",
            "[": "bracketleft", "]": "bracketright", "`": "grave", " ": "space",
        }.get(char)
    token = ALIASES.get(keysym.lower(), keysym.lower())
    return token if token in KEY_TABLE else None


def current_modifiers() -> int:
    mods = 0
    if user32.GetAsyncKeyState(0x11) & 0x8000:
        mods |= MOD_CONTROL
    if user32.GetAsyncKeyState(0x12) & 0x8000:
        mods |= MOD_ALT
    if user32.GetAsyncKeyState(0x10) & 0x8000:
        mods |= MOD_SHIFT
    if (user32.GetAsyncKeyState(0x5B) & 0x8000) or (user32.GetAsyncKeyState(0x5C) & 0x8000):
        mods |= MOD_WIN
    return mods


def build_hotkey_text(mods: int, token: str) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("ctrl")
    if mods & MOD_ALT:
        parts.append("alt")
    if mods & MOD_SHIFT:
        parts.append("shift")
    if mods & MOD_WIN:
        parts.append("win")
    parts.append(token)
    return "+".join(parts)


# ---------------------------------------------------------------- 全局热键线程

class HotkeyThread(threading.Thread):
    """在独立线程里 RegisterHotKey + 消息循环，事件通过 self.events 交给界面。"""

    def __init__(self, bindings: dict):
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = dict(bindings)          # {action: (mods, vk)}
        self.ids = {action: 0xB000 + index
                    for index, action in enumerate(ACTION_ORDER)}
        self.failures = {}                      # {action: Win32 错误码}
        self.events = queue.Queue()
        self.ready = threading.Event()
        self.tid = None

    def run(self):
        msg = wt.MSG()
        # 先强制创建本线程的消息队列，否则 stop() 里的 PostThreadMessage 会失败
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self.tid = kernel32.GetCurrentThreadId()
        try:
            for action, (mods, vk) in self.bindings.items():
                if not user32.RegisterHotKey(None, self.ids[action],
                                             mods | MOD_NOREPEAT, vk):
                    self.failures[action] = ctypes.get_last_error()
        finally:
            self.ready.set()

        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == WM_HOTKEY:
                for action, hotkey_id in self.ids.items():
                    if hotkey_id == msg.wParam:
                        self.events.put(action)
                        break

        for action in self.bindings:
            user32.UnregisterHotKey(None, self.ids[action])

    def stop(self):
        deadline = time.time() + 2.0
        while self.tid is None and time.time() < deadline and self.is_alive():
            time.sleep(0.005)
        if self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
        self.join(timeout=2.0)


# ---------------------------------------------------------------- 音频控制

AUDIO_IMPORT_ERROR = None
try:
    import comtypes
    from pycaw.pycaw import AudioUtilities
except Exception as _exc:  # noqa: BLE001
    AUDIO_IMPORT_ERROR = _exc


class AudioWorker(threading.Thread):
    """所有 COM/Core Audio 调用都在这个线程里执行（该线程负责 COM 初始化）。"""

    def __init__(self):
        super().__init__(daemon=True, name="audio")
        self._jobs = queue.Queue()
        self.events = queue.Queue()      # (callback, result, error)

    def submit(self, fn, callback=None):
        self._jobs.put((fn, callback))

    def stop(self):
        self._jobs.put(None)

    def run(self):
        comtypes.CoInitialize()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                fn, callback = job
                try:
                    result, error = fn(), None
                except Exception as exc:  # noqa: BLE001
                    result, error = None, exc
                self.events.put((callback, result, error))
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    # -- 查询 ------------------------------------------------------------
    @staticmethod
    def _describe(session):
        try:
            process = session.Process
        except Exception:  # noqa: BLE001
            process = None
        if process is None:
            return None
        try:
            exe = (process.name() or "").lower()
            pid = process.pid
        except Exception:  # noqa: BLE001
            return None
        if not exe:
            return None
        try:
            volume = session.SimpleAudioVolume
            level = float(volume.GetMasterVolume())
            muted = bool(volume.GetMute())
        except Exception:  # noqa: BLE001
            return None
        try:
            display = session.DisplayName or ""
        except Exception:  # noqa: BLE001
            display = ""
        try:
            active = int(session.State) == 1
        except Exception:  # noqa: BLE001
            active = True
        return {"exe": exe, "pid": pid, "volume": level, "muted": muted,
                "display": display, "active": active}

    def scan(self):
        found = []
        for session in AudioUtilities.GetAllSessions():
            info = self._describe(session)
            if info is not None:
                found.append((session, info))
        found.sort(key=lambda item: (not item[1]["active"], item[1]["exe"], item[1]["pid"]))
        return found

    def list_sessions(self):
        return [info for _, info in self.scan()]

    # -- 静音 / 音量 ------------------------------------------------------
    def _targets(self):
        return [(session, info) for session, info in self.scan()
                if info["exe"] == TARGET_EXE]

    def toggle_mute(self):
        matches = self._targets()
        if not matches:
            return {"ok": False, "reason": "no_session", "exe": TARGET_EXE}

        new_muted = not all(info["muted"] for _, info in matches)
        done = 0
        last_error = None
        for session, _info in matches:
            try:
                session.SimpleAudioVolume.SetMute(new_muted, None)
                done += 1
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if done == 0:
            raise last_error or RuntimeError("无法设置静音")
        return {"ok": True, "volume": matches[0][1]["volume"],
                "muted": new_muted, "exe": TARGET_EXE}

    def adjust_volume(self, delta: float):
        """在当前音量基础上加减 delta（0~1 之间的比例），不改变静音状态。"""
        matches = self._targets()
        if not matches:
            return {"ok": False, "reason": "no_session", "exe": TARGET_EXE}

        base = max(info["volume"] for _, info in matches)
        new_volume = min(1.0, max(0.0, base + delta))
        done = 0
        last_error = None
        for session, _info in matches:
            try:
                session.SimpleAudioVolume.SetMasterVolume(new_volume, None)
                done += 1
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if done == 0:
            raise last_error or RuntimeError("无法设置音量")
        return {"ok": True, "volume": new_volume,
                "muted": all(info["muted"] for _, info in matches),
                "exe": TARGET_EXE}


# ---------------------------------------------------------------- 系统托盘

class Tray:
    """用 pystray 在系统托盘显示图标，菜单动作通过回调回到界面线程。"""

    def __init__(self, icon_path: Path, on_show, on_toggle, on_restart, on_quit):
        image = Image.open(str(icon_path))
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda *_args: on_show(), default=True),
            pystray.MenuItem("静音开关", lambda *_args: on_toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重启(&R)", lambda *_args: on_restart()),
            pystray.MenuItem("退出(&X)", lambda *_args: on_quit()),
        )
        self.icon = pystray.Icon(APP_ID, image, APP_TITLE, menu)
        self.running = False

    def start(self):
        self.icon.run_detached()
        self.running = True

    def notify(self, message: str):
        try:
            self.icon.notify(message, APP_TITLE)
        except Exception:  # noqa: BLE001
            log("tray notify failed:\n" + traceback.format_exc())

    def stop(self):
        self.running = False
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            log("tray stop failed:\n" + traceback.format_exc())


# ---------------------------------------------------------------- 快捷键录制

class HotkeyRecorder(tk.Toplevel):
    MODIFIER_KEYSYMS = {
        "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
        "Super_L", "Super_R", "Win_L", "Win_R", "Caps_Lock", "Num_Lock",
    }

    def __init__(self, master, action_label: str, current: str, on_done):
        super().__init__(master)
        self.on_done = on_done
        self.title("录制快捷键")
        self.resizable(False, False)
        self.configure(bg="#f7f7f7")
        self.attributes("-topmost", True)
        icon_path = find_icon_path()
        if icon_path is not None:
            try:
                self.iconbitmap(icon_tk_path(icon_path))
            except tk.TclError:
                pass
        tk.Label(self, text=f"为「{action_label}」设置快捷键",
                 font=("Microsoft YaHei UI", 11), bg="#f7f7f7").pack(padx=28, pady=(18, 6))
        self.hint = tk.Label(self,
                             text="按下新的组合键…\n（至少包含 Ctrl / Alt / Shift / Win；Esc 取消）",
                             bg="#f7f7f7", fg="#666666", justify="center")
        self.hint.pack(padx=28, pady=(0, 12))
        tk.Label(self, text="当前：" + (format_hotkey(current) if current else "未设置"),
                 bg="#f7f7f7", fg="#333333").pack(padx=28, pady=(0, 14))
        tk.Button(self, text="取消", width=10, command=self.destroy).pack(pady=(0, 16))

        self.bind("<KeyPress>", self._on_key)
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - width) // 2
        y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.transient(master)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_force()

    def _on_key(self, event):
        keysym = event.keysym
        if keysym == "Escape":
            self.destroy()
            return
        if keysym in self.MODIFIER_KEYSYMS:
            self.hint.config(text="继续按住修饰键，再按主键…", fg="#1a7f37")
            return
        token = keysym_to_token(keysym)
        if token is None:
            self.hint.config(text=f"不支持这个键（{keysym}），换一个试试", fg="#b00020")
            return
        mods = current_modifiers()
        if not mods and not token.startswith("f"):
            self.hint.config(text="必须同时按住 Ctrl / Alt / Shift / Win 中的至少一个",
                             fg="#b00020")
            return
        text = build_hotkey_text(mods, token)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_done(text)


# ---------------------------------------------------------------- 屏幕提示

class Toast:
    """屏幕底部居中的短暂文字提示（类似音量 OSD）。

    窗口置顶、无边框、不抢焦点，显示一小会儿后淡出。
    """

    BG = "#1f1f1f"
    FG = "#ffffff"
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    GWL_EXSTYLE = -20
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWCP_ROUND = 2

    def __init__(self, root: tk.Tk):
        self.root = root
        self.win = None
        self.label = None
        self._hide_job = None
        self._fade_job = None

    # -- 对外 ------------------------------------------------------------
    def show(self, text: str, duration_ms: int = 1600) -> None:
        try:
            if self.win is None or not self.win.winfo_exists():
                self._build()
            self._cancel_jobs()
            self.label.configure(text=text)
            self.win.attributes("-alpha", 1.0)
            self._make_noactivate(self.win)   # -alpha 会重置扩展样式，重新补上
            self.win.deiconify()
            self._place()
            self.win.lift()
            self._hide_job = self.root.after(duration_ms, self._fade_out)
        except tk.TclError:
            log("toast show failed:\n" + traceback.format_exc())

    def close(self) -> None:
        self._cancel_jobs()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    # -- 内部 ------------------------------------------------------------
    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        # 用 Tk 自己的 -toolwindow 属性，它会把这个样式记进 Tk 内部缓存，
        # 之后设置 -alpha 时不会被 Tk 覆盖掉（手动 SetWindowLongW 的会被覆盖）。
        win.attributes("-toolwindow", True)
        win.attributes("-topmost", True)
        win.configure(bg=self.BG)
        self.label = tk.Label(win, text="", font=("Microsoft YaHei UI", 12),
                              fg=self.FG, bg=self.BG, padx=24, pady=12)
        self.label.pack()
        self.win = win
        win.update_idletasks()
        self._make_noactivate(win)
        win.withdraw()

    def _make_noactivate(self, win: tk.Toplevel) -> None:
        """让提示窗口不抢输入焦点，也不出现在任务栏 / Alt+Tab 里。

        每当对窗口设置 -alpha，Tk 都会用自己缓存的扩展样式整体重写
        GWL_EXSTYLE，把这里手动加的位清掉，所以 show / 淡出时都要重设一次。
        """
        try:
            hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
            style = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, self.GWL_EXSTYLE,
                style | self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW)
        except (AttributeError, OSError):
            pass
        try:  # Windows 11：圆角（不支持时静默忽略）
            dwmapi = ctypes.WinDLL("dwmapi")
            preference = ctypes.c_int(self.DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, self.DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference), ctypes.sizeof(preference))
        except (AttributeError, OSError):
            pass

    def _place(self) -> None:
        self.win.update_idletasks()
        width = self.win.winfo_width()
        height = self.win.winfo_height()
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        margin = max(72, int(screen_h * 0.12))
        x = (screen_w - width) // 2
        y = screen_h - height - margin
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _fade_out(self) -> None:
        self._hide_job = None
        try:
            alpha = float(self.win.attributes("-alpha")) - 0.12
            if alpha <= 0.05:
                self.win.withdraw()
                return
            self.win.attributes("-alpha", alpha)
            self._make_noactivate(self.win)   # -alpha 会重置扩展样式，重新补上
            self._fade_job = self.root.after(28, self._fade_out)
        except tk.TclError:
            pass

    def _cancel_jobs(self) -> None:
        for job in (self._hide_job, self._fade_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self._hide_job = None
        self._fade_job = None


# ---------------------------------------------------------------- 主界面

class App:
    def __init__(self, root: tk.Tk, start_hotkeys: bool = True,
                 with_tray: bool = True):
        self.root = root
        self.cfg = load_config()
        self.audio = AudioWorker()
        self.audio.start()
        self.hotkeys = None
        self._hotkey_failures = {}
        self._alive = True
        self._ui_queue = queue.Queue()
        self._tray_hint_shown = False
        self.tray = None
        self.toast = Toast(root)

        self._prepare_fonts()
        self._build_ui()
        self._load_into_ui()
        self._setup_icon()
        if with_tray:
            self._setup_tray()
        if start_hotkeys:
            self._apply_hotkeys()
        self._tick()

    # -- 界面 ------------------------------------------------------------
    def _prepare_fonts(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=9)
            except tk.TclError:
                pass

    def _px(self, value: int) -> int:
        """把 96 DPI 下的像素值换算成当前 DPI 下的像素值。"""
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except tk.TclError:
            return value
        return max(1, int(round(value * scale)))

    def _build_ui(self):
        self.root.title(APP_TITLE)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")

        # 固定目标（不可选择）
        target_row = ttk.Frame(outer)
        target_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(target_row, text="目标应用：").pack(side="left")
        ttk.Label(target_row, text="AndrowsVm.exe（腾讯应用引擎）",
                  foreground="#1a7f37").pack(side="left")

        # 快捷键
        hotkey_box = ttk.LabelFrame(outer, text=" 快捷键 ", padding=8)
        hotkey_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.hotkey_vars = {}
        for row, action in enumerate(ACTION_ORDER):
            ttk.Label(hotkey_box, text=ACTION_LABELS[action],
                      width=14).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.hotkey_vars[action] = var
            ttk.Entry(hotkey_box, textvariable=var, width=26,
                      state="readonly").grid(row=row, column=1,
                                             sticky="w", pady=2)
            ttk.Button(hotkey_box, text="录制", width=8,
                       command=lambda a=action: self._record_hotkey(a)
                       ).grid(row=row, column=2, padx=(8, 0), pady=2)
        ttk.Label(hotkey_box,
                  text=f"开关：按一次静音，再按一次恢复；"
                       f"音量键每按一次调整 {round(VOLUME_STEP * 100)}%",
                  foreground="#777777").grid(row=len(ACTION_ORDER), column=0,
                                             columnspan=3, sticky="w",
                                             pady=(6, 0))

        # 状态
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status_var, foreground="#555555",
                  wraplength=self._px(460), justify="left").grid(row=2, column=0,
                                                                 sticky="w", pady=(10, 0))

        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = max(40, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"+{x}+{y}")

    def _load_into_ui(self):
        hotkeys = self.cfg.get("hotkeys", {})
        for action, var in self.hotkey_vars.items():
            raw = str(hotkeys.get(action, "") or "")
            var.set(format_hotkey(raw) if raw else "")

    # -- 图标 / 托盘 ------------------------------------------------------
    def _setup_icon(self):
        path = find_icon_path()
        if path is None:
            log("没有在程序目录找到 .ico 图标文件")
            return
        try:
            self.root.iconbitmap(icon_tk_path(path))
            self.root.iconbitmap(default=icon_tk_path(path))
        except tk.TclError:
            log("iconbitmap failed:\n" + traceback.format_exc())

    def _setup_tray(self):
        if TRAY_IMPORT_ERROR is not None:
            log(f"tray unavailable: {TRAY_IMPORT_ERROR!r}")
            return
        path = find_icon_path()
        if path is None:
            return
        try:
            self.tray = Tray(
                path,
                on_show=lambda: self._ui_queue.put(self._show_window),
                on_toggle=lambda: self._ui_queue.put(self._toggle_mute),
                on_restart=lambda: self._ui_queue.put(self._restart),
                on_quit=lambda: self._ui_queue.put(self._quit),
            )
            self.tray.start()
        except Exception:  # noqa: BLE001
            log("tray setup failed:\n" + traceback.format_exc())
            self.tray = None

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _on_close(self):
        """点窗口右上角的 X：隐藏到托盘；没有托盘图标时直接退出。"""
        if self.tray is not None and self.tray.running:
            try:
                self.root.withdraw()
            except tk.TclError:
                return
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify("程序还在后台运行。右键托盘图标可以退出。")
        else:
            self._quit()

    def _status(self, text: str):
        try:
            self.status_var.set(text)
        except tk.TclError:
            pass

    # -- 快捷键 ----------------------------------------------------------
    def _current_hotkey(self, action: str):
        text = self.hotkey_vars[action].get().strip()
        if not text:
            raise ValueError("请先设置一个快捷键")
        return parse_hotkey(text)

    def _apply_hotkeys(self):
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        self._hotkey_failures = {}
        bindings = {}
        for action in ACTION_ORDER:
            try:
                bindings[action] = self._current_hotkey(action)
            except ValueError:
                continue
        if not bindings:
            return
        thread = HotkeyThread(bindings)
        thread.start()
        thread.ready.wait(timeout=2.0)
        self.hotkeys = thread
        self._hotkey_failures = dict(thread.failures)
        if self._hotkey_failures:
            log(f"hotkey register failures: {self._hotkey_failures}")

    def _failure_text(self) -> str:
        if not self._hotkey_failures:
            return ""
        names = "、".join(ACTION_LABELS.get(action, action)
                          for action in self._hotkey_failures)
        return (f"快捷键注册失败：{names}（多半是被其他程序占用了），"
                f"请换一组组合键再保存。")

    def _record_hotkey(self, action: str):
        HotkeyRecorder(self.root, ACTION_LABELS[action],
                       self.hotkey_vars[action].get(),
                       lambda text: self._on_recorded(action, text))

    def _on_recorded(self, action: str, text):
        self.hotkey_vars[action].set(format_hotkey(text))
        self._save_hotkey(action)

    # -- 音频 ------------------------------------------------------------
    def _toggle_mute(self):
        self.audio.submit(self.audio.toggle_mute, self._on_mute_result)

    def _adjust_volume(self, delta: float):
        self.audio.submit(lambda: self.audio.adjust_volume(delta),
                          self._on_volume_result)

    def _on_mute_result(self, result, error):
        if error is not None:
            log(f"mute error: {error!r}")
            self._notify(f"切换静音失败：{error}", "切换静音失败")
            return
        if not result or not result.get("ok"):
            reason = (result or {}).get("reason")
            if reason == "no_session":
                exe = (result or {}).get("exe") or TARGET_EXE
                self._notify(f"没找到 {exe} 的音频会话：引擎可能没在播放声音，"
                             f"或者已经退出。",
                             f"没找到 {exe} 的音频会话")
            else:
                self._notify("切换静音失败（未知原因）。", "切换静音失败")
            return
        muted = bool(result.get("muted"))
        percent = round(float(result.get("volume") or 0.0) * 100)
        if muted:
            self._notify(f"已静音 · {result.get('exe', '')}", "已静音")
        else:
            self._notify(f"已恢复声音 · {percent}% · {result.get('exe', '')}",
                         f"已恢复声音 · {percent}%")

    def _on_volume_result(self, result, error):
        if error is not None:
            log(f"volume error: {error!r}")
            self._notify(f"调整音量失败：{error}", "调整音量失败")
            return
        if not result or not result.get("ok"):
            reason = (result or {}).get("reason")
            if reason == "no_session":
                exe = (result or {}).get("exe") or TARGET_EXE
                self._notify(f"没找到 {exe} 的音频会话：引擎可能没在播放声音，"
                             f"或者已经退出。",
                             f"没找到 {exe} 的音频会话")
            else:
                self._notify("调整音量失败（未知原因）。", "调整音量失败")
            return
        percent = round(float(result.get("volume") or 0.0) * 100)
        short = f"音量 {percent}%"
        if result.get("muted"):
            short += " · 静音中"
        self._notify(f"{short} · {result.get('exe', '')}", short)

    def _notify(self, status_text: str, toast_text: str | None = None) -> None:
        """同时更新主窗口状态栏和屏幕底部提示。"""
        self._status(status_text)
        self.toast.show(toast_text or status_text)

    # -- 热键分发 / 计时器 -------------------------------------------------
    def _on_hotkey(self, action: str):
        log(f"hotkey: {action}")
        if action == "mute":
            self._toggle_mute()
        elif action == "up":
            self._adjust_volume(VOLUME_STEP)
        elif action == "down":
            self._adjust_volume(-VOLUME_STEP)

    def _tick(self):
        if not self._alive:
            return
        while True:
            try:
                job = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if not self._alive:
                return
            try:
                job()
            except Exception:  # noqa: BLE001
                log("ui job:\n" + traceback.format_exc())
        if not self._alive:
            return
        if self.hotkeys is not None:
            while True:
                try:
                    action = self.hotkeys.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._on_hotkey(action)
                except Exception:  # noqa: BLE001
                    log("hotkey handler:\n" + traceback.format_exc())
        while True:
            try:
                callback, result, error = self.audio.events.get_nowait()
            except queue.Empty:
                break
            if callback is None:
                continue
            try:
                callback(result, error)
            except Exception:  # noqa: BLE001
                log("audio callback:\n" + traceback.format_exc())
        try:
            self.root.after(45, self._tick)
        except tk.TclError:
            pass

    # -- 保存 ------------------------------------------------------------
    def _save_hotkey(self, action: str):
        """录制完快捷键后立即保存并生效，不需要再点按钮。"""
        try:
            pair = self._current_hotkey(action)
        except ValueError as exc:
            messagebox.showwarning("快捷键有误", str(exc), parent=self.root)
            return

        for other in ACTION_ORDER:
            if other == action:
                continue
            try:
                other_pair = self._current_hotkey(other)
            except ValueError:
                continue
            if other_pair == pair:
                messagebox.showwarning(
                    "快捷键冲突",
                    f"这组快捷键已经用在「{ACTION_LABELS[other]}」上了，"
                    f"请换一个。",
                    parent=self.root)
                return

        self.cfg["hotkeys"][action] = hotkey_text(pair)
        try:
            save_config(self.cfg)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法写入配置文件：\n{exc}",
                                 parent=self.root)
            return

        self._apply_hotkeys()
        if self._hotkey_failures:
            message = self._failure_text()
            self._status(message)
            messagebox.showwarning("快捷键注册失败", message, parent=self.root)
        else:
            self._status(f"已保存并生效：{ACTION_LABELS[action]} = "
                         f"{format_hotkey(hotkey_text(pair))}")

    def _restart(self):
        """保存配置 → 启动新实例 → 退出当前实例。"""
        try:
            save_config(self.cfg)
        except Exception:  # noqa: BLE001
            log("save before restart:\n" + traceback.format_exc())
        try:
            visible = self.root.state() == "normal"
        except tk.TclError:
            visible = False
        try:
            subprocess.Popen(relaunch_command(os.getpid(), minimized=not visible),
                             close_fds=True)
        except OSError as exc:
            log(f"restart failed: {exc!r}")
            self._status(f"重启失败：{exc}")
            messagebox.showerror(APP_TITLE, f"重启失败：\n{exc}", parent=self.root)
            return
        self._quit()

    def _quit(self):
        self._alive = False
        try:
            save_config(self.cfg)
        except Exception:  # noqa: BLE001
            log("save on exit:\n" + traceback.format_exc())
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.toast.close()
        self.audio.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------- 启动

def already_running() -> bool:
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\" + APP_ID)
    if not handle:
        return False
    return ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def show_dependency_error(exc) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        APP_TITLE,
        "缺少必需的组件（pycaw / comtypes / psutil）。\n\n"
        f"详细信息：{exc}\n\n"
        "请在命令行里运行：\n"
        "    pip install pycaw comtypes psutil",
    )
    root.destroy()


def main() -> int:
    args = {item.lower() for item in sys.argv[1:]}
    enable_dpi_awareness()      # 必须早于任何窗口创建

    # 重启时新进程先等旧进程退出，避免单实例锁和热键冲突
    wait_pid = None
    for index, item in enumerate(sys.argv):
        if item.lower() == "--restart-of" and index + 1 < len(sys.argv):
            try:
                wait_pid = int(sys.argv[index + 1])
            except ValueError:
                wait_pid = None
    if wait_pid:
        wait_for_process_exit(wait_pid)

    if AUDIO_IMPORT_ERROR is not None:
        show_dependency_error(AUDIO_IMPORT_ERROR)
        return 1

    if already_running():
        root = tk.Tk()
        apply_tk_scaling(root)
        root.withdraw()
        messagebox.showinfo(APP_TITLE, "程序已经在运行了，请在系统托盘里找到它。")
        root.destroy()
        return 0

    root = tk.Tk()
    apply_tk_scaling(root)
    root.withdraw()
    root.report_callback_exception = (
        lambda exc, value, tb: log("Tk callback:\n"
                                   + "".join(traceback.format_exception(exc, value, tb)))
    )
    app = App(root)

    if "--minimized" in args:
        if app.tray is None:      # 没有托盘图标时，退而求其次最小化到任务栏
            root.deiconify()
            root.update_idletasks()
            root.iconify()
    else:
        root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
