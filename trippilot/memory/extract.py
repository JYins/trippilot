"""从中文口语对话自动抽取记忆候选。

为什么是规则式而不是 LLM 抽取：
见 docs/decisions/20251005-memory-extraction.md——确定性优先，
无 key 可跑，误抽率靠评测集度量而不是靠模型"感觉"。

本模块只负责"发现候选"：写盘决策（deny / confirm / allow）
全部在 Memory Gate（policy_gate.evaluate_memory_candidate），
这里不复制、更不绕过 Gate 的逻辑。
"""

from __future__ import annotations

import re
from typing import Any

# 敏感兜底：sensitivity 只看内容判定，不依赖某条规则有没有标；
# 规则漏标了这里也能拦住（比如"记住我家地址"被记住型规则抽到时）。
_SENSITIVE_MARKERS = re.compile(
    r"住址|我家|身份证|护照|电话|手机|手机号|生日|密码|验证码")

# 中国大陆手机号：格式固定，基本无歧义
_PHONE_RE = re.compile(r"1[3-9]\d{9}")

# 触发词后面只剩代词/语气词：没信息量，抽到了也是噪音
_JUNK_TAIL = re.compile(r"^(这个|那个|这|那|它|这件事|那件事)?[，。！？~…]*$")

_STRIP_HEAD = "，。！？、：:； \t"
_STRIP_TAIL = "，。！？~… \t"


def _clean(raw: str) -> str:
    return raw.strip(_STRIP_HEAD).strip(_STRIP_TAIL).strip()


def _is_junk(content: str) -> bool:
    # 为什么卡长度：中文偏好至少两个字才有信息（"地铁"），
    # 单字内容（"好"）抽出来只会污染记忆库
    return not content or len(content) < 2 or bool(_JUNK_TAIL.match(content))


def _upgrade_kind(content: str, kind: str) -> str:
    """按内容关键词补 kind：触发词只定"句式"，地点/联系方式
    从内容里认，避免"记住我家地址"被记成 other。"""
    if kind != "other":
        return kind
    if re.search(r"住址|我家|小区|公司|单位|学校|机场|停车场|商场", content):
        return "place"
    if re.search(r"电话|手机|密码|验证码|账号", content):
        return "auth"
    return kind


def _upgrade_sensitivity(content: str, sensitivity: str) -> str:
    if sensitivity == "sensitive":
        return sensitivity
    if _SENSITIVE_MARKERS.search(content) or _PHONE_RE.search(content):
        return "sensitive"
    return sensitivity


# 规则表：每条 = (正则, kind, sensitivity, 置信度, 是否从句首取 content)。
# 置信度只反映"这句话是不是稳定偏好"的把握，不决定写盘（那是 Gate 的事）。
_RULES = [
    {
        "re": re.compile(r"(?:请记住|帮我记住|记住|记一下|记下)(?P<body>.+)"),
        "kind": "other", "sensitivity": "normal", "confidence": 0.90,
        "from_start": False,
        # 为什么 0.90：用户亲口说"记住"就是在下达记忆指令。
        # 误抽只可能来自复述（"他让我记住"），口语第一人称里极少见。
    },
    {
        "re": re.compile(
            r"(?:电话|手机|手机号|电话号码).{0,3}?是?(?P<body>1[3-9]\d{9})"),
        "kind": "auth", "sensitivity": "sensitive", "confidence": 0.90,
        "from_start": False,
        # 为什么 0.90：手机号格式固定，基本无歧义；
        # PII 必须敏感分级，宁可误拦不可漏拦。
    },
    {
        "re": re.compile(r"(?P<body>1[3-9]\d{9})"),
        "kind": "auth", "sensitivity": "sensitive", "confidence": 0.90,
        "from_start": False,
        # 为什么 0.90：同上。裸号码出现在口语里几乎只可能是报电话。
    },
    {
        "re": re.compile(
            r"(?:我家住在|我家在|我住在|我家是|家庭住址)(?P<body>.+)"),
        "kind": "place", "sensitivity": "sensitive", "confidence": 0.90,
        "from_start": False,
        # 为什么 0.90：报地址的句式明确无歧义；高置信抽取是为了确保
        # Gate 一定能拦下来走人工确认。
    },
    {
        "re": re.compile(
            r"(?:叫我|喊我|称呼我)\s*(?P<body>[\u4e00-\u9fa5A-Za-z]{1,8})"),
        "kind": "label", "sensitivity": "normal", "confidence": 0.85,
        "from_start": False,
        # 为什么 0.85：称呼天然稳定，误抽少；比"记住"低一档是因为
        # "叫我"偶尔是玩笑或临时（"叫我一声哥听听"）。
    },
    {
        "re": re.compile(r"(?:生日是|出生日期)(?P<body>.+)"),
        "kind": "other", "sensitivity": "sensitive", "confidence": 0.85,
        "from_start": False,
        # 为什么 0.85：触发词固定、是显式 PII；略低于住址因为生日
        # 句式偶尔出现在闲聊（"我生日是下周"），持久性稍弱。
    },
    {
        "re": re.compile(r"(?:以后都|以后就|以后都得)(?P<body>.+)"),
        "kind": "other", "sensitivity": "normal", "confidence": 0.80,
        "from_start": False,
        # 为什么 0.80："以后都"是显式持久性标记，稳定偏好信号强；
        # 但也可能是对司机的一次性指令（"以后都走这条路"），
        # 所以低于直接的"记住"。
    },
    {
        "re": re.compile(r"我(?:不?喜欢|偏爱|习惯|讨厌|反感)"),
        "kind": "other", "sensitivity": "normal", "confidence": 0.75,
        "from_start": True,
        # 为什么 0.75：偏好动词本身明确；但"喜欢"可能是当下一时情绪
        # （"今天喜欢这首歌"），没有持久性标记时降一档。
    },
    {
        "re": re.compile(r"(?:^|[，。！？])(?:以后别|别再|不要|别)"),
        "kind": "auth", "sensitivity": "normal", "confidence": 0.65,
        "from_start": True,
        # 为什么 0.65（全场最低）：禁止句式确实表达偏好（禁忌/授权），
        # 但和一次性导航指令（"别走这条路"）规则上无法区分。
        # 宁可误抽（走 Gate/人工确认）也不漏记稳定的禁忌偏好。
    },
]


def extract_candidates(text: str, context: dict[str, Any] | None = None
                       ) -> list[dict[str, Any]]:
    """从一句话里抽取记忆候选，返回按置信度降序的候选列表。

    text: 用户本轮说话内容（ASR 文本或直接输入）。
    context: 调用上下文（user_id / session_id），当前规则用不到，
        留给未来规则（如多轮指代消解）用。
    每条候选：content / kind / sensitivity / confidence /
        source_type="extracted"。
    """
    text = (text or "").strip()
    if not text:
        return []

    hits: list[dict[str, Any]] = []
    for rule in _RULES:
        m = rule["re"].search(text)
        if not m:
            continue
        raw = text[m.start():] if rule["from_start"] else m.group("body")
        content = _clean(raw)
        # 口语一句话可能含多个分句（"以后别放广告，记住我喜欢地铁"）：
        # 只取当前分句，避免一条候选吞掉整句话
        content = _clean(re.split(r"[，。！？]", content, maxsplit=1)[0])
        if _is_junk(content):
            continue
        hits.append({
            "content": content,
            "kind": _upgrade_kind(content, rule["kind"]),
            "sensitivity": _upgrade_sensitivity(content,
                                                rule["sensitivity"]),
            "confidence": rule["confidence"],
            "source_type": "extracted",
            "_pos": m.start(),
        })

    # 去重：同一位置后面的命中若被更早命中的 content 包含就丢掉，
    # 避免"记住我家住在望京"同时产出"我家住在望京"和"望京"两条；
    # 完全相同的 content 只留置信度最高的。
    ordered = sorted(hits, key=lambda h: (h["_pos"], -h["confidence"]))
    kept: list[dict[str, Any]] = []
    for h in ordered:
        dup = False
        for o in kept:
            if h["content"] == o["content"]:
                dup = True
                o["confidence"] = max(o["confidence"], h["confidence"])
                break
            if h["content"] in o["content"]:
                dup = True
                break
        if not dup:
            kept.append(h)

    kept.sort(key=lambda h: -h["confidence"])
    return [{k: h[k] for k in ("content", "kind", "sensitivity",
                               "confidence", "source_type")}
            for h in kept]


__all__ = ["extract_candidates"]
