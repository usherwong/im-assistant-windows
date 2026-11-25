# -*- coding: utf-8 -*-
"""找聊天窗口 + Windows Graphics Capture 盯着它 + 从帧里定位消息区。帧全程内存，绝不落盘。"""
import ctypes
import os
import time
from ctypes import wintypes

import numpy as np

from app import chatapps

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32  # BitBlt 兜底用（WGC 不可用时）


def dpi_aware():
    """让本进程按真实像素工作，必须在建任何窗口/截图之前调用。
    只调 SetProcessDPIAware()（system aware）在 200% 缩放的屏幕上拿到的是减半的坐标和半分辨率截图
    （Telegram 自己的日志就是 devicePixelRatio 2，实测 1626x1262 的窗口我们只看到 813x631），
    小字 OCR 直接糊一半。per-monitor v2 拿到的才是 GDI/WGC 的真像素。先试新 API，再逐级退回。"""
    u32 = ctypes.windll.user32
    try:
        if u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    u32.SetProcessDPIAware()


def find_chat_hwnd(want=None):
    """枚举可见顶层窗口，按进程名认出聊天软件，返回 (hwnd, ChatApp)。
    微信：同进程还有工具窗和看图窗，面积可能更大，所以按标题挑主窗口。
    KakaoTalk：一个对话一个窗口，主列表窗（标题「카카오톡」）不是目标，挑剩下最大的那个。
    want=某个 ChatApp 时只找它，None 时先到先得（两个都开着就按 APPS 顺序）。"""
    k32 = ctypes.windll.kernel32
    found = []

    def exe_of(pid):
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_uint(1024)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        return os.path.basename(buf.value).lower() if ok else ""

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = chatapps.by_exe(exe_of(pid.value))
        if app and (want is None or app is want):
            title = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, title, 256)
            rect = wintypes.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            found.append((hwnd, title.value, area, app))
        return True

    u32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError("没找到聊天窗口，开着吗？")
    for app in chatapps.APPS.values():
        mine = [f for f in found if f[3] is app]
        if not mine:
            continue
        if app.min_share:  # 同一进程的侧窗（看图、通话）是独立顶层窗口，按面积比筛掉
            biggest = max(f[2] for f in mine)
            mine = [f for f in mine if f[2] >= app.min_share * biggest] or mine
        if app.main_title:
            hit = next((f for f in mine if f[1] == app.main_title), None)
            if hit:
                return hit[0], app
        rooms = [f for f in mine if f[1] not in app.skip_titles] or mine
        best = max(rooms, key=lambda f: f[2])
        return best[0], app
    raise RuntimeError("没找到聊天窗口，开着吗？")


def find_wechat_hwnd():
    """老名字：只返回 hwnd，给还没改过来的调用方用。"""
    return find_chat_hwnd()[0]


def unminimize(hwnd):
    """Windows 不渲染最小化的窗口，什么截图法都拿不到画面。发现被最小化就无激活还原，再压到所有窗口最底下——
    看着跟收起来一样，但 DWM 继续画。不抢焦点、不动大小位置。返回是否动了手。"""
    if not u32.IsIconic(hwnd):
        return False
    u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    u32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x13)  # HWND_BOTTOM, SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
    return True


def _mode_color(img):
    """抽样算最常见颜色：全量 np.unique 在 2560 宽的图上要半秒。"""
    small = img[::8, ::8].reshape(-1, 3)
    vals, cnt = np.unique(small, axis=0, return_counts=True)
    return vals[cnt.argmax()]


def _wall_color(img):
    """壁纸代表色：取最常见的几种颜色，跳过近白（浅色主题里对方气泡是白的，别把气泡当壁纸）。"""
    small = img[::4, ::4].reshape(-1, 3)
    vals, cnt = np.unique(small, axis=0, return_counts=True)
    for i in np.argsort(cnt)[::-1][:6]:
        v = vals[i]
        if not (v[0] > 244 and v[1] > 244 and v[2] > 244):
            return v
    return vals[cnt.argmax()]



def _chat_area_flat(full, header_h=60):
    """消息列表区 (x0, y_top, x1, y_in, 面板底色, y_pane)，全靠像素锚点，不写死坐标，深浅主题通用：
    - 面板底色 = 右半边最常见的颜色（抽样算，全量 np.unique 在 2560 宽的图上要半秒）
    - 面板左/右边界 = 第一/最后一根「底色占比 > 30%」的列（联系人列表是另一种底色，占比 0）
    - y_pane = 面板第一行；会话名就印在 y_pane~y_top 这条头部里（公告条也在里面）
    - 横向分隔线 = 整行单色且非底色；输入框顶 y_in = 面板 45% 高度以下第一根；
      公告条下面那根（有的话）= 消息区顶 y_top，没有就用 header_h
    认不出（窗口太小 / 拖到一半布局没铺好）返回 None。
    ponytail: 输入框拉高超过面板一半会认错；header_h 按 100% DPI 给的，缩放了按比例调。"""
    H, W = full.shape[:2]
    right = full[::8, W // 2::8].reshape(-1, 3)
    vals, cnt = np.unique(right, axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    isbg = np.abs(full.astype(int) - bg).sum(-1) <= 6
    col = isbg[H // 4: H * 3 // 4].mean(0)
    x0 = int(np.argmax(col > 0.3))
    x1 = W - int(np.argmax(col[::-1] > 0.3))
    row = isbg[:, x0:x1].mean(1)
    y0 = int(np.argmax(row > 0.9))
    y1 = H - int(np.argmax(row[::-1] > 0.9))
    band = full[y0:y1, x0:x1].astype(int)
    seps = y0 + np.where((band.std(axis=(1, 2)) < 4) & (row[y0:y1] < 0.1))[0]
    seps = [int(s) for i, s in enumerate(seps) if i == 0 or s - seps[i - 1] > 3]
    below = [s for s in seps if s > y0 + 0.45 * (y1 - y0)]
    y_in = below[0] if below else y1
    above = [s for s in seps if y0 + header_h < s < y_in - 50]
    y_top = above[-1] if above else y0 + header_h
    if x1 - x0 < 100 or y_in - y_top < 40:
        return None
    return x0, y_top, x1, y_in, bg, y0


def _longest_run(flags, min_len):
    """最长的一段连续 True，返回 (start, end)；都不够长就 (None, None)。"""
    best = (None, None)
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len and i - start > (best[1] or 0) - (best[0] or 0):
                best = (start, i)
            start = None
    end = len(flags)
    if start is not None and end - start >= min_len and end - start > (best[1] or 0) - (best[0] or 0):
        best = (start, end)
    return best


def _row_share(row):
    """一行里众数颜色占的比例：纯色横条 ≈ 1，壁纸（图案/照片）低得多。"""
    vals, cnt = np.unique(row, axis=0, return_counts=True)
    return float(cnt.max()) / max(1, len(row))


def _chat_area_patterned(full, margin=6):
    """Telegram 的消息区底下是一张壁纸（图案/照片/渐变），不是纯色，所以不能像微信那样找「众数底色」。
    改成找「不是界面底色的那一大块」：
    - 界面底色 = 整窗最常见的颜色——聊天列表、头部、输入条、标题区都是它
    - 左右：壁纸列整列都不是界面底色（列表列只有头像/选中行/文字那几行不是），取最长的一段；
      窗口边缘几像素可能混进桌面别的东西，margin 里不看
    - 上下：面板范围内壁纸行占多数；头部、输入条是纯色整行，自然被排除
    - 置顶消息条：面板顶上一条铺满宽度、几乎单色的横带，不是消息，跳掉
    壁纸被设成纯色（或和界面底色一样）时这块会太窄 → 返回 None，调用方退回纯色那套。
    实测（1626x1261，浅色主题 + 默认绿壁纸）：x 585..1620、y 155..1157，跟眼睛看的边界一致。"""
    H, W = full.shape[:2]
    if W < 400 or H < 240:
        return None
    chrome = _mode_color(full)
    mask = np.abs(full.astype(np.int16) - chrome).sum(axis=2) > 26
    col = mask[H // 4: H * 3 // 4].mean(axis=0)
    col[:margin] = False
    col[W - margin:] = False
    x0, x1 = _longest_run(col > 0.5, max(120, W // 6))
    if x0 is None or x1 - x0 < max(150, W // 6):
        return None
    row = mask[:, x0:x1].mean(axis=1)
    row = np.convolve(row, np.ones(5) / 5, mode="same")  # 白泡把一两行压下去时不至于断掉
    y0, y1 = _longest_run(row > 0.3, max(80, H // 8))
    if y0 is None or y1 - y0 < max(80, H // 8):
        return None
    while y0 < y1 - 80 and _row_share(full[y0, x0:x1]) > 0.9:  # 置顶消息条：整行一个颜色
        y0 += 1
    if y1 - y0 < 80:
        return None
    # 面板底色取壁纸的左内边（气泡有内边距，这一条一定是壁纸）：worker 拿它判「字是不是印在面板上」，
    # 取消息区众数会把大白泡算成面板色，短句就被当成发言人名了
    return x0, y0, x1, y1, _wall_color(full[y0:y1, x0 + 2:x0 + 40]), 0


def chat_area(full, app=None, header_h=60):
    """按聊天软件的界面类型选找法：壁纸底（Telegram）先用 patterned，认不出再退回纯色那套。"""
    if app is not None and app.area == "patterned":
        got = _chat_area_patterned(full)
        if got is not None:
            return got
    return _chat_area_flat(full, header_h)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def window_rect(hwnd):
    """窗口的可见矩形 (left, top, right, bottom)，物理像素、DWM 扩展边界——跟截图帧同一个原点，
    悬浮窗贴靠和 fill() 的落点都用它，三处坐标才是一套。"""
    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _grab_bitblt(hwnd):
    """GetWindowDC + BitBlt 抓整窗。WGC 不可用时的兜底（虚拟机、部分 RDP 会话、老系统上
    windows-capture 会直接抛 Failed to convert item to GraphicsCaptureItem）。要求窗口在屏幕上、没被别的
    窗口完全盖住——WGC 能抓到被盖住的窗口，这条路抓不到，所以只是兜底，不改默认路径。"""
    rect = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
    # 窗口矩形比 DWM 的可见边界大一圈（不可见的拉伸边框）。WGC 的帧是从可见边界开始的，
    # 这里按同样的原点做 BitBlt，坐标才跟 fill() 里那个 DWM 矩形对得上。
    outer = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(outer))
    sx, sy = rect.left - outer.left, rect.top - outer.top
    W, H = rect.right - rect.left, rect.bottom - rect.top
    if W <= 0 or H <= 0:
        return None
    hdc = u32.GetWindowDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, W, H)
    old = g32.SelectObject(mem, bmp)
    ok = g32.BitBlt(mem, 0, 0, W, H, hdc, sx, sy, 0x00CC0020)
    buf = ctypes.create_string_buffer(W * H * 4)
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
    lines = g32.GetDIBits(mem, bmp, 0, H, buf, ctypes.byref(bi), 0)
    g32.SelectObject(mem, old)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mem)
    u32.ReleaseDC(hwnd, hdc)
    if not ok or not lines:
        return None
    return np.frombuffer(buf, np.uint8).reshape(H, W, 4)[:, :, :3].copy()


class Capture:
    """盯窗口截图。默认 WGC：采集线程只做「跟上一帧比」，settled() 在画面停稳后交出整帧，中间帧
    （滚动动画、新消息滑入的半截气泡）全跳过；动图表情永远停不稳，所以最多等 max_wait 秒照样交。
    WGC 起不来的机器（虚拟机 / 部分 RDP / 老系统）自动退回 BitBlt 轮询，接口一模一样。"""

    def __init__(self, hwnd, app=None, settle=0.25, max_wait=1.0):
        self.app = app  # 消息区找法按聊天软件走（Telegram 是壁纸底，见 chat_area）
        self.settle, self.max_wait = settle, max_wait
        self.shape = self.area = self.last = self.pending = None
        self.t = self.t0 = 0.0
        self.hwnd, self.ctl, self.backend, self.error = hwnd, None, "wgc", None
        try:
            from windows_capture import WindowsCapture

            # 包装层默认 cursor_capture=True，会去调 SetIsCursorCaptureEnabled。
            # 这个属性要 Win10 2004（build 19041）才有，1909 及更早直接抛 CursorConfigUnsupported。
            # 显式 None 走系统默认，不去切换；draw_border 同理。
            cap = WindowsCapture(cursor_capture=None, draw_border=None, window_hwnd=hwnd)
            cap.event(self.on_frame_arrived)
            cap.event(self.on_closed)
            self.ctl = cap.start_free_threaded()
        except Exception as e:  # 不是所有机器都给 WGC：退回 BitBlt 轮询
            self.backend, self.error = "bitblt", e
            self._last_grab = 0.0

    def on_frame_arrived(self, frame, control):
        full = np.ascontiguousarray(frame.frame_buffer[:, :, :3][:, :, ::-1])  # BGRA → RGB；缓冲区回调后就没了，必须拷
        self._feed(full)

    def _feed(self, full):
        if full is None or full.max() == 0:
            return
        if self.area is None or full.shape != self.shape:
            self.shape, self.area = full.shape, chat_area(full, self.app)
        if self.area is None:
            return
        x0, y0, x1, y1 = self.area[:4]  # 拿上一次的消息区做 diff 就够了，光标闪烁在输入框里，不算变化
        # ponytail: diff 不含头部——公告条会滚动，带上它就永远停不稳。切会话时消息区必然也变，照样出帧。
        chat = full[y0:y1, x0:x1]
        if self.last is not None and np.array_equal(chat, self.last):
            return
        self.last = chat
        if self.pending is None:
            self.t0 = time.perf_counter()
        self.pending, self.t = full, time.perf_counter()

    def on_closed(self):
        pass

    def settled(self):
        """停稳了就返回整帧，否则 None。"""
        if self.backend == "bitblt":
            now = time.perf_counter()
            if now - self._last_grab >= 0.05:  # 轮询抓帧：20fps 够用，也不至于把 CPU 吃满
                self._last_grab = now
                self._feed(_grab_bitblt(self.hwnd))
        if self.pending is None:
            return None
        now = time.perf_counter()
        if now - self.t < self.settle and now - self.t0 < self.max_wait:
            return None
        full, self.pending = self.pending, None
        return full

    def alive(self):
        if self.backend == "bitblt":
            return bool(u32.IsWindow(self.hwnd))
        return not self.ctl.is_finished()

    def stop(self):
        if self.ctl is not None:
            self.ctl.stop()

    def wait(self):
        if self.ctl is not None:
            self.ctl.wait()  # 采集线程若是报错死的，这里把错误抛出来
