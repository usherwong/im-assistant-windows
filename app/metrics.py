# -*- coding: utf-8 -*-
"""本地指标：不花模型调用、不猜，只数。

从"看到消息的时间"算回复频率、响应间隔、字数、问句比例、描述性句子比例。
局限写在明处：时间是**程序看到它的时间**，应用没开着的那几小时不在样本里，
所以一律返回 (值, 样本数)，样本不够就显示"样本不足"，不要拿 1 条消息算频率。
"""
from __future__ import annotations

import re
from datetime import datetime

_CJK = re.compile(r"[\u4e00-\u9fff]")
_QUESTION = re.compile(r"[?？]|吗|呢|么$|怎么|如何|为什么|多少|几[个点号]|是不是|能不能")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _span_days(times: list[datetime]) -> float:
    if len(times) < 2:
        return 0.0
    seconds = (max(times) - min(times)).total_seconds()
    return max(seconds / 86400.0, 1 / 24 / 60)  # 至少一分钟，避免除零


def summarize(history: list, times: list[datetime] | None = None) -> dict:
    """history: [(who, text, name?)]；times: 与 history 对齐的 datetime 列表（可能更短）。

    返回的每个字段都带样本数，界面按样本量决定显不显示。
    """
    times = list(times or [])
    rows = []
    for index, item in enumerate(history):
        who, text = item[0], item[1]
        when = times[index] if index < len(times) else None
        rows.append((who, str(text or ""), when))

    her = [(t, w) for w, t, w in ((r[0], r[1], r[2]) for r in rows) if w is not None]  # 占位，下面重算
    her = [(r[1], r[2]) for r in rows if r[0] == "her"]
    mine = [(r[1], r[2]) for r in rows if r[0] == "me"]

    lengths = [len(_CJK.findall(t)) or len(t.split()) for t, _ in her if t.strip()]
    questions = [1 for t, _ in her if _QUESTION.search(t)]
    emojis = [1 for t, _ in her if _EMOJI.search(t)]

    # 描述性：够长、或带逗号/分号/换行（说明他在展开讲，而不是"行""好的"）
    descriptive = [1 for t, _ in her
                   if len(_CJK.findall(t)) >= 25 or t.count("，") + t.count(",") + t.count("；") >= 2]

    stamps = [w for _, w in her if w is not None]
    days = _span_days(stamps) if len(stamps) >= 2 else 0.0
    span_hours = ((max(stamps) - min(stamps)).total_seconds() / 3600.0) if len(stamps) >= 2 else 0.0
    # 只看到一轮对话（几分钟内连着发）时算"每天几条"会得出 12960 这种鬼数字：
    # 跨度不足 6 小时、或对方消息少于 6 条，就不给频率，界面显示"样本不足"。
    per_day = (len(stamps) / days) if (days > 0 and span_hours >= 6 and len(stamps) >= 6) else None

    # 他的回复间隔：我发完之后，他下一条隔了多久
    gaps = []
    for index, (who, _, when) in enumerate(rows):
        if who != "me" or when is None:
            continue
        for who2, _, when2 in rows[index + 1:]:
            if who2 == "her" and when2 is not None:
                gaps.append((when2 - when).total_seconds() / 60.0)
                break

    return {
        "her_count": len(her),
        "me_count": len(mine),
        "avg_len": (sum(lengths) / len(lengths)) if lengths else None,
        "question_ratio": (len(questions) / len(her)) if her else None,
        "emoji_ratio": (len(emojis) / len(her)) if her else None,
        "descriptive_ratio": (len(descriptive) / len(her)) if her else None,
        "per_day": per_day,
        "span_hours": span_hours,
        "median_gap_min": sorted(gaps)[len(gaps) // 2] if gaps else None,
        "sampled": len(stamps),
    }


def style_of(metrics: dict, stance: str | None = None) -> str:
    """冷 / 中 / 热 —— 只用本地指标，不够就退回判断层给的 stance。"""
    if (metrics.get("her_count") or 0) < 3:
        return {"interested": "热", "weighing": "中", "cold": "冷", "declined": "冷"}.get(stance or "", "样本不足")
    score = 0
    if (metrics.get("avg_len") or 0) >= 20:
        score += 1
    if (metrics.get("question_ratio") or 0) >= 0.3:
        score += 1
    if (metrics.get("descriptive_ratio") or 0) >= 0.3:
        score += 1
    if (metrics.get("emoji_ratio") or 0) >= 0.2:
        score += 1
    if score >= 3:
        return "热"
    if score >= 1:
        return "中"
    return "冷"


def fmt_frequency(metrics: dict) -> str:
    if metrics.get("per_day") is None:
        return "样本不足"
    return f"{metrics['per_day']:.1f} 条/天"


def fmt_volume(metrics: dict) -> str:
    if metrics.get("avg_len") is None:
        return "样本不足"
    return f"均 {metrics['avg_len']:.0f} 字"


if __name__ == "__main__":
    base = datetime(2026, 11, 26, 10, 0)
    short = [("her", "行", base), ("me", "那明天见", base), ("her", "好", base)]
    out = summarize([(w, t) for w, t, _ in short], [x for _, _, x in short])
    assert out["her_count"] == 2 and out["avg_len"] is not None
    assert style_of(out, "cold") in ("冷", "样本不足")

    long_history = [
        ("her", "我这边预算还没定，主要是今年设备科换了负责人，流程要走两轮", datetime(2026, 11, 25, 9, 0)),
        ("me", "理解，那我先把资料整理好", datetime(2026, 11, 25, 9, 10)),
        ("her", "行，你发我邮箱吧，顺便问下你们能做分期吗？", datetime(2026, 11, 25, 9, 40)),
        ("her", "另外验收标准这块我也想看下你们之前的案例，有没有类似的", datetime(2026, 11, 26, 15, 0)),
    ]
    m = summarize([(w, t) for w, t, _ in long_history], [x for _, _, x in long_history])
    assert m["her_count"] == 3 and m["me_count"] == 1
    assert m["question_ratio"] and m["question_ratio"] >= 0.3
    assert m["median_gap_min"] == 30.0, m["median_gap_min"]
    # 一轮之内连着发的几条，不算"每天几条"（跨度不足 6 小时 → 样本不足）
    burst = [("her", "在吗", datetime(2026, 11, 26, 16, 20)),
             ("her", "有个事", datetime(2026, 11, 26, 16, 21)),
             ("her", "你方便吗", datetime(2026, 11, 26, 16, 22))]
    bm = summarize([(w, t) for w, t, _ in burst], [x for _, _, x in burst])
    assert bm["per_day"] is None, bm["per_day"]
    assert fmt_frequency(bm) == "样本不足"
    assert style_of(m, None) in ("中", "热"), style_of(m, None)
    assert fmt_frequency({"per_day": 1.234}) == "1.2 条/天"
    assert fmt_volume({"avg_len": 12.4}) == "均 12 字"
    print("metrics ok")
