# -*- coding: utf-8 -*-
"""找聊天窗口 + Windows Graphics Capture 盯着它 + 从帧里定位消息区。帧全程内存，绝不落盘。"""
import ctypes
import os
import time
from ctypes import wintypes

import numpy as np

from app import chatapps

u32 = ctypes.windll.user32


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


def _chat_area_patterned(full):
    """Telegram 的消息区底下是一张壁纸（默认带纹理的渐变），不是纯色，纯色那套「众数底色」认不出来。
    换成靠「纯色 / 花」分界：
    - 左右：聊天列表、工具条都是整列纯色（一列从上到下一个色），壁纸列一路都在变色（竖直渐变也算）；
      第一根/最后一根「整列在变色」的列就是面板左右边——气泡有内边距，贴不到面板边上
    - 上下：头部和输入条是整行纯色横条。先量最上面几行 / 最下面几行的中位色当参考，
      往下/往上找第一行明显不是它的：消息区顶 / 输入条顶
    - 消息区顶上那张铺满面板宽的纯色卡片（Telegram 的置顶消息条）不是消息，跳过
    壁纸被用户设成纯色时这里会认不出来，返回 None，调用方退回纯色那套。"""
    H, W = full.shape[:2]
    img = full.astype(np.int16)
    col_std = img.std(axis=(0, 2))
    noisy = col_std > 3.0
    if noisy.mean() < 0.03:  # 全是纯色列 = 壁纸是纯色，交给 flat 那套
        return None
    x0 = int(np.argmax(noisy))
    x1 = W - int(np.argmax(noisy[::-1]))
    if x1 - x0 < 120:
        return None
    pane = img[:, x0 + 6:x1 - 6]
    head = np.median(pane[2:min(20, H)], axis=(0, 1))
    tail = np.median(pane[max(0, H - 20):H - 2], axis=(0, 1))
    row_head = np.abs(pane - head).sum(axis=2).mean(axis=1)
    row_tail = np.abs(pane - tail).sum(axis=2).mean(axis=1)
    top = np.flatnonzero(row_head > 24)
    bottom = np.flatnonzero(row_tail > 24)
    if top.size == 0 or bottom.size == 0:
        return None
    y0, y1 = int(top[0]), int(bottom[-1]) + 1
    flat_row = pane.std(axis=2).mean(axis=1) < 6  # 行内几乎没有颜色起伏 = 纯色横条
    while y0 < H - 1 and flat_row[y0]:  # 置顶消息条：纯色、铺满宽度，不是消息
        y0 += 1
    if y1 - y0 < 60:
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


class Capture:
    """WGC 盯窗口。采集线程只做「跟上一帧比」；settled() 在画面停稳后交出整帧，中间帧（滚动动画、
    新消息滑入的半截气泡）全跳过。动图表情永远停不稳，所以最多等 max_wait 秒照样交。"""

    def __init__(self, hwnd, app=None, settle=0.25, max_wait=1.0):
        from windows_capture import WindowsCapture

        self.app = app  # 消息区找法按聊天软件走（Telegram 是壁纸底，见 chat_area）
        self.settle, self.max_wait = settle, max_wait
        self.shape = self.area = self.last = self.pending = None
        self.t = self.t0 = 0.0
        # 包装层默认 cursor_capture=True，会去调 SetIsCursorCaptureEnabled。
        # 这个属性要 Win10 2004（build 19041）才有，1909 及更早直接抛 CursorConfigUnsupported。
        # 显式 None 走系统默认，不去切换；draw_border 同理。
        cap = WindowsCapture(cursor_capture=None, draw_border=None, window_hwnd=hwnd)
        cap.event(self.on_frame_arrived)
        cap.event(self.on_closed)
        self.ctl = cap.start_free_threaded()

    def on_frame_arrived(self, frame, control):
        full = np.ascontiguousarray(frame.frame_buffer[:, :, :3][:, :, ::-1])  # BGRA → RGB；缓冲区回调后就没了，必须拷
        if full.max() == 0:
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
        if self.pending is None:
            return None
        now = time.perf_counter()
        if now - self.t < self.settle and now - self.t0 < self.max_wait:
            return None
        full, self.pending = self.pending, None
        return full

    def alive(self):
        return not self.ctl.is_finished()

    def stop(self):
        self.ctl.stop()

    def wait(self):
        self.ctl.wait()  # 采集线程若是报错死的，这里把错误抛出来
