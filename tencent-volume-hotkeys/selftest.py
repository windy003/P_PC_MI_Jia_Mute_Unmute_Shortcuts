# -*- coding: utf-8 -*-
"""
腾讯应用引擎 · 音量开关 · 自检
==============================

tencent_volume_keys.py 里只保留实际运行的程序代码，测试全部集中在这里。

自检内容：快捷键解析、音频会话、图标 / 托盘、界面构建。
自检不会注册全局快捷键，也不会修改任何应用的实际音量。

运行:
    python selftest.py
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import traceback

import tencent_volume_keys as tvk


def call_worker(worker, fn, timeout: float = 5.0):
    """在音频线程里同步执行一个函数（只给自检用）。"""
    box = {}
    done = threading.Event()

    def job():
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        done.set()
        return "<sync>"

    worker.submit(job, None)
    if not done.wait(timeout):
        raise TimeoutError("音频线程没有响应")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run() -> int:
    print("Python:", sys.version.split()[0], sys.executable)
    ok = True

    print("\n[1] 快捷键解析")
    cases = [
        ("ctrl+alt+m", (tvk.MOD_CONTROL | tvk.MOD_ALT, ord("M"))),
        ("Ctrl + Alt + M", (tvk.MOD_CONTROL | tvk.MOD_ALT, ord("M"))),
        ("win+shift+f8", (tvk.MOD_WIN | tvk.MOD_SHIFT, 0x77)),
        ("alt+space", (tvk.MOD_ALT, 0x20)),
        ("f9", (0, 0x78)),
    ]
    for text, expect in cases:
        try:
            got = tvk.parse_hotkey(text)
        except ValueError as exc:
            got = f"错误：{exc}"
        status = "OK  " if got == expect else "FAIL"
        ok = ok and got == expect
        print(f"    {status} {text!r} -> {got}")
    for bad in ("m", "ctrl+", "ctrl+alt+xyz"):
        try:
            tvk.parse_hotkey(bad)
            print(f"    FAIL {bad!r} 应该报错")
            ok = False
        except ValueError as exc:
            print(f"    OK   {bad!r} 正确报错：{exc}")
    print("    格式化:", tvk.format_hotkey("ctrl+alt+m"))

    print("\n[2] 音频会话")
    if tvk.AUDIO_IMPORT_ERROR is not None:
        print("    FAIL 无法导入 pycaw：", tvk.AUDIO_IMPORT_ERROR)
        return 1
    worker = tvk.AudioWorker()
    worker.start()
    try:
        sessions = call_worker(worker, worker.list_sessions)
        print(f"    找到 {len(sessions)} 个音频会话")
        for info in sessions[:10]:
            print(f"      - {info['exe']} (pid {info['pid']}) "
                  f"{round(info['volume'] * 100)}%"
                  + (" [静音]" if info["muted"] else ""))
        if any(info["exe"] == "androwsvm.exe" for info in sessions):
            print("    OK   检测到腾讯应用引擎 AndrowsVm.exe")
        else:
            print("    提示 当前没有检测到 AndrowsVm.exe（引擎没运行或没出声时属正常）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("    FAIL", repr(exc))
    finally:
        worker.stop()

    print("\n[3] 图标 / 托盘")
    icon_path = tvk.find_icon_path()
    if icon_path is None:
        print("    FAIL 程序目录里没有 .ico 文件")
        ok = False
    else:
        print("    图标文件:", icon_path.name)
        if tvk.TRAY_IMPORT_ERROR is not None:
            print("    FAIL pystray / Pillow 不可用：", tvk.TRAY_IMPORT_ERROR)
            ok = False
        else:
            try:
                tray = tvk.Tray(icon_path, lambda: None, lambda: None,
                                lambda: None, lambda: None)
                tray.start()
                time.sleep(1.0)
                visible = bool(getattr(tray.icon, "visible", False))
                print("    托盘图标可见:", visible)
                ok = ok and visible
                tray.stop()
            except Exception as exc:  # noqa: BLE001
                ok = False
                print("    FAIL", repr(exc))

    print("\n[4] 界面构建")
    try:
        root = tk.Tk()
        tvk.apply_tk_scaling(root)
        root.withdraw()
        root.report_callback_exception = lambda *_a: None
        app = tvk.App(root, start_hotkeys=False, with_tray=False)
        for _ in range(6):
            root.update()
            time.sleep(0.03)
        print(f"    Tk 缩放 {float(root.tk.call('tk', 'scaling')):.2f}，"
              f"屏幕 {root.winfo_screenwidth()}x{root.winfo_screenheight()}")
        app._alive = False
        app.audio.stop()
        root.destroy()
        print("    OK   界面构建与事件循环正常")
    except Exception:  # noqa: BLE001
        ok = False
        print("    FAIL")
        traceback.print_exc()

    print("\n自检结果:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


def main() -> int:
    tvk.enable_dpi_awareness()  # 必须早于任何窗口创建
    return run()


if __name__ == "__main__":
    sys.exit(main())
