# -*- coding: utf-8 -*-
"""研判：拆成「聊天研判」和「风险研判」两块，规则写死、可解释。

聊天研判：这段对话现在什么状态、能不能往前走（推进/维持/放慢/先解决顾虑/收手）。
风险研判：这次交流里有没有要警惕的信号（合规/信用/关系/信息 四类，取最高等级）。
两块各算各的，互不改写；风险判到高时只给聊天研判加一句提示。

判断的是"这次交流"，不是给这个人定性。风险只做商业四类。
"""
from __future__ import annotations

try:
    from .scenes import get as scene_of
except ImportError:  # 脚本直跑
    from scenes import get as scene_of

_LEVELS = {"none": "低", "soft": "中", "high": "高"}
_RANK = {"低": 0, "中": 1, "高": 2}
_DISPOSITION = {"低": "正常推进", "中": "留意", "高": "谨慎相处，别答应私下条件"}


def _choice(answers: dict, name: str) -> str | None:
    return ((answers or {}).get(name) or {}).get("choice")


def _score(answers: dict, scene, key: str | None = None) -> float | None:
    name = key or (scene.score_key if scene is not None else "engagement")
    value = ((answers or {}).get(name) or {}).get("score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ── 聊天研判 ────────────────────────────────────────────────────────────────

def chat_read(answers: dict, metrics: dict | None = None, scene=None, style: str | None = None) -> dict:
    if style is None:
        try:
            from app.metrics import style_of
        except ImportError:
            from metrics import style_of
        style = style_of(metrics or {}, _choice(answers, "stance"))
    stance = _choice(answers, "stance")
    willingness = _choice(answers, "willingness")
    commitment = _choice(answers, "commitment")
    delivery = _choice(answers, "delivery")
    concern = _choice(answers, "concern")
    engagement = _score(answers, scene)

    if stance == "declined":
        conclusion = {"text": "收手", "level": "stop", "why": "对方已经明确拒绝"}
    elif concern in ("still_open", "reopened"):
        conclusion = {"text": "先解决顾虑", "level": "hold",
                      "why": "对方的顾虑还没落地，这时候推进会被当成催"}
    elif commitment == "polite_deferral" and delivery == "no_commitment_yet":
        conclusion = {"text": "维持", "level": "hold",
                      "why": "对方只是客气，没有具体承诺；这轮继续给价值"}
    elif willingness in ("winding_down", "polite_only"):
        conclusion = {"text": "放慢", "level": "slow",
                      "why": "对方在收尾或只礼貌回应，追得越紧越远"}
    elif stance == "interested" and (engagement is None or engagement >= 5):
        conclusion = {"text": "推进", "level": "go",
                      "why": "对方在问细节、接话积极，可以往前推一步"}
    elif stance == "weighing":
        conclusion = {"text": "维持", "level": "hold", "why": "对方在观望，先补信息、少提要求"}
    else:
        conclusion = {"text": "维持", "level": "hold", "why": "信号不足以判断，保持现状"}

    return {
        "conclusion": conclusion,
        "style": style,
        "willingness": willingness,
        "engagement": engagement,
        "chips": _chips(metrics or {}, style, answers, scene),
    }


def _chips(metrics: dict, style: str, answers: dict, scene) -> list[str]:
    """界面那一行标签，这里只给「名字 + 原值」，翻译/拼接交给界面。"""
    out = [("风格", style)]
    if answers.get("willingness"):
        out.append(("意愿", _choice(answers, "willingness")))
    if metrics.get("her_count"):
        out.append(("频率", metrics.get("per_day")))
        out.append(("信息量", metrics.get("avg_len")))
    return [f"{k}|{v}" for k, v in out]


# ── 风险研判 ────────────────────────────────────────────────────────────────

def risk_items(answers: dict, scene) -> list[dict]:
    """四类风险，只返回中/高的，重的排前面。"""
    items: list[dict] = []
    compliance = _LEVELS.get(_choice(answers, "risk_compliance") or "none", "低")
    if compliance != "低":
        items.append({"kind": "合规", "level": compliance,
                      "why": "对方提出私下条件或灰色流程" if compliance == "高"
                             else "对方在试探有没有操作空间"})
    info = _LEVELS.get(_choice(answers, "risk_info") or "none", "低")
    if info != "低":
        items.append({"kind": "信息", "level": info,
                      "why": "对方要截图/换渠道或打探底价、名单" if info == "高"
                             else "对方问到敏感信息，但还没施压"})
    delivery = _choice(answers, "delivery")
    if delivery == "committed_not_delivered":
        items.append({"kind": "信用", "level": "中", "why": "承诺过的事还没兑现"})
    elif delivery == "partially_delivered":
        items.append({"kind": "信用", "level": "低", "why": "只兑现了一部分"})
    stance = _choice(answers, "stance")
    if stance == "declined":
        items.append({"kind": "关系", "level": "高", "why": "对方已明确拒绝或要求停止"})
    elif stance == "cold":
        items.append({"kind": "关系", "level": "中", "why": "对方明显冷淡"})

    items.sort(key=lambda item: _RANK.get(item["level"], 0), reverse=True)
    return items


def risk_read(answers: dict, scene) -> dict:
    items = risk_items(answers, scene)
    level = max((item["level"] for item in items), key=lambda v: _RANK.get(v, 0), default="低")
    return {
        "level": level,
        "disposition": _DISPOSITION[level],
        "items": items,
        "why": items[0]["why"] if items else "没有值得警惕的信号",
    }


# ── 合成 ────────────────────────────────────────────────────────────────────

def assess(answers: dict, metrics: dict | None = None, scene=None, style: str | None = None) -> dict:
    """{chat: {...}, risk: {...}}；null 表示这次没够料（界面就不显示对应卡）。"""
    scene = scene if scene is not None else scene_of(None)
    risk = risk_read(answers, scene)
    chat = chat_read(answers, metrics or {}, scene, style)
    chat["risk_warning"] = risk["level"] == "高"
    return {"chat": chat, "risk": risk}


if __name__ == "__main__":
    scene = scene_of("recruiting")

    def ans(**kw):
        base = {"engagement": {"score": 6}}
        base.update({k: {"choice": v} for k, v in kw.items()})
        return base

    good = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                      delivery="delivered_once", concern="resolved", risk_compliance="none",
                      risk_info="none"), {"her_count": 8, "avg_len": 30, "question_ratio": .4,
                                          "descriptive_ratio": .5, "emoji_ratio": .1, "per_day": 2}, scene)
    assert good["chat"]["conclusion"]["level"] == "go", good["chat"]["conclusion"]
    assert good["risk"]["level"] == "低" and good["chat"]["risk_warning"] is False

    defer = assess(ans(stance="weighing", willingness="polite_only", commitment="polite_deferral",
                       delivery="no_commitment_yet", concern="resolved"), {}, scene)
    assert defer["chat"]["conclusion"]["level"] == "hold", defer["chat"]["conclusion"]

    risky = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                       delivery="delivered_once", concern="resolved", risk_compliance="high"), {}, scene)
    assert risky["risk"]["level"] == "高" and risky["chat"]["risk_warning"] is True
    assert risky["chat"]["conclusion"]["level"] == "go", "风险不改写聊天结论"
    assert risky["risk"]["items"][0]["kind"] == "合规"

    open_concern = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                              delivery="delivered_once", concern="still_open"), {}, scene)
    assert open_concern["chat"]["conclusion"]["level"] == "hold"

    gone = assess(ans(stance="declined", willingness="declined"), {}, scene)
    assert gone["chat"]["conclusion"]["level"] == "stop"
    assert any(i["kind"] == "关系" and i["level"] == "高" for i in gone["risk"]["items"])
    print("judgement ok")
