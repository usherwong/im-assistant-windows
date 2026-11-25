# -*- coding: utf-8 -*-
"""Per-chat-app profile: which window to capture, which OCR engine, which bubble is mine.

Everything that used to be hard-coded for WeChat lives here, so adding an app is one entry.
The pixel rules stay where they were proven (see ocr.who_said); this file only names them.

Telegram Desktop（v5+，Qt6）跟微信/KakaoTalk 有三处不一样，都在这一行里声明：
- 一个窗口装下列表 + 对话，跟 KakaoTalk 的一对话一窗口相反，所以按标题认主窗口；
- 对话底是一张壁纸（默认带纹理的渐变），不是纯色，消息区边界得换一套找法（capture.chat_area）；
- 己方气泡是右对齐的：浅色主题 #EEFFDE，夜间主题 #2B5278，光看颜色分不干净，
  所以 is_me 还能拿框的位置当兜底。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


def _wechat_me(bg: np.ndarray, box=None, width: int = 0) -> bool:
    """WeChat's own bubble is green: G clearly above both R and B."""
    return bool(bg[1] > bg[0] + 40 and bg[1] > bg[2] + 40)


def _kakao_me(bg: np.ndarray, box=None, width: int = 0) -> bool:
    """KakaoTalk's own bubble is #FEE500: R and G high, B low. Measured on a live window;
    the chat ground is #BACEE0 and the other side is #FFFFFF, so both stay clear of this."""
    return bool(bg[0] > 200 and bg[1] > 180 and bg[2] < 120)


def _telegram_me(bg: np.ndarray, box=None, width: int = 0) -> bool:
    """Telegram Desktop: own messages sit on the right, and the own bubble colour depends on the theme.
    Light themes use #EEFFDE (greenish white), the night theme #2B5278 (blue); the other side is
    #FFFFFF / #182533, which neither test below accepts (white has no hue, #182533 is too dark).
    Theme packs the user installs can repaint both bubbles, so when the colour says nothing the
    bubble's right edge decides: own bubbles end near the pane's right padding, the other side's
    never do (they are left-aligned and capped well short of it)."""
    r, g, b = int(bg[0]), int(bg[1]), int(bg[2])
    if r > 190 and g > r + 10 and g > b + 20:          # #EEFFDE 一类的浅绿白
        return True
    if b > g + 18 and b > r + 30 and r + g + b > 150:  # #2B5278 一类的夜间蓝
        return True
    if box is None or not width:
        return False
    return max(float(p[0]) for p in box) > 0.82 * width


def _kakao_notice_rows(chat: np.ndarray) -> int:
    """KakaoTalk 群聊置顶公告：一张几乎铺满面板宽度的白卡片，贴在消息区顶上。
    气泡最宽也就六七成宽，所以「整行白色占宽度 80% 以上」只会是公告卡。
    返回要跳过的行数（没有公告就是 0）。实测：群聊 115~146 和 167~230 行命中，单聊一行都没有。"""
    top = int(chat.shape[0] * 0.35)   # 只在顶部找：底下再宽的白也不是公告
    white = (np.abs(chat[:top].astype(int) - 255).sum(axis=2) < 30).mean(axis=1)
    rows = np.flatnonzero(white > 0.8)
    if rows.size == 0 or rows[0] > chat.shape[0] * 0.2:
        return 0
    return int(rows[-1]) + 1


@dataclass(frozen=True)
class ChatApp:
    key: str
    label: str
    exes: tuple[str, ...]        # process names, lowercase
    main_title: str              # preferred top-level window title; "" = pick the biggest window
    skip_titles: tuple[str, ...]  # windows that are never a conversation (roster, toasts)
    ocr: str                     # "rapidocr" (zh/en) or "windows" (Windows.Media.Ocr, ko/ja/...)
    join: str                    # how OCR fragments inside one bubble are glued
    is_me: Callable[..., bool]   # (bubble_bg, ocr_box, pane_width) -> is this bubble mine
    trim_top: Callable[[np.ndarray], int] = lambda chat: 0  # rows to skip (pinned notice, ...)
    # 同一进程还开着侧窗时（看图、通话、设置），只要面积不小于最大窗口这个比例的
    min_share: float = 0.0
    # "flat" = 面板底色是纯色（微信、KakaoTalk）；"patterned" = 面板底是壁纸（Telegram）
    area: str = "flat"
    # 「填入」的落点：消息区左下角往右/往下多少像素，落在输入框文字区
    input_dx: int = 60
    input_dy: int = 40


WECHAT = ChatApp("wechat", "微信", ("weixin.exe", "wechat.exe"), "微信", (),
                 "rapidocr", "", _wechat_me)
# KakaoTalk opens one window per conversation, so the roster ("카카오톡") is never the target;
# the biggest remaining window is the chat that is actually being read.
KAKAOTALK = ChatApp("kakaotalk", "카카오톡", ("kakaotalk.exe",), "", ("카카오톡", ""),
                    "windows", " ", _kakao_me, _kakao_notice_rows)
# Telegram keeps list + conversation in ONE window titled "Telegram"; the tray icon, the titlebar
# helper and the theme/screen observers are same-process windows with their own titles.
# 壁纸底 → area="patterned"；原文拼接用空格（英文/韩文换行处要留空格，中文多出来的空格无害）。
TELEGRAM = ChatApp("telegram", "Telegram", ("telegram.exe",), "Telegram",
                   ("", "QTrayIconMessageWindow", "_q_titlebar"),
                   "rapidocr", " ", _telegram_me, min_share=0.45, area="patterned",
                   input_dx=90, input_dy=34)

APPS = {a.key: a for a in (WECHAT, KAKAOTALK, TELEGRAM)}
DEFAULT = WECHAT


def by_exe(exe: str) -> ChatApp | None:
    return next((a for a in APPS.values() if exe in a.exes), None)


def get(key: str | None) -> ChatApp:
    return APPS.get(key or "", DEFAULT)


if __name__ == "__main__":  # 自测：颜色规则用实测像素锁住，改错了当场炸
    assert by_exe("kakaotalk.exe") is KAKAOTALK and by_exe("weixin.exe") is WECHAT
    assert by_exe("telegram.exe") is TELEGRAM
    assert by_exe("chrome.exe") is None and get(None) is DEFAULT and get("kakaotalk") is KAKAOTALK
    kakao_bubble, kakao_other, kakao_ground = (254, 229, 0), (255, 255, 255), (186, 206, 224)
    assert KAKAOTALK.is_me(np.array(kakao_bubble))
    assert not KAKAOTALK.is_me(np.array(kakao_other))
    assert not KAKAOTALK.is_me(np.array(kakao_ground))
    assert WECHAT.is_me(np.array((149, 236, 105))) and not WECHAT.is_me(np.array(kakao_bubble))
    # Telegram：浅色 #EEFFDE 是己方，对方白泡不是；夜间 #2B5278 是己方，#182533 不是
    assert TELEGRAM.is_me(np.array((238, 255, 222)))
    assert not TELEGRAM.is_me(np.array((255, 255, 255)))
    assert TELEGRAM.is_me(np.array((43, 82, 120)))
    assert not TELEGRAM.is_me(np.array((24, 37, 51)))
    # 认不出颜色时靠右对齐兜底：贴到 82% 以右算己方
    assert TELEGRAM.is_me(np.array((200, 200, 200)), [(10, 10), (900, 10), (900, 30), (10, 30)], 1000)
    assert not TELEGRAM.is_me(np.array((200, 200, 200)), [(10, 10), (500, 10), (500, 30), (10, 30)], 1000)
    pane = np.full((400, 300, 3), kakao_ground, np.uint8)
    assert KAKAOTALK.trim_top(pane) == 0 and WECHAT.trim_top(pane) == 0   # 공고 없는 화면
    pane[20:60] = 255                      # 폭을 꽉 채운 흰 카드 = 공지
    assert KAKAOTALK.trim_top(pane) == 60
    pane2 = np.full((400, 300, 3), kakao_ground, np.uint8)
    pane2[20:60, :150] = 255               # 반만 채운 흰색 = 그냥 기포
    assert KAKAOTALK.trim_top(pane2) == 0
    assert TELEGRAM.area == "patterned" and TELEGRAM.min_share > 0
    print("chatapps ok")
