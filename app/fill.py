# -*- coding: utf-8 -*-
"""把选中的候选填进微信输入框：写剪贴板 → 点输入框 → Ctrl+V。绝不发回车、绝不点发送。"""
import ctypes
import ctypes.wintypes as w
import time

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

# 64 位下 ctypes.windll 默认 restype 是 32 位 c_int，而 GlobalAlloc 返回 64 位 HGLOBAL——
# 不声明类型句柄会被截断成垃圾值，GlobalLock(垃圾) 返回 NULL，memmove(NULL,…) 就是
# "access violation writing 0x0"。所有带句柄/指针的函数必须显式声明。
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
k32.GlobalFree.argtypes = [ctypes.c_void_p]
u32.SetClipboardData.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


def set_clipboard(text):
    """写剪贴板。剪贴板可能被别的程序占着（剪贴板管理器、截图工具），重试几次。"""
    data = text.encode("utf-16-le") + b"\0\0"
    for attempt in range(10):
        if not u32.OpenClipboard(None):
            time.sleep(0.05)
            continue
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x2, len(data))  # GMEM_MOVEABLE
            if not h:
                raise RuntimeError("GlobalAlloc 失败")
            p = k32.GlobalLock(h)
            if not p:
                k32.GlobalFree(h)
                raise RuntimeError("GlobalLock 失败")
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):  # CF_UNICODETEXT；成功后句柄归系统，不能 Free
                k32.GlobalFree(h)
                raise RuntimeError(f"SetClipboardData 失败 (attempt {attempt})")
            return
        finally:
            u32.CloseClipboard()
    raise RuntimeError("OpenClipboard 连续失败，剪贴板被其他程序占用")


def _log(message):
    """现场排查用：设了 JEV_FILL_LOG=<文件路径> 才写，平时一个字都不落盘（聊天内容不进日志）。"""
    import os

    path = os.environ.get("JEV_FILL_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError:
        pass


def _click(x, y):
    """把光标移过去点一下。返回光标有没有真的挪到那儿——低完整性进程（被别的沙箱/脚本拉起的那种）
    调 SetCursorPos 会被系统静默拒绝，这时候再发 Ctrl+V 就是打在空处，必须让调用方知道。"""
    u32.SetCursorPos(x, y)
    p = w.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    if (p.x, p.y) != (x, y):
        time.sleep(0.08)
        u32.SetCursorPos(x, y)
        u32.GetCursorPos(ctypes.byref(p))
    moved = (p.x, p.y) == (x, y)
    _log(f"click: want=({x},{y}) cursor=({p.x},{p.y}) moved={moved}")
    if not moved:
        return False
    time.sleep(0.05)
    u32.mouse_event(0x2, 0, 0, 0, 0)  # 左键按下
    u32.mouse_event(0x4, 0, 0, 0, 0)  # 抬起
    return True


def _activate(hwnd):
    """把聊天窗口拿到前台。调用者自己就是前台进程时直接设就行（实测返回 1 且生效）；
    后台进程会被前台锁挡住，这时才需要 AttachThreadInput 绕。
    注意 AttachThreadInput(self, self) 是空操作，而把线程从自己身上 detach 反而会把输入队列搞乱——
    所以只在两个线程真的不同时才用，省掉这个坑。"""
    if u32.GetForegroundWindow() == hwnd:
        return True
    if u32.SetForegroundWindow(hwnd):
        time.sleep(0.12)
        if u32.GetForegroundWindow() == hwnd:
            return True
    fg = u32.GetForegroundWindow()
    fg_tid = u32.GetWindowThreadProcessId(fg, None)
    our_tid = k32.GetCurrentThreadId()
    if fg_tid and fg_tid != our_tid:
        u32.AttachThreadInput(our_tid, fg_tid, True)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(our_tid, fg_tid, False)
        time.sleep(0.12)
    return u32.GetForegroundWindow() == hwnd


def fill(hwnd, area, text, app=None):
    """area = 消息区 (x0, y0, x1, y1)；输入框就在底线 y1 下面。
    app 只提供落点偏移（微信 60/40，Telegram 200/34：它的输入条左边先是附件按钮，
    点上去会弹「选择文件」，所以要落到文字区里；窄窗口按面板宽度的三分之一收回来）。"""
    from app.capture import unminimize

    set_clipboard(text)
    r = w.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # 扩展边界，跟 WGC 帧对齐
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    x0, _, x1, y1 = area
    want = app.input_dx if app is not None else 60
    # 只收紧、不放大：窄面板上把落点收回面板宽度的三分之一，免得贴到附件/发送按钮上；
    # 微信的 60 本来就在文字的左边，宽面板上算出更大的值时也还是 60（不改它的老行为）
    dx = min(want, max(60, (x1 - x0) // 3))
    dy = app.input_dy if app is not None else 40
    cx, cy = r.left + x0 + dx, r.top + y1 + dy  # 分隔线下 dy = 输入框文字区；工具栏和「发送」在输入区最底下，碰不到
    unminimize(hwnd)
    _log(f"area={tuple(area)} rect=({r.left},{r.top},{r.right},{r.bottom}) click=({cx},{cy}) "
         f"text_len={len(text)} fg0={u32.GetForegroundWindow():#x} hwnd={hwnd:#x}")

    ok = _activate(hwnd)
    _log(f"activate: ok={ok} fg1={u32.GetForegroundWindow():#x}")

    old = w.POINT()
    u32.GetCursorPos(ctypes.byref(old))
    if not _click(cx, cy):  # 第一次点：激活窗口 + 把光标放进输入框
        raise RuntimeError("光标移不到聊天窗口（本程序权限不足，请从桌面直接双击启动，不要由别的程序/脚本拉起）")
    if u32.GetForegroundWindow() != hwnd:
        # 后台进程发起的第一次点击，有时只被用来激活窗口，焦点没落到输入框——补一次
        time.sleep(0.25)
        _click(cx, cy)
    time.sleep(0.15)  # 给 Qt 把焦点落下去的时间：实测 0.05 秒不够，Ctrl+V 会打在空处
    _log(f"after click: fg2={u32.GetForegroundWindow():#x}")
    u32.SetCursorPos(old.x, old.y)
    time.sleep(0.05)
    # 光标移到已有文本的绝对末尾：点击落在文字中间时 caret 会插在中间，
    # 连续多次填入就串行错乱；Ctrl+End 保证新内容永远追加在最后
    u32.keybd_event(0x11, 0, 0, 0)  # Ctrl 按下
    u32.keybd_event(0x23, 0, 0, 0)  # End 按下（VK_END）
    u32.keybd_event(0x23, 0, 2, 0)  # End 抬起
    u32.keybd_event(0x11, 0, 2, 0)  # Ctrl 抬起
    time.sleep(0.05)
    u32.keybd_event(0x11, 0, 0, 0)  # Ctrl
    u32.keybd_event(0x56, 0, 0, 0)  # V
    u32.keybd_event(0x56, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)
    _log(f"keys sent: fg3={u32.GetForegroundWindow():#x}")
    # 到此为止。发不发、改不改，人来。
