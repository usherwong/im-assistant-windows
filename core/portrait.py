# -*- coding: utf-8 -*-
"""人物画像：把一段时间的对话 + 档案 + 研判结果，合成一份关于「这个人」的观察。

跨对话累积，存在 contacts.json 的 portrait 里；默认按需生成（点「更新画像」），
也可以由主循环在"当天聊完"之后自动跑一次（每人每天最多一次）。

只写从对话里看得到的东西：怎么说、在意什么、兑现没兑现、有什么风险信号。
不给这个人定性，也不猜身份/单位。
"""
from __future__ import annotations

import json
import re

try:
    from .jev_client import JevError, _api_key
    from .llm import chat
    from .providers import DRAFT_PROVIDERS, LLM_ENV
except ImportError:
    from jev_client import JevError, _api_key
    from llm import chat
    from providers import DRAFT_PROVIDERS, LLM_ENV

SYSTEM = (
    "你在为一对一的商务沟通做人物画像。读对话、档案和已有的研判结果，更新一份观察。\n"
    "只输出一个 JSON 对象，键固定：summary, style, concerns, motivators, reliability, risk。\n"
    "- summary：一句话画像（不超过 40 字），写他是怎么看事、怎么做决定的\n"
    "- style：沟通风格（不超过 30 字），只写看得到的：句子长短、主动不主动、问什么类型的问题\n"
    "- concerns：他在意的点，数组，最多 4 条，每条不超过 12 字（预算、审批、出差、背锅…）\n"
    "- motivators：真正推得动他的东西，数组，最多 4 条（技术认可、省事、安全…）\n"
    "- reliability：{\"promised\": 承诺过几次, \"delivered\": 兑现过几次}，只数对话里明确的\n"
    "- risk：{\"compliance\": \"低/中/高\", \"credit\": \"低/中/高\", \"relation\": \"低/中/高\", "
    "\"info\": \"低/中/高\"}，商业风险，没有就给\"低\"\n"
    "规则：只根据材料里写的东西；不猜身份、单位、职务真假；"
    "新旧观察冲突时，用新证据更新，但在 summary 里保留变化（例如“从观望转为推进”）；"
    "材料不够就少写，别凑。"
)


def build_prompt(messages, role=None, contact=None, answers=None, metrics=None,
                 previous=None, keep: int = 60) -> str:
    parts = []
    if contact:
        bits = [f"{k}：{contact[k]}" for k in
                ("display_name", "company", "position", "source", "stage", "needs", "notes")
                if str(contact.get(k) or "").strip()]
        if bits:
            parts.append("对方档案（用户手填的）：\n" + "\n".join(f"- {b}" for b in bits))
    if previous:
        parts.append("上一版画像（供参考，有变化就更新）：\n" + json.dumps(previous, ensure_ascii=False))
    if answers:
        picked = {k: (v.get("choice") or v.get("score"))
                  for k, v in answers.items() if k != "best_reply"}
        if picked:
            parts.append("这一轮的判断结果：\n" + json.dumps(picked, ensure_ascii=False))
    if metrics:
        parts.append("本地指标：" + json.dumps(
            {k: metrics.get(k) for k in ("her_count", "me_count", "avg_len", "question_ratio",
                                         "descriptive_ratio", "per_day", "median_gap_min")},
            ensure_ascii=False))
    lines = []
    for item in list(messages)[-keep:]:
        if isinstance(item, dict):
            who, text, name = item.get("from"), item.get("text"), item.get("name")
        else:
            who, text = item[0], item[1]
            name = item[2] if len(item) > 2 else None
        speaker = name or ("我" if who == "me" else "TA")
        lines.append(f"{speaker}: {str(text).strip()}")
    parts.append("对话原文（最后一条最新）：\n<<<对话开始>>>\n" + "\n".join(lines) + "\n<<<对话结束>>>")
    return "\n\n".join(parts)


def _norm(data: dict, previous: dict | None = None) -> dict:
    """把模型输出收敛成固定形状；缺的字段保留旧值，别把已有画像洗掉。"""
    previous = previous or {}

    def _list(value, cap=4):
        if isinstance(value, str):
            value = [part for part in re.split(r"[、,，;；\n]", value) if part.strip()]
        if not isinstance(value, list):
            return list(previous.get("concerns") or [])[:cap] if cap == 4 else []
        return [str(v).strip()[:20] for v in value if str(v).strip()][:cap]

    reliability = data.get("reliability") if isinstance(data.get("reliability"), dict) else {}
    old_rel = previous.get("reliability") or {}
    risk = data.get("risk") if isinstance(data.get("risk"), dict) else {}
    old_risk = previous.get("risk") or {}
    levels = ("低", "中", "高")
    return {
        "summary": str(data.get("summary") or previous.get("summary") or "").strip()[:60],
        "style": str(data.get("style") or previous.get("style") or "").strip()[:40],
        "concerns": _list(data.get("concerns")) or list(previous.get("concerns") or [])[:4],
        "motivators": _list(data.get("motivators")) or list(previous.get("motivators") or [])[:4],
        "reliability": {
            "promised": int(reliability.get("promised", old_rel.get("promised", 0)) or 0),
            "delivered": int(reliability.get("delivered", old_rel.get("delivered", 0)) or 0),
        },
        "risk": {k: (risk.get(k) if risk.get(k) in levels else old_risk.get(k, "低"))
                 for k in ("compliance", "credit", "relation", "info")},
    }


def update_portrait(messages, *, role=None, contact=None, answers=None, metrics=None,
                    previous=None, provider: str = "deepseek", model: str | None = None,
                    base_url: str | None = None, timeout: float = 45) -> dict:
    """跑一次画像更新。返回 {"summary", "style", "concerns", "motivators",
    "reliability", "risk", "updated_at", "based_on"}。"""
    spec = DRAFT_PROVIDERS[provider]
    user = build_prompt(messages, role=role, contact=contact, answers=answers,
                        metrics=metrics, previous=previous)
    key = _api_key(LLM_ENV)
    content = chat(spec.protocol, base_url or spec.base, key, model or spec.default, SYSTEM, [user],
                   temperature=0.3, max_tokens=700, thinking=False,
                   extra_body=None, headers=spec.headers, timeout=timeout)
    text = re.sub(r"^```(?:json)?|```$", "", str(content or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception as exc:
        raise JevError(f"画像结果解析不出来: {str(content)[:120]!r}") from exc
    portrait = _norm(data, previous)
    portrait["updated_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    portrait["based_on"] = {"messages": (metrics or {}).get("her_count") or 0}
    return portrait


def to_text(portrait: dict) -> str:
    """画像 → 界面上几行文字（空字段跳过）。"""
    if not portrait:
        return ""
    lines = []
    if portrait.get("summary"):
        lines.append(portrait["summary"])
    if portrait.get("style"):
        lines.append("风格：" + portrait["style"])
    if portrait.get("concerns"):
        lines.append("在意：" + "、".join(portrait["concerns"]))
    if portrait.get("motivators"):
        lines.append("吃得动：" + "、".join(portrait["motivators"]))
    rel = portrait.get("reliability") or {}
    if rel.get("promised"):
        lines.append(f"兑现：承诺 {rel['promised']} 次 / 兑现 {rel.get('delivered', 0)} 次")
    risk = portrait.get("risk") or {}
    high = [(k, v) for k, v in (("合规", risk.get("compliance")), ("信用", risk.get("credit")),
                               ("关系", risk.get("relation")), ("信息", risk.get("info")))
            if v and v != "低"]
    if high:
        lines.append("风险：" + " ｜ ".join(f"{k} {v}" for k, v in high))
    return "\n".join(lines)


if __name__ == "__main__":
    assert _norm({"summary": "预算敏感、怕背锅", "concerns": "预算、审批"})["concerns"] == ["预算", "审批"]
    merged = _norm({"summary": "更主动了"}, previous={"concerns": ["出差"], "reliability": {"promised": 2, "delivered": 1}})
    assert merged["concerns"] == ["出差"] and merged["reliability"]["delivered"] == 1
    bad_risk = _norm({"risk": {"compliance": "高", "credit": "瞎写"}}, previous={"risk": {"credit": "中"}})
    assert bad_risk["risk"]["compliance"] == "高" and bad_risk["risk"]["credit"] == "中"
    text = to_text({"summary": "s", "concerns": ["预算"], "reliability": {"promised": 3, "delivered": 1},
                    "risk": {"compliance": "中"}})
    assert "报价" not in text and "兑现：承诺 3 次 / 兑现 1 次" in text and "合规 中" in text
    assert to_text({}) == ""
    print("portrait ok")
