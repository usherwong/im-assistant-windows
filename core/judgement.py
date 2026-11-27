# -*- coding: utf-8 -*-
"""研判：把判断层的答案 + 本地指标合成「结论 + 风险」，规则写死、可解释。

分层：
- 风格（冷/中/热）→ app.metrics 本地算；
- 接触意愿 / 风险信号 → Jev 的题（willingness / risk_compliance / risk_info）；
- 信用风险 → 兑现记录（delivery）；关系风险 → 对方状态（stance）；
- 结论由这里按固定优先级合成，**不让模型自由发挥**，每条都给依据。

风险只做商业四类：合规 / 信用 / 关系 / 信息。判断的是"这次交流里有没有值得警惕的信号"，
不是给这个人定性。
"""
from __future__ import annotations

try:
    from .scenes import get as scene_of
except ImportError:  # 脚本直跑
    from scenes import get as scene_of

_LEVELS = {"none": "低", "soft": "中", "high": "高"}
_LEVEL_RANK = {"低": 0, "中": 1, "高": 2}


def _choice(answers: dict, name: str) -> str | None:
    item = (answers or {}).get(name) or {}
    return item.get("choice")


def _score(answers: dict, scene, key: str | None = None) -> float | None:
    name = key or (scene.score_key if scene is not None else "engagement")
    item = (answers or {}).get(name) or {}
    value = item.get("score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _risk(kind: str, level: str, why: str) -> dict:
    return {"kind": kind, "level": level, "why": why}


def risks_of(answers: dict, scene) -> list[dict]:
    """四类风险，只返回中/高的，按严重程度排。"""
    out: list[dict] = []
    compliance = _LEVELS.get(_choice(answers, "risk_compliance") or "none")
    if compliance and compliance != "低":
        out.append(_risk("合规", compliance,
                         "对方提出私下条件或灰色流程" if compliance == "高" else "对方在试探有没有操作空间"))
    info = _LEVELS.get(_choice(answers, "risk_info") or "none")
    if info and info != "低":
        out.append(_risk("信息", info,
                         "对方要截图/换渠道或打探底价、名单" if info == "高" else "对方问到敏感信息，但还没施压"))

    delivery = _choice(answers, "delivery")
    if delivery == "committed_not_delivered":
        out.append(_risk("信用", "中", "承诺过的事还没兑现"))
    elif delivery == "partially_delivered":
        out.append(_risk("信用", "低", "只兑现了一部分"))

    stance = _choice(answers, "stance")
    if stance == "declined":
        out.append(_risk("关系", "高", "对方已明确拒绝或要求停止"))
    elif stance == "cold":
        out.append(_risk("关系", "中", "对方明显冷淡"))

    out.sort(key=lambda item: _LEVEL_RANK.get(item["level"], 0), reverse=True)
    return out


def conclude(answers: dict, scene, metrics: dict | None = None) -> dict:
    """结论：推进 / 维持 / 放慢 / 先解决顾虑 / 谨慎相处 / 收手。带一句依据。"""
    risks = risks_of(answers, scene)
    top = risks[0]["level"] if risks else "低"
    compliance = _LEVELS.get(_choice(answers, "risk_compliance") or "none")
    info = _LEVELS.get(_choice(answers, "risk_info") or "none")
    stance = _choice(answers, "stance")
    willingness = _choice(answers, "willingness")
    commitment = _choice(answers, "commitment")
    delivery = _choice(answers, "delivery")
    concern = _choice(answers, "concern")
    engagement = _score(answers, scene)

    if compliance == "高" or info == "高":
        return {"text": "谨慎相处", "level": "caution",
                "why": "有高等级风险信号，别答应私下条件、别给底价或名单"}
    if stance == "declined":
        return {"text": "收手", "level": "stop", "why": "对方已经明确拒绝"}
    if concern in ("still_open", "reopened"):
        return {"text": "先解决顾虑", "level": "hold", "why": "对方的顾虑还没落地，这时候推进会被当成催"}
    if top == "高":
        return {"text": "谨慎相处", "level": "caution", "why": "风险信号偏重，先降速"}
    if commitment == "polite_deferral" and delivery == "no_commitment_yet":
        return {"text": "维持", "level": "hold", "why": "对方只是客气，没有具体承诺；这轮继续给价值"}
    if willingness in ("winding_down", "polite_only"):
        return {"text": "放慢", "level": "slow", "why": "对方在收尾或只礼貌回应，追得越紧越远"}
    if stance == "interested" and (engagement is None or engagement >= 5):
        return {"text": "推进", "level": "go", "why": "对方在问细节、接话积极，可以往前推一步"}
    if stance == "weighing":
        return {"text": "维持", "level": "hold", "why": "对方在观望，先补信息、少提要求"}
    return {"text": "维持", "level": "hold", "why": "信号不足以判断，保持现状"}


def assess(answers: dict, metrics: dict | None = None, scene=None, style: str | None = None) -> dict:
    """给界面用的一份研判：结论 + 四类风险 + 几个展示用标签。"""
    metrics = metrics or {}
    scene = scene if scene is not None else scene_of(None)
    if style is None:
        try:
            from app.metrics import style_of
        except ImportError:
            from metrics import style_of
        style = style_of(metrics, _choice(answers, "stance"))
    conclusion = conclude(answers, scene, metrics)
    return {
        "style": style,
        "willingness": _choice(answers, "willingness"),
        "conclusion": conclusion,
        "risks": risks_of(answers, scene),
        "engagement": _score(answers, scene),
    }


if __name__ == "__main__":
    scene = scene_of("recruiting")

    def ans(**kw):
        return {k: {"choice": v} for k, v in kw.items()} | {"engagement": {"score": 6}}

    good = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                      delivery="delivered_once", concern="resolved", risk_compliance="none",
                      risk_info="none"), {"her_count": 8, "avg_len": 30, "question_ratio": .4,
                                          "descriptive_ratio": .5, "emoji_ratio": .1, "per_day": 2}, scene)
    assert good["conclusion"]["level"] == "go", good["conclusion"]
    assert good["risks"] == []

    defer = assess(ans(stance="weighing", willingness="polite_only", commitment="polite_deferral",
                       delivery="no_commitment_yet", concern="resolved"), {}, scene)
    assert defer["conclusion"]["level"] == "hold", defer["conclusion"]

    risky = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                       delivery="delivered_once", concern="resolved", risk_compliance="high"), {}, scene)
    assert risky["conclusion"]["text"] == "谨慎相处" and risky["risks"][0]["kind"] == "合规"

    open_concern = assess(ans(stance="interested", willingness="open", commitment="true_commitment",
                              delivery="delivered_once", concern="still_open"), {}, scene)
    assert open_concern["conclusion"]["level"] == "hold", open_concern["conclusion"]

    gone = assess(ans(stance="declined", willingness="declined"), {}, scene)
    assert gone["conclusion"]["level"] == "stop"
    assert any(r["kind"] == "关系" and r["level"] == "高" for r in gone["risks"])
    print("judgement ok")
