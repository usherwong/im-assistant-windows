# -*- coding: utf-8 -*-
"""按场景分套的判断题（Jev questions）。见 docs/PERSONA.md 3.5 / docs/ROADMAP.md R2。

每套题必须满足「契约」（不然界面和起草小抄会取不到值）：
- 恰好 1 个 score 题（强度分：亲密=紧张度；职场=对方兴趣度）
- 至少 1 个 choice 承担「下一步动作」 → 界面「建议」+ 排序题
- 至少 1 个 choice 承担「对方需要什么」 → 界面「可能需要」
- 可选 noul 题（字面意思之类），跨场景共用
- 每个 choice 的选项都要有中文标签

新增场景 = 在这里加一个 Scene（questions / labels / guide / score_label），别的代码不用动。
"""
from __future__ import annotations

from dataclasses import dataclass

from .questions import CHOICE_LABELS, JUDGE_QUESTIONS


@dataclass(frozen=True)
class Scene:
    key: str
    label: str
    questions: dict
    labels: dict           # {题名: {选项: 中文}}
    guide: tuple           # ((题名, 中文标题), ...) —— 进起草小抄的字段
    score_key: str
    score_label: str       # 界面上那个分数的名字
    noul_key: str | None = None
    noul_label: str = ""


# 字面意思这题跨场景通用，抄一份避免两处维护
def _literal_question() -> dict:
    return {
        "type": "noul",
        "instructions": (
            "Is the other person's latest message meant purely literally, with no subtext? "
            "Judge from the whole thread, not one sentence in isolation."
        ),
        "criteria": {
            "true": ("The latest message is a straightforward statement, question, or plan "
                     "with no implied accusation, test, sarcasm, hint, or unsaid request."),
            "false": ("There is subtext: a test, sarcasm, an implied complaint, a hint they will not say "
                      "outright, a trap question, or a cold/short line that really means something else."),
        },
    }


def _professional(kind: str) -> tuple[dict, dict, tuple]:
    """职场/招聘两套共用结构，只换措辞和选项标签。返回 (questions, labels, guide)。"""
    recruiting = kind == "recruiting"
    score_instruction = (
        "How open is the other person to the opportunity right now? 0 = not interested at all, "
        "9 = actively pushing to move forward. Judge from their latest message and the thread."
        if recruiting else
        "How engaged is the other person in this exchange right now? 0 = cold / minimal, "
        "9 = actively driving it forward. Judge from their latest message and the thread."
    )
    score_criteria = [
        "Cold: one-word replies, no follow-up, clearly not in the mood to talk.",
        "Polite but flat; answers only what was asked.",
        "Mild interest; keeps the conversation alive but asks nothing back.",
        "Asks a clarifying question or reacts with substance.",
        "Shows real interest; brings up their own situation or needs.",
        "Wants details and starts weighing options out loud.",
        "Pushes for specifics (timing, terms, next step) and expects an answer.",
        "Actively wants to move; proposes or accepts a next step.",
        "Driving hard: wants a commitment now, or is ready to commit if you say yes.",
        "Already committed and pushing you to execute.",
    ]
    questions = {
        "literal_question": _literal_question(),
        "engagement": {"type": "score", "instructions": score_instruction, "criteria": score_criteria},
        "stance": {
            "type": "choice",
            "instructions": (
                "Where does the other person stand after their latest message? "
                "Prefer what they actually show over what they politely say."
            ),
            "criteria": {
                "cold": ("Cold or brushing off: very short replies, no questions back, "
                         "changes the subject, or leaves the thread hanging."),
                "weighing": ("Weighing it: asks about details but will not commit, "
                             "compares options, keeps things non-committal."),
                "interested": ("Interested: asks about terms, timing, or the next step, "
                               "or volunteers their own constraints and needs."),
                "declined": ("Declined or closed: says no, not interested, stop asking, "
                             "or clearly ends the topic without room to reopen."),
                "next_step": ("Already agreed on a next step: a call, a meeting, sending a document, "
                              "or an explicit 'let us do X'."),
            },
        },
        "need": {
            "type": "choice",
            "instructions": (
                "What does the other person need from you right now? Judge the LATEST message first. "
                "If they already got what they needed and closed warmly, choose nothing."
            ),
            "criteria": {
                "info": ("Concrete information they are missing: scope, process, terms, timing, "
                         "team, location — they cannot decide without it."),
                "commitment": ("A clear time, promise, or next step from you; they are waiting on you."),
                "understanding": ("To feel heard: they are venting, hesitating, or testing whether you "
                                  "take them seriously. Facts would land badly here."),
                "nothing": ("Nothing further: they accepted, closed the topic, or the ball is in your "
                            "court only for a courtesy reply."),
            },
        },
        "best_action": {
            "type": "choice",
            "instructions": (
                "What type of next action is best? Choose the action, not the wording. "
                "If you do not actually know the fact they asked for, choose verify_first "
                "instead of inventing an answer."
            ),
            "criteria": {
                "give_info": ("Give the concrete information they are missing, short and specific."),
                "ask_question": ("Ask one specific question that moves the conversation forward "
                                 "(their constraint, timing, or what they are comparing)."),
                "propose_time": ("Propose or confirm a concrete time to talk, meet, or send something."),
                "acknowledge": ("Keep it short and human without pushing; they need space or a light reply."),
                "verify_first": ("You do not have the fact yet — look it up or say you will check, "
                                 "do not guess or promise numbers."),
            },
        },
        "should_push": {
            "type": "noul",
            "instructions": (
                "Should this next message move things forward (propose a call, ask for a document, "
                "push for a decision)? Answer false if they need space, already declined, "
                "or the useful move is simply to answer what they asked."
            ),
            "criteria": {
                "true": "They are engaged and a concrete next step is welcome now.",
                "false": "Pushing now would be too much: they are cold, hesitating, angry, or just closed.",
            },
        },
    }
    if recruiting:
        labels = {
            "stance": {"cold": "还没接话或很冷淡", "weighing": "在观望",
                       "interested": "有兴趣，在问细节", "declined": "明确拒绝",
                       "next_step": "已约下一步"},
            "need": {"info": "缺岗位信息", "commitment": "等你给明确安排",
                     "understanding": "需要被理解", "nothing": "无需补充"},
            "best_action": {"give_info": "给岗位信息", "ask_question": "问动机或顾虑",
                            "propose_time": "约电话", "acknowledge": "保持轻，不推进",
                            "verify_first": "先核实对方状态"},
            "engagement": {},
        }
    else:
        labels = {
            "stance": {"cold": "冷淡或敷衍", "weighing": "在观望",
                       "interested": "有兴趣，在问细节", "declined": "明确拒绝",
                       "next_step": "已谈定下一步"},
            "need": {"info": "缺具体信息", "commitment": "等你给明确安排",
                     "understanding": "需要被理解", "nothing": "无需补充"},
            "best_action": {"give_info": "给具体信息", "ask_question": "问一个具体问题",
                            "propose_time": "约时间", "acknowledge": "简短回应，不推进",
                            "verify_first": "先核实再答"},
            "engagement": {},
        }
    guide = (("stance", "对方状态"), ("need", "对方需要"), ("best_action", "建议动作"))
    return questions, labels, guide


_WORK_Q, _WORK_L, _WORK_G = _professional("work")
_RECRUITING_Q, _RECRUITING_L, _RECRUITING_G = _professional("recruiting")

INTIMATE = Scene(
    key="intimate", label="亲密关系",
    questions=JUDGE_QUESTIONS,
    labels=CHOICE_LABELS,
    guide=(("true_intent", "对方意图"), ("she_needs", "对方需要"), ("best_action", "建议动作")),
    score_key="danger_level", score_label="紧张度",
    noul_key="literal_question", noul_label="字面意思",
)
WORK = Scene(
    key="work", label="职场沟通",
    questions=_WORK_Q, labels=_WORK_L, guide=_WORK_G,
    score_key="engagement", score_label="对方投入度",
    noul_key="literal_question", noul_label="字面意思",
)
RECRUITING = Scene(
    key="recruiting", label="招聘/挖人",
    questions=_RECRUITING_Q, labels=_RECRUITING_L, guide=_RECRUITING_G,
    score_key="engagement", score_label="对方兴趣度",
    noul_key="literal_question", noul_label="字面意思",
)

SCENES = {s.key: s for s in (INTIMATE, WORK, RECRUITING)}
DEFAULT = WORK


def get(key: str | None) -> Scene:
    return SCENES.get(key or "", DEFAULT)


def for_role(role_row: dict | None) -> Scene:
    """角色上写的 scene 决定用哪套题；没写/不认识就用默认（职场）。"""
    return get((role_row or {}).get("scene"))


if __name__ == "__main__":
    # 契约自测：每套题都满足「1 score + 动作 choice + 需要 choice + 标签齐全」
    for key, scene in SCENES.items():
        types = [q["type"] for q in scene.questions.values()]
        assert types.count("score") == 1, (key, types)
        assert scene.score_key in scene.questions, (key, scene.score_key)
        assert scene.questions[scene.score_key]["type"] == "score"
        assert any(name == "best_action" for name, _ in scene.guide), key
        assert any(name in ("need", "she_needs") for name, _ in scene.guide), key
        for name, _ in scene.guide:
            assert name in scene.questions, (key, name)
            options = scene.questions[name]["criteria"]
            labels = scene.labels.get(name, {})
            assert set(options) == set(labels), (key, name, set(options) ^ set(labels))
        if scene.noul_key:
            assert scene.questions[scene.noul_key]["type"] == "noul", key
    # 角色 → 题集
    assert for_role({"scene": "recruiting"}).key == "recruiting"
    assert for_role({"scene": "nope"}).key == DEFAULT.key and for_role(None).key == DEFAULT.key
    # 标签能取到中文
    assert RECRUITING.labels["stance"]["weighing"] == "在观望"
    print("scenes ok")
