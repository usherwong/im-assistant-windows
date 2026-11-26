# -*- coding: utf-8 -*-
"""浅色置顶回复助手：回复建议和独立设置页。发送始终由用户确认。"""
import os
import sys
import threading
from datetime import datetime
from math import isfinite
from types import SimpleNamespace

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CheckBox, ComboBox, EditableComboBox, FluentIcon as FIF,
    HyperlinkButton, IndeterminateProgressBar, LineEdit, PasswordLineEdit, PlainTextEdit,
    PrimaryPushButton, PushButton, ScrollArea, SpinBox, SwitchButton, Theme, TransparentToolButton,
    setCustomStyleSheet, setFont, setTheme, setThemeColor,
)

from app import i18n, roles, settings
from app.version import VERSION
from core import jev_client, llm, providers, scenes
from core.questions import CHOICE_LABELS
from app.i18n import LANGUAGES, T, bind

_LOG_LINES = 300
_MUTED = "#68776f"
_GREEN = "#18794e"
_RELATIONSHIPS = [
    ("不填", ""), ("恋人", "romantic partners"), ("朋友", "friends"), ("同事", "colleagues"),
    ("家人", "family"), ("自定义", None),
]


def _choice(answers, name, scene=None):
    """按题集取选项的中文标签：亲密关系用 CHOICE_LABELS，招聘/职场用各自题集的标签表。"""
    labels = (getattr(scene, "labels", None) or CHOICE_LABELS).get(name) or {}
    return T(labels.get((answers.get(name) or {}).get("choice"), "暂未判断"))


def _mp_banner_path() -> str:
    """打包后在 _MEIPASS/docs，源码跑在仓库 docs/。"""
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "docs", "wechat-mp.png")


class _MpBanner(QLabel):
    """公众号长条横幅，宽度跟着设置页走，高度按原图比例。"""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._src = QPixmap(path)
        self._shown = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        if self._src.isNull() or w <= 0 or self._src.width() <= 0:
            return 0
        return max(1, round(w * self._src.height() / self._src.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if w <= 0 or w == self._shown or self._src.isNull():
            return
        h = self.heightForWidth(w)
        self._shown = w
        self.setPixmap(self._src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if self.height() != h:
            self.setFixedHeight(h)


class _FitCombo(ComboBox):
    """长名字不撑开窄布局。按钮上按当前宽度省略；条目仍是全文，findText 靠它。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setText(self, text):
        self._full = text or ""
        QPushButton.setText(self, self._elide(self._full))
        if self._full and self.text() != self._full:
            self.setToolTip(self._full)

    def setPlaceholderText(self, text):
        index = self.currentIndex()
        super().setPlaceholderText(text)
        # Fluent ComboBox 会连第 0 项一起覆盖；重译占位文案时保留当前会话。
        if index >= 0:
            self.setCurrentIndex(index)
            self.setText(self.itemText(index))

    def minimumSizeHint(self):
        hint = QPushButton.minimumSizeHint(self)
        return QSize(48, hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        shown = self._elide(self._full)
        if shown != self.text():
            QPushButton.setText(self, shown)
        if self._full and shown != self._full:
            self.setToolTip(self._full)

    def _elide(self, text):
        # 右侧箭头大约 28px。还没排上版时先按一个窄宽度省略，避免最小宽度被整句名字撑开。
        avail = self.width() - 36 if self.width() > 64 else 120
        return self.fontMetrics().elidedText(text, Qt.ElideRight, max(24, avail))


def _label(text="", size=14, color=None, bold=False, parent=None):
    label = BodyLabel(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    setFont(label, size, QFont.DemiBold if bold else QFont.Normal)
    if color:
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(label, qss, qss)
    return label


def _tlabel(source, *args, **kwargs):
    return bind(_label("", *args, **kwargs), source)


def _tool(icon, title, callback, parent=None):
    button = TransparentToolButton(icon, parent)
    button.setFixedSize(32, 32)
    bind(button, title, "setToolTip")
    bind(button, title, "setAccessibleName")
    button.clicked.connect(callback)
    return button


class _Surface(CardWidget):
    def __init__(self, parent=None, accent=False):
        self.accent = accent
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def _normalBackgroundColor(self):
        return QColor("#edf7f0" if self.accent else "#ffffff")

    def _hoverBackgroundColor(self):
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):
        return self._normalBackgroundColor()


class _Fetched(QObject):
    """取模型列表的后台线程 → 主线程：哪一组（SimpleNamespace）、取回来的模型 id、失败原因（成功是空串）。
    Qt 不让跨线程碰控件，信号是跨线程唯一干净的路。"""
    done = Signal(object, list, str)


class _TitleBar(QWidget):
    """只有标题栏可拖动，选择正文或按按钮不会意外移动窗口。"""
    def __init__(self, parent):
        super().__init__(parent)
        self._drag = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window().pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)


class _MainWindow(QWidget):
    """窗口大小变了就叫 Overlay 重新排布；断点没跨过时 _relayout 自己不做事，这里不用防抖。"""
    def __init__(self, relayout):
        super().__init__()
        self._relayout = relayout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width(), event.size().height())


class _ReplyCard(_Surface):
    def __init__(self, owner, index, recommended=False, number=1, score=None):
        super().__init__(accent=recommended)
        box = QVBoxLayout(self)
        self.box = box
        box.setSpacing(10)
        top = QHBoxLayout()
        title = lambda: T("推荐回复") if recommended else f"{T('备选')} {number}"
        top.addWidget(_tlabel(lambda: title() + (f" · {round(score * 100)}%" if score is not None else ""),
                             12, _GREEN if recommended else _MUTED, True))
        self.copyButton = _tool(FIF.COPY, "复制这条回复", lambda: owner._copy(index), self)
        self.copyButton.setFixedSize(24, 24)
        top.addWidget(self.copyButton)
        box.addLayout(top)
        self.text = _label(owner.cands[index], 15)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.text)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.fillButton = bind((PrimaryPushButton if recommended else PushButton)("", self), "填入")
        bind(self.fillButton, lambda: T("填入 {reply}").format(reply=title()), "setAccessibleName")
        self.fillButton.clicked.connect(lambda: owner._fill(index))
        bottom.addWidget(self.fillButton)
        box.addLayout(bottom)
        self.set_compact(owner._compact)

    def set_available(self, enabled):
        self.fillButton.setEnabled(enabled)
        self.copyButton.setEnabled(enabled)

    def set_compact(self, compact):
        self.box.setContentsMargins(12, 8, 12, 8) if compact else self.box.setContentsMargins(16, 12, 16, 12)
        self.fillButton.setMinimumWidth(80 if compact else 100)


class Overlay:
    def __init__(self, on_fill, on_toggle_capture=None, on_target_change=None, result_of=None,
                 on_toggle_debug=None, on_language_changed=None, on_summary=None):
        """result_of(会话名) → 那个会话上次的结果或 None；切着看别的会话时用它把旧结果放回来。
        on_target_change(会话名, 人名) → 用户在群里挑了回复对象。
        on_toggle_debug(开不开) → 开关调试视图那个独立窗口。
        on_summary(会话名) → 用户点了「总结」，后台跑一次。"""
        self.app = QApplication.instance() or QApplication([])
        setTheme(Theme.LIGHT)
        setThemeColor(_GREEN, save=False)
        self.on_fill = on_fill
        self.on_toggle_capture = on_toggle_capture
        self.on_target_change = on_target_change
        self.on_toggle_debug = on_toggle_debug
        self.on_language_changed = on_language_changed
        self.on_summary = on_summary
        self._summaries = {}   # {会话名: 五段文本}
        self._summary_stale = set()  # 有了新消息、但总结还没重算的会话
        self._summary_busy = set()   # 正在总结的会话
        self.result_of = result_of
        self.cands = []
        self.cards = []
        self._busy = False
        self._current = False
        self._compact = None  # 断点模式：None 保证 _relayout 第一次调用必定生效
        self._pageLayouts = []
        self._hintLabels = []
        self.feeds = {}  # {会话名: [(who, name, text, timestamp)]}，换语言只重画说话人标签
        self._feed_entries = []
        self.counts = {}  # {会话名: 消息条数}
        self.hers = {}  # {会话名: 对方最近一句}
        self.targets = {}  # {会话名: ([发言人], 当前回复对象)}
        self._chat = ""  # 微信当前开着的会话
        self._shown = ""  # 界面上正在看的会话（浏览时和上面不一样）
        self.win = _MainWindow(self._relayout)
        self.win.setObjectName("assistantWindow")
        self.win.setWindowTitle("京安智猎")
        self.win.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.win.setStyleSheet(
            "QWidget#assistantWindow { background: #f5f7f6; border: 1px solid #dce3de; border-radius: 14px; }"
        )
        self.win.setMinimumWidth(320)
        self.win.setMaximumWidth(640)
        outer = QVBoxLayout(self.win)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        header = _TitleBar(self.win)
        title = QHBoxLayout(header)
        title.setContentsMargins(18, 12, 10, 10)
        title.setSpacing(8)
        name = _label("京安智猎", 20, "#233c2f", True)  # 中文名比原来的「Jev」宽，别再钉死宽度
        name.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.addWidget(name)
        self.subtitle = _label("聊天回复助手", 12, _MUTED)
        self.subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.addWidget(self.subtitle, 1)
        self.captureSwitch = SwitchButton(header)
        bind(self.captureSwitch, "采集中", "setOnText")
        bind(self.captureSwitch, "已暂停", "setOffText")
        bind(self.captureSwitch, "开启或暂停采集", "setToolTip")
        bind(self.captureSwitch, "开启或暂停采集", "setAccessibleName")
        self.captureSwitch.setChecked(True)
        self.captureSwitch.checkedChanged.connect(self._capture_toggled)
        title.addWidget(self.captureSwitch)
        self.settingsButton = _tool(FIF.SETTING, "设置", self.open_settings, header)
        title.addWidget(self.settingsButton)
        title.addWidget(_tool(FIF.REMOVE, "最小化", self.win.showMinimized, header))
        title.addWidget(_tool(FIF.CLOSE, "关闭助手", self.win.close, header))
        outer.addWidget(header)
        self.updateBar = QWidget(self.win)
        update_row = QHBoxLayout(self.updateBar)
        update_row.setContentsMargins(18, 4, 8, 4)
        update_row.setSpacing(8)
        self.updateLabel = _label("", 12, _GREEN, True)
        update_row.addWidget(self.updateLabel, 1)
        self.updateLink = bind(HyperlinkButton("", "", self.updateBar), "去下载")
        self.updateLink.setFixedHeight(24)
        update_row.addWidget(self.updateLink)
        closeUpdate = TransparentToolButton(FIF.CLOSE, self.updateBar)
        closeUpdate.setFixedSize(20, 20)
        bind(closeUpdate, "关闭更新提示", "setToolTip")
        bind(closeUpdate, "关闭更新提示", "setAccessibleName")
        closeUpdate.clicked.connect(lambda: self.updateBar.hide())
        update_row.addWidget(closeUpdate)
        self.updateBar.setFixedHeight(32)
        self.updateBar.hide()
        outer.addWidget(self.updateBar)
        self.pages = QStackedWidget(self.win)
        outer.addWidget(self.pages, 1)
        self._build_home()
        self._build_settings()
        self._build_profile()
        footer = QHBoxLayout()
        footer.setContentsMargins(20, 9, 8, 8)
        footer.addWidget(_tlabel(lambda: f"{T('仅填入输入框 · 发送由你确认')} · v{VERSION}", 11, _MUTED), 1)
        grip = QSizeGrip(self.win)
        grip.setFixedSize(16, 16)
        footer.addWidget(grip, 0, Qt.AlignBottom)
        outer.addLayout(footer)
        screen = self.app.primaryScreen().availableGeometry()
        self.win.setMinimumHeight(min(360, screen.height() - 32))
        self.win.resize(min(440, screen.width() - 32), min(820, screen.height() - 48))
        self.win.move(screen.right() - self.win.width() - 20, screen.top() + 24)
        self._relayout(self.win.width(), self.win.height())  # resizeEvent 补不到构造时这一次
        self.set_status("等待新消息" if settings.has_key() else "需要配置模型",
                        "idle" if settings.has_key() else "warning")
        self.win.show()
        if os.environ.get("JEV_OPEN_PROFILE"):  # 调试/截图用：启动就直接进档案页
            QTimer.singleShot(800, self.open_profile)

    def dock_to(self, rect):
        """把面板贴到聊天窗口右侧：优先右边，屏幕放不下换左边，再不行贴屏幕右缘；竖直方向跟窗口顶部对齐。
        rect 是聊天窗口的物理像素矩形（capture.window_rect），Qt 这边是逻辑像素，200% 缩放下要折半。"""
        dpr = self.win.devicePixelRatioF() or 1.0
        left, top, right = (v / dpr for v in rect[:3])
        screen = (self.app.screenAt(QPoint(int((left + right) / 2), int(top) + 40))
                  or self.app.primaryScreen())
        avail = screen.availableGeometry()
        gap, w, h = 12, self.win.width(), self.win.height()
        x = right + gap                                    # 先试右边
        if x + w > avail.right():
            x = left - gap - w                             # 右边放不下 → 左边
        x = max(avail.left() + gap, min(x, avail.right() - w - gap))
        y = max(avail.top() + 8, min(int(top), avail.bottom() - h - 8))
        if abs(self.win.x() - int(x)) > 2 or abs(self.win.y() - int(y)) > 2:
            self.win.move(int(x), int(y))

    def _scroll_page(self):
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("pageContent")
        content.setStyleSheet("QWidget#pageContent { background: transparent; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 8, 20, 12)
        layout.setSpacing(14)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self._pageLayouts.append(layout)
        return scroll, layout

    def _relayout(self, w, h):
        """宽度跨过断点才重新摆布局（省事）；高度每次都重算，反正只是设个定高。"""
        compact = w < 400
        if compact != self._compact:
            self._compact = compact
            self._apply_compact(compact)
        self.feed.setFixedHeight(max(100, min(240, int(h * 0.25))))

    def _apply_compact(self, compact):
        """紧凑/常规两套间距和可见性；断点没变时不会被调用。"""
        self.subtitle.setVisible(not compact)
        bind(self.captureSwitch, lambda: "" if self._compact else T("采集中"), "setOnText")
        bind(self.captureSwitch, lambda: "" if self._compact else T("已暂停"), "setOffText")
        for label in self._hintLabels:
            label.setVisible(not compact)
        self.referenceNote.setVisible(bool(self.cands) and not compact)
        self._sync_model_fields()
        margins = (12, 8, 12, 12) if compact else (20, 8, 20, 12)
        for layout in self._pageLayouts:
            layout.setContentsMargins(*margins)
        for card in self.cards:
            card.set_compact(compact)

    def _build_home(self):
        self.home, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_tlabel("回复建议", 23, "#24382d", True), 1)
        self.updated = _label("", 11, _MUTED)
        self.updated.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        heading.addWidget(self.updated)
        body.addLayout(heading)
        chat_row = QHBoxLayout()
        chat_row.setSpacing(8)
        prefix = _tlabel("当前会话", 12, _MUTED)
        prefix.setFixedWidth(56)
        chat_row.addWidget(prefix)
        self.chatBox = _FitCombo()
        bind(self.chatBox, "尚未识别到会话", "setPlaceholderText")
        bind(self.chatBox, "当前会话", "setAccessibleName")
        bind(self.chatBox, "聊天窗口切到哪个会话这里就跟到哪个；也可以自己选一个，只看它的记录和建议", "setToolTip")
        self.chatBox.currentIndexChanged.connect(self._on_chat_selected)
        chat_row.addWidget(self.chatBox, 1)
        self.profileButton = _tool(FIF.PEOPLE, "角色与档案", self.open_profile, self.win)
        chat_row.addWidget(self.profileButton)
        self.chatFollow = _label("", 11, _MUTED)
        self.chatFollow.setFixedWidth(52)
        self.chatFollow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        chat_row.addWidget(self.chatFollow)
        body.addLayout(chat_row)
        self.targetRow = QWidget()  # 只有开了「群聊指定回复对象」且这个会话是群聊才露出来
        target_row = QHBoxLayout(self.targetRow)
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(8)
        target_prefix = _tlabel("回复对象", 12, _MUTED)
        target_prefix.setFixedWidth(56)
        target_row.addWidget(target_prefix)
        self.targetBox = _FitCombo()
        bind(self.targetBox, "回复对象", "setAccessibleName")
        bind(self.targetBox, "三条候选都按这个人来写；不选就跟着最近说话的那位", "setToolTip")
        self.targetBox.currentIndexChanged.connect(self._on_target_selected)
        target_row.addWidget(self.targetBox, 1)
        self.atCheck = bind(CheckBox(""), "填入时带 @")
        self.atCheck.setChecked(True)
        bind(self.atCheck, "填入时在开头加「@名字 」。只是普通文字，不会变成真正的 @", "setToolTip")
        target_row.addWidget(self.atCheck)
        self.targetRow.hide()
        body.addWidget(self.targetRow)
        self.status = _label("", 12, _MUTED)
        body.addWidget(self.status)
        self.progress = IndeterminateProgressBar()
        self.progress.setFixedHeight(3)
        self.progress.hide()
        body.addWidget(self.progress)
        self.context = QWidget()
        context_box = QVBoxLayout(self.context)
        context_box.setContentsMargins(0, 0, 0, 0)
        context_box.setSpacing(5)
        context_box.addWidget(_tlabel("对方最近说", 11, _MUTED))
        self.latest = _label("", 14, "#42574a")
        self.latest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        context_box.addWidget(self.latest)
        self.context.hide()
        body.addWidget(self.context)

        self.insight = _Surface()
        insight_box = QVBoxLayout(self.insight)
        insight_box.setContentsMargins(14, 12, 14, 12)
        insight_box.setSpacing(7)
        row = QHBoxLayout()
        self.insightTitle = _tlabel("对话参考", 12, _MUTED)
        row.addWidget(self.insightTitle, 1)
        self.tension = _label("", 11)
        self.tension.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.tension)
        insight_box.addLayout(row)
        self.summary = _label("", 14, "#304c3c", True)
        insight_box.addWidget(self.summary)
        self.intent = _label("", 12, _MUTED)
        insight_box.addWidget(self.intent)
        self.signals = _label("", 11, _MUTED)  # 承诺成色 / 兑现记录 / 顾虑状态（招聘与职场题集才有）
        insight_box.addWidget(self.signals)
        bind(self.insight, "根据当前聊天片段推测，可能理解有偏差。紧张度为 0–9 的参考评分。", "setToolTip")
        self.insight.hide()
        body.addWidget(self.insight)

        self.empty = _Surface()
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(24, 36, 24, 36)
        empty_box.setSpacing(14)
        symbol = _label("…", 30, _GREEN, True)
        symbol.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(symbol)
        self.emptyTitle = _tlabel("等待对方的新消息", 17, "#304c3c", True)
        self.emptyTitle.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.emptyTitle)
        self.emptyHint = _tlabel("保持聊天窗口打开。\n收到新消息后，回复建议会出现在这里。", 13, _MUTED)
        self.emptyHint.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.emptyHint)
        self.setupButton = bind(PrimaryPushButton(""), "前往设置")
        self.setupButton.clicked.connect(self.open_settings)
        self.setupButton.setVisible(not settings.has_key())
        empty_box.addWidget(self.setupButton, 0, Qt.AlignHCenter)
        if not settings.has_key():
            bind(self.emptyTitle, "先设置，再开始", "setText")
            bind(self.emptyHint, "配置模型和关系背景，\n让建议更贴近你们的对话。", "setText")
        body.addWidget(self.empty)
        self.replyBox = QVBoxLayout()
        self.replyBox.setSpacing(10)
        body.addLayout(self.replyBox)
        self.referenceNote = _tlabel("AI 建议仅供参考，按你的语气调整后再发送。", 11, _MUTED)
        self.referenceNote.hide()
        body.addWidget(self.referenceNote)

        self.historyButton = bind(PushButton(FIF.HISTORY, ""), "聊天记录")
        self.historyButton.clicked.connect(self._toggle_history)
        bind(self.historyButton, "展开或收起聊天记录", "setAccessibleName")
        body.addWidget(self.historyButton)
        self.feed = PlainTextEdit()
        self.feed.setReadOnly(True)
        bind(self.feed, "识别到的聊天内容会显示在这里", "setPlaceholderText")
        self.feed.setMaximumBlockCount(_LOG_LINES)
        self.feed.setFixedHeight(160)
        self.feed.hide()
        body.addWidget(self.feed)
        self._history_title()

        # ── 对话总结（按需：点一下才跑，不跟着每条消息自动算） ──
        self.summaryCard = _Surface()
        sbox = QVBoxLayout(self.summaryCard)
        sbox.setContentsMargins(14, 12, 14, 12)
        sbox.setSpacing(6)
        srow = QHBoxLayout()
        srow.addWidget(_tlabel("对话总结", 13, "#304c3c", True), 1)
        self.summaryButton = PushButton("")
        self.summaryButton.setFixedHeight(26)
        self.summaryButton.clicked.connect(self._on_summary_clicked)
        srow.addWidget(self.summaryButton)
        sbox.addLayout(srow)
        self.summaryBody = _label("", 12, "#42574a")
        sbox.addWidget(self.summaryBody)
        self.summaryNote = _label("", 11, _MUTED)
        sbox.addWidget(self.summaryNote)
        body.addWidget(self.summaryCard)
        self._render_summary()

        body.addStretch(1)

    def _build_settings(self):
        self.settingsPage, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回回复建议", self._back_home))
        heading.addWidget(_tlabel("设置", 23, "#24382d", True), 1)
        body.addLayout(heading)
        banner = _mp_banner_path()
        if os.path.exists(banner):
            body.addWidget(_MpBanner(banner))

        language = _Surface()
        box = QVBoxLayout(language)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        language_label = _tlabel("界面语言 / Language", 16, "#304c3c", True)
        box.addWidget(language_label)
        self.languageBox = ComboBox()
        self.languageBox.setMinimumWidth(0)
        self.languageBox.addItems([name for name, code in LANGUAGES])
        bind(self.languageBox, "界面语言 / Language", "setAccessibleName")
        language_label.setBuddy(self.languageBox)
        self.languageBox.currentIndexChanged.connect(self._language_changed)
        box.addWidget(self.languageBox)
        self.languageHint = _label("", 12, _MUTED)
        box.addWidget(self.languageHint)
        self.languageOverrideHint = _tlabel("本次选择立即生效；下次启动仍优先使用 JEVCHAT_LANG 指定的语言。", 12, _MUTED)
        box.addWidget(self.languageOverrideHint)
        body.addWidget(language)

        body.addWidget(_tlabel("调整关系背景，配置判断和起草用的两个模型。", 13, _MUTED))
        preference = _Surface()
        box = QVBoxLayout(preference)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_tlabel("回复偏好", 16, "#304c3c", True))
        relation_label = _tlabel("你们的关系", 13)
        box.addWidget(relation_label)
        self.relationshipBox = ComboBox()
        self.relationshipBox.setMinimumWidth(0)
        self.relationshipBox.addItems([T(name) for name, value in _RELATIONSHIPS])
        bind(self.relationshipBox, "你们的关系", "setAccessibleName")
        relation_label.setBuddy(self.relationshipBox)
        box.addWidget(self.relationshipBox)
        self.relEdit = LineEdit()
        bind(self.relEdit, "例如：刚认识的朋友，正在慢慢熟悉", "setPlaceholderText")
        bind(self.relEdit, "自定义关系背景", "setAccessibleName")
        box.addWidget(self.relEdit)
        self.relationshipBox.currentIndexChanged.connect(
            lambda index: self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        )
        box.addWidget(self._hint("帮助助手把握称呼、语气和回应分寸。"))
        style_label = _tlabel("说话风格（可选）", 13)
        box.addWidget(style_label)
        self.styleEdit = LineEdit()
        bind(self.styleEdit, "例如：话少、不用标点、偶尔用 doge、不说客套话", "setPlaceholderText")
        bind(self.styleEdit, "说话风格", "setAccessibleName")
        style_label.setBuddy(self.styleEdit)
        box.addWidget(self.styleEdit)
        box.addWidget(self._hint("候选本来就照着你最近发的消息模仿；这里可以再补一句你自己的口吻。"))
        context_label = _tlabel("参考上下文", 13)
        box.addWidget(context_label)
        self.contextBox = SpinBox()
        self.contextBox.setRange(3, 30)
        bind(self.contextBox, "参考的最近消息条数", "setAccessibleName")
        context_label.setBuddy(self.contextBox)
        box.addWidget(self.contextBox)
        box.addWidget(self._hint(
            "生成和判断时看最近这么多条消息。太少会丢上下文，太多会稀释重点，建议 6–12。"
        ))
        target_row = QHBoxLayout()
        target_row.addWidget(_tlabel("群聊指定回复对象", 13), 1)
        self.targetSwitch = SwitchButton()
        bind(self.targetSwitch, "开", "setOnText")
        bind(self.targetSwitch, "关", "setOffText")
        bind(self.targetSwitch, "群聊指定回复对象", "setAccessibleName")
        target_row.addWidget(self.targetSwitch)
        box.addLayout(target_row)
        box.addWidget(self._hint(
            "开了以后群聊里可以选回复给谁，候选会针对 TA 写，填入时可带 @。关了就正常回复。"
        ))
        update_row = QHBoxLayout()
        update_row.addWidget(_tlabel("启动时检查更新", 13), 1)
        self.updateSwitch = SwitchButton()
        bind(self.updateSwitch, "开", "setOnText")
        bind(self.updateSwitch, "关", "setOffText")
        bind(self.updateSwitch, "启动时检查更新", "setAccessibleName")
        update_row.addWidget(self.updateSwitch)
        box.addLayout(update_row)
        box.addWidget(self._hint(
            "只向 GitHub 查最新版本号，不发送任何数据。国内访问 GitHub 慢的话关掉也行。"
        ))
        debug_row = QHBoxLayout()
        debug_row.addWidget(_tlabel("调试视图", 13), 1)
        self.debugSwitch = SwitchButton()
        bind(self.debugSwitch, "开", "setOnText")
        bind(self.debugSwitch, "关", "setOffText")
        bind(self.debugSwitch, "调试视图", "setAccessibleName")
        self.debugSwitch.checkedChanged.connect(self._debug_toggled)  # 这个开关立刻生效，不等「保存设置」
        debug_row.addWidget(self.debugSwitch)
        box.addLayout(debug_row)
        box.addWidget(self._hint(
            "另开一个窗口实时显示截到的画面和识别框：绿 = 我、蓝 = 对方、灰 = 过滤掉的灰字、"
            "红 = 当成图片丢掉、黄 = 小字丢掉。只在内存里画，不存图。"
        ))
        body.addWidget(preference)

        models = _Surface()
        box = QVBoxLayout(models)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_tlabel("模型", 16, "#304c3c", True))
        self._fetched = _Fetched()
        self._fetched.done.connect(self._models_fetched)
        self.jev = self._model_group(box, "判断 · Jev", "jev", providers.JEV_PROVIDERS)
        box.addWidget(self._hint(
            "判断意图、紧张度，并给三条候选排序。两家给的是同一个 Jev，必填。"
        ))
        self.draft = self._model_group(box, "起草 · 语言模型", "draft", providers.DRAFT_PROVIDERS)
        box.addWidget(self._hint(
            "写那三条候选。OpenAI / Anthropic / Gemini 三种接口都走各自官方 SDK。"
            "默认 DeepSeek 官网直连，国内最快。"
        ))
        think_row = QHBoxLayout()
        think_row.addWidget(_tlabel("起草时开启思考模式", 13), 1)
        self.thinkingSwitch = SwitchButton()
        bind(self.thinkingSwitch, "开", "setOnText")
        bind(self.thinkingSwitch, "关", "setOffText")
        bind(self.thinkingSwitch, "起草时开启思考模式", "setAccessibleName")
        think_row.addWidget(self.thinkingSwitch)
        box.addLayout(think_row)
        box.addWidget(self._hint(
            lambda: T("关：秒回，够用。开：模型先想再写，更斟酌但慢好几倍、贵一些。只有 {providers} 认这个开关。")
            .format(providers=" / ".join(providers.THINKING))
        ))
        body.addWidget(models)
        self.settingsFeedback = _label("", 13, _GREEN)
        self.settingsFeedback.hide()
        body.addWidget(self.settingsFeedback)
        actions = QHBoxLayout()
        back = bind(PushButton(""), "返回")
        back.clicked.connect(self._back_home)
        actions.addWidget(back)
        actions.addStretch(1)
        self.saveButton = bind(PrimaryPushButton(""), "保存设置")
        self.saveButton.clicked.connect(self._save)
        actions.addWidget(self.saveButton)
        body.addLayout(actions)
        body.addWidget(self._hint("保存后用于下一次生成的回复。"))
        body.addStretch(1)
        self._load_settings()

    def _hint(self, text):
        """设置页字段下面的灰字说明：记下来，紧凑模式一起隐藏。"""
        label = _tlabel(text, 12, _MUTED)
        self._hintLabels.append(label)
        return label

    def _model_group(self, box, title, kind, table):
        """一组「来源 / 密钥 / 模型」控件，判断和起草各一份。table 是 core/providers.py 里那张表。"""
        group = SimpleNamespace(kind=kind, table=table, ids=list(table),
                                keyTitle="判断" if kind == "jev" else "起草",
                                stored_key=lambda k=kind: (settings.jev_key() if k == "jev"
                                                           else settings.llm_key()))
        heading = QHBoxLayout()
        heading.addWidget(_tlabel(title, 14, "#304c3c", True), 1)
        group.keyState = _label("", 12, _GREEN)
        group.keyState.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        heading.addWidget(group.keyState)
        box.addLayout(heading)
        source_label = _tlabel("来源", 13)
        box.addWidget(source_label)
        group.providerBox = ComboBox()
        group.providerBox.setMinimumWidth(0)  # 选项文字长短不一，别让它撑开设置页
        group.providerBox.addItems([table[i].name for i in group.ids])
        bind(group.providerBox, lambda: f"{T(title)} · {T('来源')}", "setAccessibleName")
        source_label.setBuddy(group.providerBox)
        box.addWidget(group.providerBox)
        if kind == "draft":  # 只有两个「自定义」来源要自己填地址，别的来源这一行藏着
            self.baseLabel = _label("Base URL", 13)
            box.addWidget(self.baseLabel)
            self.baseEdit = LineEdit()
            bind(self.baseEdit, "https://你的服务/v1", "setPlaceholderText")
            bind(self.baseEdit, "自定义来源 Base URL", "setAccessibleName")
            self.baseLabel.setBuddy(self.baseEdit)
            box.addWidget(self.baseEdit)
        key_label = _tlabel("密钥", 13)
        box.addWidget(key_label)
        group.keyEdit = PasswordLineEdit()
        bind(group.keyEdit, lambda: f"{T(title)} · {T('密钥')}", "setAccessibleName")
        key_label.setBuddy(group.keyEdit)
        group.keyEdit.returnPressed.connect(self._save)
        box.addWidget(group.keyEdit)
        box.addWidget(self._hint(
            "OpenRouter 的 key 或 TypeSafe 的 key，看上面选的来源。" if kind == "jev"
            else "上面选哪家就填哪家的 key；换来源重填一次，只存这一把。"))
        model_label = _tlabel("模型", 13)
        box.addWidget(model_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        group.modelBox = EditableComboBox()  # 能选也能手打，接口新出的模型不用等我改代码
        group.modelBox.setMinimumWidth(0)
        bind(group.modelBox, lambda: f"{T(title)} · {T('模型')}", "setAccessibleName")
        model_label.setBuddy(group.modelBox)
        row.addWidget(group.modelBox, 1)
        group.fetchButton = bind(PushButton(""), "获取模型")
        bind(group.fetchButton, lambda: f"{T(title)} · {T('获取模型')}", "setAccessibleName")
        group.fetchButton.clicked.connect(lambda: self._fetch_models(group))
        row.addWidget(group.fetchButton)
        box.addLayout(row)
        group.status = _label("", 12, _MUTED)
        box.addWidget(group.status)
        group.providerBox.currentIndexChanged.connect(lambda _: self._provider_changed(group))
        return group

    @staticmethod
    def _provider_of(group):
        return group.ids[max(0, group.providerBox.currentIndex())]

    def _provider_changed(self, group):
        """换来源：模型框回到这家该有的值（存的就是这家才用存的，否则用它的默认），状态清掉。"""
        provider = self._provider_of(group)
        saved = settings.jev_provider() if group.kind == "jev" else settings.draft_provider()
        stored = settings.jev_model() if group.kind == "jev" else settings.draft_model()
        group.modelBox.clear()
        group.modelBox.setText(stored if provider == saved else group.table[provider].default)
        bind(group.status, "", "setText")
        self._sync_model_fields()

    def _sync_model_fields(self):
        """两组共用：密钥已配置/未配置、占位文案、自定义 Base URL 行的显隐，
        外加紧凑模式下把来源按钮上的文字省略——ComboBox 是 QPushButton，
        minimumSizeHint 按整段文字算，不会自动换行/省略，长名字会把设置页撑宽。"""
        for group in (self.jev, self.draft):
            provider = self._provider_of(group)
            name = group.table[provider].name
            configured = bool(group.stored_key())
            bind(group.keyState, "已配置" if configured else "未配置")
            bind(group.keyEdit, "已配置，留空保留" if configured else
                 lambda name=name: T("输入 {name} API 密钥").format(name=name), "setPlaceholderText")
            if self._compact:
                name = group.providerBox.fontMetrics().elidedText(name, Qt.ElideRight, 180)
            group.providerBox.setText(name)
        custom = self._provider_of(self.draft) in providers.CUSTOM
        self.baseLabel.setVisible(custom)
        self.baseEdit.setVisible(custom)

    def _fetch_models(self, group):
        """「获取模型」：拿填的 key（没填就拿存的）去问接口，网络调用丢后台线程。"""
        provider = self._provider_of(group)
        custom = group.kind == "draft" and provider in providers.CUSTOM
        base = self.baseEdit.text().strip() if custom else None
        key = group.keyEdit.text().strip() or group.stored_key()
        if not key:
            bind(group.status, "先填密钥", "setText")
            return
        if custom and not base:
            bind(group.status, "先填 Base URL", "setText")
            return
        bind(group.status, "获取中…", "setText")
        group.fetchButton.setEnabled(False)
        threading.Thread(target=lambda: self._list_models(group, provider, key, base),
                         daemon=True).start()

    def _list_models(self, group, provider, key, base):
        """后台线程：判断走 jev_client，起草按协议走 llm；失败把原因一起送回主线程。"""
        try:
            if group.kind == "jev":
                models = jev_client.list_models(provider, key)
            else:
                spec = providers.DRAFT_PROVIDERS[provider]
                models = llm.list_models(spec.protocol, base or spec.base, key, headers=spec.headers)
                if spec.keep:  # 目录里混了别的协议时，只留这条路打得通的
                    models = [m for m in models if spec.keep(m)]
            reason = "" if models else "这个来源没返回任何模型"
        except Exception as exc:  # 线程里漏异常会静默吞掉，按钮就永远停在禁用态
            models, reason = [], str(exc)[:120]
        self._fetched.done.emit(group, models, reason)

    def _models_fetched(self, group, models, reason):
        """回到主线程：填进下拉框，原来选中的还在列表里就留着。"""
        group.fetchButton.setEnabled(True)
        if not models:
            bind(group.status, reason or "获取失败，检查密钥、网络或 Base URL")
            return
        current = group.modelBox.text().strip()
        group.modelBox.clear()
        group.modelBox.addItems(models)
        if current in models:
            group.modelBox.setCurrentIndex(models.index(current))
        else:
            group.modelBox.setText(current)  # 手打的没在列表里也不清掉
        bind(group.status, lambda: T("共 {count} 个").format(count=len(models)))

    def _set_group(self, group, provider, model):
        """把存下来的来源和模型放回一组控件里；填充不算用户操作，别触发换来源的重置。"""
        group.providerBox.blockSignals(True)
        group.providerBox.setCurrentIndex(group.ids.index(provider))
        group.providerBox.blockSignals(False)
        group.keyEdit.clear()
        group.modelBox.clear()
        group.modelBox.setText(model)
        bind(group.status, "", "setText")

    def _load_language(self):
        lang = i18n.language()
        index = next((i for i, (_, code) in enumerate(LANGUAGES) if code == lang), 0)
        self.languageBox.blockSignals(True)
        self.languageBox.setCurrentIndex(index)
        self.languageBox.blockSignals(False)
        bind(self.languageHint, "选择后立即生效，并自动保存。", "setText")
        self.languageOverrideHint.setVisible(bool(os.environ.get("JEVCHAT_LANG", "").strip()))

    def _language_changed(self, index):
        if not 0 <= index < len(LANGUAGES):
            return
        try:
            settings.save(lang_text=LANGUAGES[index][1])
        except Exception:
            self._load_language()
            bind(self.languageHint, "语言保存失败，请检查配置文件是否可写后重试。", "setText")
            return
        i18n.reload(LANGUAGES[index][1])
        self._retranslate()
        bind(self.languageHint, "语言已切换并保存。", "setText")
        if self.on_language_changed:
            self.on_language_changed()

    def _retranslate(self):
        """只重画文字，保留输入、控件状态和进行中的模型请求。"""
        i18n.retranslate(self.win)
        blocked = self.relationshipBox.blockSignals(True)
        for index, (name, _) in enumerate(_RELATIONSHIPS):
            self.relationshipBox.setItemText(index, T(name))
        self.relationshipBox.blockSignals(blocked)
        self._refresh_history_text()

    def _load_settings(self):
        self._load_language()
        relationship = settings.relationship()
        index = next((i for i, (_, value) in enumerate(_RELATIONSHIPS) if value == relationship),
                     len(_RELATIONSHIPS) - 1)
        self.relationshipBox.setCurrentIndex(index)
        self.relEdit.setText(relationship if _RELATIONSHIPS[index][1] is None else "")
        self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        self.styleEdit.setText(settings.style())
        self.contextBox.setValue(settings.context())
        self.targetSwitch.setChecked(settings.reply_target())
        self._set_group(self.jev, settings.jev_provider(), settings.jev_model())
        self._set_group(self.draft, settings.draft_provider(), settings.draft_model())
        self.baseEdit.setText(settings.draft_base_url())
        self.thinkingSwitch.setChecked(settings.thinking())
        self.updateSwitch.setChecked(settings.check_update())
        self.set_debug_switch(settings.debug_view())  # 屏蔽信号地拨，别在加载时开关一遍窗口
        self._sync_model_fields()  # 上面屏蔽了信号，这里补一次
        self.settingsFeedback.hide()

    def _save(self):
        relationship = _RELATIONSHIPS[self.relationshipBox.currentIndex()][1]
        relationship = relationship or self.relEdit.text().strip()
        jev_provider = self._provider_of(self.jev)
        draft_provider = self._provider_of(self.draft)
        base = self.baseEdit.text().strip()
        if not relationship:
            self._settings_feedback("请填写关系背景，或选择一个已有选项。", error=True)
            self.relEdit.setFocus()
            return
        if draft_provider in providers.CUSTOM and not base:
            self._settings_feedback("自定义来源要填 Base URL。", error=True)
            self.baseEdit.setFocus()
            return
        for group, provider in ((self.jev, jev_provider), (self.draft, draft_provider)):
            name = group.table[provider].name
            if not group.keyEdit.text().strip() and not group.stored_key():
                self._settings_feedback(lambda group=group:
                                        T("请先填写 {name} 的 API 密钥。").format(name=T(group.keyTitle)), error=True)
                group.keyEdit.setFocus()
                return
            if not group.modelBox.text().strip():
                self._settings_feedback(lambda: T("{name} 请先获取并选择一个模型。").format(name=name), error=True)
                group.modelBox.setFocus()
                return
        try:
            settings.save(relationship, self.contextBox.value(),
                          jev_provider_text=jev_provider,
                          jev_key_text=self.jev.keyEdit.text().strip() or None,
                          jev_model_text=self.jev.modelBox.text().strip(),
                          draft_provider_text=draft_provider,
                          llm_key_text=self.draft.keyEdit.text().strip() or None,
                          draft_model_text=self.draft.modelBox.text().strip(),
                          draft_base_url_text=base,
                          reply_target_on=self.targetSwitch.isChecked(),
                          style_text=self.styleEdit.text().strip(),
                          thinking_on=self.thinkingSwitch.isChecked(),
                          check_update_on=self.updateSwitch.isChecked())
        except Exception:
            self._settings_feedback("保存失败，请检查配置文件是否可写后重试。", error=True)
            return
        self._load_settings()
        self._render_targets()  # 开关刚改过，回到首页时这一行该显该藏得重算一次
        self._settings_feedback("设置已保存，将用于下一次回复。")
        self.setupButton.hide()
        if not self.cands and not self._busy:
            self._empty_text()
            self.set_status("设置已就绪，等待新消息", "idle")

    def _debug_toggled(self, on):
        """调试视图独立于「保存设置」：拨一下就开窗/收窗，顺手落盘，重启还在。"""
        settings.save(debug_view_on=on)
        if self.on_toggle_debug:
            self.on_toggle_debug(on)

    def set_debug_switch(self, on):
        """调试窗被用户直接关掉时把开关拨回去；屏蔽信号，免得又回调一圈。"""
        self.debugSwitch.blockSignals(True)
        self.debugSwitch.setChecked(on)
        self.debugSwitch.blockSignals(False)

    def _settings_feedback(self, text, error=False):
        color = "#b44832" if error else _GREEN
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.settingsFeedback, qss, qss)
        bind(self.settingsFeedback, text)
        self.settingsFeedback.show()

    def open_settings(self):
        if self.pages.currentWidget() != self.settingsPage:
            self._load_settings()
        self.pages.setCurrentWidget(self.settingsPage)
        self.settingsButton.setEnabled(False)
        self.languageBox.setFocus()
        self.settingsPage.verticalScrollBar().setValue(0)

    def _back_home(self):
        self.jev.keyEdit.clear()
        self.draft.keyEdit.clear()
        self.pages.setCurrentWidget(self.home)
        self.settingsButton.setEnabled(True)

    def _fill(self, index):
        if self._busy or not self._current or index >= len(self.cands):
            return
        try:
            self.on_fill(self.cands[index])
        except Exception as e:
            # 状态栏直接给原因（权限、剪贴板被占…），聊天记录里留类型和压缩堆栈
            reason = " ".join(str(e).split())[:42]
            import traceback
            self.set_status(f"{T('未能填入')}：{reason}" if reason else T("未能填入，请复制回复。"), "error")
            self.log(f"[填入失败] {type(e).__name__}: {e}")
            self.log(f"[填入失败堆栈] {' '.join(traceback.format_exc().split())[:300]}")
            return
        self.set_status("已尝试填入，请确认内容后发送。", "success")

    def _copy(self, index):
        if self._busy or not self._current or index >= len(self.cands):
            return
        self.app.clipboard().setText(self.cands[index])
        self.set_status("回复已复制，可粘贴并修改。", "success")

    def _capture_toggled(self, on):
        """用户自己拨的开关：界面先改，再通知父进程去开/停采集。"""
        self._capture_text(on)
        if self.on_toggle_capture:
            self.on_toggle_capture(on)

    def set_update(self, latest, url):
        """main.py 后台线程查到比当前新的版本才会调这个。只显示版本号和 Release 链接，别的什么都没有。"""
        bind(self.updateLabel, lambda: T("有新版本 v{version}").format(version=latest))
        self.updateLink.setUrl(url)
        self.updateBar.show()

    def set_capture(self, on, reason=""):
        """父进程回报的状态：只改界面，不回调（不然和父进程来回打架）。reason 为空用默认说明。"""
        self.captureSwitch.blockSignals(True)
        self.captureSwitch.setChecked(on)
        self.captureSwitch.blockSignals(False)
        self._capture_text(on, reason)

    def _capture_text(self, on, reason=""):
        """开关状态对应的状态行和空态文案。已有的候选不受影响，暂停了照样能填入/复制。"""
        configured = settings.has_key()
        if not on:
            self.set_status(reason or "采集已暂停，聊天内容不再读取", "warning")
        elif configured:
            self.set_status("等待新消息", "idle")
        else:
            self.set_status("请先在设置中配置模型", "warning")
        if self._busy or self.cands:  # 正在生成或已有候选时，空态卡片本来就看不见
            return
        if not on:
            bind(self.emptyTitle, "采集已暂停", "setText")
            bind(self.emptyHint, "聊天内容暂时不再读取。\n打开标题栏的开关，继续接收新消息。", "setText")
            self.setupButton.setVisible(not configured)
        else:
            self._empty_text()

    def set_busy(self, busy):
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.invalidate_replies()
            self.progress.start()
            self.set_status("正在根据新消息整理回复…", "busy")
            if not self.cands:
                bind(self.emptyTitle, "正在想一句合适的回复", "setText")
                bind(self.emptyHint, "正在结合上下文生成建议，稍等一下。", "setText")
                self.setupButton.hide()
        else:
            self.progress.stop()
            if not self.cands:
                self._empty_text()
        for card in self.cards:
            card.set_available(self._current and not busy)

    def _empty_text(self):
        """空态卡片的默认文案，配好没配好两套说法。"""
        configured = settings.has_key()
        bind(self.emptyTitle, "等待对方的新消息" if configured else "先设置，再开始")
        bind(self.emptyHint, "保持聊天窗口打开。\n收到新消息后，回复建议会出现在这里。"
                            if configured else "配置模型和关系背景，\n让建议更贴近你们的对话。")
        self.setupButton.setVisible(not configured)

    def invalidate_replies(self):
        self._current = False
        if self.cands:
            bind(self.updated, "上次建议", "setText")
        for card in self.cards:
            card.set_available(False)

    def set_status(self, text, kind="idle"):
        colors = {"idle": _MUTED, "busy": _GREEN, "success": _GREEN,
                  "warning": "#93611d", "error": "#b44832"}
        markers = {"idle": "●", "busy": "●", "success": "✓", "warning": "!", "error": "!"}
        qss = f"BodyLabel {{ color: {colors.get(kind, _MUTED)}; background: transparent; }}"
        setCustomStyleSheet(self.status, qss, qss)
        bind(self.status, lambda: f"{markers.get(kind, '●')}  {text() if callable(text) else T(text)}")
        if kind == "error" and self._busy:
            self.set_busy(False)
        if kind == "error" and not self.cands:
            bind(self.emptyTitle, "暂时没有可用的回复", "setText")
            bind(self.emptyHint, "请按上方提示处理。收到新的对方消息后会再次尝试。", "setText")
            self.setupButton.setVisible(not settings.has_key())

    def _toggle_history(self):
        self.feed.setVisible(self.feed.isHidden())
        self._history_title()

    def _history_title(self):
        action = "展开聊天记录" if self.feed.isHidden() else "收起聊天记录"
        count = self.counts.get(self._shown, 0)
        bind(self.historyButton, lambda: T(action) + (f" · {count}" if count else ""))

    @staticmethod
    def _log_text(entry):
        if isinstance(entry, str):  # 诊断信息保持原文
            return entry
        who, name, text, timestamp = entry
        speaker = (name or T("对方")) if who == "her" else T("我")
        return f"{timestamp}  {speaker}\n{text}\n"

    def _refresh_history_text(self):
        bar = self.feed.verticalScrollBar()
        position = bar.value()
        follow = position >= bar.maximum() - 4
        self.feed.setPlainText("\n".join(self._log_text(entry) for entry in self._feed_entries))
        bar.setValue(bar.maximum() if follow else position)

    def log(self, line):
        """采集状态行：只进正在看的那个会话，不按会话存。"""
        bar = self.feed.verticalScrollBar()
        follow = self.feed.isHidden() or bar.value() >= bar.maximum() - 4
        self._feed_entries.append(line)
        del self._feed_entries[:-_LOG_LINES]
        self.feed.appendPlainText(self._log_text(line))
        if follow:
            bar.setValue(bar.maximum())

    def log_message(self, who, text, name="", timestamp=None, chat=None):
        """按会话存一份；只有正在看的那个会往显示区里写。"""
        chat = chat or self._shown
        timestamp = timestamp or datetime.now().strftime("%H:%M")
        self.counts[chat] = self.counts.get(chat, 0) + 1
        lines = self.feeds.setdefault(chat, [])
        lines.append((who, name, text, timestamp))
        del lines[:-_LOG_LINES]
        if who == "her":
            self.hers[chat] = text
        self._add_chat(chat)
        if chat != self._shown:
            return
        self.log(lines[-1])
        if who == "her":
            self._show_latest(text)
        self._history_title()

    def _show_latest(self, text):
        self.latest.setText(text if len(text) <= 120 else text[:120] + "…")
        self.latest.setToolTip(text)
        self.context.show()

    def current_chat(self):
        """界面上正在看的会话（不一定是微信当前开着的那个）。"""
        return self._shown

    def set_chat(self, title):
        """微信切到了哪个会话：登记进下拉框并自动跟过去，不触发用户选择的回调。"""
        if not title:
            return
        browsing = self._shown != self._chat  # 正看着的就是它、但之前是「浏览中」：也得重画，把填入放开
        self._chat = title
        self._add_chat(title)
        if title != self._shown or browsing:
            self.chatBox.blockSignals(True)
            self.chatBox.setCurrentIndex(self.chatBox.findText(title))
            self.chatBox.blockSignals(False)
            self._switch_to(title)
        self._follow_text()

    def _add_chat(self, title):
        """新会话自动进下拉框；addItem 添第一条时会自己选中，别让它触发切换。"""
        if not title or self.chatBox.findText(title) >= 0:
            return
        self.chatBox.blockSignals(True)
        self.chatBox.addItem(title)
        self.chatBox.blockSignals(False)

    def _on_chat_selected(self, index):
        """用户自己挑了一个会话：只换看的内容，微信那边不动。"""
        title = self.chatBox.itemText(index)
        if title and title != self._shown:
            self._switch_to(title)

    def _switch_to(self, title):
        """换正在看的会话：记录、对方最近说、条数、上次的建议一起换过去。"""
        self._shown = title
        self._feed_entries = list(self.feeds.get(title, []))
        self._refresh_history_text()
        self.feed.verticalScrollBar().setValue(self.feed.verticalScrollBar().maximum())
        her = self.hers.get(title)
        if her:
            self._show_latest(her)
        else:
            self.context.hide()
        self._history_title()
        self._follow_text()
        self._render_targets()
        self.show_cached(self.result_of(title) if self.result_of else None)
        self._render_summary()

    def set_targets(self, chat, senders, current):
        """某个会话的发言人名单（最近的在前）和当前回复对象；正看着它才重画。"""
        self.targets[chat] = (list(senders), current)
        if chat == self._shown:
            self._render_targets()

    def _render_targets(self):
        """开关关着、或这个会话没有发言人（单聊），这一行就不出现。
        重填下拉框时屏蔽信号，别把自己的填充当成用户挑的。"""
        senders, current = self.targets.get(self._shown, ([], None))
        visible = bool(senders) and settings.reply_target()
        self.targetRow.setVisible(visible)
        if not visible:
            return
        self.targetBox.blockSignals(True)
        self.targetBox.clear()
        self.targetBox.addItems(senders)
        self.targetBox.setCurrentIndex(senders.index(current) if current in senders else 0)
        self.targetBox.blockSignals(False)

    def _on_target_selected(self, index):
        """用户挑了回复对象。浏览别的会话时改的就是那个会话的对象——记录、候选也都按会话走，口径一致。"""
        name = self.targetBox.itemText(index)
        if not name:
            return
        senders, _ = self.targets.get(self._shown, ([], None))
        self.targets[self._shown] = (senders, name)
        self.set_status(lambda: T("按「{name}」重新生成…").format(name=name), "busy")
        if self.on_target_change:
            self.on_target_change(self._shown, name)

    def at_prefix_enabled(self):
        """填入时要不要带「@名字 」前缀（只记在界面上，不落盘）。"""
        return self.atCheck.isChecked()

    def _follow_text(self):
        bind(self.chatFollow, ("跟随" if self._shown == self._chat else "浏览中") if self._chat else "")

    def show_cached(self, result):
        """把某个会话上次的结果放回界面；没有就回到空态。浏览别的会话时只给看不给填——
        微信当前开着的不是它，填进去就串会话了。"""
        if result:
            self.show(result)
        else:
            self.cands = []
            self._clear_cards()
            self.insight.hide()
            self.referenceNote.hide()
            self.empty.show()
            bind(self.updated, "", "setText")
            self._empty_text()
        if self._shown != self._chat:
            self.invalidate_replies()
            self.set_status(lambda: T("正在浏览「{name}」，只看不填；切回这个会话才能用。").format(name=self._shown))

    def show(self, result):
        """按推荐顺序展示，按钮始终绑定 candidates 的原始索引。"""
        self.cands = result["candidates"]
        self.set_busy(False)
        self._current = bool(self.cands)
        self._clear_cards()
        best = result.get("best_index", 0)
        if best not in range(len(self.cands)):
            best = 0
        raw_scores = result.get("scores") or []
        scores = [raw_scores[i] if i < len(raw_scores) else None for i in range(len(self.cands))]
        if not any(scores):  # 全 0/None（旧结果或接口未返回）就不展示百分比
            scores = [None] * len(self.cands)
        # 按概率降序排，推荐位（API 给的 choice）强制第一，同分按原索引
        order = sorted(range(len(self.cands)), key=lambda i: (i != best, -(scores[i] or 0), i))
        for position, index in enumerate(order):
            card = _ReplyCard(self, index, recommended=index == best, number=position, score=scores[index])
            self.replyBox.addWidget(card)
            self.cards.append(card)
        reply_to = result.get("reply_to")
        bind(self.insightTitle, lambda: T("对话参考 · 回复给 {name}").format(name=reply_to)
             if reply_to else T("对话参考"))
        answers = result.get("answers") or {}
        scene = scenes.get(result.get("scene"))   # 结果自带题集：招聘场景的字段名/分数名都不一样
        self._scene = scene
        action_key = next((name for name, _ in scene.guide if name == "best_action"), "best_action")
        action_title = next((title for name, title in scene.guide if name == action_key), "建议动作")
        bind(self.summary, lambda: T(action_title) + "：" + _choice(answers, action_key, scene))

        def _intent_text():
            parts = []
            for name, title in scene.guide:
                if name in (action_key, "commitment", "delivery", "concern"):
                    continue
                parts.append(T(title) + " · " + _choice(answers, name, scene))
            return "\n".join(parts)

        bind(self.intent, _intent_text)

        def _signals_text():
            parts = []
            for name, title in scene.guide:
                if name not in ("commitment", "delivery", "concern"):
                    continue
                parts.append(f"{T(title)} {_choice(answers, name, scene)}")
            return " ｜ ".join(parts)

        bind(self.signals, _signals_text)
        self.signals.setVisible(bool(_signals_text()))
        score = (answers.get(scene.score_key) or {}).get("score")
        valid_score = isinstance(score, (int, float)) and isfinite(score) and 0 <= score <= 9
        score_label = scene.score_label
        level = "低" if not valid_score or score <= 3 else ("中" if score <= 6 else "高")
        bind(self.tension, lambda: f"{T(score_label)} {score:.0f}/9 · {T(level)}" if valid_score
             else T(score_label + "待判断"))
        color = "#996819" if valid_score and score >= 4 else _MUTED   # 低/中/高 与颜色同一条线
        if valid_score and score >= 7:
            color = "#b44832"
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.tension, qss, qss)
        self.empty.setVisible(not self.cands)
        self.insight.setVisible(bool(self.cands))
        self.referenceNote.setVisible(bool(self.cands) and not self._compact)
        updated_at = datetime.now().strftime("%H:%M")
        bind(self.updated, lambda: updated_at + " " + T("更新"))
        if self.cands:
            self.set_status("建议已更新，选一句适合你的回复", "success")
        else:
            self.set_status("未生成可用回复，请等待下一条新消息。", "error")

    # ── 角色与档案（M2）──────────────────────────────────────────────────
    def _build_profile(self):
        self.profile, body = self._scroll_page()
        self._role_ids: list[str] = []
        self._scene_ids: list[str] = [s.key for s in scenes.SCENES.values()]
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回", self._close_profile, self.win))
        heading.addWidget(_tlabel("角色与档案", 23, "#24382d", True), 1)
        body.addLayout(heading)
        body.addWidget(_tlabel("这个会话以谁的身份说话，以及对方的档案。保存后下一条消息立刻生效。",
                               12, _MUTED))

        def add_field(box, label, widget, hint=None):
            box.addWidget(_tlabel(label, 13))
            box.addWidget(widget)
            if hint:
                box.addWidget(self._hint(hint))

        # —— 我的角色 ——
        card = _Surface()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)
        box.addWidget(_tlabel("我的角色", 16, "#304c3c", True))
        box.addWidget(_tlabel("改选就绑定到这个会话：以后进这个会话一直用它。", 12, _MUTED))
        self.profileRoleBox = ComboBox()
        self.profileRoleBox.setMinimumWidth(0)
        bind(self.profileRoleBox, "这个会话用的角色", "setAccessibleName")
        self.profileRoleBox.currentIndexChanged.connect(self._profile_role_changed)
        box.addWidget(self.profileRoleBox)

        self.roleName = LineEdit()
        bind(self.roleName, "角色名，例如：员工1", "setPlaceholderText")
        add_field(box, "名字", self.roleName)

        self.roleScene = ComboBox()
        self.roleScene.setMinimumWidth(0)
        add_field(box, "判断题库", self.roleScene, "亲密关系用原来那 7 题；职场/招聘用各自的题集。")

        self.roleIdentity = LineEdit()
        bind(self.roleIdentity, "例如：做 AI 方向的猎头", "setPlaceholderText")
        add_field(box, "身份", self.roleIdentity)

        self.roleTone = LineEdit()
        bind(self.roleTone, "例如：句子短、不用「亲/您」、偶尔自嘲", "setPlaceholderText")
        add_field(box, "说话方式", self.roleTone)

        self.rolePersonality = LineEdit()
        bind(self.rolePersonality, "例如：共情强、结果导向、不喜欢被临时打断", "setPlaceholderText")
        add_field(box, "性格", self.rolePersonality)

        self.roleGoal = LineEdit()
        bind(self.roleGoal, "例如：判断对方是否真在考虑机会", "setPlaceholderText")
        add_field(box, "这轮目标", self.roleGoal)

        self.roleBoundaries = PlainTextEdit()
        self.roleBoundaries.setFixedHeight(64)
        bind(self.roleBoundaries, "一行一条，例如：不聊私人事务", "setPlaceholderText")
        add_field(box, "边界（一行一条）", self.roleBoundaries)

        self.roleFacts = PlainTextEdit()
        self.roleFacts.setFixedHeight(150)
        bind(self.roleFacts, "标签 | 内容 | 补充 | public/personal | 别名1,别名2", "setPlaceholderText")
        add_field(box, "事实表（命中才注入，一行一条）", self.roleFacts,
                  "被问到才用：标签 | 内容 | 补充 | 敏感度(public/personal) | 检索别名（中英都写，逗号分隔）。"
                  "secret 的不许写这里，放进保险箱。最多 30 条、每条 ≤60 字。")

        self.roleConstraints = PlainTextEdit()
        self.roleConstraints.setFixedHeight(64)
        bind(self.roleConstraints, "一行一条，例如：被问私人生活只回「私人事务，暂无评论」", "setPlaceholderText")
        add_field(box, "红线（一行一条，每次都会注入）", self.roleConstraints)

        self.roleVault = _label("", 12, _MUTED)
        add_field(box, "保险箱（只在这里看，永不进提示词）", self.roleVault)

        self.roleStatus = _label("", 12, _MUTED)
        save_role = PrimaryPushButton(T("保存角色"))
        save_role.clicked.connect(self._save_role)
        box.addWidget(save_role)
        box.addWidget(self.roleStatus)
        body.addWidget(card)

        # —— 对方档案 ——
        card = _Surface()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)
        box.addWidget(_tlabel("对方档案", 16, "#304c3c", True))
        self.contactWho = _label("", 12, _MUTED)
        box.addWidget(self.contactWho)

        def contact_field(label, attr, placeholder):
            edit = LineEdit()
            bind(edit, placeholder, "setPlaceholderText")
            add_field(box, label, edit)
            setattr(self, attr, edit)
            return edit

        contact_field("姓名 / 称呼", "contactName", "例如：赵明")
        contact_field("公司", "contactCompany", "例如：某新能源公司")
        contact_field("职位", "contactPosition", "例如：工艺总监")
        contact_field("来源 / 归属", "contactSource", "例如：候选池-A / 预选客户")
        contact_field("阶段", "contactStage", "例如：在观望 / 已约面试")
        contact_field("对方想要什么", "contactNeeds", "例如：薪资涨幅 30%，不想长期出差")
        self.contactNotes = PlainTextEdit()
        self.contactNotes.setFixedHeight(70)
        bind(self.contactNotes, "备注：聊过什么、忌讳、家庭情况…", "setPlaceholderText")
        add_field(box, "备注", self.contactNotes)

        self.contactStatus = _label("", 12, _MUTED)
        save_contact = PrimaryPushButton(T("保存对方档案"))
        save_contact.clicked.connect(self._save_contact)
        box.addWidget(save_contact)
        box.addWidget(self.contactStatus)
        body.addWidget(card)

    def open_profile(self):
        self._refresh_profile()
        self.pages.setCurrentWidget(self.profile)

    def _close_profile(self):
        self.pages.setCurrentWidget(self.home)

    def _refresh_profile(self):
        chat = self.current_chat()
        rows = roles.all_roles()
        current = roles.role_for(chat)
        self._role_ids = list(rows.keys())
        self.profileRoleBox.blockSignals(True)
        self.profileRoleBox.clear()
        self.profileRoleBox.addItems([rows[rid].get("name") or rid for rid in self._role_ids])
        if current["id"] in self._role_ids:
            self.profileRoleBox.setCurrentIndex(self._role_ids.index(current["id"]))
        self.profileRoleBox.blockSignals(False)
        self.roleScene.blockSignals(True)
        self.roleScene.clear()
        self.roleScene.addItems([T(s.label) for s in scenes.SCENES.values()])
        self.roleScene.blockSignals(False)
        self._fill_role(current)
        self._fill_contact(chat)

    def _fill_role(self, row):
        voice = row.get("voice") or {}
        self.roleName.setText(row.get("name", ""))
        index = self._scene_ids.index(row.get("scene")) if row.get("scene") in self._scene_ids else 0
        self.roleScene.blockSignals(True)
        self.roleScene.setCurrentIndex(index)
        self.roleScene.blockSignals(False)
        self.roleIdentity.setText(voice.get("identity", ""))
        self.roleTone.setText(voice.get("tone", ""))
        self.rolePersonality.setText(voice.get("personality", ""))
        self.roleGoal.setText(voice.get("goal", ""))
        self.roleBoundaries.setPlainText("\n".join(voice.get("boundaries") or []))
        self.roleFacts.setPlainText(roles.facts_to_text(row))
        self.roleConstraints.setPlainText("\n".join(row.get("constraints") or []))
        vault = [str(item.get("label", "")) for item in row.get("vault") or []]
        self.roleVault.setText("；".join(vault) if vault else T("（空）"))
        self.roleStatus.setText("")

    def _fill_contact(self, chat):
        row = roles.contact(chat)
        self.contactWho.setText(T("当前会话：") + (chat or T("未识别")))
        for attr, key in (("contactName", "display_name"), ("contactCompany", "company"),
                          ("contactPosition", "position"), ("contactSource", "source"),
                          ("contactStage", "stage"), ("contactNeeds", "needs")):
            getattr(self, attr).setText(str(row.get(key) or ""))
        self.contactNotes.setPlainText(str(row.get("notes") or ""))
        self.contactStatus.setText("")

    def _profile_role_changed(self, index):
        if not (0 <= index < len(self._role_ids)):
            return
        role_id = self._role_ids[index]
        roles.bind_role(self.current_chat(), role_id)
        self._fill_role(roles.role(role_id))

    def _save_role(self):
        index = self.profileRoleBox.currentIndex()
        if not (0 <= index < len(self._role_ids)):
            return
        try:
            row = roles.update_role(
                self._role_ids[index], name=self.roleName.text(),
                scene=self._scene_ids[self.roleScene.currentIndex()],
                identity=self.roleIdentity.text(), tone=self.roleTone.text(),
                personality=self.rolePersonality.text(), goal=self.roleGoal.text(),
                boundaries=self.roleBoundaries.toPlainText(),
                constraints=self.roleConstraints.toPlainText(),
                facts_text=self.roleFacts.toPlainText())
        except (ValueError, OSError) as exc:
            self.roleStatus.setText(f"{T('保存失败')}：{exc}")
            self.set_status(f"{T('保存失败')}：{exc}", "warning")
            return
        self._fill_role(row)
        self.roleStatus.setText(T("已保存，下一条消息按新档案生成"))

    def _save_contact(self):
        try:
            roles.upsert_contact(
                self.current_chat(), display_name=self.contactName.text().strip(),
                company=self.contactCompany.text().strip(), position=self.contactPosition.text().strip(),
                source=self.contactSource.text().strip(), stage=self.contactStage.text().strip(),
                needs=self.contactNeeds.text().strip(), notes=self.contactNotes.toPlainText().strip())
        except OSError as exc:
            self.contactStatus.setText(f"{T('保存失败')}：{exc}")
            return
        self.contactStatus.setText(T("已保存"))

    # ── 对话总结（M3）────────────────────────────────────────────────────
    def _on_summary_clicked(self):
        if self.on_summary:
            self.on_summary(self.current_chat())

    def _render_summary(self):
        title = self.current_chat()
        text = self._summaries.get(title)
        if title in self._summary_busy:
            self.summaryButton.setEnabled(False)
            self.summaryButton.setText(T("总结中…"))
            self.summaryBody.setText(T("正在读这段对话…"))
            self.summaryNote.setText("")
            return
        self.summaryButton.setEnabled(True)
        self.summaryButton.setText(T("重新总结") if text else T("总结"))
        if text:
            self.summaryBody.setText(text)
            self.summaryNote.setText(T("有新消息，建议重新总结") if title in self._summary_stale else "")
        else:
            self.summaryBody.setText(T("点「总结」看看这段聊到哪了"))
            self.summaryNote.setText("")

    def show_summary(self, title, text, stale=False):
        self._summaries[title] = text
        if stale:
            self._summary_stale.add(title)
        else:
            self._summary_stale.discard(title)
        if title == self.current_chat():
            self._render_summary()

    def set_summary_busy(self, title, busy):
        if busy:
            self._summary_busy.add(title)
        else:
            self._summary_busy.discard(title)
        if title == self.current_chat():
            self._render_summary()

    def set_summary_error(self, title, message):
        self._summary_busy.discard(title)
        if title == self.current_chat():
            self._render_summary()
            self.summaryNote.setText(f"{T('总结失败')}：{message}")

    def mark_summary_stale(self, title):
        if title in self._summaries:
            self._summary_stale.add(title)
            if title == self.current_chat():
                self._render_summary()

    def _clear_cards(self):
        for card in self.cards:
            self.replyBox.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []

    def after(self, ms, fn):
        QTimer.singleShot(ms, fn)

    def run(self):
        self.app.exec()
