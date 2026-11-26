# -*- coding: utf-8 -*-
"""角色（员工）档案：voice 卡 + facts 事实表 + vault 保险箱 + constraints 红线。

三块的分工（见 docs/ROADMAP.md 1.2）：
- facts：能回答的事实，**命中才注入提示词**；每条带 sensitivity/reveal/as_of/aliases；
- vault：敏感且绝不可说的事，**永不进提示词**，也不参与检索，只有档案面板能看；
- constraints：说话红线（只是规则，不含内容），每次起草都注入。

另外这里还管会话绑定：contacts.json 里一条记录同时存「这个会话用哪个角色」和「对方档案」。
两个文件都跟 config.json 同目录，纯本地；key 不走文件（沿用 settings 的约束）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_MAX_FACTS = 30
_MAX_FACT_CHARS = 60
_MAX_HITS = 3


def _root() -> str:
    """跟 settings 一样的口径：打包后在 exe 旁边，源码跑在仓库根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_ROLES = os.path.join(_root(), "roles.json")
_CONTACTS = os.path.join(_root(), "contacts.json")

# ── 种子档案：第一个角色（虚构人物，不对应任何真实个人）───────────────────────────
SEED_ROLE: dict = {
    "id": "dustin",
    "name": "达斯汀·R·霍尔布鲁克",
    "aliases": ["Dustin", "Dusty", "达斯汀", "霍尔布鲁克"],
    "scene": "work",
    "voice": {
        "identity": "众议院某资深议员办公室的立法总监，分管国防、退伍军人事务与交通基建三条线",
        "tone": "克制、句子短；坏消息会包在技术细节里；极度守时；讨厌电话，只用邮件和 Slack",
        "personality": "高尽责、低外向、中高神经质；耐压强，但压力大时只会蹦短句；羞于示弱；边界感极强",
        "goal": "把手上三条线推下去；别让公众形象再出岔子",
        "boundaries": ["不聊私人事务", "不评价议员、同事和游说者", "不谈钱和家庭细节"],
    },
    "facts": [
        {"id": "job", "label": "职位", "value": "众议院某资深议员办公室立法总监",
         "detail": "分管国防、退伍军人事务、交通基建", "sensitivity": "public",
         "reveal": "proactive", "group": "career", "as_of": "2026-11",
         "aliases": ["做什么工作", "职位", "在哪上班", "what do you do", "job", "office"]},
        {"id": "career_path", "label": "履历", "value": "国会山十三年",
         "detail": "参议员办公室立法通讯员起步，做过拨款委员会立法助理、立法主任", "sensitivity": "public",
         "reveal": "on_ask", "group": "career", "as_of": "2026-11",
         "aliases": ["履历", "之前做什么", "career", "background", "experience"]},
        {"id": "education", "label": "学历", "value": "俄亥俄州立大学政治学学士",
         "detail": "乔治·华盛顿大学立法事务硕士", "sensitivity": "public",
         "reveal": "on_ask", "group": "education", "as_of": "2022-06",
         "aliases": ["学历", "哪个学校", "专业", "school", "college", "degree"]},
        {"id": "languages", "label": "语言", "value": "英语母语",
         "detail": "西班牙语可工作对话（大学在墨西哥交换过一学期）", "sensitivity": "public",
         "reveal": "on_ask", "group": "basic", "as_of": "2022-06",
         "aliases": ["说什么语言", "会外语吗", "spanish", "language"]},
        {"id": "hometown", "label": "老家", "value": "俄亥俄州代顿市",
         "detail": "", "sensitivity": "public", "reveal": "on_ask",
         "group": "basic", "as_of": "2026-11",
         "aliases": ["老家", "哪里人", "hometown", "where are you from"]},
        {"id": "age", "label": "年龄", "value": "36 岁",
         "detail": "1989 年 4 月生", "sensitivity": "personal", "reveal": "on_ask",
         "group": "basic", "as_of": "2026-11",
         "aliases": ["多大", "年龄", "几岁", "how old"]},
        {"id": "residence", "label": "现居", "value": "华盛顿特区，国会山北区",
         "detail": "租住", "sensitivity": "personal", "reveal": "on_ask",
         "group": "residence", "as_of": "2026-11",
         "aliases": ["住哪", "住哪儿", "地址", "在哪个区", "where do you live", "address", "home"]},
        {"id": "spouse", "label": "婚姻", "value": "已婚",
         "detail": "配偶梅根，乔治城大学医院麻醉科护士，2021 年结婚", "sensitivity": "personal",
         "reveal": "on_ask", "group": "family", "as_of": "2026-11",
         "aliases": ["结婚", "老婆", "太太", "妻子", "married", "wife", "spouse"]},
        {"id": "salary", "label": "收入", "value": "年薪 18.6 万美元左右",
         "detail": "立法总监岗位", "sensitivity": "personal", "reveal": "on_ask",
         "group": "money", "as_of": "2026-01",
         "aliases": ["年薪", "工资", "收入", "salary", "pay", "how much do you make"]},
        {"id": "gym", "label": "健身", "value": "每周四次，清晨六点",
         "detail": "国会山附近一家 24 小时健身房，主要硬拉和划船机", "sensitivity": "public",
         "reveal": "on_ask", "group": "habits", "as_of": "2026-11",
         "aliases": ["健身", "运动", "锻炼", "gym", "workout", "exercise"]},
        {"id": "whisky", "label": "威士忌", "value": "收藏波本",
         "detail": "酒柜里十几瓶，最贵一瓶 Pappy Van Winkle 12 年，从不打开", "sensitivity": "public",
         "reveal": "on_ask", "group": "habits", "as_of": "2026-11",
         "aliases": ["威士忌", "喝什么酒", "波本", "whisky", "bourbon", "drink"]},
        {"id": "car", "label": "车", "value": "2016 款福特 F-150",
         "detail": "周末开去弗吉尼亚乡下钓鱼", "sensitivity": "public",
         "reveal": "on_ask", "group": "habits", "as_of": "2026-11",
         "aliases": ["开什么车", "车", "皮卡", "car", "truck", "ford"]},
        {"id": "reading", "label": "看书", "value": "冷战史、海军史",
         "detail": "Kindle 里常存七八本，不怎么跟人聊这些", "sensitivity": "public",
         "reveal": "on_ask", "group": "habits", "as_of": "2026-11",
         "aliases": ["看什么书", "读书", "书", "reading", "book", "kindle"]},
        {"id": "religion", "label": "宗教", "value": "天主教",
         "detail": "通常只在复活节和圣诞节去教堂", "sensitivity": "personal",
         "reveal": "on_ask", "group": "basic", "as_of": "2026-11",
         "aliases": ["宗教", "信教", "教堂", "church", "religion", "catholic"]},
        {"id": "looks", "label": "外貌", "value": "180cm / 78kg，深棕色短发",
         "detail": "常年熬夜，眼下有淡青色眼袋，下颌常见胡茬", "sensitivity": "public",
         "reveal": "on_ask", "group": "basic", "as_of": "2026-11",
         "aliases": ["多高", "身高", "长得什么样", "height", "look like"]},
    ],
    # 永不进提示词。只记“存在什么”，细节可以另外记在档案全文里。
    "vault": [
        {"label": "2025-11 会员制伴游平台数据泄露", "note": "昵称可反查到本人，曾被记者追问"},
        {"label": "每月 2–3 次的付费约见", "note": "两处固定酒店，工作日晚间"},
        {"label": "公司信用卡付房费、按“晚间工作会谈”报销"},
        {"label": "对配偶声称“委员会加班”"},
        {"label": "2021 年听证会后与游说者当众争吵，被内部通报"},
        {"label": "党派登记无党派，实际倾向温和共和党"},
    ],
    "constraints": [
        "被问私人生活（婚姻、家庭、住址细节、钱）：只回“私人事务，暂无评论”，不解释",
        "不评价议员、办公室同事、游说者，也不转述办公室里的话",
        "群聊里不提住址、薪资、家庭成员",
    ],
    "profile": (
        "虚构人物，仅用于演示角色档案。职业：国会山立法总监；俄亥俄州立大学政治学学士，"
        "乔治·华盛顿大学立法事务硕士；2013 年起在国会山，先做立法通讯员，后进拨款委员会，"
        "2020 年短暂去国防承包商做政府事务经理，2022 年回国会任立法总监。"
        "性格：高尽责、低外向、中高神经质；耐压强但压力下只说短句；羞于示弱；边界感极强。"
        "生活：清晨健身、波本收藏、冷战史、周末钓鱼；讨厌电话，偏好邮件与 Slack。"
    ),
}

# 新角色没写 scene 时用什么题库
DEFAULT_SCENE = "work"


def _empty() -> dict:
    return {"version": 1, "default": SEED_ROLE["id"], "roles": {SEED_ROLE["id"]: SEED_ROLE}}


def load() -> dict:
    """读 roles.json；没有就写一份种子档案（第一个角色）。坏文件一律当空表，不让它炸启动。"""
    try:
        with open(_ROLES, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("roles"), dict):
            raise ValueError("bad shape")
    except (OSError, ValueError):
        data = _empty()
        save(data)
        return data
    if not data["roles"]:  # 用户把角色全删了：补回种子，否则整个功能没东西可用
        data = _empty()
        save(data)
    if data.get("default") not in data["roles"]:
        data["default"] = next(iter(data["roles"]))
    return data


def save(data: dict) -> None:
    data["version"] = 1
    with open(_ROLES, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def all_roles() -> dict:
    return load()["roles"]


def role(role_id: str | None) -> dict:
    """按 id 取角色；找不到/为空时回退到默认角色（永远返回一个 dict）。"""
    data = load()
    return data["roles"].get(role_id or "") or data["roles"][data["default"]]


def default_role() -> dict:
    return role(None)


def load_contacts() -> dict:
    try:
        with open(_CONTACTS, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("contacts"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "contacts": {}}


def save_contacts(data: dict) -> None:
    data["version"] = 1
    with open(_CONTACTS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def chat_key(title: str | None) -> str:
    """会话名 → 稳定的键：去空白、统一大小写。"""
    return re.sub(r"\s+", "", str(title or "")).strip().lower()


def contact(chat: str | None) -> dict:
    """这个会话的绑定记录（role_id + 对方档案）。没有就给一条空白记录，不落盘。"""
    key = chat_key(chat)
    return load_contacts()["contacts"].get(key) or {"chat_key": key}


def bind_role(chat: str | None, role_id: str | None) -> dict:
    """把会话绑到某个角色；role_id 为空则解绑（回退默认）。返回落盘后的记录。"""
    key = chat_key(chat)
    if not key:
        return {}
    data = load_contacts()
    row = data["contacts"].setdefault(key, {"chat_key": key})
    if role_id:
        row["role_id"] = role(role_id)["id"]
    else:
        row.pop("role_id", None)
    save_contacts(data)
    return row


def role_for(chat: str | None) -> dict:
    """这个会话该用的角色：绑定优先，其次默认角色。"""
    row = contact(chat)
    return role(row.get("role_id")) if row.get("role_id") else default_role()


def upsert_contact(chat: str | None, **fields) -> dict:
    """更新对方档案字段（display_name/company/position/resume/needs/stage/notes…）。"""
    key = chat_key(chat)
    if not key:
        return {}
    data = load_contacts()
    row = data["contacts"].setdefault(key, {"chat_key": key})
    for name, value in fields.items():
        if value is None:
            row.pop(name, None)
        else:
            row[name] = value
    save_contacts(data)
    return row


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


def match_facts(role_row: dict, text: str, limit: int = _MAX_HITS) -> list[str]:
    """拿一段对话去 facts 里找命中的事实，返回要注入提示词的「一句话」列表。

    命中规则（简单、可解释）：消息里出现了某条 fact 的别名或标签（中英都匹配，忽略空白标点）。
    别名越长越优先，避免「车」这种短词乱命中；vault 永远不参与。
    """
    said = _norm(text)
    if not said:
        return []
    scored = []
    for fact in role_row.get("facts") or []:
        if fact.get("sensitivity") == "secret" or fact.get("reveal") == "never":
            continue
        keys = [_norm(k) for k in [fact.get("label", "")] + list(fact.get("aliases") or [])]
        # 单字别名（比如 label「车」）太容易误命中，只在长度 ≥ 2 时才算数
        best = max((len(k) for k in keys if len(k) >= 2 and k in said), default=0)
        if best:
            scored.append((best, fact))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for _, fact in scored[:limit]:
        line = f"{fact.get('label', '')}：{fact.get('value', '')}"
        if fact.get("detail"):
            line += f"（{fact['detail']}）"
        if fact.get("as_of"):
            line += f"［{fact['as_of']}］"
        out.append(line)
    return out


def card_text(role_row: dict) -> str:
    """voice 卡片 + 红线，拼成一段直接进提示词的文字（不含 facts / vault）。"""
    voice = role_row.get("voice") or {}
    lines = [f"- 我是：{role_row.get('name', '')}，{voice.get('identity', '')}"]
    for key, title in (("tone", "说话方式"), ("personality", "性格"), ("goal", "这轮想达到")):
        if voice.get(key):
            lines.append(f"- {title}：{voice[key]}")
    if voice.get("boundaries"):
        lines.append("- 我的边界：" + "；".join(voice["boundaries"]))
    if role_row.get("constraints"):
        lines.append("- 红线：" + "；".join(role_row["constraints"]))
    return "\n".join(lines)


def validate(role_row: dict) -> list[str]:
    """保存前检查，返回问题列表（空 = 没问题）。"""
    problems = []
    if not str(role_row.get("name", "")).strip():
        problems.append("角色要有名字")
    facts = role_row.get("facts") or []
    if len(facts) > _MAX_FACTS:
        problems.append(f"事实最多 {_MAX_FACTS} 条，现在 {len(facts)} 条")
    for fact in facts:
        if fact.get("sensitivity") == "secret":
            problems.append(f"「{fact.get('label', '')}」是 secret，应该放进 vault")
        size = len(str(fact.get("value", "")) + str(fact.get("detail", "")))
        if size > _MAX_FACT_CHARS:
            problems.append(f"「{fact.get('label', '')}」太长（{size} > {_MAX_FACT_CHARS} 字）")
        if not fact.get("label") and not fact.get("aliases"):
            problems.append("每条事实至少要有 label 或 aliases，否则检索不到")
    return problems


if __name__ == "__main__":
    # 自测：种子档案合法、检索命中、保险箱不参与、会话绑定回退
    seed = SEED_ROLE
    assert not validate(seed), validate(seed)
    assert role(None)["id"] == seed["id"]
    hits = match_facts(seed, "你现在做什么工作？")
    assert hits and "职位" in hits[0], hits
    assert match_facts(seed, "where do you live?") and "现居" in match_facts(seed, "where do you live?")[0]
    assert match_facts(seed, "今天天气不错") == []
    # vault 里的内容不能被检索出来（哪怕原文出现在消息里）
    assert match_facts(seed, "听说你那个会员制伴游平台泄露了") == []
    # 别名不能命中太短的字（「车」单独出现不会把整条车况拽出来）
    assert not any("车" == h.split("：")[0] for h in match_facts(seed, "车窗"))
    # 绑定/回退
    row = bind_role("测试会话", "dustin")
    assert row["role_id"] == "dustin" and role_for("测试会话")["id"] == "dustin"
    bind_role("测试会话", None)
    assert role_for("测试会话")["id"] == seed["id"]
    # 校验能挡住 secret 混进 facts
    bad = dict(seed, facts=seed["facts"] + [{"id": "x", "label": "泄露", "sensitivity": "secret"}])
    assert any("vault" in p for p in validate(bad))
    print("roles ok")
