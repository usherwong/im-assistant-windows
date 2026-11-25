# -*- coding: utf-8 -*-
"""Telegram Desktop 适配探针：量真实窗口的像素，验证/调参用。只读，不点击、不输入、不发送。

跑一遍就能看到：
- 找到的是哪个窗口（标题、进程、矩形）
- 消息区被认成哪块（patterned 壁纸那套 / flat 纯色那套各给一份）
- 消息区里出现最多的几种颜色（气泡色、壁纸色一眼就能分出来）
- 头部 OCR 出的会话名、每一条消息被分成谁说的什么
- 内部各尺寸，方便核对 DPI 缩放（header_h、输入框落点偏移按这个调）

用法（仓库根目录，先装 requirements）：
    python -m probe.probe_telegram            # 抓一帧，打印
    python -m probe.probe_telegram --save out.png   # 顺手把这一帧存下来看（默认不落盘）
    python -m probe.probe_telegram --seconds 8      # 连抓 8 秒，画面变了就重打一次

注意：窗口最小化 / 托盘里收着都抓不到画面，Telegram 必须显示在桌面上。
"""
import argparse
import ctypes
import ctypes.wintypes as w
import io
import os
import sys
import time

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import chatapps  # noqa: E402
from app.capture import _chat_area_flat, _chat_area_patterned, chat_area, dpi_aware, find_chat_hwnd  # noqa: E402

u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32


def grab(hwnd):
    """整窗截图：直接走程序里的 Capture（默认 WGC，起不来自己退回 BitBlt），跟线上路径完全一致。"""
    from app.capture import Capture

    cap = Capture(hwnd, chatapps.TELEGRAM)
    print(f"frame 后端: {cap.backend}" + (f"（WGC 起不来：{str(cap.error)[:90]}）" if cap.error else ""))
    full = None
    t0 = time.time()
    while time.time() - t0 < 8 and full is None:
        time.sleep(0.15)
        full = cap.settled()
    cap.stop()
    cap.wait()
    return full


def show_area(tag, area):
    if area is None:
        print(f"  {tag:9s}: None（认不出）")
        return
    x0, y0, x1, y1, bg, yp = area
    print(f"  {tag:9s}: x {x0}..{x1} ({x1 - x0}px)  y {y0}..{y1} ({y1 - y0}px)  "
          f"面板顶 {yp}  面板色 #{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}")


def palette(img, n=8):
    small = img[::4, ::4].reshape(-1, 3)
    vals, cnt = np.unique(small, axis=0, return_counts=True)
    total = cnt.sum()
    rows = []
    for i in np.argsort(cnt)[::-1][:n]:
        v = vals[i]
        rows.append(f"#{v[0]:02X}{v[1]:02X}{v[2]:02X} {cnt[i] * 100 / total:5.1f}%")
    return "  ".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="把抓到的这一帧存成 PNG（默认只在内存里看）")
    ap.add_argument("--seconds", type=float, default=0, help="连抓多少秒，画面变了就重打")
    args = ap.parse_args()
    dpi_aware()  # 跟程序一样先声明 per-monitor DPI，否则在 200% 屏上量到的是半分辨率

    hwnd, app = find_chat_hwnd(chatapps.TELEGRAM)
    title = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, title, 256)
    rect = w.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"window  : hwnd={hwnd:#x} title={title.value!r} app={app.key}")
    print(f"rect    : {rect.left},{rect.top} - {rect.right},{rect.bottom} "
          f"({rect.right - rect.left}x{rect.bottom - rect.top})")
    print(f"visible : {bool(u32.IsWindowVisible(hwnd))}  minimized: {bool(u32.IsIconic(hwnd))}")

    full = grab(hwnd)
    print(f"frame   : {full.shape[1]}x{full.shape[0]}  mean={full.reshape(-1, 3).mean(0).round(1)}")

    flat = _chat_area_flat(full)
    patt = _chat_area_patterned(full)
    show_area("flat", flat)
    show_area("patterned", patt)
    show_area("dispatch", chat_area(full, app))

    area = chat_area(full, app)
    if area is None:
        print("\n消息区没认出来：窗口太小 / 壁纸是纯色 / 布局没铺好。")
        return
    x0, y0, x1, y1, bg, yp = area
    chat = full[y0:y1, x0:x1]
    print(f"\nmessage area palette: {palette(chat)}")
    print(f"header    palette: {palette(full[yp:y0, x0:x1])}")

    try:
        from app.ocr import Reader, read_title

        name = read_title(full[yp:y0, x0:x1], app)
        print(f"title OCR : {name!r}")
        r = Reader(app)
        lines = r.read(chat, bg)
        print(f"ocr       : {r.last_ms} ms, {len(lines)} lines")
        for who, nm, text, y in lines:
            print(f"  {who:3s} y={float(y):7.1f} {('[%s] ' % nm) if nm else ''}{text!r}")
        print("boxes     :")
        for x0b, y0b, x1b, y1b, kind, text in r.last_boxes:
            print(f"  {kind:5s} ({x0b:4d},{y0b:4d})-({x1b:4d},{y1b:4d}) {text!r}")
    except ImportError as e:
        print(f"OCR 跳过（{e}）：先 pip install -r requirements.txt")

    if args.save:
        from PIL import Image

        Image.fromarray(full).save(args.save)
        print(f"saved     : {args.save}")


if __name__ == "__main__":
    main()
